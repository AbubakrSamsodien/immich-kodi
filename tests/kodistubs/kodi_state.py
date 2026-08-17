"""Shared recorder + resource parsers behind the xbmc* stubs.

Every stub writes here, and the test suite reads here. Nothing in this module
imports the addon, so importing a stub never drags the system under test in.
"""

from __future__ import annotations

import os
import re
import xml.etree.ElementTree as ET

# Repo root: tests/kodistubs/kodi_state.py -> ../../
ADDON_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

PO_PATH = os.path.join(
    ADDON_ROOT, "resources", "language", "resource.language.en_gb", "strings.po"
)
SETTINGS_PATH = os.path.join(ADDON_ROOT, "resources", "settings.xml")
ADDON_XML_PATH = os.path.join(ADDON_ROOT, "addon.xml")


# --------------------------------------------------------------------------
# strings.po
# --------------------------------------------------------------------------


def _unquote_po(line: str) -> str:
    match = re.match(r'^\s*"(.*)"\s*$', line)
    if match is None:
        return ""
    return match.group(1).encode("utf-8").decode("unicode_escape")


def parse_po(path: str = PO_PATH) -> dict:
    """Return {int id: msgid} for every `msgctxt "#NNNNN"` block."""
    strings = {}
    ctx = None
    mode = None
    buf = []

    def flush():
        nonlocal ctx, mode, buf
        if ctx is not None and mode == "msgid":
            strings[ctx] = "".join(buf)
        mode = None
        buf = []

    with open(path, "r", encoding="utf-8") as handle:
        for raw in handle:
            line = raw.rstrip("\n")
            stripped = line.strip()
            if stripped.startswith("#") and not stripped.startswith("#~"):
                continue
            if stripped.startswith("msgctxt"):
                flush()
                found = re.search(r'"#(\d+)"', stripped)
                ctx = int(found.group(1)) if found else None
                continue
            if stripped.startswith("msgid"):
                flush()
                mode = "msgid"
                buf = [_unquote_po(stripped[len("msgid"):])]
                continue
            if stripped.startswith("msgstr"):
                flush()
                mode = "msgstr"
                buf = []
                continue
            if stripped.startswith('"') and mode is not None:
                buf.append(_unquote_po(stripped))
                continue
            if not stripped:
                flush()
                ctx = None
    flush()
    return strings


# --------------------------------------------------------------------------
# settings.xml
# --------------------------------------------------------------------------


def parse_settings(path: str = SETTINGS_PATH) -> dict:
    """Return {id: {"type": str, "default": python value}} from the schema."""
    tree = ET.parse(path)
    declared = {}
    for node in tree.iter("setting"):
        sid = node.get("id")
        if not sid:
            continue
        stype = node.get("type") or "string"
        level_node = node.find("level")
        try:
            level = int((level_node.text or "0").strip()) if level_node is not None else 0
        except (TypeError, ValueError):
            level = 0
        default_node = node.find("default")
        raw = default_node.text if default_node is not None else None
        if stype == "boolean":
            value = str(raw).strip().lower() == "true" if raw else False
        elif stype in ("integer", "number"):
            try:
                value = int(str(raw).strip())
            except (TypeError, ValueError):
                value = 0
        else:
            value = raw if raw is not None else ""
        declared[sid] = {"type": stype, "default": value, "level": level}
    return declared


def parse_addon_xml(path: str = ADDON_XML_PATH) -> dict:
    root = ET.parse(path).getroot()
    info = {
        "id": root.get("id", ""),
        "version": root.get("version", ""),
        "name": root.get("name", ""),
        "author": root.get("provider-name", ""),
        "path": ADDON_ROOT,
        "profile": os.path.join(ADDON_ROOT, ".test-profile"),
        "type": "xbmc.python.pluginsource",
        "icon": os.path.join(ADDON_ROOT, "resources", "icon.png"),
        "fanart": os.path.join(ADDON_ROOT, "resources", "fanart.jpg"),
        "changelog": "",
        "description": "",
        "disclaimer": "",
        "summary": "",
    }
    for tag in ("summary", "description", "disclaimer", "news"):
        node = root.find(f".//{tag}")
        if node is not None and node.text:
            info["changelog" if tag == "news" else tag] = node.text
    return info


STRINGS = parse_po()
SETTINGS_SCHEMA = parse_settings()
ADDON_INFO = parse_addon_xml()


# --------------------------------------------------------------------------
# The recorder
# --------------------------------------------------------------------------


class Recorder:
    """One invocation's worth of Kodi interaction, plus process-lifetime state."""

    def __init__(self):
        # Process-lifetime (survives reset(), like a real Kodi session).
        self.window_properties = {}
        self.setting_values = {}
        self.strict_missing_setting = True
        self.dialog_input_queue = []
        self.reset()

    def reset(self):
        self.log_lines = []
        self.list_items = []
        self.directory_items = []  # [(handle, [(url, item, isfolder)], total)]
        self.end_of_directory = []  # [(handle, succeeded, updateListing, cacheToDisc)]
        self.content = []  # [(handle, content)]
        self.categories = []
        self.sort_methods = []
        self.plugin_fanart = []
        self.resolved_urls = []
        self.dialogs = []  # [("ok"|"input"|..., args)]
        self.notifications = []
        self.builtins = []
        self.settings_opened = 0
        self.localized_requests = []  # [(id, found)]
        self.core_string_requests = []  # [(id, found)] via xbmc.getLocalizedString
        self.setting_requests = []  # [(id, kind, declared)]
        self.region_requests = []
        self.violations = []  # stub-detected API misuse

    def note(self, message: str):
        self.violations.append(message)


STATE = Recorder()


# --------------------------------------------------------------------------
# Type policing helpers. Kodi's SWIG layer raises TypeError for a mismatched
# argument type, so the stubs do the same rather than coercing.
# --------------------------------------------------------------------------


def need_str(value, where: str, name: str):
    if not isinstance(value, str):
        raise TypeError(
            f"{where}: argument '{name}' expects str, got {type(value).__name__}"
        )
    return value


def need_int(value, where: str, name: str):
    if isinstance(value, bool):
        return int(value)
    if not isinstance(value, int):
        raise TypeError(
            f"{where}: argument '{name}' expects int, got {type(value).__name__}"
        )
    return value


def need_bool(value, where: str, name: str):
    if not isinstance(value, (bool, int)):
        raise TypeError(
            f"{where}: argument '{name}' expects bool, got {type(value).__name__}"
        )
    return bool(value)


def need_str_dict(value, where: str, name: str):
    if not isinstance(value, dict):
        raise TypeError(
            f"{where}: argument '{name}' expects dict, got {type(value).__name__}"
        )
    for key, item in value.items():
        need_str(key, where, f"{name} key")
        need_str(item, where, f"{name}[{key!r}]")
    return value


def need_str_list(value, where: str, name: str):
    if not isinstance(value, (list, tuple)):
        raise TypeError(
            f"{where}: argument '{name}' expects list, got {type(value).__name__}"
        )
    for item in value:
        need_str(item, where, f"{name} element")
    return list(value)
