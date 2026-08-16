"""The test cases.

Each case takes the Harness and returns a list of problem strings. An empty
list is a pass. Cases are registered with @case and run in declaration order.
"""

from __future__ import annotations

import ast
import glob
import os
import re
import subprocess
import sys
from urllib.parse import parse_qsl, unquote, urlparse

import harness
import xbmc
from harness import Harness, check_kodi_url, discover_routes, standard_checks
from kodi_state import SETTINGS_SCHEMA, STRINGS
from mockimmich import API_KEY, _uuid, parse_version

REPO = harness.REPO
LIB = os.path.join(REPO, "resources", "lib")
MEDIA_DIR = os.path.join(REPO, "resources", "media")
STUBS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "kodistubs")

CASES = []
COVERED = set()


def case(name, route=None):
    def register(function):
        CASES.append((name, function, route))
        return function

    return register


ALBUM_1 = _uuid("b", 1)
ALBUM_2 = _uuid("b", 2)
MEMORY_1 = _uuid("m", 1)
PERSON_1 = _uuid("p", 1)
TAG_1 = _uuid("t", 1)


# ==========================================================================
# Static consistency
# ==========================================================================


def _lib_sources():
    return sorted(glob.glob(os.path.join(LIB, "*.py")) + [os.path.join(REPO, "addon.py")])


@case("static: every localise()/error_dialog() id exists in strings.po")
def static_string_ids(h):
    problems = []
    for path in _lib_sources():
        tree = ast.parse(open(path, encoding="utf-8").read(), path)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
                continue
            if node.func.id not in ("localise", "error_dialog"):
                continue
            for argument in node.args:
                if isinstance(argument, ast.Constant) and isinstance(argument.value, int):
                    if argument.value not in STRINGS:
                        problems.append(
                            f"{path}:{node.lineno}: {node.func.id}"
                            f"({argument.value}) has no msgid in strings.po"
                        )
    return problems


@case("static: every label/help id in settings.xml exists in strings.po")
def static_settings_labels(h):
    problems = []
    path = os.path.join(REPO, "resources", "settings.xml")
    text = open(path, encoding="utf-8").read()
    for match in re.finditer(r'(label|help|heading)>?="?(\d{5})', text):
        if int(match.group(2)) not in STRINGS:
            line = text[: match.start()].count("\n") + 1
            problems.append(
                f"{path}:{line}: {match.group(1)}={match.group(2)} has no "
                f"msgid in strings.po"
            )
    for match in re.finditer(r"<heading>(\d{5})</heading>", text):
        if int(match.group(1)) not in STRINGS:
            problems.append(f"{path}: heading {match.group(1)} missing from strings.po")
    return problems


@case("static: every setting id the code reads is declared in settings.xml")
def static_setting_ids(h):
    problems = []
    path = os.path.join(LIB, "kodiutils.py")
    tree = ast.parse(open(path, encoding="utf-8").read(), path)
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in ("_string", "_bool", "_int")
            and node.args
            and isinstance(node.args[0], ast.Constant)
        ):
            key = node.args[0].value
            if key not in SETTINGS_SCHEMA:
                problems.append(
                    f"{path}:{node.lineno}: reads setting {key!r}, which "
                    f"settings.xml never declares"
                )
                continue
            wanted = {"_string": "string", "_bool": "boolean", "_int": "integer"}[
                node.func.attr
            ]
            actual = SETTINGS_SCHEMA[key]["type"]
            if actual != wanted:
                problems.append(
                    f"{path}:{node.lineno}: reads setting {key!r} as {wanted}, "
                    f"but settings.xml declares it as {actual}; Kodi raises "
                    f"TypeError('Invalid setting type')"
                )
    return problems


@case("static: each lib module imports standalone (no cycles, no path surprises)")
def static_module_imports(h):
    problems = []
    for module in ("api", "kodiutils", "listing", "router", "views"):
        code = (
            f"import sys; sys.path[:0] = [{STUBS!r}, {LIB!r}]; import {module}"
        )
        finished = subprocess.run(
            [sys.executable, "-c", code], capture_output=True, text=True
        )
        if finished.returncode != 0:
            problems.append(
                f"importing {module!r} first fails:\n{finished.stderr.strip()}"
            )
    code = f"import sys; sys.path[:0] = [{STUBS!r}]; import runpy; " \
           f"sys.argv=['plugin://x/','-1','']; runpy.run_path({harness.ADDON_PY!r}, run_name='__main__')"
    finished = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    if finished.returncode != 0:
        problems.append(
            f"addon.py cannot bootstrap in a clean interpreter:\n{finished.stderr.strip()}"
        )
    return problems


@case("static: every bundled art path handed to Kodi exists on disk")
def static_media_icons(h):
    """Checks the emitted art rather than instrumenting the media() helper.

    An `icon` key that is empty, or that points at a file which is not there,
    is precisely what makes Kodi fall back to DefaultVideo.png. So the values
    that reach addDirectoryItems are what matter, not which helper built them.
    """
    problems = []
    checked = 0
    routes = (
        "",
        "action=timeline",
        "action=albums",
        "action=people",
        "action=places",
        "action=tags",
        "action=memories",
        "action=bucket&id=2025-06-01",
    )
    for query in routes:
        h.reset()
        record = h.invoke(query)
        where = query or "(root)"
        for _url, item, _folder in record.items:
            if not item.getArt("icon"):
                problems.append(f"{where}: {item.label!r} has an empty 'icon' art key")
            for key in ("icon", "thumb", "poster", "fanart"):
                value = item.getArt(key)
                if not value or value.startswith(("http://", "https://")):
                    continue
                checked += 1
                if not os.path.exists(value):
                    problems.append(
                        f"{where}: {item.label!r} art[{key}] points at a missing "
                        f"file: {value}"
                    )
    if not checked:
        problems.append("no local art paths were emitted - instrumentation failed")
    return problems


# ==========================================================================
# Happy-path routes
# ==========================================================================


@case("route '': root menu", route="")
def route_root(h):
    h.reset()
    record = h.invoke("")
    problems = standard_checks(record, expect_content="files")
    labels = [item.label for _u, item, _f in record.items]
    for expected in ("Timeline", "Videos", "Albums", "Favourites", "People",
                     "Places", "Tags", "Memories", "Search", "Random", "Settings"):
        if expected not in labels:
            problems.append(f"root menu is missing {expected!r} (got {labels})")
    for url, item, isfolder in record.items:
        if item.label != "Settings" and not isfolder:
            problems.append(f"root entry {item.label!r} is not a folder")
        if item.isfolder != isfolder:
            problems.append(
                f"root entry {item.label!r}: setIsFolder({item.isfolder}) "
                f"disagrees with the addDirectoryItems flag {isfolder}"
            )
        if not item.getProperty("description"):
            problems.append(f"root entry {item.label!r} has no description")
    return problems


@case("route 'timeline': month list", route="timeline")
def route_timeline(h):
    h.reset()
    record = h.invoke("action=timeline")
    problems = standard_checks(record, expect_content="files")
    if len(record.items) != 3:
        problems.append(f"expected 3 month folders, got {len(record.items)}")
    for url, item, _f in record.items:
        query = dict(parse_qsl(urlparse(url).query))
        if query.get("action") != "bucket" or not query.get("id"):
            problems.append(f"month item URL is not a bucket link: {url!r}")
    # The server must have been asked with a pinned visibility.
    calls = [r for r in h.server.requests if r["path"] == "/api/timeline/buckets"]
    if not calls:
        problems.append("no /api/timeline/buckets request reached the server")
    elif calls[-1]["query"].get("visibility") != "timeline":
        problems.append(
            f"buckets requested without visibility=timeline: {calls[-1]['query']}"
        )
    return problems


@case("route 'bucket': one month of assets", route="bucket")
def route_bucket(h):
    h.reset()
    record = h.invoke("action=bucket&id=2026-08-01")
    problems = standard_checks(record, expect_content="images")
    if len(record.items) != 12:
        problems.append(f"expected 12 assets in the bucket, got {len(record.items)}")
    videos = [i for u, i, _ in record.items if "/video/playback" in u]
    stills = [i for u, i, _ in record.items if "/video/playback" not in u]
    if not videos:
        problems.append("columnar isImage=false never produced a video item")
    if not stills:
        problems.append("columnar isImage=true never produced a still")
    for item in stills:
        if item.video_tag_requested:
            problems.append(f"still {item.label!r} touched getVideoInfoTag")
        if not item.picture_tag_requested:
            problems.append(f"still {item.label!r} has no picture info tag")
    for item in videos:
        tag = item._video_tag
        if tag is None or tag.data.get("duration") != 83:
            got = None if tag is None else tag.data.get("duration")
            problems.append(
                f"video duration from the 2.7.5 'H:MM:SS.sss' string is {got!r}, "
                f"expected 83 seconds"
            )
    if record.categories and record.categories[0][1] != "August 2026":
        problems.append(
            f"bucket breadcrumb is {record.categories[0][1]!r}, expected "
            f"'August 2026'"
        )
    return problems


@case("route 'albums': album list", route="albums")
def route_albums(h):
    h.reset()
    record = h.invoke("action=albums")
    problems = standard_checks(record, expect_content="files")
    if len(record.items) != 2:
        problems.append(f"expected 2 albums, got {len(record.items)}")
    for url, item, _f in record.items:
        if item.label == "Untitled":
            # albumThumbnailAssetId is null: must still get the bundled icon.
            if not item.art.get("icon"):
                problems.append("album with a null thumbnail has no icon")
            if item.art.get("thumb", "").startswith("http") and "None" in item.art["thumb"]:
                problems.append(f"null thumbnail leaked into a URL: {item.art['thumb']}")
    return problems


@case("route 'album': assets embedded in a 2.7.5 album", route="album")
def route_album(h):
    h.reset()
    record = h.invoke(f"action=album&id={ALBUM_1}&title=Holiday+2026")
    problems = standard_checks(record, expect_content="images")
    if len(record.items) != 6:
        problems.append(f"expected 6 album assets, got {len(record.items)}")
    if not any(r["path"] == f"/api/albums/{ALBUM_1}" for r in h.server.requests):
        problems.append("2.7.5 album assets were not read from GET /api/albums/{id}")
    if record.categories and record.categories[0][1] != "Holiday 2026":
        problems.append(f"album category is {record.categories[0][1]!r}")
    return problems


@case("edge: album with a null albumThumbnailAssetId and zero assets")
def route_album_empty(h):
    h.reset()
    record = h.invoke(f"action=album&id={ALBUM_2}&title=Untitled")
    problems = standard_checks(record, expect_content="images")
    if record.items:
        problems.append(f"empty album produced {len(record.items)} items")
    return problems


@case("route 'favourites': mutates request.params then re-enters timeline",
      route="favourites")
def route_favourites(h):
    h.reset()
    record = h.invoke("action=favourites")
    problems = standard_checks(record, expect_content="files")
    if not record.items:
        problems.append("favourites produced no month folders")
    calls = [r for r in h.server.requests if r["path"] == "/api/timeline/buckets"]
    if not calls:
        problems.append("favourites never queried the timeline")
    elif calls[-1]["query"].get("isFavorite") != "true":
        problems.append(
            f"favourites did not pass isFavorite=true: {calls[-1]['query']}"
        )
    for url, item, _f in record.items:
        query = dict(parse_qsl(urlparse(url).query))
        if query.get("favorite") != "1":
            problems.append(
                f"favourite month link loses the favorite filter: {url!r}"
            )
    if record.categories and record.categories[0][1] != "Favourites":
        problems.append(
            f"favourites category is {record.categories[0][1]!r}, expected "
            f"'Favourites'"
        )
    return problems


@case("route 'people': named, non-hidden people only", route="people")
def route_people(h):
    h.reset()
    record = h.invoke("action=people")
    problems = standard_checks(record, expect_content="files")
    labels = [i.label for _u, i, _f in record.items]
    if labels != ["Alice", "Bob"]:
        problems.append(f"expected ['Alice', 'Bob'], got {labels}")
    for url, item, _f in record.items:
        thumb = item.art.get("thumb", "")
        if "/api/people/" not in thumb:
            problems.append(f"person thumb is not a people thumbnail URL: {thumb!r}")
        check_kodi_url(thumb, problems, "person thumb")
    return problems


@case("route 'places': one folder per distinct city", route="places")
def route_places(h):
    h.reset()
    record = h.invoke("action=places")
    problems = standard_checks(record, expect_content="files")
    labels = [i.label for _u, i, _f in record.items]
    if labels != ["Amsterdam", "Lisbon"]:
        problems.append(f"expected ['Amsterdam', 'Lisbon'], got {labels}")
    return problems


@case("route 'place': assets in one city", route="place")
def route_place(h):
    h.reset()
    record = h.invoke("action=place&city=Amsterdam&title=Amsterdam")
    problems = standard_checks(record, expect_content="images")
    if not record.items:
        problems.append("place produced no assets")
    posts = [r for r in h.server.requests if r["path"] == "/api/search/metadata"]
    if not posts:
        problems.append("place did not POST /api/search/metadata")
    elif posts[0]["body"].get("city") != "Amsterdam":
        problems.append(f"search body missing city: {posts[0]['body']}")
    return problems


@case("route 'tags': tag list", route="tags")
def route_tags(h):
    h.reset()
    record = h.invoke("action=tags")
    problems = standard_checks(record, expect_content="files")
    labels = [i.label for _u, i, _f in record.items]
    if labels != ["Travel/Japan", "Family"]:
        problems.append(f"expected the nested tag values, got {labels}")
    for url, _i, _f in record.items:
        query = dict(parse_qsl(urlparse(url).query))
        if not query.get("tagId"):
            problems.append(f"tag link carries no tagId: {url!r}")
    return problems


@case("route 'memories': on-this-day folders", route="memories")
def route_memories(h):
    h.reset()
    record = h.invoke("action=memories")
    problems = standard_checks(record, expect_content="files")
    labels = [i.label for _u, i, _f in record.items]
    if labels != ["2019"]:
        problems.append(f"expected ['2019'] (the empty memory is skipped), got {labels}")
    return problems


@case("route 'memory': assets inside a memory", route="memory")
def route_memory(h):
    h.reset()
    record = h.invoke(f"action=memory&id={MEMORY_1}")
    problems = standard_checks(record, expect_content="images")
    if len(record.items) != 3:
        problems.append(f"expected 3 memory assets, got {len(record.items)}")
    return problems


@case("route 'memory': a memory that no longer exists fails gracefully")
def route_memory_missing(h):
    h.reset()
    record = h.invoke("action=memory&id=" + _uuid("m", 99))
    problems = standard_checks(
        record, expect_succeeded=False, expect_content=None, allow_dialog=True
    )
    if record.exception is not None:
        problems.append(f"unhandled exception: {record.exception!r}")
    return problems


@case("route 'memory': no id at all")
def route_memory_no_id(h):
    h.reset()
    record = h.invoke("action=memory")
    problems = []
    if record.exception is not None:
        problems.append(f"unhandled exception: {record.exception!r}")
    if len(record.end_of_directory) != 1:
        problems.append(
            f"views.py:307 calls client.memory(None), which builds the path "
            f"/api/memories/None; endOfDirectory ran "
            f"{len(record.end_of_directory)} times"
        )
    return problems


@case("route 'search': search menu", route="search")
def route_search(h):
    h.reset()
    record = h.invoke("action=search")
    problems = standard_checks(record, expect_content="files")
    labels = [i.label for _u, i, _f in record.items]
    if labels != ["Search your photos", "Smart search"]:
        problems.append(f"unexpected search menu {labels}")
    h.reset()
    h.dataset.features["smartSearch"] = False
    record = h.invoke("action=search")
    problems += standard_checks(record, expect_content="files")
    labels = [i.label for _u, i, _f in record.items]
    if labels != ["Search your photos"]:
        problems.append(
            f"smartSearch=false should hide the smart entry, got {labels}"
        )
    return problems


@case("route 'search_text': keyboard input then metadata search", route="search_text")
def route_search_text(h):
    h.reset()
    harness.STATE.dialog_input_queue.append("IMG_030")
    record = h.invoke("action=search_text")
    problems = standard_checks(record, expect_content="images")
    if not record.items:
        problems.append("search returned nothing for a filename that exists")
    posts = [r for r in h.server.requests if r["path"] == "/api/search/metadata"]
    if not posts or posts[0]["body"].get("originalFileName") != "IMG_030":
        problems.append(f"first search body was {posts[0]['body'] if posts else None}")
    return problems


@case("route 'search_text': cancelled input aborts navigation")
def route_search_text_cancel(h):
    h.reset()
    record = h.invoke("action=search_text")
    return standard_checks(record, expect_succeeded=False, expect_content=None)


@case("route 'search_smart': CLIP search", route="search_smart")
def route_search_smart(h):
    h.reset()
    harness.STATE.dialog_input_queue.append("a dog on a beach")
    record = h.invoke("action=search_smart")
    problems = standard_checks(record, expect_content="images")
    posts = [r for r in h.server.requests if r["path"] == "/api/search/smart"]
    if not posts:
        problems.append("no /api/search/smart request")
    elif posts[0]["body"].get("query") != "a dog on a beach":
        problems.append(f"smart search body {posts[0]['body']}")
    return problems


@case("route 'random': unpaged random selection", route="random")
def route_random(h):
    h.reset()
    record = h.invoke("action=random")
    problems = standard_checks(record, expect_content="images")
    if len(record.items) != 12:
        problems.append(f"expected 12 random assets, got {len(record.items)}")
    posts = [r for r in h.server.requests if r["path"] == "/api/search/random"]
    if not posts:
        problems.append("no /api/search/random request")
    elif not isinstance(posts[0]["body"].get("size"), int):
        problems.append(f"random body has no integer size: {posts[0]['body']}")
    return problems


@case("route 'settings': opens the dialog and aborts navigation", route="settings")
def route_settings(h):
    h.reset()
    record = h.invoke("action=settings")
    problems = standard_checks(record, expect_succeeded=False, expect_content=None)
    if record.settings_opened != 1:
        problems.append(
            f"openSettings called {record.settings_opened} times, expected 1"
        )
    return problems


@case("route 'slideshow': RunPlugin builtin, no directory", route="slideshow")
def route_slideshow(h):
    h.reset()
    target = "plugin://plugin.video.immich/?action=timeline"
    from urllib.parse import quote
    record = h.invoke(f"action=slideshow&target={quote(target, safe='')}", handle=-1)
    problems = standard_checks(
        record, expect_directory=False, expect_content=None, allow_dialog=True
    )
    if len(record.builtins) != 1:
        problems.append(f"expected one executebuiltin, got {record.builtins}")
    else:
        builtin = record.builtins[0]
        if not builtin.startswith('SlideShow("'):
            problems.append(f"unexpected builtin {builtin!r}")
        if target not in builtin:
            problems.append(f"slideshow target was lost: {builtin!r}")
        if not builtin.endswith(',recursive,notrandom)'):
            problems.append(f"slideshow flags are wrong: {builtin!r}")
    return problems


@case("route 'test_connection': reports the signed-in user", route="test_connection")
def route_test_connection(h):
    h.reset()
    record = h.invoke("action=test_connection", handle=-1)
    problems = standard_checks(
        record, expect_directory=False, expect_content=None, allow_dialog=True
    )
    oks = [d for d in record.dialogs if d[0] == "ok"]
    if len(oks) != 1:
        problems.append(f"expected one ok dialog, got {record.dialogs}")
    else:
        heading, message = oks[0][1], oks[0][2]
        if heading != "Connected":
            problems.append(f"heading {heading!r}")
        if "Admin User" not in message or "2.7.5" not in message:
            problems.append(f"message does not name the user and version: {message!r}")
    return problems


@case("dispatch: an unknown action falls back to the root menu")
def route_unknown_action(h):
    h.reset()
    record = h.invoke("action=not_a_route")
    problems = standard_checks(record, expect_content="files")
    if not record.items:
        problems.append("unknown action produced an empty listing")
    return problems


@case("route 'timeline' with video=1: videos-only listing")
def route_videos(h):
    h.reset()
    record = h.invoke("action=timeline&video=1")
    problems = standard_checks(record, expect_content="files")
    child = None
    for url, _i, _f in record.items:
        query = dict(parse_qsl(urlparse(url).query))
        if query.get("video") != "1":
            problems.append(f"video flag not inherited by the month link: {url!r}")
        child = child or url
    if child:
        record2 = h.invoke("?" + urlparse(child).query)
        problems += standard_checks(record2, expect_content="videos")
        for url, item, _f in record2.items:
            if "/video/playback" not in url:
                problems.append(f"videos-only listing contains a still: {url!r}")
    return problems


# ==========================================================================
# Failure paths
# ==========================================================================


@case("failure: server unreachable (connection refused)")
def fail_connection_refused(h):
    h.reset()
    import socket
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    dead = probe.getsockname()[1]
    probe.close()
    h.set_setting("immich_url", f"http://127.0.0.1:{dead}")
    record = h.invoke("action=timeline")
    problems = standard_checks(
        record, expect_succeeded=False, expect_content=None, allow_dialog=True
    )
    oks = [d for d in record.dialogs if d[0] == "ok"]
    if not oks or oks[0][1] != "Connection error":
        problems.append(f"expected the connection-error dialog, got {record.dialogs}")
    return problems


@case("failure: HTTP 401 from every authenticated endpoint")
def fail_401(h):
    h.reset()
    h.dataset.force_status["/timeline"] = (401, "Invalid API key")
    record = h.invoke("action=timeline")
    problems = standard_checks(
        record, expect_succeeded=False, expect_content=None, allow_dialog=True
    )
    oks = [d for d in record.dialogs if d[0] == "ok"]
    if not oks or oks[0][1] != "Authorization error":
        problems.append(f"expected the auth dialog, got {record.dialogs}")
    return problems


@case("failure: HTTP 500")
def fail_500(h):
    h.reset()
    h.dataset.force_status["/timeline"] = (500, "Internal server error")
    record = h.invoke("action=timeline")
    problems = standard_checks(
        record, expect_succeeded=False, expect_content=None, allow_dialog=True
    )
    oks = [d for d in record.dialogs if d[0] == "ok"]
    if not oks:
        problems.append(f"a 500 produced no dialog: {record.dialogs}")
        return problems
    heading, message = oks[0][1], oks[0][2]
    # The heading must come from strings.po, not be hardcoded English.
    if heading not in set(STRINGS.values()):
        problems.append(
            f"a 500 used the hardcoded heading {heading!r}; it is not in strings.po"
        )
    # The user should see Immich's message, not its JSON error envelope.
    if "{" in message or "statusCode" in message:
        problems.append(f"a 500 leaked the raw JSON body to the user: {message!r}")
    if "Internal server error" not in message:
        problems.append(f"a 500 dropped the server's message: {message!r}")
    return problems


@case("failure: malformed JSON body with a 200 status")
def fail_malformed_json(h):
    h.reset()
    h.dataset.malformed.add("/timeline")
    record = h.invoke("action=timeline")
    return standard_checks(
        record, expect_succeeded=False, expect_content=None, allow_dialog=True
    )


@case("edge: an entirely empty library")
def edge_empty_library(h):
    h.reset()
    h.dataset.buckets = []
    h.dataset.bucket_sizes = {}
    h.dataset.albums = []
    h.dataset.people = {"people": [], "total": 0, "hidden": 0, "hasNextPage": False}
    h.dataset.tags = []
    h.dataset.memories = []
    h.dataset.cities = []
    h.dataset.search_results = []
    h.dataset.random_results = []
    problems = []
    for query, content in (
        ("action=timeline", "files"),
        ("action=albums", "files"),
        ("action=people", "files"),
        ("action=places", "files"),
        ("action=tags", "files"),
        ("action=memories", "files"),
        ("action=random", "images"),
    ):
        record = h.invoke(query)
        problems += standard_checks(record, expect_content=content)
        if record.items:
            problems.append(f"{query}: empty library still produced items")
    return problems


@case("edge: columnar bucket with mismatched array lengths")
def edge_columnar_mismatch(h):
    h.reset()
    h.dataset.bucket_mismatch = True
    record = h.invoke("action=bucket&id=2026-08-01")
    problems = standard_checks(record, expect_content="images")
    if len(record.items) != 12:
        problems.append(
            f"a truncated column changed the item count to {len(record.items)}"
        )
    videos = [u for u, _i, _f in record.items if "/video/playback" in u]
    if videos:
        problems.append(
            "api.py:441-444 replaces a short column wholesale with defaults, so "
            f"the truncated isImage column silently reclassified assets ({len(videos)} videos)"
        )
    return problems


@case("edge: asset with null city, country and duration")
def edge_null_fields(h):
    h.reset()
    h.dataset.bucket_nulls = True
    record = h.invoke("action=bucket&id=2026-07-01")
    problems = standard_checks(record, expect_content="images")
    for _u, item, _f in record.items:
        if item.label2 not in ("", None):
            problems.append(f"null city/country produced label2 {item.label2!r}")
        if "None" in (item.getProperty("plot") or ""):
            problems.append("a None leaked into the plot text")
    return problems


@case("edge: blank immich_url goes straight to the settings dialog")
def edge_blank_url(h):
    h.reset()
    h.set_setting("immich_url", "")
    record = h.invoke("action=timeline")
    problems = standard_checks(
        record, expect_succeeded=False, expect_content=None, allow_dialog=True
    )
    oks = [d for d in record.dialogs if d[0] == "ok"]
    if not oks or oks[0][1] != "Not configured":
        problems.append(f"expected the 'Not configured' dialog, got {record.dialogs}")
    if record.settings_opened != 1:
        problems.append("the settings dialog was not opened")
    if h.server.requests:
        problems.append("a request was made despite there being no server URL")
    return problems


@case("edge: immich_url behind a reverse-proxy subpath")
def edge_subpath_url(h):
    h.reset()
    h.dataset.path_prefix = "/immich"
    h.set_setting("immich_url", h.server.url + "/immich")
    record = h.invoke("action=timeline")
    problems = []
    paths = [r["path"] for r in h.server.requests]
    if any(not p.startswith("/immich/") for p in paths):
        problems.append(
            "api.py:292 builds the request path as '/api' + path from the parsed "
            "netloc only, dropping any base-URL path prefix. Requests went to "
            f"{sorted(set(paths))} instead of /immich/api/... . With "
            f"immich_url={h.server.url + '/immich'!r} every API call 404s while "
            "the thumbnail/playback URLs handed to Kodi (api.py:373, which uses "
            "the full base_url) still carry the prefix, so the two disagree."
        )
    if record.exception is not None:
        problems.append(f"unhandled exception: {record.exception!r}")
    return problems


# ==========================================================================
# Paging
# ==========================================================================


@case("paging: a large bucket splits into pages that round-trip")
def paging(h):
    h.reset(page_size=50)
    h.dataset.buckets = [{"timeBucket": "2026-08-01", "count": 130}]
    h.dataset.bucket_sizes = {"2026-08-01": 130}

    problems = []
    query = "action=bucket&id=2026-08-01"
    seen_pages = []
    for expected_items, expect_next in ((50, True), (50, True), (30, False)):
        record = h.invoke(query)
        problems += standard_checks(record, expect_content="images")
        next_items = [
            (u, i) for u, i, folder in record.items if folder and i.label == "Next page"
        ]
        assets = len(record.items) - len(next_items)
        if assets != expected_items:
            problems.append(
                f"{query}: expected {expected_items} assets on this page, got {assets}"
            )
        if expect_next and not next_items:
            problems.append(f"{query}: no 'Next page' item but {130 - assets} remain")
            break
        if not expect_next:
            if next_items:
                problems.append(f"{query}: the final page still offers a next page")
            break
        next_url, next_item = next_items[0]
        parsed = dict(parse_qsl(urlparse(next_url).query))
        if parsed.get("action") != "bucket" or parsed.get("id") != "2026-08-01":
            problems.append(f"next-page URL loses its context: {next_url!r}")
        page = parsed.get("page")
        seen_pages.append(page)
        if not next_item.art.get("icon"):
            problems.append("the 'Next page' item has no icon")
        query = urlparse(next_url).query
    if seen_pages != ["1", "2"]:
        problems.append(f"page numbers did not advance 1 then 2: {seen_pages}")
    return problems


@case("paging: a dialog-driven search loses its query on page 2")
def paging_search_query(h):
    h.reset(page_size=50)
    h.dataset.search_results = [
        __import__("mockimmich").asset_dto(i) for i in range(300, 420)
    ]
    harness.STATE.dialog_input_queue.append("IMG_03")
    record = h.invoke("action=search_text")
    problems = standard_checks(record, expect_content="images")
    next_items = [
        u for u, i, folder in record.items if folder and i.label == "Next page"
    ]
    if not next_items:
        problems.append("120 results with page_size=50 produced no 'Next page' item")
        return problems
    parsed = dict(parse_qsl(urlparse(next_items[0]).query))
    if "q" not in parsed:
        problems.append(
            "views.py:436 builds the next-page URL from request.params, which for "
            "a keyboard-driven search contains only action=search_text. Following "
            f"{next_items[0]!r} re-opens the keyboard instead of showing page 2 of "
            "the same search."
        )
    return problems


# ==========================================================================
# reuselanguageinvoker
# ==========================================================================


@case("reuse: several routes in one interpreter do not contaminate each other")
def reuse_sequence(h):
    h.reset()
    sequence = [
        ("action=timeline", "files", 1),
        ("action=bucket&id=2026-08-01", "images", 2),
        ("action=albums", "files", 3),
        ("action=people", "files", 4),
        ("", "files", 5),
        ("action=tags", "files", 6),
        ("action=bucket&id=2026-07-01", "images", 7),
    ]
    problems = []
    for query, content, handle in sequence:
        record = h.invoke(query, handle=handle)
        problems += standard_checks(record, expect_content=content)
        for eod_handle, _s, _u, _c in record.end_of_directory:
            if eod_handle != handle:
                problems.append(
                    f"{query}: endOfDirectory used a stale handle {eod_handle} "
                    f"instead of {handle}"
                )
        for dir_handle, _entries, _total in record.directory_items:
            if dir_handle != handle:
                problems.append(
                    f"{query}: addDirectoryItems used a stale handle {dir_handle}"
                )
    counts = [len(r.items) for r in h.invocations[-len(sequence):]]
    if counts[1] != 12 or counts[6] != 3:
        problems.append(f"bucket contents leaked between invocations: {counts}")
    return problems


@case("reuse: the cached server version is not invalidated when the URL changes")
def reuse_session_cache(h):
    h.reset()
    h.invoke("action=timeline")
    from mockimmich import MockImmich
    other = MockImmich().start()
    try:
        other.dataset.version = {"major": 1, "minor": 132, "patch": 0}
        other.dataset.albums = h.dataset.albums
        other.dataset.album_assets = h.dataset.album_assets
        h.set_setting("immich_url", other.url)
        record = h.invoke("action=albums")
        problems = standard_checks(record, expect_content="files")
        asked = [r for r in other.requests if r["path"] == "/api/server/version"]
        if not asked:
            problems.append(
                "api.py:315-336 caches the detected version on the home window "
                "(kodiutils.py:54) and nothing clears it when immich_url changes. "
                "After switching to a server reporting 1.132.0 the addon kept the "
                "2.7.5 branch and never re-probed /api/server/version, so it will "
                "parse a bare-array timeline bucket as columnar."
            )
        return problems
    finally:
        other.stop()


@case("hygiene: the API key travels as a header on every request")
def api_key_header(h):
    h.reset()
    for query in ("action=timeline", "action=albums", "action=people",
                  "action=tags", "action=memories", "action=places"):
        h.invoke(query)
    problems = []
    for request in h.server.requests:
        if request["path"] in ("/api/server/version", "/api/server/features"):
            continue
        if request["api_key"] != API_KEY:
            problems.append(
                f"{request['method']} {request['path']} sent x-api-key="
                f"{request['api_key']!r}"
            )
        if "key=" in (request["query"].get("key") or ""):
            problems.append(f"{request['path']} put the key in the query string")
    return problems


@case("hygiene: no route asks the timeline for a param Immich 2.7.5 dropped")
def dead_params(h):
    h.reset()
    for query in ("action=timeline", "action=bucket&id=2026-08-01",
                  "action=favourites"):
        h.invoke(query)
    problems = []
    dead = ("size", "isArchived", "page", "pageSize")
    for request in h.server.requests:
        if not request["path"].startswith("/api/timeline"):
            continue
        for name in dead:
            if name in request["query"]:
                problems.append(
                    f"{request['path']} still sends the removed param "
                    f"{name}={request['query'][name]!r}"
                )
    return problems


@case("labels: month headings are clean, asset labels use the Kodi region format")
def label_formats(h):
    h.reset()
    record = h.invoke("action=timeline")
    problems = []
    weekdays = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday",
                "Saturday", "Sunday")
    for _u, item, _f in record.items:
        if any(day in item.label for day in weekdays):
            problems.append(f"month heading carries a weekday: {item.label!r}")
        if "  " in item.label or item.label.strip(" ,-/") != item.label:
            problems.append(f"month heading has stray separators: {item.label!r}")
        if "%" in item.label:
            problems.append(f"month heading has a raw strftime token: {item.label!r}")

    # Kodi's en_GB long date is "%A, %-d %B %Y"; the addon must apply it.
    record = h.invoke("action=bucket&id=2026-08-01")
    for _u, item, _f in record.items:
        if "%" in item.label:
            problems.append(f"asset label has a raw strftime token: {item.label!r}")
        break
    return problems


@case("labels: an Android-style region format without %-d still renders")
def label_region_variants(h):
    saved = dict(xbmc.REGION_FORMATS)
    problems = []
    try:
        for datelong, timefmt in (
            ("DDDD, MMMM D, YYYY", "hh:mm:ss xx"),   # en_US
            ("D. MMMM YYYY", "HH:mm:ss"),            # de_DE
            ("YYYY'年'M'月'D'日'", "HH:mm:ss"),        # ja_JP, quoted literals
        ):
            xbmc.REGION_FORMATS["datelong"] = datelong
            xbmc.REGION_FORMATS["time"] = timefmt
            code = (
                "import sys; sys.path[:0] = [%r, %r];\n"
                "import xbmc; xbmc.REGION_FORMATS['datelong'] = %r;\n"
                "xbmc.REGION_FORMATS['time'] = %r;\n"
                "import listing, datetime;\n"
                "print(listing.format_datetime(datetime.datetime(2026, 8, 1, 14, 5, 6)))"
                % (STUBS, LIB, datelong, timefmt)
            )
            finished = subprocess.run(
                [sys.executable, "-c", code], capture_output=True, text=True
            )
            if finished.returncode != 0:
                problems.append(
                    f"listing.format_datetime crashes with datelong={datelong!r}: "
                    f"{finished.stderr.strip().splitlines()[-1]}"
                )
            elif "%" in finished.stdout:
                problems.append(
                    f"datelong={datelong!r} left a raw token in "
                    f"{finished.stdout.strip()!r}"
                )
    finally:
        xbmc.REGION_FORMATS.clear()
        xbmc.REGION_FORMATS.update(saved)
    return problems


# ==========================================================================
# Immich version matrix
#
# Shapes and parameter windows come from immich-api-reference.md. The addon
# claims "from 1.13x through 3.x" (addon.xml news), so each branch it takes on
# version gets driven against a server that really behaves that way.
# ==========================================================================


def _timeline_queries(h):
    return [r["query"] for r in h.server.requests
            if r["path"].endswith(("/timeline/buckets", "/timeline/bucket"))]


def _matrix_hygiene(h, version):
    """Params this server version would silently drop, plus required ones."""
    problems = list(h.server.param_violations)
    if parse_version(version) < (1, 133, 0):
        for query in _timeline_queries(h):
            if "size" not in query:
                problems.append(
                    "api.py:540 _timeline_params() never emits a size param. "
                    "It is optional only from v1.133.0 (reference section 1); on "
                    f"Immich {version} both /timeline/buckets and "
                    "/timeline/bucket require size=DAY|MONTH and 400 without it. "
                    f"Sent: {query}"
                )
                break
    return problems


@case("immich 1.132.0: size=MONTH is sent, so the timeline works")
def matrix_1132_size(h):
    """`size` was mandatory on both timeline endpoints until v1.133 removed it.

    Omitting it makes every listing 400 on an older server, so the client must
    send it below that boundary and must not send it above.
    """
    h.reset()
    h.set_version("1.132.0")
    record = h.invoke("action=timeline")
    problems = standard_checks(record, expect_content="files")
    if not record.items:
        problems.append("the 1.132 timeline produced no months")

    buckets = [r for r in h.server.requests if r["path"] == "/api/timeline/buckets"]
    if not buckets:
        problems.append("/api/timeline/buckets was never called")
    for call in buckets:
        if call["query"].get("size") != "MONTH":
            problems.append(
                f"1.132 requires size=MONTH on /timeline/buckets, sent {call['query']}"
            )

    # And the same param must be absent once the server no longer accepts it.
    h.reset()
    h.set_version("2.7.5")
    h.invoke("action=timeline")
    for call in [r for r in h.server.requests if r["path"] == "/api/timeline/buckets"]:
        if "size" in call["query"]:
            problems.append(f"2.7.5 must not send size, sent {call['query']}")

    problems += _matrix_hygiene(h, "2.7.5")
    return problems


@case("immich 1.132.0: bare-array bucket parses, and visibility is not sent")
def matrix_1132_shape(h):
    h.reset()
    h.set_version("1.132.0")
    # Isolate the response shape from the missing-size defect above.
    h.dataset.enforce_legacy_size = False

    problems = standard_checks(h.invoke("action=timeline"), expect_content="files")
    record = h.invoke("action=bucket&id=2026-08-01")
    problems += standard_checks(record, expect_content="images")

    if len(record.items) != 12:
        problems.append(
            f"the pre-1.133 bare AssetResponseDto array yielded "
            f"{len(record.items)} items, expected 12"
        )
    for _u, item, _f in record.items:
        if not item.label:
            problems.append("bare-array asset produced a blank label")
            break
    videos = [i for u, i, _f in record.items if "/video/playback" in u]
    if not videos:
        problems.append("no video was recognised from type=VIDEO in the array")
    for query in _timeline_queries(h):
        if "visibility" in query:
            problems.append(
                f"visibility is a v1.133+ param but was sent to 1.132.0: {query}"
            )
        if "isArchived" in query:
            problems.append(f"isArchived sent without an archive filter: {query}")
    problems += list(h.server.param_violations)
    return problems


@case("immich 1.132.0: image_quality=fullsize degrades to preview")
def matrix_1132_fullsize(h):
    h.reset(image_quality=1)
    h.set_version("1.132.0")
    h.dataset.enforce_legacy_size = False
    record = h.invoke("action=bucket&id=2026-08-01")
    problems = standard_checks(record, expect_content="images")
    offenders = [u for u, _i, _f in record.items if "size=fullsize" in u]
    if offenders:
        problems.append(
            "image_url() emitted ?size=fullsize on 1.132.0. That value was "
            "only added in v1.133.0 (reference section 5); before it the size "
            "enum accepts thumbnail|preview only, so the request 400s and the "
            "still never renders. It must fall back to preview below that "
            f"boundary. Example URL: {offenders[0].split('|')[0]}"
        )
    return problems


@case("immich 1.134.0: columnar localDateTime still resolves a taken-at")
def matrix_1134(h):
    h.reset()
    h.set_version("1.134.0")
    record = h.invoke("action=bucket&id=2026-08-01")
    problems = standard_checks(record, expect_content="images")
    if len(record.items) != 12:
        problems.append(f"expected 12 items, got {len(record.items)}")
    for _u, item, _f in record.items:
        if not item.label:
            problems.append("localDateTime-only columnar produced a blank label")
            break
        if "11:12:33" not in item.label:
            problems.append(
                f"localDateTime 11:12:33 did not reach the label: {item.label!r}"
            )
            break
        if not item.datetime:
            problems.append(f"item {item.label!r} has no setDateTime value")
            break
    problems += list(h.server.param_violations)
    return problems


@case("immich 1.140.0: fileCreatedAt plus a fractional localOffsetHours")
def matrix_1140(h):
    h.reset()
    h.set_version("1.140.0")
    h.dataset.offset_hours = 5.5
    record = h.invoke("action=bucket&id=2026-08-01")
    problems = standard_checks(record, expect_content="images")
    if len(record.items) != 12:
        problems.append(f"expected 12 items, got {len(record.items)}")
    # 09:12:33 UTC + 5.5h == 14:42:33 local wall clock.
    for _u, item, _f in record.items:
        if "14:42:33" not in item.label:
            problems.append(
                "api.py:496-501 did not apply localOffsetHours=5.5 to "
                f"fileCreatedAt 09:12:33; label is {item.label!r}, expected the "
                "local wall clock 14:42:33"
            )
        if item.datetime and not item.datetime.endswith("T14:42:33"):
            problems.append(f"setDateTime is {item.datetime!r}, expected T14:42:33")
        break
    problems += list(h.server.param_violations)
    return problems


@case("immich 3.1.0: album assets come from search, durations are integer ms")
def matrix_310_album(h):
    h.reset()
    h.set_version("3.1.0")
    record = h.invoke(f"action=album&id={ALBUM_1}&title=Holiday+2026")
    problems = standard_checks(record, expect_content="images")

    if len(record.items) != 6:
        problems.append(
            f"v3 album fallback listed {len(record.items)} assets, expected 6"
        )
    if any(r["path"].endswith(f"/albums/{ALBUM_1}") for r in h.server.requests):
        problems.append(
            "the addon still called GET /api/albums/{id} on 3.1.0, where the "
            "response has no assets key at all"
        )
    posts = [r for r in h.server.requests if r["path"].endswith("/search/metadata")]
    if not posts:
        problems.append("v3 album did not fall back to POST /api/search/metadata")
    elif posts[0]["body"].get("albumIds") != [ALBUM_1]:
        problems.append(f"search body lacks albumIds: {posts[0]['body']}")

    videos = [i for u, i, _f in record.items if "/video/playback" in u]
    if not videos:
        problems.append("no video item in the v3 album listing")
    for item in videos:
        got = item._video_tag.data.get("duration") if item._video_tag else None
        if got != 83:
            problems.append(
                f"v3 integer-millisecond duration 83456 rendered as {got!r} "
                f"seconds, expected 83"
            )
    stills = [i for u, i, _f in record.items if "/video/playback" not in u]
    for item in stills:
        if item.video_tag_requested:
            problems.append("a v3 still with duration=null got a video info tag")
    problems += list(h.server.param_violations)
    return problems


@case("immich 3.1.0: shared_only sends isShared, not the removed shared param")
def matrix_310_shared(h):
    h.reset(shared_only=True)
    h.set_version("3.1.0")
    record = h.invoke("action=albums")
    problems = standard_checks(record, expect_content="files")
    calls = [r for r in h.server.requests if r["path"].endswith("/api/albums")]
    if not calls:
        problems.append("no GET /api/albums request")
    else:
        query = calls[-1]["query"]
        if query.get("isShared") != "true":
            problems.append(f"expected isShared=true on 3.1.0, got {query}")
        if "shared" in query:
            problems.append(f"the v3-removed shared param was still sent: {query}")
    problems += list(h.server.param_violations)
    return problems


@case("immich 3.1.0: the rest of the routes survive the v3 shapes")
def matrix_310_routes(h):
    h.reset()
    h.set_version("3.1.0")
    problems = []
    for query, content in (
        ("", "files"),
        ("action=timeline", "files"),
        ("action=bucket&id=2026-08-01", "images"),
        ("action=albums", "files"),
        ("action=people", "files"),
        ("action=places", "files"),
        ("action=tags", "files"),
        ("action=memories", "files"),
        ("action=random", "images"),
    ):
        problems += standard_checks(h.invoke(query), expect_content=content)
    problems += list(h.server.param_violations)
    return problems


@case("immich 2.7.5: no request carries a param this version dropped or lacks")
def matrix_275_params(h):
    h.reset(shared_only=True, include_partners=True)
    for query in ("", "action=timeline", "action=bucket&id=2026-08-01",
                  "action=albums", f"action=album&id={ALBUM_1}",
                  "action=favourites", "action=people", "action=places",
                  "action=place&city=Amsterdam", "action=tags",
                  "action=memories", f"action=memory&id={MEMORY_1}",
                  "action=random"):
        h.invoke(query)
    return list(h.server.param_violations)


@case("immich 1.134.0: an empty album lists nothing, not the whole library")
def matrix_1134_album_ids(h):
    h.reset()
    h.set_version("1.134.0")
    record = h.invoke(f"action=album&id={ALBUM_2}&title=Untitled")
    problems = standard_checks(record, expect_content="images")
    if record.items:
        problems.append(
            "an album with assetCount 0 listed "
            f"{len(record.items)} assets. album_assets() must treat an empty "
            "embedded list as empty rather than falling through to "
            "search_metadata(albumIds=[id]): albumIds only exists from v1.135.0 "
            "(reference section 6) and the server strips unknown body keys "
            "instead of rejecting them (reference section 0), so the filter "
            "would vanish and the whole library would be listed."
        )
    return problems


# ==========================================================================
# Settings variations
# ==========================================================================


def _still_urls(record):
    return [u for u, _i, _f in record.items
            if "/api/assets/" in u and "/video/playback" not in u]


@case("setting image_quality: 0 preview, 1 fullsize, 2 original")
def setting_image_quality(h):
    problems = []
    for value, expected in (
        (0, "/thumbnail?size=preview"),
        (1, "/thumbnail?size=fullsize"),
        (2, "/original"),
    ):
        h.reset(image_quality=value)
        record = h.invoke("action=bucket&id=2026-08-01")
        problems += standard_checks(record, expect_content="images")
        urls = _still_urls(record)
        if not urls:
            problems.append(f"image_quality={value}: no still URLs emitted")
            continue
        head = urls[0].split("|")[0]
        if not head.endswith(expected):
            problems.append(
                f"image_quality={value}: still URL is {head!r}, expected it to "
                f"end with {expected!r}"
            )
        # The grid thumbnail must stay small whatever the open-quality is.
        thumb = record.items[0][1].art.get("thumb", "").split("|")[0]
        if not thumb.endswith("/thumbnail?size=thumbnail"):
            problems.append(
                f"image_quality={value}: grid thumb is {thumb!r}, expected "
                f"?size=thumbnail"
            )
    return problems


@case("setting asset_name: date vs original filename, with a bucket fallback")
def setting_asset_name(h):
    problems = []

    # A timeline bucket has no filenames at all (reference section 3).
    for mode in (0, 1):
        h.reset(asset_name=mode)
        record = h.invoke("action=bucket&id=2026-08-01")
        problems += standard_checks(record, expect_content="images")
        for _u, item, _f in record.items:
            if not item.label:
                problems.append(f"asset_name={mode}: blank label in a bucket")
            elif not any(ch.isdigit() for ch in item.label):
                problems.append(
                    f"asset_name={mode}: bucket label {item.label!r} is neither "
                    f"a date nor a filename"
                )
            break

    # An album carries full DTOs, so mode 1 must actually use the filename.
    h.reset(asset_name=0)
    dated = h.invoke(f"action=album&id={ALBUM_1}&title=x")
    problems += standard_checks(dated, expect_content="images")
    h.reset(asset_name=1)
    named = h.invoke(f"action=album&id={ALBUM_1}&title=x")
    problems += standard_checks(named, expect_content="images")

    date_labels = [i.label for _u, i, _f in dated.items]
    name_labels = [i.label for _u, i, _f in named.items]
    if not name_labels or not all(l.startswith("IMG_") for l in name_labels):
        problems.append(f"asset_name=1 did not use originalFileName: {name_labels}")
    if date_labels == name_labels:
        problems.append("asset_name made no difference to album labels")
    if any(l.startswith("IMG_") for l in date_labels):
        problems.append(f"asset_name=0 used a filename anyway: {date_labels}")
    return problems


@case("setting shared_only: GET /api/albums carries the version's shared flag")
def setting_shared_only(h):
    problems = []
    for version, flag in (("2.7.5", "shared"), ("3.1.0", "isShared")):
        h.reset(shared_only=True)
        h.set_version(version)
        record = h.invoke("action=albums")
        problems += standard_checks(record, expect_content="files")
        calls = [r for r in h.server.requests if r["path"].endswith("/api/albums")]
        if not calls:
            problems.append(f"{version}: no GET /api/albums")
            continue
        if calls[-1]["query"].get(flag) != "true":
            problems.append(
                f"{version}: expected {flag}=true, got {calls[-1]['query']}"
            )
        labels = [i.label for _u, i, _f in record.items]
        if labels != ["Untitled"]:
            problems.append(f"{version}: shared filter listed {labels}")

    # And with the setting off, no flag at all.
    h.reset(shared_only=False)
    h.invoke("action=albums")
    calls = [r for r in h.server.requests if r["path"].endswith("/api/albums")]
    if calls and ("shared" in calls[-1]["query"] or "isShared" in calls[-1]["query"]):
        problems.append(f"shared_only=false still sent a flag: {calls[-1]['query']}")
    return problems


@case("setting include_partners: only on a plain timeline")
def setting_include_partners(h):
    problems = []

    h.reset(include_partners=True)
    h.invoke("action=timeline")
    plain = _timeline_queries(h)
    if not plain or plain[-1].get("withPartners") != "true":
        problems.append(f"withPartners missing from a plain timeline: {plain}")
    if plain and plain[-1].get("visibility") != "timeline":
        problems.append(
            f"withPartners requires visibility=timeline or Immich 400s: {plain}"
        )

    # Immich 400s on withPartners together with isFavorite.
    h.reset(include_partners=True)
    h.invoke("action=favourites")
    fav = _timeline_queries(h)
    for query in fav:
        if query.get("withPartners"):
            problems.append(
                f"withPartners sent alongside isFavorite, which Immich rejects "
                f"with a 400: {query}"
            )
    if not any(q.get("isFavorite") == "true" for q in fav):
        problems.append(f"favourites lost its isFavorite filter: {fav}")

    # withPartners is a no-op once albumId narrows the query.
    h.reset(include_partners=True)
    h.invoke(f"action=timeline&albumId={ALBUM_1}")
    scoped = _timeline_queries(h)
    for query in scoped:
        if query.get("withPartners"):
            problems.append(f"withPartners sent on an album-scoped timeline: {query}")
        if query.get("albumId") != ALBUM_1:
            problems.append(f"albumId did not reach the timeline: {query}")

    h.reset(include_partners=False)
    h.invoke("action=timeline")
    off = _timeline_queries(h)
    if off and off[-1].get("withPartners"):
        problems.append(f"include_partners=false still sent withPartners: {off}")
    return problems


@case("setting show_videos_in_timeline: false hides videos only in the timeline")
def setting_show_videos(h):
    problems = []

    h.reset(show_videos_in_timeline=False)
    bucket = h.invoke("action=bucket&id=2026-08-01")
    problems += standard_checks(bucket, expect_content="images")
    if any("/video/playback" in u for u, _i, _f in bucket.items):
        problems.append("show_videos_in_timeline=false left videos in the bucket")
    if not bucket.items:
        problems.append("hiding videos emptied the bucket entirely")

    album = h.invoke(f"action=album&id={ALBUM_1}&title=x")
    problems += standard_checks(album, expect_content="images")
    if not any("/video/playback" in u for u, _i, _f in album.items):
        problems.append(
            "views.py _emit_assets applied the timeline video preference to an "
            "album listing, which the user asked for by name"
        )

    harness.STATE.dialog_input_queue.append("IMG_030")
    search = h.invoke("action=search_text")
    problems += standard_checks(search, expect_content="images")
    if not any("/video/playback" in u for u, _i, _f in search.items):
        problems.append(
            "the timeline video preference also hid videos from search results"
        )

    h.reset(show_videos_in_timeline=True)
    both = h.invoke("action=bucket&id=2026-08-01")
    problems += standard_checks(both, expect_content="images")
    if not any("/video/playback" in u for u, _i, _f in both.items):
        problems.append("show_videos_in_timeline=true still hid videos")
    return problems


@case("setting timeout and ignore_ssl_errors reach ImmichClient")
def setting_client_transport(h):
    problems = []
    for timeout, ignore_ssl in ((5, False), (45, True), (120, False)):
        h.reset(timeout=timeout, ignore_ssl_errors=ignore_ssl)
        before = len(harness.CLIENT_INITS)
        h.invoke("action=timeline")
        made = harness.CLIENT_INITS[before:]
        if not made:
            problems.append("no ImmichClient was constructed")
            continue
        got = made[0]
        if got["timeout"] != timeout:
            problems.append(
                f"timeout={timeout} reached the client as {got['timeout']!r}"
            )
        if got["verify_ssl"] is not (not ignore_ssl):
            problems.append(
                f"ignore_ssl_errors={ignore_ssl} produced "
                f"verify_ssl={got['verify_ssl']!r}"
            )

    # The schema minimum is 5; the code clamps below that independently.
    h.reset(timeout=1)
    before = len(harness.CLIENT_INITS)
    h.invoke("action=timeline")
    made = harness.CLIENT_INITS[before:]
    if made and made[0]["timeout"] < 5:
        problems.append(
            f"kodiutils.py:117 should clamp the timeout to 5s, got "
            f"{made[0]['timeout']}"
        )
    return problems


@case("setting page_size: the floor is enforced and the window matches")
def setting_page_size(h):
    problems = []
    h.reset(page_size=50)
    h.dataset.buckets = [{"timeBucket": "2026-08-01", "count": 120}]
    h.dataset.bucket_sizes = {"2026-08-01": 120}
    record = h.invoke("action=bucket&id=2026-08-01")
    problems += standard_checks(record, expect_content="images")
    assets = [u for u, i, folder in record.items if not folder]
    if len(assets) != 50:
        problems.append(f"page_size=50 emitted {len(assets)} assets")

    # Below the schema minimum the code must still not produce a tiny page.
    h.reset(page_size=10)
    h.dataset.buckets = [{"timeBucket": "2026-08-01", "count": 120}]
    h.dataset.bucket_sizes = {"2026-08-01": 120}
    record = h.invoke("action=bucket&id=2026-08-01")
    problems += standard_checks(record, expect_content="images")
    assets = [u for u, i, folder in record.items if not folder]
    if len(assets) != 50:
        problems.append(
            f"kodiutils.py:146 clamps page_size to a minimum of 50, but a stored "
            f"value of 10 produced {len(assets)} assets"
        )
    return problems
