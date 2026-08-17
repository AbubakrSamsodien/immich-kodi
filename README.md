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

Installing a newer zip over an existing install keeps your settings. Kodi stores
them in `userdata/addon_data/plugin.video.immich/`, which the installer never
touches.

## Setup

Create an API key in Immich under **Account Settings → API Keys**, then enter it
in the add-on settings along with your server URL.

The URL must include the scheme and the port, for example
`https://photos.example.com` or `http://192.168.1.10:2283`.

Use **Test connection** to confirm both are correct before browsing.

### Settings worth knowing

| Setting | Default | Notes |
| --- | --- | --- |
| Photo quality | Preview | `Preview` is a 1440px JPEG the server generates, and always displays. `Original` is the untouched file, so HEIC and RAW will not render in Kodi. `Full size` only works if your admin enabled full-size generation; otherwise Immich falls back to preview. |
| Ignore SSL certificate errors | Off | Turn on only for a self-signed certificate on your own network. |
| Include partner photos | Off | Merges libraries a partner shared with you into the timeline. |
| Items per page | 500 | Months larger than this are split across pages. |

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

`resources/lib/api.py` imports nothing from Kodi, so it can be exercised
directly from a normal Python interpreter.

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

## Licence

GPL-3.0-or-later. See `LICENSE.txt`.

Earlier versions declared MIT in `addon.xml` while shipping the GPLv3 text. The
tag was wrong, not the licence; upstream `vladd11/immich-kodi` is GPL-3.0 too.
