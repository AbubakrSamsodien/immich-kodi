# Performance notes

Read this before proposing an optimisation. Several plausible ideas below were
measured and rejected; re-running them wastes your time and mine.

## How to measure

```sh
python3 tests/bench.py
```

Spins the mock Immich against a ten-year library — 120 months, ~47k assets, a
3000-asset album — and reports HTTP requests, item counts and wall clock per
listing. The 22-asset test fixture is too small to show anything.

## What the metric is

**HTTP requests and bytes per listing, not wall clock.**

Kodi freezes the interface until `endOfDirectory` returns, so everything a
listing does is user-visible latency. The mock answers on loopback, so the
milliseconds here are parse cost only and understate a real server by whatever
its round-trip latency is. A listing that issues 4 requests against a LAN
Immich at 15ms is 60ms of frozen UI that the bench reports as free.

Payload weight, measured:

| Response | Bytes per asset |
| --- | --- |
| Full `AssetResponseDto` (album, search) | ~1530 B |
| Columnar timeline bucket | ~255 B |

That 6× difference is why the timeline is cheap and albums are not.

## Ledger

| Idea | Baseline → Result | Verdict | Why |
| --- | --- | --- | --- |
| Server-side paging for search-backed listings (`videos`, `places`, text search) | videos: 3 reqs / 81, 78 ms → 2 reqs / 38, 37 ms | **kept** | `search_metadata` walked every page then sliced out one. On a 2000-video library that was 3.2 MB to render 500 items, repeated per page. Now 0.8 MB, constant. Well outside the ±5 ms run-to-run variance. |
| Server-side paging for album assets | album: 101, 98 ms → 35, 36 ms | **kept** | `GET /albums/{id}` embeds every asset with no paging, so page six of a 3000-asset album re-downloaded all 4.5 MB. Now pages via `search_metadata` with `albumIds`, which exists from v1.135. |
| Caching timeline buckets across pages | not attempted | **rejected on measurement** | A 900-asset month is 224 KiB and 5 ms to fetch, 27 ms end to end. Caching would add a disk cache and an invalidation problem to save ~20 ms. Below the noise floor of the complexity. |
| Eliminating the per-listing `/server/version` call | not attempted | **rejected, already solved** | The bench shows 2 requests per listing because it resets the Kodi session for each case. In use the version is cached in a home-window property for the whole session, so only the first navigation pays. |
| Reusing one HTTP connection across a listing | already in place | n/a | `ImmichClient` opens one keep-alive connection per invocation and closes it in `router.dispatch`'s `finally`. |

## Ordering caveat introduced by album paging

An album's `order` is only `asc`/`desc` (`AlbumResponseDto.order`), not a manual
sequence, and `POST /search/metadata` accepts the same `order`, so paging
preserves the album's ordering. The albums listing forwards `order` in each
album URL for this reason.

`order` is optional on `AlbumResponseDto`. An album that declares none takes
the server default at both ends, which is consistent but is not guaranteed to
match what the Immich web UI shows for that album.

## Guards

Three cases in `tests/suite.py` fail if the paging work is reverted:

- `perf: videos fetches one page, not the whole result set`
- `perf: an album page does not re-download the whole album`
- `perf: a malformed album id is rejected before any request is sent`

Each was verified by reverting the change and confirming the test fails.

## Raspberry Pi 5

The reference target. Cortex-A76 at 2.4GHz is roughly 3-5x slower than a recent
desktop for single-threaded Python, so the figures above scale accordingly:

| Work | Measured here | Estimated on a Pi 5 |
| --- | --- | --- |
| Render a 500-item page (parse + Asset + ListItem) | 20 ms | 60-100 ms |
| The pre-paging album path (3000 DTOs, 4.5 MB) | ~95 ms | ~300-475 ms |

That second row is why the paging work matters more on this hardware than the
bench suggests. The multiplier is an estimate, not a measurement; nothing here
has been profiled on the board itself.

Hardware decode is the one place where the fastest path is counterintuitive:
the Pi 5 has a 4Kp60 HEVC decoder and no hardware H.264 decoder, while Immich
transcodes non-H.264 to H.264 720p by default. For an HEVC library the
*untranscoded* original is both cheaper to decode and higher resolution, which
is what the **Video playback** setting exposes.

## Month preview thumbnails

Measured on the 120-month library:

| Setting | Requests | Local | At 5ms LAN latency |
| --- | --- | --- | --- |
| `month_previews=false` (default) | 2 | 8 ms | ~10 ms |
| `month_previews=true` | 122 | 231 ms | ~610 ms |

`/api/timeline/buckets` returns only `{timeBucket, count}` and Immich has no
per-bucket cover image, so a preview means one `POST /search/metadata` per
month with `size=1`. There is no cheaper route. Hence opt-in, with the cost
stated in the setting's help text.

## Known remaining cost

Timeline buckets are still paged client-side: page two of a 900-asset month
re-fetches the month. That is 224 KiB, and the timeline endpoints removed
`page`/`pageSize` in v1.135, so there is no server-side alternative. Measured
and accepted.
