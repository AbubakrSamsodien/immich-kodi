"""Thin wrappers over the Kodi APIs the addon uses.

Keeping every `xbmc*` call behind this module means `api.py` stays importable
outside Kodi, which is what makes the client testable.
"""

from __future__ import annotations

import os
from typing import Optional

import xbmc
import xbmcaddon
import xbmcgui
import xbmcvfs

ADDON = xbmcaddon.Addon()
ADDON_ID = ADDON.getAddonInfo("id")
ADDON_NAME = ADDON.getAddonInfo("name")
ADDON_PATH = xbmcvfs.translatePath(ADDON.getAddonInfo("path"))
ADDON_ICON = os.path.join(ADDON_PATH, "resources", "icon.png")
ADDON_FANART = os.path.join(ADDON_PATH, "resources", "fanart.jpg")
MEDIA_PATH = os.path.join(ADDON_PATH, "resources", "media")

# The home window outlives a plugin invocation, which is the only cheap way to
# carry the detected server version between directory listings.
_HOME_WINDOW = 10000


def log(message: str, level: int = xbmc.LOGDEBUG):
    xbmc.log(f"[{ADDON_ID}] {message}", level)


def log_error(message: str):
    log(message, xbmc.LOGERROR)


def localise(string_id: int) -> str:
    return ADDON.getLocalizedString(string_id)


def notify(heading: str, message: str, icon: str = xbmcgui.NOTIFICATION_INFO, time: int = 5000):
    xbmcgui.Dialog().notification(heading, message, icon, time)


def error_dialog(heading_id: int, message_id: int):
    xbmcgui.Dialog().ok(localise(heading_id), localise(message_id))


def open_settings():
    ADDON.openSettings()


class SessionCache:
    """Get/set pair backed by home-window properties.

    Values live for the Kodi session, not for the plugin invocation, so the
    server version is probed once rather than on every navigation.
    """

    def __init__(self):
        self._window = xbmcgui.Window(_HOME_WINDOW)

    def get(self, key: str) -> Optional[str]:
        return self._window.getProperty(key) or None

    def set(self, key: str, value: str):
        self._window.setProperty(key, value)

    def clear(self, key: str):
        self._window.clearProperty(key)


class Settings:
    """Typed settings access.

    A fresh `Addon()` per request, because a long-lived one can hand back stale
    values once <reuselanguageinvoker> keeps the interpreter alive.

    Kodi supplies the `<default>` from settings.xml for any declared setting, so
    the `default` arguments here only cover the case where a read raises — a
    setting missing from the schema, or declared with a different type. The
    previous version called `int()` straight onto a setting string, so a fresh
    install crashed before the settings dialog could open.
    """

    def __init__(self):
        addon = xbmcaddon.Addon()
        # xbmcaddon.Settings is the v20+ API; Addon().getSettingString and
        # friends are deprecated. Fall back for older Kodi builds.
        self._settings = addon.getSettings() if hasattr(addon, "getSettings") else None
        self._addon = addon

    def _string(self, key: str, default: str = "") -> str:
        try:
            value = (
                self._settings.getString(key)
                if self._settings is not None
                else self._addon.getSettingString(key)
            )
        except (TypeError, ValueError, RuntimeError):
            return default
        return value if value else default

    def _bool(self, key: str, default: bool = False) -> bool:
        try:
            if self._settings is not None:
                return bool(self._settings.getBool(key))
            return bool(self._addon.getSettingBool(key))
        except (TypeError, ValueError, RuntimeError):
            return default

    def _int(self, key: str, default: int = 0) -> int:
        try:
            if self._settings is not None:
                return int(self._settings.getInt(key))
            return int(self._addon.getSettingInt(key))
        except (TypeError, ValueError, RuntimeError):
            return default

    @property
    def server_url(self) -> str:
        return self._string("immich_url").rstrip("/")

    @property
    def api_key(self) -> str:
        return self._string("api_key")

    @property
    def verify_ssl(self) -> bool:
        return not self._bool("ignore_ssl_errors", False)

    @property
    def timeout(self) -> int:
        return max(5, self._int("timeout", 20))

    @property
    def shared_only(self) -> bool:
        return self._bool("shared_only", False)

    @property
    def include_partners(self) -> bool:
        return self._bool("include_partners", False)

    @property
    def asset_name(self) -> int:
        """0 = date and time, 1 = original file name."""
        return self._int("asset_name", 0)

    @property
    def image_quality(self) -> str:
        """Which endpoint stills are opened from.

        `original` is byte-for-byte and will fail on HEIC and RAW, so it is not
        the default even though it is the highest quality. The index is clamped
        at both ends: a negative value would otherwise select from the end of
        the tuple and quietly pick `original`.
        """
        choices = ("preview", "fullsize", "original")
        return choices[max(0, min(self._int("image_quality", 0), len(choices) - 1))]

    @property
    def page_size(self) -> int:
        return max(50, self._int("page_size", 500))

    @property
    def show_videos_in_timeline(self) -> bool:
        return self._bool("show_videos_in_timeline", True)


def media(filename: str) -> str:
    """Absolute path to a bundled artwork file.

    Kodi resolves plugin art from the filesystem, so menu icons must be real
    paths inside the addon rather than resource:// URLs.
    """
    candidate = os.path.join(MEDIA_PATH, filename)
    if os.path.exists(candidate):
        return candidate
    # Silently substituting the addon icon is how every row ends up looking the
    # same, which is the visual signature of the bug this addon exists to fix.
    log_error(f"missing bundled artwork: resources/media/{filename}")
    return ADDON_ICON
