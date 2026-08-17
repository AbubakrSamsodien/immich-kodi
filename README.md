# Immich for Kodi

An unofficial Kodi add-on for browsing an [Immich](https://immich.app) photo and
video library on your TV.

Browse by month, album, person, place, tag or memory. Search by file name,
description or natural language. Play any listing as a slideshow.

## Requirements

- Kodi 20 (Nexus) or newer. Tested against Kodi 21 (Omega) on LibreELEC 12.
- An Immich server, version 1.133 or newer.
- An Immich API key.

## Install

1. Download `plugin.video.immich.zip` from the releases page, or build it
   yourself with `python3 build.py`.
2. In Kodi, go to **Settings → Add-ons → Install from zip file** and pick the
   zip.
3. Open the add-on. It will prompt for settings on first run.

The add-on lives under **Pictures → Add-ons**, not Videos. That is deliberate:
Kodi's video window has no code path for displaying a still, so a photo opened
from there fails. The Pictures window handles both — it hands videos to the
normal player and everything else to the picture viewer.

Installing a newer zip over an existing install keeps your settings. Kodi stores
them in `userdata/addon_data/plugin.video.immich/`, which the installer never
touches.

## Setup

Create an API key in Immich under **Account Settings → API Keys**, then enter it
in the add-on settings along with your server URL.

The URL must include the scheme and the port, for example
`https://photos.example.com` or `http://192.168.1.10:2283`.

Use **Test connection** to confirm both are correct before browsing. It also
reports the key's permissions, and warns if `asset.download` is missing — that
scope gates the **Original** options for photos and video, and without it Kodi
reports only "playback failed".

The permissions a full experience needs: `timeline.read`, `asset.read`,
`asset.view`, `album.read`, `person.read`, `tag.read`, `memory.read`, and
`asset.download` if you want Original quality. Granting `all` is simplest.

### Browsing photos and videos as one sequence

Clicking any item opens Kodi's picture viewer, which builds a slideshow from
the whole listing and starts at the item you picked. Next and previous then
step through the listing as one continuous sequence: photos display, videos
play, and anything the player cannot handle is skipped.

Videos are only included if **Settings → Media → Pictures → Show video files in
listings** is on (`pictures.showvideos`). With it off you get photos only.

### Two ways to browse

**All media** is one flat chronological listing. Because Kodi builds a
slideshow from the current directory only
(`CGUIWindowPictures::ShowPicture` iterates `m_vecItems`), next and previous can
never cross a folder boundary. So use this if you want to keep pressing next
and carry on past the end of a month.

**Timeline** groups by month, which is better for finding a particular period,
but next stops at the end of each month. There is no way around that in Kodi.

The **Start slideshow** context menu on the Timeline itself runs recursively
across every month, if you want the whole library hands-off.

### Settings worth knowing

| Setting | Default | Notes |
| --- | --- | --- |
| Photo quality | Preview | `Preview` is a 1440px JPEG the server generates, and always displays. `Original` is the untouched file, so HEIC and RAW will not render in Kodi. `Full size` only works if your admin enabled full-size generation; otherwise Immich falls back to preview. |
| Ignore SSL certificate errors | Off | Turn on only for a self-signed certificate on your own network. |
| Include partner photos | Off | Merges libraries a partner shared with you into the timeline. |
| Items per page | 500 | Listings larger than this are split across pages. Also the length of one uninterrupted next/previous run in **All media**. |
| Preview picture on each month | Off | Puts a cover thumbnail on each Timeline month. Costs one request per month: measured on a 120-month library it turns a 2-request menu into 122, roughly 600ms extra before the timeline opens. |
| Video playback | Transcoded | See the Raspberry Pi 5 section. |

## Raspberry Pi 5

Two things about that board change the right settings.

**Video.** The Pi 5 spec lists a *4Kp60 HEVC decoder* and no hardware H.264
decoder — the Pi 4 had one, the Pi 5 dropped it. Immich's default transcode
policy accepts H.264 only (`acceptedVideoCodecs: [H264]`) and re-encodes
everything else to H.264 at 720p. So a phone HEVC clip is served to the Pi as
software-decoded 720p H.264, while the untouched original would decode in
hardware at full resolution.

If most of your videos are HEVC — recent iPhones, most modern Android — set
**Video playback** to *Original file*. Caveats: the API key needs the
`asset.download` scope, and any container the player cannot handle will fail
where the transcode would have worked. Leave it on *Transcoded* if your
library is mostly H.264, which the Pi 5 decodes fine in software at 1080p.

**Photos.** Leave **Photo quality** on *Preview*. The 1440px JPEG the server
generates is already right-sized for a 4K screen, and *Original* makes the Pi
decode full-resolution JPEGs — or fail outright on HEIC and RAW.

Everything else is sized for it already. `<reuselanguageinvoker>` keeps the
Python interpreter alive between navigations, which matters more on a Pi than
on a desktop, and listings page server-side so a large album never materialises
in memory. A 500-item page costs roughly 60-100ms of Python on an A76 —
see `PERF.md`.

## Server compatibility

Immich treats its timeline endpoints as internal and has changed their response
shape more than once. The add-on reads the server version at startup and adapts,
so the same build works across these boundaries:

| Immich version | What changes |
| --- | --- |
| 1.133 | Timeline buckets became columnar. `size=MONTH` stopped doing anything. |
| 1.135 | Bucket `localDateTime` became `fileCreatedAt` plus `localOffsetHours`. |
| 3.0 | Album responses stopped embedding assets. Durations became milliseconds. `?shared` became `?isShared`. |

## Development

```sh
python3 build.py          # writes dist/plugin.video.immich-<version>.zip
python3 tools/make_icons.py   # regenerates resources/media/ (macOS only)
```

```sh
python3 tests/run.py      # the offline suite
python3 tests/bench.py    # listing cost against a ten-year library
```

`resources/lib/api.py` imports nothing from Kodi, so it can be exercised
directly from a normal Python interpreter.

Read `PERF.md` before proposing a performance change. It records what was
measured, what was kept, and which plausible ideas were rejected and why.

Bump `version` in `addon.xml` for every build you intend to distribute. Kodi
does not compare versions when installing from a zip, but its add-on cache and
the update UI both key off the version string.

### Releasing

Pushing a `v*` tag builds and publishes a GitHub release
(`.github/workflows/release.yml`):

```sh
# 1. bump <addon version="..."> in addon.xml and update its <news> block
# 2. commit that
git tag v2.0.1
git push origin v2.0.1
```

The workflow refuses to release if the tag and the `addon.xml` version disagree,
runs the test suite, and checks the zip has exactly one top-level directory
before publishing. Each release carries two identical archives: the versioned
`plugin.video.immich-<version>.zip`, and `plugin.video.immich.zip` under a
stable name, so this always points at the newest build:

```
https://github.com/AbubakrSamsodien/immich-kodi/releases/latest/download/plugin.video.immich.zip
```

### Setting ids are a compatibility surface

Kodi matches saved settings by id. Renaming one silently discards the user's
value on upgrade and substitutes the new default. Add settings freely, but do
not rename `immich_url`, `api_key`, `shared_only` or `asset_name`.

## Deprecations

### `?action=timeline&video=1`

**Status:** Deprecated (advisory) as of 2.0.3
**Replacement:** `?action=videos`
**Removal:** No date. It costs two lines and old favourites are impossible to
count from here.
**Reason:** The timeline endpoints take no asset-type filter, so this URL built
month folders from every month in the library. Photo-only months were listed
with a full asset count and opened empty.

This was the Videos menu entry in 1.0.0 and 2.0.0, so it is saved in users'
Kodi favourites and bookmarks. It is adapted onto the `videos` route rather
than merely tolerated: the old URL now returns exactly what the current menu
entry returns.

No action is needed. If you saved a favourite, re-save it against `Videos` in
the menu to drop the legacy path.

`?action=bucket&...&video=1` is unaffected and still filters a single month to
videos.

### Removed in 2.0.3

Unreachable once `addon.xml` began requiring `xbmc.python` 3.0.1 (Kodi 20),
where `Addon().getSettings()` is always present:

- the `getSettingString` / `getSettingBool` / `getSettingInt` fallback
- `kodiutils.ADDON_NAME`, `listing._duration_label`, `resources/media/slideshow.png`

A test asserts each stays gone, so the fallback cannot creep back.

## Licence

GPL-3.0-or-later. See `LICENSE.txt`.

Earlier versions declared MIT in `addon.xml` while shipping the GPLv3 text. The
tag was wrong, not the licence; upstream `vladd11/immich-kodi` is GPL-3.0 too.
