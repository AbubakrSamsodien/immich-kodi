"""Stub of the Kodi 21 (Omega) `xbmcaddon` module.

`getLocalizedString` really parses resources/language/.../strings.po and
`getSetting*` really reads resources/settings.xml, so a string id or setting id
the code invents is observable rather than silently absorbed.
"""

from __future__ import annotations

from kodi_state import (
    ADDON_INFO,
    SETTINGS_SCHEMA,
    STATE,
    STRINGS,
    need_int,
    need_str,
)

_INFO_KEYS = {
    "author",
    "changelog",
    "description",
    "disclaimer",
    "fanart",
    "icon",
    "id",
    "name",
    "path",
    "profile",
    "stars",
    "summary",
    "type",
    "version",
}


class Settings:
    """xbmcaddon.Settings, added in Kodi 20 (Nexus)."""

    def __init__(self, addon):
        self._addon = addon

    def getBool(self, id):  # noqa: A002
        return self._addon.getSettingBool(id)

    def getInt(self, id):  # noqa: A002
        return self._addon.getSettingInt(id)

    def getString(self, id):  # noqa: A002
        return self._addon.getSettingString(id)


class Addon:
    """xbmcaddon.Addon([id])."""

    def __init__(self, id=None):  # noqa: A002
        if id is not None:
            need_str(id, "xbmcaddon.Addon", "id")
            if id != ADDON_INFO["id"]:
                # Kodi raises RuntimeError("Unknown addon id ...").
                raise RuntimeError(f"Unknown addon id '{id}'.")
        self._id = ADDON_INFO["id"]

    # -- info ---------------------------------------------------------------

    def getAddonInfo(self, id):  # noqa: A002
        need_str(id, "Addon.getAddonInfo", "id")
        if id not in _INFO_KEYS:
            # Kodi: throws AddonException -> Python RuntimeError.
            raise RuntimeError(f"'{id}' is an invalid Id")
        return ADDON_INFO.get(id, "")

    def getLocalizedString(self, id):  # noqa: A002
        need_int(id, "Addon.getLocalizedString", "id")
        found = id in STRINGS
        STATE.localized_requests.append((id, found))
        # Kodi returns an empty string when the id is not in the addon's po.
        return STRINGS.get(id, "")

    def openSettings(self):
        STATE.settings_opened += 1

    def getSettings(self):
        return Settings(self)

    # -- settings -----------------------------------------------------------

    def _lookup(self, id, kind, wanted_type):
        """Mirror CAddon::GetSettingValue<TSetting>.

        `if (setting == nullptr || setting->GetType() != TSetting::Type())
        return false;` and Addon.cpp then does
        `throw XBMCAddon::WrongTypeException("Invalid setting type")`, which the
        SWIG wrapper turns into a Python TypeError. So an id that settings.xml
        never declares, and an id read with the wrong accessor, both raise
        TypeError - they are indistinguishable to the addon.
        """
        need_str(id, f"Addon.getSetting{kind}", "id")
        declared = id in SETTINGS_SCHEMA
        STATE.setting_requests.append((id, kind, declared))
        if not declared:
            if STATE.strict_missing_setting:
                raise TypeError("Invalid setting type")
            return None
        schema_type = SETTINGS_SCHEMA[id]["type"]
        if wanted_type is not None and schema_type != wanted_type:
            raise TypeError("Invalid setting type")
        if id in STATE.setting_values:
            return STATE.setting_values[id]
        return SETTINGS_SCHEMA[id]["default"]

    def getSetting(self, id):  # noqa: A002
        """CAddon::GetSetting returns "" for an unknown key and never throws."""
        need_str(id, "Addon.getSetting", "id")
        declared = id in SETTINGS_SCHEMA
        STATE.setting_requests.append((id, "", declared))
        if not declared:
            return ""
        value = STATE.setting_values.get(id, SETTINGS_SCHEMA[id]["default"])
        if isinstance(value, bool):
            return "true" if value else "false"
        return str(value)

    def getSettingString(self, id):  # noqa: A002
        value = self._lookup(id, "String", "string")
        return "" if value is None else str(value)

    def getSettingBool(self, id):  # noqa: A002
        value = self._lookup(id, "Bool", "boolean")
        return bool(value)

    def getSettingInt(self, id):  # noqa: A002
        value = self._lookup(id, "Int", "integer")
        return 0 if value is None else int(value)

    def getSettingNumber(self, id):  # noqa: A002
        value = self._lookup(id, "Number", "number")
        return 0.0 if value is None else float(value)

    def setSetting(self, id, value):  # noqa: A002
        need_str(id, "Addon.setSetting", "id")
        need_str(value, "Addon.setSetting", "value")
        STATE.setting_values[id] = value

    def setSettingString(self, id, value):  # noqa: A002
        need_str(value, "Addon.setSettingString", "value")
        STATE.setting_values[id] = value
        return True

    def setSettingBool(self, id, value):  # noqa: A002
        STATE.setting_values[id] = bool(value)
        return True

    def setSettingInt(self, id, value):  # noqa: A002
        need_int(value, "Addon.setSettingInt", "value")
        STATE.setting_values[id] = value
        return True
