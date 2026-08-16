"""URL building, dispatch and the per-request context.

Nothing in this module is created at import time. With
`<reuselanguageinvoker>` enabled Kodi keeps the interpreter alive between
invocations, so any module-level `sys.argv` or handle would be stale on the
next navigation.
"""

from __future__ import annotations

import sys
from urllib.parse import parse_qsl, urlencode

import xbmc
import xbmcgui
import xbmcplugin

from api import ImmichAuthError, ImmichClient, ImmichConnectionError, ImmichError
from kodiutils import (
    ADDON_FANART,
    SessionCache,
    Settings,
    error_dialog,
    localise,
    log,
    log_error,
    open_settings,
)

_ROUTES = {}


def route(action: str):
    """Register a view against an `action` query value."""

    def register(function):
        _ROUTES[action] = function
        return function

    return register


class Request:
    """Everything one plugin invocation needs, built fresh each time."""

    def __init__(self, argv):
        self.base_url = argv[0]
        self.handle = int(argv[1])
        self.params = dict(parse_qsl(argv[2][1:])) if len(argv) > 2 else {}
        self.settings = Settings()
        self.cache = SessionCache()
        self._client = None

    @property
    def action(self) -> str:
        return self.params.get("action", "")

    def param(self, name: str, default=None):
        return self.params.get(name, default)

    def int_param(self, name: str, default: int = 0) -> int:
        try:
            return int(self.params.get(name, default))
        except (TypeError, ValueError):
            return default

    def url(self, **kwargs) -> str:
        clean = {k: v for k, v in kwargs.items() if v not in (None, "")}
        return f"{self.base_url}?{urlencode(clean)}"

    @property
    def client(self) -> ImmichClient:
        if self._client is None:
            self._client = ImmichClient(
                base_url=self.settings.server_url,
                api_key=self.settings.api_key,
                timeout=self.settings.timeout,
                verify_ssl=self.settings.verify_ssl,
                log=log,
            )
            # Resolved before any call that branches on it. Cached on the home
            # window, so this costs one request per Kodi session, not per view.
            self._client.detect_version(self.cache)
        return self._client

    # -- directory helpers ---------------------------------------------------

    @property
    def is_directory(self) -> bool:
        """False for RunPlugin invocations, which Kodi calls with handle -1."""
        return self.handle >= 0

    def add_items(self, items, content: str = "files", category: str = "", sort=()):
        """Emit a finished directory.

        Sort methods are registered before the items because the first
        registered method becomes the listing's default sort.
        """
        if not self.is_directory:
            return
        xbmcplugin.setContent(self.handle, content)
        if category:
            xbmcplugin.setPluginCategory(self.handle, category)
        for method in sort:
            xbmcplugin.addSortMethod(self.handle, sortMethod=method)
        xbmcplugin.addDirectoryItems(self.handle, items, len(items))
        xbmcplugin.setPluginFanart(self.handle, ADDON_FANART)
        xbmcplugin.endOfDirectory(self.handle, cacheToDisc=False)

    def fail(self):
        """Abort navigation, leaving the user where they were."""
        if self.is_directory:
            xbmcplugin.endOfDirectory(self.handle, succeeded=False)


def dispatch(argv):
    """Route one invocation, converting API failures into user-facing dialogs."""
    request = Request(argv)

    if not request.settings.server_url or not request.settings.api_key:
        # Nothing can be listed without credentials, so go straight to setup.
        error_dialog(30050, 30051)
        open_settings()
        request.fail()
        return

    handler = _ROUTES.get(request.action)
    if handler is None:
        handler = _ROUTES[""]

    try:
        handler(request)
    except ImmichAuthError as error:
        log_error(f"auth rejected: {error}")
        error_dialog(30009, 30010)
        request.fail()
    except ImmichConnectionError as error:
        log_error(f"connection failed: {error}")
        error_dialog(30007, 30008)
        request.fail()
    except ImmichError as error:
        log_error(f"api error: {error}")
        xbmcgui.Dialog().ok(localise(30086), str(error))
        request.fail()
    except Exception as error:  # noqa: BLE001 - a crash here would hang the UI
        log_error(f"unhandled: {error!r}")
        xbmcgui.Dialog().ok(localise(30086), str(error))
        request.fail()
    finally:
        if request._client is not None:
            request._client.close()


def run():
    """Entry point. Reads argv per invocation, never caching it."""
    import views  # noqa: F401 - importing registers every route

    dispatch(sys.argv)
