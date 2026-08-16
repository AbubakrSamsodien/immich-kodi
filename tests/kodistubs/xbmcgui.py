"""Stub of the Kodi 21 (Omega) `xbmcgui` module.

The ListItem records which info tags were *requested*, not just which were
populated: `VIDEO::IsVideo()` is true as soon as a video info tag exists on the
item, so `getVideoInfoTag()` on a still is itself the bug.
"""

from __future__ import annotations

import re

from kodi_state import (
    STATE,
    need_bool,
    need_int,
    need_str,
    need_str_list,
)


class ListItemException(RuntimeError):
    """XBMCCOMMONS_STANDARD_EXCEPTION(ListItemException) -> Python RuntimeError."""


def _properties(value, where, name):
    """XBMCAddon::Properties == Dictionary<StringOrInt>.

    Keys go through PyXBMCGetUnicodeString with coerceToString=false, so a
    non-str key is a TypeError. Values are StringOrInt, which coerces int and
    float but still rejects None, lists and objects.
    """
    if not isinstance(value, dict):
        raise TypeError(
            f"{where}: argument '{name}' expects dict, got {type(value).__name__}"
        )
    out = {}
    for key, item in value.items():
        need_str(key, where, f"{name} key")
        if isinstance(item, str):
            out[key] = item
        elif isinstance(item, (int, float)) and not isinstance(item, bool):
            out[key] = str(item)
        else:
            raise TypeError(
                f'{where}: argument "{name}[{key!r}]" must be unicode or str, '
                f"got {type(item).__name__}"
            )
    return out

# ModuleXbmcgui.cpp: NOTIFICATION_* are const char*, not ints.
NOTIFICATION_INFO = "info"
NOTIFICATION_WARNING = "warning"
NOTIFICATION_ERROR = "error"

# Dialog.h:27-35
INPUT_ALPHANUM = 0
INPUT_NUMERIC = 1
INPUT_DATE = 2
INPUT_TIME = 3
INPUT_IPADDRESS = 4
INPUT_PASSWORD = 5

PASSWORD_VERIFY = 1
ALPHANUM_HIDE_INPUT = 2

DLG_YESNO_NO_BTN = 10
DLG_YESNO_YES_BTN = 11
DLG_YESNO_CUSTOM_BTN = 12

_W3C = re.compile(r"^\d{4}-\d{2}-\d{2}(T\d{2}:\d{2}(:\d{2})?(Z|[+-]\d{2}:\d{2})?)?$")


class ControlError(Exception):
    pass


class WindowException(Exception):
    pass


# --------------------------------------------------------------------------
# Info tags
# --------------------------------------------------------------------------


class InfoTagVideo:
    """xbmcgui.InfoTagVideo (Kodi 20+ setters)."""

    def __init__(self, offscreen=False):
        self.offscreen = bool(offscreen)
        self.data = {}

    def setMediaType(self, mediatype):
        need_str(mediatype, "InfoTagVideo.setMediaType", "mediatype")
        self.data["mediatype"] = mediatype

    def setTitle(self, title):
        need_str(title, "InfoTagVideo.setTitle", "title")
        self.data["title"] = title

    def setPlot(self, plot):
        need_str(plot, "InfoTagVideo.setPlot", "plot")
        self.data["plot"] = plot

    def setPlotOutline(self, plotoutline):
        need_str(plotoutline, "InfoTagVideo.setPlotOutline", "plotoutline")

    def setDuration(self, duration):
        # C++ signature is `void setDuration(int duration)`.
        if isinstance(duration, bool) or not isinstance(duration, int):
            raise TypeError(
                "InfoTagVideo.setDuration: argument 'duration' expects int, "
                f"got {type(duration).__name__}"
            )
        self.data["duration"] = duration

    def setPremiered(self, premiered):
        need_str(premiered, "InfoTagVideo.setPremiered", "premiered")
        if not re.match(r"^\d{4}-\d{2}-\d{2}$", premiered):
            STATE.note(
                f"InfoTagVideo.setPremiered({premiered!r}) is not YYYY-MM-DD"
            )
        self.data["premiered"] = premiered

    def setDateAdded(self, dateadded):
        need_str(dateadded, "InfoTagVideo.setDateAdded", "dateadded")
        if not re.match(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$", dateadded):
            STATE.note(
                "InfoTagVideo.setDateAdded expects 'YYYY-MM-DD HH:MM:SS', got "
                f"{dateadded!r}"
            )
        self.data["dateadded"] = dateadded

    def setCountries(self, countries):
        need_str_list(countries, "InfoTagVideo.setCountries", "countries")
        self.data["countries"] = list(countries)

    def setGenres(self, genres):
        need_str_list(genres, "InfoTagVideo.setGenres", "genres")

    def setTagLine(self, tagline):
        need_str(tagline, "InfoTagVideo.setTagLine", "tagline")

    def setYear(self, year):
        need_int(year, "InfoTagVideo.setYear", "year")

    def setResumePoint(self, time, totaltime=0.0):
        pass


class InfoTagPicture:
    """xbmcgui.InfoTagPicture, added in Kodi 20 (Nexus)."""

    def __init__(self, offscreen=False):
        self.offscreen = bool(offscreen)
        self.data = {}

    def setResolution(self, width, height):
        need_int(width, "InfoTagPicture.setResolution", "width")
        need_int(height, "InfoTagPicture.setResolution", "height")
        self.data["resolution"] = (width, height)

    def setDateTimeTaken(self, datetimetaken):
        """String only - a datetime object raises TypeError in Kodi."""
        need_str(datetimetaken, "InfoTagPicture.setDateTimeTaken", "datetimetaken")
        if datetimetaken and not _W3C.match(datetimetaken):
            STATE.note(
                f"InfoTagPicture.setDateTimeTaken({datetimetaken!r}) is not W3C"
            )
        self.data["datetimetaken"] = datetimetaken

    def setPicturePath(self, path):
        need_str(path, "InfoTagPicture.setPicturePath", "path")

    def setExifInfo(self, exif):
        raise TypeError("InfoTagPicture.setExifInfo does not exist in Kodi 21")


class InfoTagMusic:
    def __init__(self, offscreen=False):
        self.offscreen = bool(offscreen)


# --------------------------------------------------------------------------
# ListItem
# --------------------------------------------------------------------------


class ListItem:
    """xbmcgui.ListItem([label, label2, path, offscreen]).

    Kodi 20 removed the `iconImage` and `thumbnailImage` constructor arguments;
    passing either raises here, as it does in Kodi.
    """

    def __init__(self, label="", label2="", path="", offscreen=False, **kwargs):
        if kwargs:
            raise TypeError(
                "xbmcgui.ListItem: unexpected keyword argument(s) "
                f"{sorted(kwargs)} (iconImage/thumbnailImage were removed in Kodi 20)"
            )
        need_str(label, "xbmcgui.ListItem", "label")
        need_str(label2, "xbmcgui.ListItem", "label2")
        need_str(path, "xbmcgui.ListItem", "path")
        need_bool(offscreen, "xbmcgui.ListItem", "offscreen")

        self.label = label
        self.label2 = label2
        self.path = path
        self.offscreen = bool(offscreen)

        self.art = {}
        self.properties = {}
        self.mimetype = ""
        self.contentlookup = None
        self.datetime = ""
        self.isfolder = False
        self.context_menu = []
        self.selected = False
        self.subtitles = []
        self.info_calls = []

        self._video_tag = None
        self._picture_tag = None
        self._music_tag = None
        self.video_tag_requested = False
        self.picture_tag_requested = False
        self.music_tag_requested = False

        STATE.list_items.append(self)

    # -- labels -------------------------------------------------------------

    def getLabel(self):
        return self.label

    def getLabel2(self):
        return self.label2

    def setLabel(self, label):
        need_str(label, "ListItem.setLabel", "label")
        self.label = label

    def setLabel2(self, label):
        need_str(label, "ListItem.setLabel2", "label")
        self.label2 = label

    # -- art ----------------------------------------------------------------

    def setArt(self, dictionary):
        dictionary = _properties(dictionary, "ListItem.setArt", "dictionary")
        # CGUIListItem::SetArt lowercases the key before storing it.
        for key, value in dictionary.items():
            self.art[key.lower()] = value

    def getArt(self, key):
        need_str(key, "ListItem.getArt", "key")
        return self.art.get(key.lower(), "")

    # -- properties ---------------------------------------------------------

    def setProperty(self, key, value):
        need_str(key, "ListItem.setProperty", "key")
        need_str(value, "ListItem.setProperty", "value")
        self.properties[key.lower()] = value

    def setProperties(self, dictionary):
        dictionary = _properties(dictionary, "ListItem.setProperties", "dictionary")
        for key, value in dictionary.items():
            self.properties[key.lower()] = value

    def getProperty(self, key):
        need_str(key, "ListItem.getProperty", "key")
        return self.properties.get(key.lower(), "")

    # -- path / playback ----------------------------------------------------

    def setPath(self, path):
        need_str(path, "ListItem.setPath", "path")
        self.path = path

    def getPath(self):
        return self.path

    def setMimeType(self, mimetype):
        need_str(mimetype, "ListItem.setMimeType", "mimetype")
        self.mimetype = mimetype

    def setContentLookup(self, enable):
        need_bool(enable, "ListItem.setContentLookup", "enable")
        self.contentlookup = bool(enable)

    def setSubtitles(self, subtitleFiles):
        need_str_list(subtitleFiles, "ListItem.setSubtitles", "subtitleFiles")
        self.subtitles = list(subtitleFiles)

    def setIsFolder(self, isFolder):
        need_bool(isFolder, "ListItem.setIsFolder", "isFolder")
        self.isfolder = bool(isFolder)

    def setDateTime(self, dateTime):
        """ListItem.cpp setDateTimeRaw never raises.

        A value exactly 10 characters long is parsed as DD-MM-YYYY, not the
        documented YYYY-MM-DD, so it silently yields a garbage date. Recorded
        rather than raised, because Kodi records it rather than raising.
        """
        need_str(dateTime, "ListItem.setDateTime", "dateTime")
        if len(dateTime) == 10:
            STATE.note(
                f"ListItem.setDateTime({dateTime!r}): a 10-character value is "
                "parsed as DD-MM-YYYY by CDateTime, producing a garbage date"
            )
        elif dateTime and not _W3C.match(dateTime):
            STATE.note(f"ListItem.setDateTime({dateTime!r}) is not W3C")
        self.datetime = dateTime

    def select(self, selected):
        need_bool(selected, "ListItem.select", "selected")
        self.selected = bool(selected)

    def isSelected(self):
        return self.selected

    # -- info ---------------------------------------------------------------

    def setInfo(self, type, infoLabels):  # noqa: A002
        need_str(type, "ListItem.setInfo", "type")
        if not isinstance(infoLabels, dict):
            raise TypeError("ListItem.setInfo: argument 'infoLabels' expects dict")
        if type.lower() not in ("video", "music", "pictures", "game"):
            STATE.note(f"ListItem.setInfo: unknown type {type!r} is a silent no-op")
        self.info_calls.append((type, dict(infoLabels)))

    def addContextMenuItems(self, items, replaceItems=False):
        if not isinstance(items, (list, tuple)):
            raise TypeError(
                'The parameter "items" must be either a Tuple or a List.'
            )
        need_bool(replaceItems, "ListItem.addContextMenuItems", "replaceItems")
        for entry in items:
            if not isinstance(entry, (tuple, list)):
                raise TypeError(
                    'The parameter "items" must be either a Tuple or a List.'
                )
            if len(entry) < 2:
                # ListItemException -> RuntimeError, not TypeError.
                raise ListItemException(
                    "Must pass in a list of tuples of pairs of strings. One "
                    f"entry in the list only has {len(entry)} elements."
                )
            need_str(entry[0], "ListItem.addContextMenuItems", "label")
            need_str(entry[1], "ListItem.addContextMenuItems", "action")
        if replaceItems:
            self.context_menu = [tuple(e[:2]) for e in items]
        else:
            self.context_menu.extend(tuple(e[:2]) for e in items)

    def getVideoInfoTag(self):
        self.video_tag_requested = True
        if self._video_tag is None:
            self._video_tag = InfoTagVideo(offscreen=self.offscreen)
        return self._video_tag

    def getPictureInfoTag(self):
        self.picture_tag_requested = True
        if self._picture_tag is None:
            self._picture_tag = InfoTagPicture(offscreen=self.offscreen)
        return self._picture_tag

    def getMusicInfoTag(self):
        self.music_tag_requested = True
        if self._music_tag is None:
            self._music_tag = InfoTagMusic(offscreen=self.offscreen)
        return self._music_tag

    def addStreamInfo(self, cType, dictionary):
        need_str(cType, "ListItem.addStreamInfo", "cType")


# --------------------------------------------------------------------------
# Dialog / Window
# --------------------------------------------------------------------------


class Dialog:
    def ok(self, heading, message):
        need_str(heading, "Dialog.ok", "heading")
        need_str(message, "Dialog.ok", "message")
        STATE.dialogs.append(("ok", heading, message))
        return True

    def yesno(self, heading, message, nolabel="", yeslabel="", autoclose=0,
              defaultbutton=DLG_YESNO_NO_BTN):
        need_str(heading, "Dialog.yesno", "heading")
        need_str(message, "Dialog.yesno", "message")
        STATE.dialogs.append(("yesno", heading, message))
        return False

    def notification(self, heading, message, icon="", time=0, sound=True):
        need_str(heading, "Dialog.notification", "heading")
        need_str(message, "Dialog.notification", "message")
        need_str(icon, "Dialog.notification", "icon")
        need_int(time, "Dialog.notification", "time")
        need_bool(sound, "Dialog.notification", "sound")
        STATE.notifications.append((heading, message, icon, time))

    def input(self, heading, defaultt="", type=INPUT_ALPHANUM, option=0,  # noqa: A002
              autoclose=0):
        need_str(heading, "Dialog.input", "heading")
        need_str(defaultt, "Dialog.input", "defaultt")
        need_int(type, "Dialog.input", "type")
        need_int(option, "Dialog.input", "option")
        need_int(autoclose, "Dialog.input", "autoclose")
        STATE.dialogs.append(("input", heading, type))
        if STATE.dialog_input_queue:
            return STATE.dialog_input_queue.pop(0)
        return ""

    def select(self, heading, list, autoclose=0, preselect=-1,  # noqa: A002
               useDetails=False):
        need_str(heading, "Dialog.select", "heading")
        STATE.dialogs.append(("select", heading))
        return -1

    def textviewer(self, heading, text, usemono=False):
        need_str(heading, "Dialog.textviewer", "heading")
        need_str(text, "Dialog.textviewer", "text")


class DialogProgress:
    def create(self, heading, message=""):
        pass

    def update(self, percent, message=""):
        pass

    def iscanceled(self):
        return False

    def close(self):
        pass


class Window:
    def __init__(self, existingWindowId=-1):
        need_int(existingWindowId, "xbmcgui.Window", "existingWindowId")
        self._id = existingWindowId

    def getProperty(self, key):
        need_str(key, "Window.getProperty", "key")
        return STATE.window_properties.get((self._id, key), "")

    def setProperty(self, key, value):
        need_str(key, "Window.setProperty", "key")
        need_str(value, "Window.setProperty", "value")
        STATE.window_properties[(self._id, key)] = value

    def clearProperty(self, key):
        need_str(key, "Window.clearProperty", "key")
        STATE.window_properties.pop((self._id, key), None)

    def clearProperties(self):
        for key in [k for k in STATE.window_properties if k[0] == self._id]:
            STATE.window_properties.pop(key, None)

    def getId(self):
        return self._id


class WindowXML(Window):
    pass
