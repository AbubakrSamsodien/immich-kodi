"""Stub of the Kodi 21 (Omega) `xbmc` module.

Signatures follow xbmc/interfaces/legacy/ModuleXbmc.h on the Omega branch.
Argument types are policed the way Kodi's SWIG layer polices them: a wrong
type raises TypeError rather than being coerced.
"""

from __future__ import annotations

from kodi_state import STATE, need_bool, need_int, need_str

# xbmc/utils/log.h -> spdlog levels exposed in AddonModuleXbmc.i (Omega).
LOGDEBUG = 0
LOGINFO = 1
LOGWARNING = 2
LOGERROR = 3
LOGFATAL = 4
LOGNONE = 5

# Kodi 19 removed these; anything still referencing them is a bug.
# (Deliberately not defined: LOGNOTICE, LOGSEVERE.)

PLAYLIST_MUSIC = 0
PLAYLIST_VIDEO = 1

TRAY_OPEN = 16
DRIVE_NOT_READY = 1
TRAY_CLOSED_NO_MEDIA = 64
TRAY_CLOSED_MEDIA_PRESENT = 96


# The raw Kodi region formats. These are the values from
# resource.language.en_gb/resources/langinfo.xml, region "United Kingdom".
# REGION_FORMATS is patched by the suite to exercise other locales.
REGION_FORMATS = {
    "datelong": "DDDD, D MMMM YYYY",
    "dateshort": "DD/MM/YYYY",
    "time": "HH:mm:ss",
}


def _kodi_date_to_strftime(fmt: str) -> str:
    """CDateTime::GetAsLocalizedDate(fmt, ReturnFormat::CHOICE_YES).

    Kodi does not string-replace here: it run-length scans D/d, M/m and Y/y and
    emits the matching strftime token, copying everything else through. A run of
    1 emits the no-pad `%-d` / `%-m` form.
    """
    out = []
    index = 0
    length = len(fmt)
    while index < length:
        char = fmt[index]
        if char == "'":
            index += 1
            while index < length and fmt[index] != "'":
                out.append(fmt[index])
                index += 1
            index += 1
            continue
        if char in "DdMmYy":
            run = 0
            while index + run < length and fmt[index + run] == char:
                run += 1
            if char in "Dd":
                out.append("%-d" if run == 1 else "%d" if run == 2
                           else ("%A" if char == "D" else "%a"))
            elif char in "Mm":
                out.append("%-m" if run == 1 else "%m" if run == 2
                           else ("%B" if char == "M" else "%b"))
            else:
                out.append("%y" if run <= 2 else "%Y")
            index += run
            continue
        out.append(char)
        index += 1
    return "".join(out)


def _kodi_time_to_strftime(fmt: str) -> str:
    """The explicit ordered Replace chain in xbmc::getRegion(id == "time")."""
    result = fmt
    if result.startswith("HH"):
        result = result.replace("HH", "%H")
    else:
        result = result.replace("H", "%H")
        result = result.replace("hh", "%I")
        result = result.replace("h", "%I")
    for src, dst in (("mm", "%M"), ("m", "%M"), ("ss", "%S"), ("s", "%S"),
                     ("xx", "%p")):
        result = result.replace(src, dst)
    return result


def getRegion(id):  # noqa: A002 - Kodi's own parameter name
    """xbmc.getRegion(id) -> str; unsupported ids return "" and never raise."""
    need_str(id, "xbmc.getRegion", "id")
    STATE.region_requests.append(id)
    key = id.lower()
    if key == "datelong":
        return _kodi_date_to_strftime(REGION_FORMATS["datelong"])
    if key == "dateshort":
        return _kodi_date_to_strftime(REGION_FORMATS["dateshort"])
    if key == "time":
        return _kodi_time_to_strftime(REGION_FORMATS["time"])
    if key == "datelongraw":
        return REGION_FORMATS["datelong"]
    if key == "dateshortraw":
        return REGION_FORMATS["dateshort"]
    if key == "timeraw":
        return REGION_FORMATS["time"]
    if key == "meridiem":
        return "AM/PM"
    if key == "tempunit":
        return "C"
    if key == "speedunit":
        return "kmh"
    return ""


def log(msg, level=LOGDEBUG):
    need_str(msg, "xbmc.log", "msg")
    need_int(level, "xbmc.log", "level")
    # ModuleXbmc.cpp clamps an out-of-range level to LOGDEBUG rather than raising.
    if level < LOGDEBUG or level > LOGNONE:
        STATE.note(f"xbmc.log called with out-of-range level {level}")
        level = LOGDEBUG
    STATE.log_lines.append((level, msg))


def executebuiltin(function, wait=False):
    need_str(function, "xbmc.executebuiltin", "function")
    need_bool(wait, "xbmc.executebuiltin", "wait")
    STATE.builtins.append(function)


def executeJSONRPC(jsonrpccommand):
    need_str(jsonrpccommand, "xbmc.executeJSONRPC", "jsonrpccommand")
    return '{"jsonrpc":"2.0","result":{},"id":1}'


def sleep(timemillis):
    need_int(timemillis, "xbmc.sleep", "timemillis")


def getLocalizedString(id):  # noqa: A002
    need_int(id, "xbmc.getLocalizedString", "id")
    return ""


def getInfoLabel(cLine):
    need_str(cLine, "xbmc.getInfoLabel", "cLine")
    return ""


def getCondVisibility(condition):
    need_str(condition, "xbmc.getCondVisibility", "condition")
    return False


def getLanguage(format=0, region=False):  # noqa: A002
    need_int(format, "xbmc.getLanguage", "format")
    need_bool(region, "xbmc.getLanguage", "region")
    return "English"


def getSkinDir():
    return "skin.estuary"


class Monitor:
    def abortRequested(self):
        return False

    def waitForAbort(self, timeout=-1):
        return False


class Player:
    def play(self, item="", listitem=None, windowed=False, startpos=-1):
        pass
