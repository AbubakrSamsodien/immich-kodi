"""Immich REST client.

Nothing here runs at import time. The addon must be able to open its settings
dialog even when the server URL is blank or the API key is wrong, which the
previous version could not do.

Immich marks the timeline endpoints `x-immich-state: Internal` and has already
changed their response shape twice (v1.133 array to columnar, v1.135 date
fields). The client therefore detects the server version once per Kodi session
and normalises every shape into the same `Asset` objects, so the view code
never branches on version.
"""

from __future__ import annotations

import http.client
import json
import socket
import ssl
from datetime import datetime, timedelta
from functools import total_ordering
from hashlib import md5
from typing import Any, Optional
from urllib.parse import quote, urlencode, urlparse

# Kodi caches nothing across plugin invocations, but a window property on the
# home window outlives them, which is enough to avoid re-probing the version on
# every directory listing.
_VERSION_PROPERTY = "immich.server.version"
_FEATURES_PROPERTY = "immich.server.features"


class ImmichError(Exception):
    """Base for every failure the views are expected to handle."""


class ImmichConnectionError(ImmichError):
    """The server could not be reached at all."""


class ImmichAuthError(ImmichError):
    """The API key was rejected, or lacks the scope for this call."""


class ImmichHTTPError(ImmichError):
    def __init__(self, status: int, message: str):
        super().__init__(f"HTTP {status}: {message}")
        self.status = status


@total_ordering
class Version:
    """A comparable Immich server version.

    Only the release tuple matters here; Immich's own feature gates are all
    expressed as "since vX.Y.Z".
    """

    def __init__(self, major: int, minor: int, patch: int = 0):
        self.major = major
        self.minor = minor
        self.patch = patch

    @classmethod
    def parse(cls, text: str) -> "Version":
        parts = text.strip().lstrip("vV").split("-")[0].split(".")
        numbers = []
        for part in parts[:3]:
            try:
                numbers.append(int(part))
            except ValueError:
                numbers.append(0)
        while len(numbers) < 3:
            numbers.append(0)
        return cls(*numbers)

    def _tuple(self):
        return (self.major, self.minor, self.patch)

    def __eq__(self, other) -> bool:
        return isinstance(other, Version) and self._tuple() == other._tuple()

    def __lt__(self, other: "Version") -> bool:
        return self._tuple() < other._tuple()

    def __hash__(self):
        return hash(self._tuple())

    def __str__(self) -> str:
        return f"{self.major}.{self.minor}.{self.patch}"


# Version boundaries the client branches on.
V_VISIBILITY_ENUM = Version(1, 133, 0)  # isArchived -> visibility
V_BUCKET_SIZE_REMOVED = Version(1, 133, 0)  # size=DAY|MONTH required before
V_FULLSIZE_THUMB = Version(1, 133, 0)  # ?size=fullsize added
V_ALBUM_IDS_SEARCH = Version(1, 135, 0)  # search/metadata gained albumIds
V_ALBUM_ASSETS_GONE = Version(3, 0, 0)  # albums/{id} no longer embeds assets
V_ALBUM_IS_SHARED = Version(3, 0, 0)  # ?shared -> ?isShared

# Three further boundaries need no branch, because the response is sniffed
# rather than predicted: the bucket array-to-columnar switch (v1.133), the
# columnar localDateTime-to-fileCreatedAt+localOffsetHours switch (v1.135), and
# duration changing from a string to integer milliseconds (v3.0).


class Asset:
    """One renderable item, normalised across every API shape.

    Fields that only some endpoints supply are None rather than absent, so the
    listing code can offer richer metadata when it has it without asking where
    the asset came from.
    """

    __slots__ = (
        "id",
        "is_video",
        "taken_at",
        "duration",
        "ratio",
        "city",
        "country",
        "is_favorite",
        "live_photo_video_id",
        "projection_type",
        "filename",
        "mime_type",
        "width",
        "height",
        "description",
        "rating",
        "exif",
        "people",
    )

    def __init__(self, id: str, is_video: bool = False, **kwargs):
        self.id = id
        self.is_video = is_video
        for name in self.__slots__:
            if name in ("id", "is_video"):
                continue
            setattr(self, name, kwargs.get(name))

    @property
    def is_360(self) -> bool:
        return self.projection_type == "EQUIRECTANGULAR"


def _parse_duration(value: Any) -> Optional[float]:
    """Return seconds from either API duration representation.

    Pre-v3.0.0 durations are `"H:MM:SS.sss"` strings; v3.0.0 made them integer
    milliseconds, and null for stills.
    """
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return float(value) / 1000.0
    if isinstance(value, str):
        try:
            hours, minutes, seconds = value.split(":")
            return int(hours) * 3600 + int(minutes) * 60 + float(seconds)
        except (ValueError, AttributeError):
            return None
    return None


def local_naive(value: Optional[str]) -> Optional[datetime]:
    """Parse a timestamp Immich already expressed in local wall-clock time.

    `localDateTime` carries a `Z` suffix but is not UTC, so the offset is
    dropped rather than applied. Every Asset.taken_at is naive local time so the
    listing code never has to ask which convention it got.
    """
    parsed = parse_datetime(value)
    return parsed.replace(tzinfo=None) if parsed is not None else None


def _error_detail(raw: bytes) -> str:
    """Pull the human-readable part out of an Immich error body.

    Errors come back as `{"message": ..., "statusCode": ..., "error": ...}`,
    and dumping that JSON verbatim into a Kodi dialog is unreadable.
    """
    text = raw.decode("utf-8", "replace").strip()
    if not text:
        return ""
    try:
        payload = json.loads(text)
    except ValueError:
        return text[:300]
    if isinstance(payload, dict):
        message = payload.get("message") or payload.get("error")
        if isinstance(message, list):
            message = "; ".join(str(part) for part in message)
        if message:
            return str(message)[:300]
    return text[:300]


def parse_datetime(value: Optional[str]) -> Optional[datetime]:
    """Parse an Immich ISO 8601 timestamp.

    Python 3.11 handles the trailing `Z` natively, but Kodi ships older
    interpreters on some platforms and Immich is inconsistent about fractional
    second precision, so this normalises both before parsing.
    """
    if not value:
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    # Trim sub-millisecond precision that fromisoformat rejects before 3.11.
    if "." in text:
        head, _, tail = text.partition(".")
        digits = ""
        for character in tail:
            if character.isdigit():
                digits += character
            else:
                break
        remainder = tail[len(digits):]
        text = f"{head}.{digits[:6]:<06}{remainder}" if digits else head + remainder
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        for pattern in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
            try:
                return datetime.strptime(text, pattern)
            except ValueError:
                continue
    return None


class ImmichClient:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        timeout: int = 20,
        verify_ssl: bool = True,
        log=None,
    ):
        self.base_url = (base_url or "").rstrip("/")
        self.api_key = api_key or ""
        self.timeout = timeout
        self.verify_ssl = verify_ssl
        self._log = log or (lambda message, level=None: None)
        self._parsed = urlparse(self.base_url)
        # Immich is commonly reverse-proxied onto a subpath such as
        # https://host/immich. http.client only takes host:port, so the path
        # prefix has to be re-attached to every request line by hand or the
        # API calls 404 while the media URLs, built from base_url, still work.
        self._prefix = self._parsed.path.rstrip("/")
        # Cached server facts are namespaced by server, so pointing the addon
        # at a different Immich does not reuse the previous one's version.
        self._namespace = md5(self.base_url.encode("utf-8")).hexdigest()[:12]
        self._connection: Optional[http.client.HTTPConnection] = None
        self._version: Optional[Version] = None
        self._features: Optional[dict] = None

    # -- connection handling -------------------------------------------------

    def _connect(self) -> http.client.HTTPConnection:
        if self._connection is not None:
            return self._connection
        host = self._parsed.netloc
        if not host:
            raise ImmichConnectionError("No server URL configured")
        if self._parsed.scheme == "https":
            context = ssl.create_default_context()
            if not self.verify_ssl:
                context.check_hostname = False
                context.verify_mode = ssl.CERT_NONE
            self._connection = http.client.HTTPSConnection(
                host, timeout=self.timeout, context=context
            )
        else:
            self._connection = http.client.HTTPConnection(host, timeout=self.timeout)
        return self._connection

    def close(self):
        if self._connection is not None:
            try:
                self._connection.close()
            finally:
                self._connection = None

    def _headers(self, extra: Optional[dict] = None) -> dict:
        headers = {
            "Accept": "application/json",
            "x-api-key": self.api_key,
        }
        if extra:
            headers.update(extra)
        return headers

    def request(
        self,
        method: str,
        path: str,
        params: Optional[dict] = None,
        body: Optional[Any] = None,
        _retry: bool = True,
    ) -> Any:
        """Perform one API call and return the decoded JSON body.

        A keep-alive connection that the server has already dropped surfaces as
        an exception on send rather than on connect, so a dropped connection is
        retried once on a fresh socket before it becomes a user-visible error.
        """
        url = path
        if params:
            cleaned = {k: v for k, v in params.items() if v is not None}
            if cleaned:
                url = f"{path}?{urlencode(cleaned, doseq=True)}"

        headers = self._headers()
        payload = None
        if body is not None:
            payload = json.dumps(body)
            headers["Content-Type"] = "application/json"

        try:
            connection = self._connect()
            connection.request(method, f"{self._prefix}/api{url}", payload, headers)
            response = connection.getresponse()
            raw = response.read()
        except (http.client.HTTPException, socket.error, OSError) as error:
            self.close()
            if _retry:
                self._log(f"retrying {method} {url} after {error!r}")
                return self.request(method, path, params, body, _retry=False)
            raise ImmichConnectionError(str(error)) from error

        if response.status >= 400:
            detail = _error_detail(raw)
            if response.status in (401, 403):
                raise ImmichAuthError(detail)
            raise ImmichHTTPError(response.status, detail)
        if not raw:
            return None
        try:
            return json.loads(raw.decode("utf-8"))
        except ValueError as error:
            raise ImmichHTTPError(response.status, f"Malformed JSON: {error}") from error

    # -- server capabilities -------------------------------------------------

    def detect_version(self, cache=None) -> Version:
        """Resolve the server version, preferring a cached value.

        `cache` is an optional get/set pair so the caller can persist this
        across plugin invocations without this module importing Kodi.
        """
        if self._version is not None:
            return self._version
        key = f"{_VERSION_PROPERTY}.{self._namespace}"
        if cache is not None:
            cached = cache.get(key)
            if cached:
                self._version = Version.parse(cached)
                return self._version
        data = self.request("GET", "/server/version")
        self._version = Version(
            int(data.get("major", 1)),
            int(data.get("minor", 0)),
            int(data.get("patch", 0)),
        )
        if cache is not None:
            cache.set(key, str(self._version))
        return self._version

    def features(self, cache=None) -> dict:
        """Server feature flags, used to hide menu entries that would 400."""
        if self._features is not None:
            return self._features
        key = f"{_FEATURES_PROPERTY}.{self._namespace}"
        if cache is not None:
            cached = cache.get(key)
            if cached:
                try:
                    self._features = json.loads(cached)
                    return self._features
                except ValueError:
                    pass
        try:
            self._features = self.request("GET", "/server/features") or {}
        except ImmichError:
            self._features = {}
        if cache is not None:
            cache.set(key, json.dumps(self._features))
        return self._features

    def me(self) -> dict:
        return self.request("GET", "/users/me")

    # -- media URLs ----------------------------------------------------------

    def _media_url(self, path: str, params: Optional[dict] = None) -> str:
        """Build a direct media URL with the API key attached as a header.

        Kodi's `|Header=Value` suffix sends real HTTP headers rather than query
        parameters, which keeps the key out of Immich's access logs. Kodi splits
        that suffix on `&` and percent-decodes each value exactly once, so the
        key is encoded here or a key containing `&` would truncate the header.
        """
        query = f"?{urlencode(params)}" if params else ""
        key = quote(self.api_key, safe="")
        return f"{self.base_url}/api{path}{query}|x-api-key={key}"

    def thumbnail_url(self, asset_id: str, size: str = "thumbnail") -> str:
        return self._media_url(f"/assets/{asset_id}/thumbnail", {"size": size})

    def image_url(self, asset_id: str, quality: str = "preview") -> str:
        """URL to display a still.

        `preview` is a 1440px JPEG that Kodi can always decode. `original` is
        byte-for-byte, which means HEIC and RAW originals will not render, so it
        is only ever chosen deliberately. `fullsize` needs the admin to have
        enabled full-size generation, and silently falls back to preview.
        """
        version = self._version or Version(1, 133, 0)
        if quality == "original":
            return self._media_url(f"/assets/{asset_id}/original")
        # Before v1.133 the size enum was thumbnail|preview only, so asking for
        # fullsize would 400 and the still would not render at all.
        if quality == "fullsize" and version >= V_FULLSIZE_THUMB:
            return self._media_url(f"/assets/{asset_id}/thumbnail", {"size": "fullsize"})
        return self._media_url(f"/assets/{asset_id}/thumbnail", {"size": "preview"})

    def video_url(self, asset_id: str) -> str:
        return self._media_url(f"/assets/{asset_id}/video/playback")

    def person_thumbnail_url(self, person_id: str) -> str:
        return self._media_url(f"/people/{person_id}/thumbnail")

    # -- normalisation -------------------------------------------------------

    def _asset_from_dto(self, data: dict) -> Asset:
        """Build an Asset from a full AssetResponseDto."""
        exif = data.get("exifInfo") or {}
        taken = local_naive(
            data.get("localDateTime")
            or exif.get("dateTimeOriginal")
            or data.get("fileCreatedAt")
            or data.get("fileModifiedAt")
        )
        return Asset(
            id=data["id"],
            is_video=data.get("type") == "VIDEO",
            taken_at=taken,
            duration=_parse_duration(data.get("duration")),
            ratio=None,
            city=exif.get("city"),
            country=exif.get("country"),
            is_favorite=data.get("isFavorite", False),
            live_photo_video_id=data.get("livePhotoVideoId"),
            projection_type=exif.get("projectionType"),
            filename=data.get("originalFileName"),
            mime_type=data.get("originalMimeType"),
            width=data.get("width") or exif.get("exifImageWidth"),
            height=data.get("height") or exif.get("exifImageHeight"),
            description=exif.get("description"),
            rating=exif.get("rating"),
            exif=exif or None,
            people=[p.get("name") for p in (data.get("people") or []) if p.get("name")],
        )

    def _assets_from_columnar(self, data: dict) -> list:
        """Expand a columnar bucket into Assets.

        The columnar form is parallel arrays keyed by field name, so every list
        is indexed together. Field availability moves with the server version,
        hence the per-column defaults.
        """
        ids = data.get("id") or []
        count = len(ids)

        def column(name, default=None):
            values = data.get(name)
            if not isinstance(values, list) or len(values) != count:
                return [default] * count
            return values

        is_image = column("isImage", True)
        durations = column("duration")
        ratios = column("ratio")
        cities = column("city")
        countries = column("country")
        favorites = column("isFavorite", False)
        live_photos = column("livePhotoVideoId")
        projections = column("projectionType")

        # v1.135 replaced localDateTime with UTC plus a fractional hour offset.
        local_dates = column("localDateTime")
        created = column("fileCreatedAt")
        offsets = column("localOffsetHours")

        assets = []
        for index in range(count):
            if local_dates[index]:
                taken = local_naive(local_dates[index])
            else:
                taken = parse_datetime(created[index])
                offset = offsets[index]
                if taken is not None and offset is not None:
                    taken = (taken + timedelta(hours=float(offset))).replace(tzinfo=None)
                elif taken is not None:
                    taken = taken.replace(tzinfo=None)
            assets.append(
                Asset(
                    id=ids[index],
                    is_video=not bool(is_image[index]),
                    taken_at=taken,
                    duration=_parse_duration(durations[index]),
                    ratio=ratios[index],
                    city=cities[index],
                    country=countries[index],
                    is_favorite=bool(favorites[index]),
                    live_photo_video_id=live_photos[index],
                    projection_type=projections[index],
                )
            )
        return assets

    # -- timeline ------------------------------------------------------------

    def timeline_buckets(self, **filters) -> list:
        """Month buckets, newest first. Returns [{timeBucket, count}]."""
        params = self._timeline_params(filters)
        data = self.request("GET", "/timeline/buckets", params)
        return data or []

    def timeline_bucket(self, time_bucket: str, **filters) -> list:
        params = self._timeline_params(filters)
        params["timeBucket"] = time_bucket
        data = self.request("GET", "/timeline/bucket", params)
        if isinstance(data, list):
            # Pre-v1.133 servers return a bare array of full asset objects.
            return [self._asset_from_dto(item) for item in data]
        if isinstance(data, dict):
            return self._assets_from_columnar(data)
        return []

    def _timeline_params(self, filters: dict) -> dict:
        """Translate view-level filters into version-correct query params.

        `withPartners` 400s unless visibility is pinned to `timeline`, and the
        default visibility is timeline plus archive, so archived photos leak
        into an unfiltered listing. Both are handled here rather than in views.
        """
        version = self._version or Version(1, 133, 0)
        params: dict = {}

        visibility = filters.get("visibility", "timeline")
        if version >= V_VISIBILITY_ENUM:
            params["visibility"] = visibility
        elif visibility == "archive":
            params["isArchived"] = "true"

        # Before v1.133 `size` was mandatory on both timeline endpoints; from
        # v1.133 it is dropped, and buckets are always months either way.
        if version < V_BUCKET_SIZE_REMOVED:
            params["size"] = "MONTH"

        for key in ("albumId", "personId", "tagId", "userId"):
            if filters.get(key):
                params[key] = filters[key]
        if filters.get("isFavorite"):
            params["isFavorite"] = "true"
        # The server rejects withPartners outright unless visibility is pinned
        # to timeline and neither isFavorite nor isTrashed is set. It is also a
        # no-op once albumId narrows the query.
        partners_allowed = (
            visibility == "timeline"
            and not filters.get("isFavorite")
            and not filters.get("albumId")
        )
        if filters.get("withPartners") and partners_allowed:
            params["withPartners"] = "true"
        if filters.get("order"):
            params["order"] = filters["order"]
        return params

    # -- albums --------------------------------------------------------------

    def albums(self, shared_only: bool = False) -> list:
        version = self._version or Version(1, 133, 0)
        params = {}
        if shared_only:
            params["isShared" if version >= V_ALBUM_IS_SHARED else "shared"] = "true"
        data = self.request("GET", "/albums", params or None)
        return data or []

    def album(self, album_id: str) -> dict:
        return self.request("GET", f"/albums/{album_id}")

    def album_assets(self, album_id: str) -> list:
        """Every asset in an album, ordered as the album orders them.

        v3.0.0 removed the embedded `assets` array, so newer servers go through
        search. Older servers keep the single-request path because it preserves
        the album's own ordering, which search does not.
        """
        version = self._version or Version(1, 133, 0)
        if version < V_ALBUM_ASSETS_GONE:
            data = self.album(album_id)
            # Presence of the key decides, not truthiness: a genuinely empty
            # album must return nothing rather than fall through to a broader
            # query that would list unrelated assets.
            if "assets" in data:
                return [self._asset_from_dto(item) for item in data["assets"] or []]
        if version >= V_ALBUM_IDS_SEARCH:
            return self.search_metadata(albumIds=[album_id])
        # `albumIds` does not exist before v1.135 and the server strips unknown
        # body keys instead of rejecting them, so searching here would silently
        # drop the filter and return the entire library. Walk the album's own
        # timeline instead.
        return self._assets_via_timeline(albumId=album_id)

    def _assets_via_timeline(self, **filters) -> list:
        """Collect every asset a filtered timeline covers, month by month."""
        assets = []
        for bucket in self.timeline_buckets(**filters):
            time_bucket = bucket.get("timeBucket")
            if time_bucket:
                assets.extend(self.timeline_bucket(time_bucket, **filters))
        return assets

    # -- search --------------------------------------------------------------

    def search_metadata(self, page_limit: int = 20, **filters) -> list:
        """Walk `POST /search/metadata` to exhaustion.

        The response envelope reports `total` as the current page length, so the
        only reliable end-of-results signal is a null `nextPage`. `page_limit`
        stops a pathological library from pinning Kodi indefinitely.
        """
        assets = []
        page = 1
        while page <= page_limit:
            body = {"size": 1000, "page": page, "withExif": True}
            body.update({k: v for k, v in filters.items() if v is not None})
            data = self.request("POST", "/search/metadata", body=body)
            block = (data or {}).get("assets") or {}
            items = block.get("items") or []
            assets.extend(self._asset_from_dto(item) for item in items)
            next_page = block.get("nextPage")
            if not next_page or not items:
                break
            page += 1
        return assets

    def search_smart(self, query: str, size: int = 250) -> list:
        data = self.request(
            "POST",
            "/search/smart",
            body={"query": query, "size": size, "page": 1, "withExif": True},
        )
        items = ((data or {}).get("assets") or {}).get("items") or []
        return [self._asset_from_dto(item) for item in items]

    def search_random(self, size: int = 250, **filters) -> list:
        body = {"size": size, "withExif": True}
        body.update({k: v for k, v in filters.items() if v is not None})
        data = self.request("POST", "/search/random", body=body)
        # This endpoint returns a bare array, unlike the other search calls.
        if isinstance(data, list):
            return [self._asset_from_dto(item) for item in data]
        items = ((data or {}).get("assets") or {}).get("items") or []
        return [self._asset_from_dto(item) for item in items]

    def suggestions(self, kind: str) -> list:
        data = self.request("GET", "/search/suggestions", {"type": kind})
        return [value for value in (data or []) if value]

    def cities(self) -> list:
        """One representative asset per city.

        Preferred over `/search/suggestions?type=city` because it comes with a
        thumbnail, which turns the Places listing into a picture grid rather
        than a list of names.
        """
        data = self.request("GET", "/search/cities")
        if not isinstance(data, list):
            return []
        return [self._asset_from_dto(item) for item in data]

    # -- people, tags, memories ----------------------------------------------

    def people(self, page: int = 1, size: int = 500) -> dict:
        return self.request("GET", "/people", {"page": page, "size": size}) or {}

    def tags(self) -> list:
        return self.request("GET", "/tags") or []

    def memories(self, for_date: Optional[str] = None) -> list:
        params = {"type": "on_this_day"}
        if for_date:
            params["for"] = for_date
        data = self.request("GET", "/memories", params)
        return data or []

    def memory(self, memory_id: str) -> dict:
        return self.request("GET", f"/memories/{memory_id}") or {}

    def memory_assets(self, memory: dict) -> list:
        return [self._asset_from_dto(item) for item in (memory.get("assets") or [])]
