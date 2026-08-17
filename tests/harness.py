"""Drives plugin.video.immich the way Kodi drives it.

Kodi hands a plugin `sys.argv = [plugin_url, handle, query_string]` and executes
`addon.py` as `__main__`. With `<reuselanguageinvoker>true</reuselanguageinvoker>`
it keeps the interpreter alive, so `sys.modules` survives between navigations
and only the script is re-run. `Harness.invoke` reproduces exactly that: one
`runpy.run_path(addon.py, run_name="__main__")` per navigation, in one process.
"""

from __future__ import annotations

import ast
import os
import sys
import traceback
from urllib.parse import parse_qsl, unquote, urlparse

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ADDON_PY = os.path.join(REPO, "addon.py")
VIEWS_PY = os.path.join(REPO, "resources", "lib", "views.py")
PLUGIN_URL = "plugin://plugin.video.immich/"

import xbmcplugin  # noqa: E402 - stub, path is set up by run.py
from kodi_state import STATE  # noqa: E402
from mockimmich import API_KEY, MockImmich  # noqa: E402

MEDIA_CALLS = []
CLIENT_INITS = []
VERSION_MATRIX = []   # [(version, case name)]
CURRENT_CASE = ""


# --------------------------------------------------------------------------
# Route discovery
# --------------------------------------------------------------------------


def discover_routes():
    """Read the @route decorators straight out of views.py.

    Parsed rather than imported so the list is independent of whether the
    module imported cleanly, and so a route that fails to register is still
    counted as one that must be covered.
    """
    tree = ast.parse(open(VIEWS_PY, "r", encoding="utf-8").read(), VIEWS_PY)
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        for decorator in node.decorator_list:
            if (
                isinstance(decorator, ast.Call)
                and isinstance(decorator.func, ast.Name)
                and decorator.func.id == "route"
                and decorator.args
                and isinstance(decorator.args[0], ast.Constant)
            ):
                found.append((decorator.args[0].value, node.name, node.lineno))
    return found


# --------------------------------------------------------------------------
# One invocation's captured result
# --------------------------------------------------------------------------


class Invocation:
    def __init__(self, query, handle):
        self.query = query
        self.handle = handle
        self.exception = None
        self.traceback = ""
        self.directory_items = []
        self.end_of_directory = []
        self.content = []
        self.categories = []
        self.sort_methods = []
        self.dialogs = []
        self.notifications = []
        self.builtins = []
        self.localized_requests = []
        self.core_string_requests = []
        self.setting_requests = []
        self.violations = []
        self.list_items = []
        self.settings_opened = 0

    def capture(self):
        self.directory_items = list(STATE.directory_items)
        self.end_of_directory = list(STATE.end_of_directory)
        self.content = list(STATE.content)
        self.categories = list(STATE.categories)
        self.sort_methods = list(STATE.sort_methods)
        self.dialogs = list(STATE.dialogs)
        self.notifications = list(STATE.notifications)
        self.builtins = list(STATE.builtins)
        self.localized_requests = list(STATE.localized_requests)
        self.core_string_requests = list(STATE.core_string_requests)
        self.setting_requests = list(STATE.setting_requests)
        self.violations = list(STATE.violations)
        self.list_items = list(STATE.list_items)
        self.settings_opened = STATE.settings_opened
        return self

    @property
    def items(self):
        """Flattened [(url, listitem, isfolder)] across every addDirectoryItems."""
        out = []
        for _handle, entries, _total in self.directory_items:
            out.extend(entries)
        return out

    @property
    def urls(self):
        return [url for url, _item, _folder in self.items]

    def item_labelled(self, label):
        for url, item, folder in self.items:
            if item.label == label:
                return url, item, folder
        return None


# --------------------------------------------------------------------------
# Harness
# --------------------------------------------------------------------------

DEFAULT_SETTINGS = {
    "immich_url": None,  # filled with the mock URL by reset()
    "api_key": API_KEY,
    "ignore_ssl_errors": False,
    "timeout": 5,
    "show_videos_in_timeline": True,
    "include_partners": False,
    "shared_only": False,
    "page_size": 500,
    "asset_name": 0,
    "image_quality": 0,
}


class Harness:
    def __init__(self):
        self.server = MockImmich().start()
        self.invocations = []

    def stop(self):
        self.server.stop()

    # -- configuration ------------------------------------------------------

    def reset(self, **settings):
        """Fresh dataset, fresh settings, fresh Kodi session."""
        self.server.reset()
        STATE.window_properties.clear()
        STATE.setting_values.clear()
        del STATE.dialog_input_queue[:]
        values = dict(DEFAULT_SETTINGS)
        values["immich_url"] = self.server.url
        values.update(settings)
        STATE.setting_values.update(values)

    def set_setting(self, key, value):
        STATE.setting_values[key] = value

    def clear_session(self):
        STATE.window_properties.clear()

    @property
    def dataset(self):
        return self.server.dataset

    def set_version(self, text, **settings):
        """Point the mock at another Immich release and forget cached facts.

        The detected version is cached on the home window per server URL, so a
        version switch against the same URL must clear the session or the
        addon keeps the previous branch.
        """
        self.server.set_version(text)
        STATE.window_properties.clear()
        VERSION_MATRIX.append((text, CURRENT_CASE))
        return self

    # -- driving ------------------------------------------------------------

    def invoke(self, query="", handle=1):
        """One Kodi navigation.

        Kodi re-executes addon.py as `__main__` on every navigation while
        sys.modules survives, so the script is compiled and exec'd here rather
        than run through runpy (which rewrites sys.argv[0] and would hide a
        stale-argv bug behind its own behaviour).
        """
        if query and not query.startswith("?"):
            query = "?" + query
        STATE.reset()
        saved = list(sys.argv)
        sys.argv = [PLUGIN_URL, str(handle), query]
        record = Invocation(query, handle)
        namespace = {
            "__name__": "__main__",
            "__file__": ADDON_PY,
            "__builtins__": __builtins__,
        }
        try:
            with open(ADDON_PY, "r", encoding="utf-8") as script:
                code = compile(script.read(), ADDON_PY, "exec")
            exec(code, namespace)  # noqa: S102 - this is what Kodi does
        except BaseException as error:  # noqa: BLE001 - the point is to catch it
            record.exception = error
            record.traceback = traceback.format_exc()
        finally:
            sys.argv = saved
        record.capture()
        self.invocations.append(record)
        return record


# --------------------------------------------------------------------------
# Shared assertions
# --------------------------------------------------------------------------

MEDIA_MARKERS = ("/api/assets/", "/api/people/")
VIDEO_MARKER = "/video/playback"


def parse_media_url(url):
    """Split a Kodi `path|Header=Value` URL, or return None if it is not one."""
    head, sep, options = url.partition("|")
    if not sep:
        return None
    return head, options


def check_kodi_url(url, problems, where):
    """A URL handed to Kodi must be usable by Kodi."""
    if url.startswith("plugin://"):
        parsed = urlparse(url)
        if not parsed.netloc:
            problems.append(f"{where}: plugin URL has no addon id: {url!r}")
        if parsed.query:
            # Must round-trip through parse_qsl the way Request does.
            pairs = parse_qsl(parsed.query)
            if not pairs:
                problems.append(f"{where}: plugin URL query does not parse: {url!r}")
        return

    split = parse_media_url(url)
    if split is None:
        problems.append(f"{where}: media URL is missing the |x-api-key= suffix: {url!r}")
        return
    head, options = split
    parsed = urlparse(head)
    if parsed.scheme not in ("http", "https"):
        problems.append(f"{where}: bad scheme in {url!r}")
    if not parsed.netloc:
        problems.append(f"{where}: no host in {url!r}")
    if not parsed.path.startswith("/api/"):
        problems.append(f"{where}: path does not start with /api/: {url!r}")
    if "|" in options:
        problems.append(f"{where}: more than one | separator in {url!r}")
    if not options.startswith("x-api-key="):
        problems.append(f"{where}: header suffix is not x-api-key=: {options!r}")
        return
    raw = options[len("x-api-key="):]
    for character in ("&", "|", " "):
        if character in raw:
            problems.append(
                f"{where}: api key in the header suffix contains an unescaped "
                f"{character!r}: {options!r}"
            )
    if unquote(raw) != API_KEY:
        problems.append(
            f"{where}: api key does not survive one percent-decode: "
            f"{unquote(raw)!r} != {API_KEY!r}"
        )


def standard_checks(record, expect_succeeded=True, expect_content=None,
                    expect_directory=True, allow_dialog=False):
    """Everything that must hold for any directory-producing route."""
    problems = []
    where = record.query or "<root>"

    if record.exception is not None:
        problems.append(
            f"{where}: unhandled exception escaped the entry point: "
            f"{record.exception!r}\n{record.traceback}"
        )

    if expect_directory:
        if len(record.end_of_directory) != 1:
            problems.append(
                f"{where}: endOfDirectory called {len(record.end_of_directory)} "
                f"times, expected exactly 1"
            )
        else:
            handle, succeeded, _update, _cache = record.end_of_directory[0]
            if handle != record.handle:
                problems.append(
                    f"{where}: endOfDirectory used handle {handle}, expected "
                    f"{record.handle}"
                )
            if succeeded != expect_succeeded:
                problems.append(
                    f"{where}: endOfDirectory succeeded={succeeded}, expected "
                    f"{expect_succeeded}"
                )
            if succeeded and not _cache:
                problems.append(
                    f"{where}: endOfDirectory cacheToDisc={_cache}; a cached "
                    f"listing makes Back free instead of a round trip"
                )
    else:
        if record.end_of_directory:
            problems.append(
                f"{where}: endOfDirectory called on a RunPlugin action "
                f"({record.end_of_directory})"
            )

    # setContent
    if expect_content is not None:
        values = [value for _handle, value in record.content]
        if len(values) != 1:
            problems.append(
                f"{where}: setContent called {len(values)} times, expected 1"
            )
        elif values[0] != expect_content:
            problems.append(
                f"{where}: setContent({values[0]!r}), expected {expect_content!r}"
            )
    for _handle, value in record.content:
        if value not in xbmcplugin.VALID_CONTENT:
            problems.append(
                f"{where}: setContent({value!r}) is not one of the documented "
                f"content types {list(xbmcplugin.VALID_CONTENT)}"
            )

    # Every emitted item
    for url, item, isfolder in record.items:
        icon = item.art.get("icon", "")
        if not icon:
            problems.append(
                f"{where}: ListItem {item.label!r} has an empty 'icon' art key, "
                f"so Kodi will fall back to DefaultVideo.png"
            )
        elif icon.startswith(("http://", "https://")):
            # The artwork floor: `icon` must be a bundled file, never remote.
            # A skin asked for art that has not resolved falls through to
            # DefaultVideo.png, which is the bug this addon exists to fix.
            problems.append(
                f"{where}: ListItem {item.label!r} points 'icon' at a remote "
                f"URL ({icon!r}); it must be a bundled file so the row never "
                f"falls back to DefaultVideo.png while the thumbnail caches"
            )
        elif not icon.startswith(("special://", "resource://")):
            if not os.path.exists(icon.split("|")[0]):
                problems.append(
                    f"{where}: ListItem {item.label!r} icon does not exist on "
                    f"disk: {icon!r}"
                )
        if not item.label:
            problems.append(f"{where}: emitted a ListItem with an empty label")

        # setContentLookup(False) suppresses the HEAD probe Kodi would use to
        # learn the stream type, so it is only safe once a mimetype was given.
        if item.contentlookup is False and not item.mimetype:
            problems.append(
                f"{where}: ListItem {item.label!r} disabled content lookup "
                f"without setting a mimetype, leaving Kodi no way to identify "
                f"the stream ({url!r})"
            )

        is_video_url = VIDEO_MARKER in url
        if not is_video_url and item.video_tag_requested:
            problems.append(
                f"{where}: getVideoInfoTag() was called on non-video item "
                f"{item.label!r} ({url!r}); Kodi will classify it as video"
            )
        if is_video_url and not item.video_tag_requested:
            problems.append(
                f"{where}: video item {item.label!r} has no video info tag"
            )
        check_kodi_url(url, problems, f"{where} item {item.label!r}")
        for key, value in item.art.items():
            if value.startswith(("http://", "https://")):
                check_kodi_url(value, problems, f"{where} art[{key}] of {item.label!r}")
        for _label, action in item.context_menu:
            if action.startswith("RunPlugin(") and action.endswith(")"):
                check_kodi_url(action[len("RunPlugin("):-1], problems,
                               f"{where} context menu")

    # Localisation and settings hygiene
    for string_id, found in record.localized_requests:
        if not found:
            problems.append(
                f"{where}: getLocalizedString({string_id}) is not in strings.po"
            )
    for string_id, found in record.core_string_requests:
        if 30000 <= string_id <= 33999:
            problems.append(
                f"{where}: xbmc.getLocalizedString({string_id}) is an addon "
                f"string id; it resolves against Kodi core strings and returns "
                f"the wrong text or nothing"
            )
        elif not found:
            problems.append(
                f"{where}: xbmc.getLocalizedString({string_id}) is not a known "
                f"Kodi core string"
            )
    for setting_id, kind, declared in record.setting_requests:
        if not declared:
            problems.append(
                f"{where}: getSetting{kind}({setting_id!r}) is not declared in "
                f"resources/settings.xml"
            )

    problems.extend(f"{where}: {note}" for note in record.violations)

    if not allow_dialog:
        for dialog in record.dialogs:
            if dialog[0] == "ok":
                problems.append(f"{where}: unexpected error dialog {dialog[1:]}")
    return problems
