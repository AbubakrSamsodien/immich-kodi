"""A real HTTP server that speaks Immich 2.7.5.

Shapes come from scratchpad/immich-api-reference.md, which is authoritative:

  * `/api/timeline/bucket` is the v1.133+ columnar object, with the v1.135+
    `fileCreatedAt` + `localOffsetHours` pair (no `localDateTime`), a
    `duration` column of `"H:MM:SS.sss"` strings (v3.0.0 made it integer ms,
    2.7.5 has not), and the undocumented-but-real `status` column.
  * `/api/albums/{id}` still embeds `assets` (removed in v3.0.0).
  * `/api/search/{metadata,smart}` return the `{albums:{...},assets:{items,
    total,count,facets,nextPage}}` envelope; `/search/random` and
    `/search/cities` return bare arrays.

The server runs on a loopback socket so the addon's own `http.client` code path
is exercised end to end. `Dataset` is mutated in place by the tests to inject
faults; the server reads it on every request.
"""

from __future__ import annotations

import json
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

API_KEY = "test-api-key-&-with-specials"

# Every version boundary below is sourced from immich-api-reference.md section 1
# ("Breaking-change timeline") and the per-endpoint sections it references.
V_COLUMNAR = (1, 133, 0)      # /timeline/bucket array -> columnar object
V_VISIBILITY = (1, 133, 0)    # isArchived -> visibility enum
V_FULLSIZE = (1, 133, 0)      # ?size=fullsize added to /assets/{id}/thumbnail
V_OFFSET_HOURS = (1, 135, 0)  # columnar localDateTime -> fileCreatedAt+offset
V_ALBUM_IDS = (1, 135, 0)     # /search/metadata gained albumIds
V_SLUG = (1, 137, 0)
V_COORDS = (1, 142, 0)        # columnar latitude/longitude + withCoordinates
V_BBOX = (2, 6, 0)            # bbox param; width/height on AssetResponseDto
V_V3 = (3, 0, 0)              # duration ms, album assets gone, shared->isShared


def parse_version(text):
    parts = [int(p) for p in str(text).split(".")]
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts[:3])

# --------------------------------------------------------------------------
# Fixture builders
# --------------------------------------------------------------------------


def _uuid(prefix: str, index: int) -> str:
    """A deterministic but genuinely valid UUID.

    The old form produced nine characters in the first group and used non-hex
    prefixes like 'p', so fixture ids were not UUIDs at all. Immich validates
    ids as UUIDs, so an invalid fixture hides a whole class of behaviour.
    The prefix is folded to a hex digit, keeping ids distinct per entity type.
    """
    head = format(ord(prefix) % 16, "x")
    return f"{head}{index:07d}-0000-4000-8000-000000000000"


def _exif(index: int, city, country):
    return {
        "make": "Canon",
        "model": "EOS R6",
        "lensModel": "RF24-105mm F4 L IS USM",
        "exifImageWidth": 4032,
        "exifImageHeight": 3024,
        "fileSizeInByte": 3_500_000 + index,
        "orientation": "1",
        "dateTimeOriginal": "2026-08-01T09:12:33.000Z",
        "modifyDate": "2026-08-01T09:12:33.000Z",
        "timeZone": "Europe/Amsterdam",
        "lensModelId": None,
        "fNumber": 4.0,
        "focalLength": 35.0,
        "iso": 400,
        "exposureTime": "1/250",
        "latitude": 52.37 + index / 1000.0,
        "longitude": 4.89 + index / 1000.0,
        "city": city,
        "state": "North Holland",
        "country": country,
        "description": f"Description {index}" if index % 3 == 0 else "",
        "projectionType": "EQUIRECTANGULAR" if index % 7 == 0 else None,
        "rating": 3 if index % 4 == 0 else None,
    }


def asset_dto(index: int, is_video=False, city="Amsterdam", country="Netherlands",
              duration=None, null_exif=False, version=(2, 7, 5)):
    """One AssetResponseDto, shaped for the given server version."""
    stamp = f"2026-08-{(index % 28) + 1:02d}T09:12:33.000Z"
    local = f"2026-08-{(index % 28) + 1:02d}T11:12:33.000Z"
    if duration is None:
        if version >= V_V3:
            # v3.0.0: integer milliseconds, and null for stills (#28003).
            duration = 83456 if is_video else None
        else:
            duration = "0:01:23.45600" if is_video else "0:00:00.00000"
    dto = {
        "id": _uuid("a", index),
        "ownerId": _uuid("u", 1),
        "type": "VIDEO" if is_video else "IMAGE",
        "originalPath": f"upload/library/admin/2026/IMG_{index:04d}.jpg",
        "originalFileName": f"IMG_{index:04d}.{'mp4' if is_video else 'jpg'}",
        "originalMimeType": "video/mp4" if is_video else "image/jpeg",
        "fileCreatedAt": stamp,
        "fileModifiedAt": stamp,
        "localDateTime": local,
        "createdAt": stamp,
        "updatedAt": stamp,
        "duration": duration,
        "width": 4032,
        "height": 3024,
        "thumbhash": "1QcSHQRnh493V4dIh4eXh1h4kJUI",
        "checksum": f"checksum-{index}",
        "visibility": "timeline",
        "isArchived": False,
        "isFavorite": index % 5 == 0,
        "isTrashed": False,
        "isOffline": False,
        "isEdited": False,
        "hasMetadata": True,
        "livePhotoVideoId": None,
        "duplicateId": None,
        "stack": None,
        "people": [] if index % 2 else [
            {"id": _uuid("p", 1), "name": "Alice", "birthDate": None,
             "thumbnailPath": "", "isHidden": False, "updatedAt": stamp,
             "isFavorite": False, "color": "#ffffff", "faces": []}
        ],
        "tags": [],
        "exifInfo": None if null_exif else _exif(index, city, country),
    }
    if version < V_BBOX:
        # width/height only exist from v2.6.0.
        dto.pop("width")
        dto.pop("height")
        dto.pop("isEdited")
    if version < V_VISIBILITY:
        dto.pop("visibility")
    if version < (1, 140, 0):
        dto.pop("createdAt")
    if version >= V_V3:
        # v3.0.0 removed deviceId/deviceAssetId/unassignedFaces (never emitted
        # here) and stopped shipping stack unless requested.
        dto.pop("stack", None)
    return dto


def columnar_bucket(count: int, start=0, video_every=4, nulls=False,
                    mismatch=False, duration_style=None, version=(2, 7, 5),
                    offset_hours=2.0, with_coordinates=False):
    """The v1.133+ columnar timeline bucket, shaped for `version`.

    v1.133-v1.134 carry `localDateTime`; v1.135 replaced it with
    `fileCreatedAt` + `localOffsetHours` (reference section 2, shape B).
    """
    if duration_style is None:
        duration_style = "ms" if version >= V_V3 else "string"
    ids, owners, ratios, hashes = [], [], [], []
    is_image, favourite, trashed, visibility = [], [], [], []
    live, projection, duration, city, country = [], [], [], [], []
    created, offsets, status = [], [], []

    for offset in range(count):
        index = start + offset
        video = video_every and (index % video_every == 0)
        ids.append(_uuid("a", index))
        owners.append(_uuid("u", 1))
        ratios.append(1.333)
        hashes.append("1QcSHQRnh493V4dIh4eXh1h4kJUI")
        is_image.append(not video)
        favourite.append(index % 5 == 0)
        trashed.append(False)
        visibility.append("timeline")
        live.append(None)
        projection.append("EQUIRECTANGULAR" if index % 7 == 0 else None)
        if not video:
            if nulls or duration_style == "ms":
                duration.append(None)
            else:
                duration.append("0:00:00.00000")
        elif duration_style == "ms":
            duration.append(83456)
        else:
            duration.append("0:01:23.45600")
        city.append(None if nulls else "Amsterdam")
        country.append(None if nulls else "Netherlands")
        created.append(f"2026-08-{(index % 28) + 1:02d}T09:12:33.000Z")
        offsets.append(offset_hours)
        status.append("active")

    payload = {
        "id": ids,
        "ownerId": owners,
        "ratio": ratios,
        "thumbhash": hashes,
        "isImage": is_image,
        "isFavorite": favourite,
        "isTrashed": trashed,
        "visibility": visibility,
        "livePhotoVideoId": live,
        "projectionType": projection,
        "duration": duration,
        "city": city,
        "country": country,
        "status": status,
    }
    if version >= V_OFFSET_HOURS:
        payload["fileCreatedAt"] = created
        payload["localOffsetHours"] = offsets
    else:
        # v1.133-v1.134 only: local wall-clock in one field, Z-suffixed but
        # not actually UTC.
        payload["localDateTime"] = [
            stamp.replace("T09:", "T%02d:" % (9 + int(offset_hours)))
            for stamp in created
        ]
    if version >= V_V3:
        payload["createdAt"] = list(created)
    if version >= V_COORDS and with_coordinates:
        payload["latitude"] = [52.37] * count
        payload["longitude"] = [4.89] * count
    if mismatch:
        # A short column: Immich would never do this, but a proxy truncating a
        # response would, and the client must not index off the end.
        payload["isImage"] = payload["isImage"][: max(0, count - 2)]
        payload["city"] = payload["city"][: max(0, count - 1)]
        payload["duration"] = payload["duration"][: max(0, count - 3)]
    return payload


# --------------------------------------------------------------------------
# Per-version parameter matrix
#
# (name, min_version, removed_in). `None` means "always". Sourced from
# immich-api-reference.md sections 2, 4, 6, 7, 9 and 10. A param outside its
# window is what Immich would silently drop (section 0: "Unknown query params
# are silently dropped, never rejected"), which is worse than an error, so the
# mock records it as a violation instead of failing the request.
# --------------------------------------------------------------------------

_TIMELINE_PARAMS = [
    ("albumId", None, None), ("personId", None, None), ("tagId", None, None),
    ("userId", None, None), ("isFavorite", None, None), ("isTrashed", None, None),
    ("withStacked", None, None), ("withPartners", None, None),
    ("order", None, None), ("key", None, None),
    ("timeBucket", None, None),
    ("slug", V_SLUG, None),
    ("size", None, V_COLUMNAR),            # required <=1.132, removed 1.133
    ("isArchived", None, V_VISIBILITY),    # replaced by visibility in 1.133
    ("visibility", V_VISIBILITY, None),
    ("page", V_COLUMNAR, V_OFFSET_HOURS),  # existed only in 1.133.x-1.134.x
    ("pageSize", V_COLUMNAR, V_OFFSET_HOURS),
    ("withCoordinates", V_COORDS, None),
    ("bbox", V_BBOX, None),
    ("orderBy", V_V3, None),
]

PARAM_MATRIX = {
    "/timeline/buckets": _TIMELINE_PARAMS,
    "/timeline/bucket": _TIMELINE_PARAMS,
    "/albums": [
        ("assetId", None, None),
        ("shared", None, V_V3),
        ("isShared", V_V3, None), ("isOwned", V_V3, None),
        ("id", V_V3, None), ("name", V_V3, None),
    ],
    "/people": [
        ("page", None, None), ("size", None, None), ("withHidden", None, None),
        ("closestAssetId", (1, 124, 0), None), ("closestPersonId", (1, 124, 0), None),
    ],
    "/tags": [],
    "/memories": [
        ("isSaved", None, None), ("isTrashed", None, None),
        ("for", (1, 130, 0), None), ("type", (1, 130, 0), None),
        ("order", (2, 4, 0), None), ("size", (2, 4, 0), None),
    ],
    "/search/cities": [],
    "/search/suggestions": [
        ("type", None, None), ("country", None, None), ("state", None, None),
        ("make", None, None), ("model", None, None), ("includeNull", None, None),
    ],
}

_SEARCH_BODY = [
    ("personIds", None, None), ("city", None, None), ("country", None, None),
    ("state", None, None), ("make", None, None), ("model", None, None),
    ("lensModel", None, None), ("originalFileName", None, None),
    ("originalPath", None, None), ("checksum", None, None), ("id", None, None),
    ("libraryId", None, None), ("type", None, None), ("isFavorite", None, None),
    ("isMotion", None, None), ("isOffline", None, None), ("isEncoded", None, None),
    ("isNotInAlbum", None, None), ("withDeleted", None, None),
    ("withExif", None, None), ("withPeople", None, None),
    ("withStacked", None, None), ("order", None, None), ("page", None, None),
    ("size", None, None),
    ("takenBefore", None, None), ("takenAfter", None, None),
    ("createdBefore", None, None), ("createdAfter", None, None),
    ("updatedBefore", None, None), ("updatedAfter", None, None),
    ("trashedBefore", None, None), ("trashedAfter", None, None),
    ("tagIds", (1, 130, 0), None), ("description", (1, 130, 0), None),
    ("rating", (1, 130, 0), None),
    ("isArchived", None, V_VISIBILITY), ("visibility", V_VISIBILITY, None),
    ("albumIds", V_ALBUM_IDS, None),
    ("ocr", (2, 2, 0), None),
]

BODY_MATRIX = {
    "/search/metadata": _SEARCH_BODY,
    "/search/smart": _SEARCH_BODY + [
        ("query", None, None), ("language", V_VISIBILITY, None),
        ("queryAssetId", (1, 143, 0), None),
    ],
    "/search/random": _SEARCH_BODY,
}


def check_params(route, supplied, version, kind="query"):
    """Return a list of params this version of Immich would silently drop."""
    table = PARAM_MATRIX.get(route) if kind == "query" else BODY_MATRIX.get(route)
    if table is None:
        return []
    windows = {name: (lo, hi) for name, lo, hi in table}
    problems = []
    for name in supplied:
        if name not in windows:
            problems.append(
                f"{route}: Immich {'.'.join(map(str, version))} has no "
                f"{kind} param {name!r} at all; it is silently dropped"
            )
            continue
        low, high = windows[name]
        if low is not None and version < low:
            problems.append(
                f"{route}: {kind} param {name!r} was only added in "
                f"{'.'.join(map(str, low))}, but the server is "
                f"{'.'.join(map(str, version))}; it is silently dropped"
            )
        if high is not None and version >= high:
            problems.append(
                f"{route}: {kind} param {name!r} was removed in "
                f"{'.'.join(map(str, high))}, but the addon still sends it to "
                f"{'.'.join(map(str, version))}; it is silently dropped"
            )
    return problems


class Dataset:
    """Everything the mock will serve. Mutated in place by the tests."""

    def __init__(self):
        self.reset()

    def reset(self):
        self.version = {"major": 2, "minor": 7, "patch": 5, "prerelease": None}
        self.features = {
            "smartSearch": True,
            "facialRecognition": True,
            "map": True,
            "reverseGeocoding": True,
            "importFaces": False,
            "sidecar": True,
            "search": True,
            "trash": True,
            "oauth": False,
            "oauthAutoLaunch": False,
            "passwordLogin": True,
            "configFile": False,
            "duplicateDetection": True,
            "email": False,
            "ocr": True,
        }
        self.buckets = [
            {"timeBucket": "2026-08-01", "count": 12},
            {"timeBucket": "2026-07-01", "count": 3},
            {"timeBucket": "2025-12-01", "count": 7},
        ]
        self.bucket_sizes = {"2026-08-01": 12, "2026-07-01": 3, "2025-12-01": 7}
        self.bucket_nulls = False
        self.bucket_mismatch = False
        self.albums = _default_albums()
        self.album_assets = {
            _uuid("b", 1): [asset_dto(i, is_video=(i % 4 == 0)) for i in range(6)],
            _uuid("b", 2): [],
        }
        self.people = {
            "people": [
                {"id": _uuid("p", 1), "name": "Alice", "birthDate": None,
                 "thumbnailPath": "upload/thumbs/p1.jpeg", "isHidden": False,
                 "updatedAt": "2026-08-01T09:00:00.000Z", "isFavorite": False,
                 "color": "#aabbcc"},
                {"id": _uuid("p", 2), "name": "Bob", "birthDate": "1980-01-01",
                 "thumbnailPath": "upload/thumbs/p2.jpeg", "isHidden": False,
                 "updatedAt": "2026-08-01T09:00:00.000Z", "isFavorite": False,
                 "color": "#ccbbaa"},
                {"id": _uuid("p", 3), "name": "", "birthDate": None,
                 "thumbnailPath": "upload/thumbs/p3.jpeg", "isHidden": False,
                 "updatedAt": "2026-08-01T09:00:00.000Z", "isFavorite": False,
                 "color": None},
                {"id": _uuid("p", 4), "name": "Hidden Person", "birthDate": None,
                 "thumbnailPath": "upload/thumbs/p4.jpeg", "isHidden": True,
                 "updatedAt": "2026-08-01T09:00:00.000Z", "isFavorite": False,
                 "color": None},
            ],
            "total": 4,
            "hidden": 1,
            "hasNextPage": False,
        }
        self.tags = [
            {"id": _uuid("t", 1), "name": "Japan", "value": "Travel/Japan",
             "parentId": _uuid("t", 9), "color": None,
             "createdAt": "2026-01-01T00:00:00.000Z",
             "updatedAt": "2026-01-01T00:00:00.000Z"},
            {"id": _uuid("t", 2), "name": "Family", "value": "Family",
             "parentId": None, "color": "#123456",
             "createdAt": "2026-01-01T00:00:00.000Z",
             "updatedAt": "2026-01-01T00:00:00.000Z"},
        ]
        self.memories = [
            {"id": _uuid("m", 1), "ownerId": _uuid("u", 1), "type": "on_this_day",
             "data": {"year": 2019}, "memoryAt": "2019-08-16T00:00:00.000Z",
             "createdAt": "2026-08-16T00:00:00.000Z",
             "updatedAt": "2026-08-16T00:00:00.000Z",
             "isSaved": False, "seenAt": None, "showAt": None, "hideAt": None,
             "deletedAt": None,
             "assets": [asset_dto(i) for i in range(100, 103)]},
            {"id": _uuid("m", 2), "ownerId": _uuid("u", 1), "type": "on_this_day",
             "data": {"year": 2021}, "memoryAt": "2021-08-16T00:00:00.000Z",
             "createdAt": "2026-08-16T00:00:00.000Z",
             "updatedAt": "2026-08-16T00:00:00.000Z",
             "isSaved": False, "seenAt": None, "showAt": None, "hideAt": None,
             "deletedAt": None,
             "assets": []},
        ]
        self.cities = [
            asset_dto(200, city="Amsterdam", country="Netherlands"),
            asset_dto(201, city="Lisbon", country="Portugal"),
            asset_dto(202, city="Amsterdam", country="Netherlands"),
            asset_dto(203, city=None, country=None),
        ]
        self.search_results = [asset_dto(i, is_video=(i % 6 == 0)) for i in range(300, 312)]
        self.smart_results = [asset_dto(i) for i in range(400, 405)]
        self.random_results = [asset_dto(i, is_video=(i % 3 == 0)) for i in range(500, 512)]
        self.me = {
            "id": _uuid("u", 1),
            "email": "admin@example.com",
            "name": "Admin User",
            "profileImagePath": "",
            "avatarColor": "primary",
            "storageLabel": "admin",
            "shouldChangePassword": False,
            "isAdmin": True,
            "createdAt": "2025-01-01T00:00:00.000Z",
            "deletedAt": None,
            "updatedAt": "2026-08-01T00:00:00.000Z",
            "oauthId": "",
            "quotaSizeInBytes": None,
            "quotaUsageInBytes": 12345,
            "status": "active",
            "license": None,
        }
        # Fault injection
        self.offset_hours = 2.0
        # Fault injection for the transport tests.
        self.hang_paths = set()      # route prefixes that accept then never reply
        self.hang_seconds = 12.0
        self.drop_once = set()       # route prefixes closed once without a reply
        self.raw_override = {}       # route -> (status, raw bytes) served verbatim
        # <=1.132 made size=DAY|MONTH mandatory on both timeline endpoints.
        # Turn this off to inspect the legacy response shape on its own.
        self.enforce_legacy_size = True
        self.force_status = {}      # path prefix -> (status, body dict)
        self.malformed = set()      # path prefixes returning invalid JSON
        self.require_api_key = True
        self.path_prefix = ""       # e.g. "/immich" for a subpath reverse proxy

    def add_motion_photo(self, album_id=None):
        """A still whose container mimetype is video/*.

        Android motion photos and Apple Live Photo containers really do come
        back as type=IMAGE with originalMimeType video/quicktime.
        """
        dto = asset_dto(777)
        dto["type"] = "IMAGE"
        dto["originalMimeType"] = "video/quicktime"
        dto["originalFileName"] = "MVIMG_0777.jpg"
        if album_id:
            self.album_assets.setdefault(album_id, []).append(dto)
        return dto


def _strip_v3(album):
    """v3.0.0 removed assets, owner and ownerId from AlbumResponseDto."""
    stripped = {k: v for k, v in album.items()
                if k not in ("assets", "owner", "ownerId")}
    stripped["albumUsers"] = [
        {"user": {"id": _uuid("u", 1), "email": "admin@example.com",
                  "name": "Admin User", "profileImagePath": "",
                  "avatarColor": "primary"},
         "role": "editor"}
    ]
    return stripped


def _version_tuple(dataset):
    v = dataset.version
    return (v["major"], v["minor"], v["patch"])


def _default_albums():
    return [
        {
            "id": _uuid("b", 1),
            "albumName": "Holiday 2026",
            "description": "Summer",
            "createdAt": "2026-06-01T00:00:00.000Z",
            "updatedAt": "2026-08-01T00:00:00.000Z",
            "shared": False,
            "hasSharedLink": False,
            "albumThumbnailAssetId": _uuid("a", 0),
            "assetCount": 6,
            "isActivityEnabled": True,
            "order": "desc",
            "startDate": "2026-06-01T00:00:00.000Z",
            "endDate": "2026-06-30T00:00:00.000Z",
            "lastModifiedAssetTimestamp": "2026-06-30T00:00:00.000Z",
            "albumUsers": [],
            "owner": {"id": _uuid("u", 1), "email": "admin@example.com",
                      "name": "Admin User", "profileImagePath": "",
                      "avatarColor": "primary"},
            "ownerId": _uuid("u", 1),
            "assets": [],
        },
        {
            "id": _uuid("b", 2),
            "albumName": "Untitled",
            "description": "",
            "createdAt": "2026-01-01T00:00:00.000Z",
            "updatedAt": "2026-01-01T00:00:00.000Z",
            "shared": True,
            "hasSharedLink": True,
            # Immich returns null here for an album with no cover picked yet.
            "albumThumbnailAssetId": None,
            "assetCount": 0,
            "isActivityEnabled": True,
            "startDate": None,
            "endDate": None,
            "albumUsers": [],
            "owner": {"id": _uuid("u", 1), "email": "admin@example.com",
                      "name": "Admin User", "profileImagePath": "",
                      "avatarColor": "primary"},
            "ownerId": _uuid("u", 1),
            "assets": [],
        },
    ]


# --------------------------------------------------------------------------
# Server
# --------------------------------------------------------------------------


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "Immich/2.7.5"

    # -- plumbing -----------------------------------------------------------

    def log_message(self, *args):
        pass

    def handle_one_request(self):
        # http.client drops keep-alive sockets abruptly when the addon closes
        # its connection; that is normal and must not spam the report.
        try:
            super().handle_one_request()
        except (ConnectionResetError, BrokenPipeError):
            self.close_connection = True

    @property
    def data(self) -> Dataset:
        return self.server.dataset

    @property
    def ver(self):
        v = self.data.version
        return (v["major"], v["minor"], v["patch"])

    def _shape(self, dtos):
        return [as_version(dto, self.ver) for dto in dtos]

    def _shape_memory(self, memory):
        out = dict(memory)
        out["assets"] = self._shape(memory.get("assets") or [])
        return out

    def _send(self, status, payload, raw=None):
        """Write one response and report that the request was handled.

        The truthy return matters: _handle() treats a None result from a route
        as "no such route" and writes a 404 as well. Two responses on one
        keep-alive connection desynchronise the stream, and the stray body is
        then read as the answer to the client's next request, which shows up as
        an unrelated test failing at random.
        """
        if raw is None:
            raw = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)
        return True

    def _error(self, status, message):
        if self.ver >= V_V3:
            return self._send(status, {"message": message})
        return self._send(status, {"message": message, "error": "Error",
                                   "statusCode": status})

    def do_GET(self):
        self._handle("GET")

    def do_POST(self):
        self._handle("POST")

    def _handle(self, method):
        parsed = urlparse(self.path)
        path = parsed.path
        query = {k: v[-1] for k, v in parse_qs(parsed.query, keep_blank_values=True).items()}
        length = int(self.headers.get("Content-Length") or 0)
        raw_body = self.rfile.read(length) if length else b""
        try:
            body = json.loads(raw_body) if raw_body else None
        except ValueError:
            body = None

        for name in ("query", "body"):
            supplied = query if name == "query" else (body or {})
            if isinstance(supplied, dict):
                self.server.param_violations.extend(
                    check_params(parsed.path[len(self.data.path_prefix or ""):]
                                 [len("/api"):], supplied, self.ver, kind=name)
                )

        self.server.requests.append(
            {
                "method": method,
                "path": path,
                "query": query,
                "body": body,
                "api_key": self.headers.get("x-api-key"),
                "accept": self.headers.get("Accept"),
                "content_type": self.headers.get("Content-Type"),
            }
        )

        prefix = self.data.path_prefix
        if prefix:
            if not path.startswith(prefix):
                self._error(404, f"Cannot {method} {path}")
                return
            path = path[len(prefix):]

        if not path.startswith("/api/"):
            self._error(404, f"Cannot {method} {path}")
            return
        route = path[len("/api"):]

        if route in self.data.raw_override:
            status, raw = self.data.raw_override[route]
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            if raw:
                self.wfile.write(raw)
            return
        for bad in list(self.data.drop_once):
            if route.startswith(bad):
                # A keep-alive socket the server closed while idle: the client
                # sees RemoteDisconnected before the request is processed, which
                # is the one failure a retry may paper over.
                self.data.drop_once.discard(bad)
                self.close_connection = True
                return
        for bad in self.data.hang_paths:
            if route.startswith(bad):
                time.sleep(self.data.hang_seconds)
                self.close_connection = True
                return
        for bad, (status, message) in self.data.force_status.items():
            if route.startswith(bad):
                self._error(status, message)
                return
        for bad in self.data.malformed:
            if route.startswith(bad):
                raw = b'{"assets": {"items": [ this is not json'
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)
                return

        unauthenticated = route in ("/server/version", "/server/features",
                                    "/server/ping", "/server/config")
        if self.data.require_api_key and not unauthenticated:
            if self.headers.get("x-api-key") != API_KEY:
                self._error(401, "Invalid API key")
                return

        handler = getattr(self, f"_route_{method}", None)
        result = handler(route, query, body) if handler else None
        if result is None:
            self._error(404, f"Cannot {method} {path}")

    # -- routes -------------------------------------------------------------

    def _route_GET(self, route, query, body):
        data = self.data

        if route == "/server/version":
            return self._send(200, data.version)
        if route == "/server/features":
            return self._send(200, data.features)
        if route == "/server/ping":
            return self._send(200, {"res": "pong"})
        if route == "/users/me":
            return self._send(200, data.me)

        if route == "/timeline/buckets":
            if (self.ver < V_COLUMNAR and data.enforce_legacy_size
                    and "size" not in query):
                # size=DAY|MONTH was mandatory until v1.133 removed it.
                return self._error(400, "size must be one of the following "
                                        "values: DAY, MONTH")
            buckets = list(data.buckets)
            if query.get("isFavorite") == "true":
                buckets = buckets[:1]
            if query.get("albumId") or query.get("personId") or query.get("tagId"):
                buckets = buckets[:2]
            return self._send(200, buckets)

        if route == "/timeline/bucket":
            key = query.get("timeBucket")
            if not key:
                return self._error(400, "timeBucket must be a string")
            if self.ver < V_COLUMNAR:
                if data.enforce_legacy_size and "size" not in query:
                    return self._error(400, "size must be one of the following "
                                            "values: DAY, MONTH")
                # Shape A: a bare array of full AssetResponseDto objects.
                count = data.bucket_sizes.get(key, 0)
                return self._send(
                    200,
                    [asset_dto(i, is_video=(i % 4 == 0), version=self.ver)
                     for i in range(count)],
                )
            count = data.bucket_sizes.get(key)
            if count is None:
                return self._send(200, columnar_bucket(0, version=self.ver))
            start = abs(hash(key)) % 50
            return self._send(
                200,
                columnar_bucket(count, start=start, nulls=data.bucket_nulls,
                                mismatch=data.bucket_mismatch,
                                version=self.ver,
                                offset_hours=data.offset_hours,
                                with_coordinates=query.get("withCoordinates") == "true"),
            )

        if route == "/albums":
            albums = list(data.albums)
            flag = "isShared" if self.ver >= V_V3 else "shared"
            if query.get(flag) == "true":
                albums = [a for a in albums if a.get("shared")]
            if self.ver >= V_V3:
                albums = [_strip_v3(a) for a in albums]
            return self._send(200, albums)

        if route.startswith("/albums/"):
            album_id = route[len("/albums/"):]
            for album in data.albums:
                if album["id"] == album_id:
                    if self.ver >= V_V3:
                        # v3.0.0 removed the embedded asset list entirely.
                        return self._send(200, _strip_v3(album))
                    payload = dict(album)
                    payload["assets"] = self._shape(
                        data.album_assets.get(album_id, [])
                    )
                    return self._send(200, payload)
            return self._error(400, "Not found or no album.read access")

        if route == "/search/cities":
            return self._send(200, self._shape(data.cities))
        if route == "/search/suggestions":
            return self._send(200, ["Amsterdam", "Lisbon"])

        if route == "/people":
            return self._send(200, data.people)
        if route == "/tags":
            return self._send(200, data.tags)

        if route == "/memories":
            return self._send(200, [self._shape_memory(m) for m in data.memories])

        if route.startswith("/memories/"):
            memory_id = route[len("/memories/"):]
            for memory in data.memories:
                if memory["id"] == memory_id:
                    return self._send(200, self._shape_memory(memory))
            return self._error(400, "Not found or no memory.read access")

        if route.startswith("/assets/") or route.startswith("/people/"):
            raw = b"\x89PNG\r\n\x1a\n"
            self.send_response(200)
            self.send_header("Content-Type", "image/webp")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
            return True
        return None

    def _route_POST(self, route, query, body):
        data = self.data
        body = body or {}

        if route == "/search/metadata":
            # Immich validates albumIds as UUIDs and 400s on anything else.
            for album_id in (body.get("albumIds") or []):
                if not re.match(r"^[0-9a-fA-F-]{36}$", str(album_id)):
                    return self._error(400, "albumIds must be a UUID")
            pool = data.search_results
            if body.get("albumIds") and self.ver < V_ALBUM_IDS:
                # whitelist:true strips the unknown key, so the filter vanishes
                # and the caller gets the whole library back.
                pass
            elif body.get("albumIds"):
                pool = data.album_assets.get(body["albumIds"][0], [])
            if body.get("city"):
                pool = [
                    a for a in data.search_results
                    if (a.get("exifInfo") or {}).get("city") == body["city"]
                ] or data.search_results
            if body.get("type"):
                pool = [a for a in pool if a.get("type") == body["type"]]
            if body.get("originalFileName"):
                pool = [
                    a for a in data.search_results
                    if body["originalFileName"] in (a.get("originalFileName") or "")
                ]
            if body.get("description"):
                pool = [
                    a for a in data.search_results
                    if body["description"] in ((a.get("exifInfo") or {}).get("description") or "")
                ]
            return self._send(200, _envelope(self._shape(pool), body))

        if route == "/search/smart":
            if not body.get("query") and not body.get("queryAssetId"):
                return self._error(400, "query should not be empty")
            return self._send(200, _envelope(self._shape(data.smart_results), body))

        if route == "/search/random":
            size = int(body.get("size") or 250)
            return self._send(200, self._shape(data.random_results[:size]))

        if route == "/search/statistics":
            return self._send(200, {"total": len(data.search_results)})
        return None


def as_version(dto, version):
    """Reshape a 2.7.5 AssetResponseDto fixture for another server version."""
    out = dict(dto)
    if version >= V_V3:
        out["duration"] = 83456 if dto.get("type") == "VIDEO" else None
        out.pop("stack", None)
    if version < V_BBOX:
        for key in ("width", "height", "isEdited"):
            out.pop(key, None)
    if version < V_VISIBILITY:
        out.pop("visibility", None)
    if version < (1, 140, 0):
        out.pop("createdAt", None)
    return out


def _envelope(pool, body):
    size = int(body.get("size") or 100)
    page = int(body.get("page") or 1)
    start = (page - 1) * size
    items = pool[start:start + size]
    has_more = start + size < len(pool)
    return {
        "albums": {"total": 0, "count": 0, "items": [], "facets": []},
        "assets": {
            "total": len(items),
            "count": len(items),
            "items": items,
            "facets": [],
            "nextPage": str(page + 1) if has_more else None,
        },
    }


class _Server(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address):
        pass


class MockImmich:
    def __init__(self):
        self.dataset = Dataset()
        self._server = _Server(("127.0.0.1", 0), _Handler)
        self._server.dataset = self.dataset
        self._server.requests = []
        self._server.param_violations = []
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    @property
    def requests(self):
        return self._server.requests

    @property
    def param_violations(self):
        return self._server.param_violations

    def set_version(self, text):
        self.dataset.version = dict(
            zip(("major", "minor", "patch"), parse_version(text))
        )
        self.dataset.version["prerelease"] = None
        return self

    @property
    def port(self):
        return self._server.server_address[1]

    @property
    def url(self):
        return f"http://127.0.0.1:{self.port}"

    def start(self):
        self._thread.start()
        return self

    def stop(self):
        self._server.shutdown()
        self._server.server_close()

    def reset(self):
        self.dataset.reset()
        del self._server.requests[:]
        del self._server.param_violations[:]
