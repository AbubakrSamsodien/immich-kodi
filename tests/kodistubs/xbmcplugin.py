"""Stub of the Kodi 21 (Omega) `xbmcplugin` module."""

from __future__ import annotations

from kodi_state import STATE, need_bool, need_int, need_str
from xbmcgui import ListItem

# xbmc/SortFileItem.h SORT_METHOD enum, exported by the SWIG_CONSTANT block in
# interfaces/legacy/ModuleXbmcplugin.h (Omega). Values verified against source.
SORT_METHOD_NONE = 0
SORT_METHOD_LABEL = 1
SORT_METHOD_LABEL_IGNORE_THE = 2
SORT_METHOD_DATE = 3
SORT_METHOD_SIZE = 4
SORT_METHOD_FILE = 5
SORT_METHOD_DRIVE_TYPE = 6
SORT_METHOD_TRACKNUM = 7
SORT_METHOD_DURATION = 8
SORT_METHOD_TITLE = 9
SORT_METHOD_TITLE_IGNORE_THE = 10
SORT_METHOD_ARTIST = 11
SORT_METHOD_ARTIST_IGNORE_THE = 13
SORT_METHOD_ALBUM = 14
SORT_METHOD_ALBUM_IGNORE_THE = 15
SORT_METHOD_GENRE = 16
SORT_METHOD_COUNTRY = 17
SORT_METHOD_VIDEO_YEAR = 18
SORT_METHOD_VIDEO_RATING = 19
SORT_METHOD_VIDEO_USER_RATING = 20
SORT_METHOD_DATEADDED = 21
SORT_METHOD_PROGRAM_COUNT = 22
SORT_METHOD_PLAYLIST_ORDER = 23
SORT_METHOD_EPISODE = 24
SORT_METHOD_VIDEO_TITLE = 25
SORT_METHOD_VIDEO_SORT_TITLE = 26
SORT_METHOD_VIDEO_SORT_TITLE_IGNORE_THE = 27
SORT_METHOD_PRODUCTIONCODE = 28
SORT_METHOD_SONG_RATING = 29
SORT_METHOD_SONG_USER_RATING = 30
SORT_METHOD_MPAA_RATING = 31
SORT_METHOD_VIDEO_RUNTIME = 32
SORT_METHOD_STUDIO = 33
SORT_METHOD_STUDIO_IGNORE_THE = 34
SORT_METHOD_FULLPATH = 35
SORT_METHOD_LABEL_IGNORE_FOLDERS = 36
SORT_METHOD_LASTPLAYED = 37
SORT_METHOD_PLAYCOUNT = 38
SORT_METHOD_LISTENERS = 39
SORT_METHOD_UNSORTED = 40
SORT_METHOD_CHANNEL = 41
SORT_METHOD_BITRATE = 43
SORT_METHOD_DATE_TAKEN = 44
SORT_METHOD_VIDEO_ORIGINAL_TITLE = 49
SORT_METHOD_VIDEO_ORIGINAL_TITLE_IGNORE_THE = 50
SORT_METHOD_MAX = 53

# xbmcplugin.setContent doxygen (Omega) valid values.
VALID_CONTENT = (
    "",
    "files",
    "songs",
    "artists",
    "albums",
    "movies",
    "tvshows",
    "episodes",
    "musicvideos",
    "videos",
    "images",
    "games",
)

_SORT_VALUES = {
    value for name, value in list(globals().items())
    if name.startswith("SORT_METHOD_") and isinstance(value, int)
}


def _check_handle(handle, where):
    need_int(handle, where, "handle")
    return handle


def addDirectoryItem(handle, url, listitem, isFolder=False, totalItems=0):
    _check_handle(handle, "xbmcplugin.addDirectoryItem")
    need_str(url, "xbmcplugin.addDirectoryItem", "url")
    if listitem is None:
        # ModuleXbmcplugin.cpp does `throw new WrongTypeException(...)`, which
        # the catch(const&) clause misses, so it surfaces as RuntimeError.
        raise RuntimeError(
            'Unknown exception thrown from the call "addDirectoryItem"'
        )
    if not isinstance(listitem, ListItem):
        raise TypeError(
            "xbmcplugin.addDirectoryItem: argument 'listitem' expects "
            f"xbmcgui.ListItem, got {type(listitem).__name__}"
        )
    need_bool(isFolder, "xbmcplugin.addDirectoryItem", "isFolder")
    need_int(totalItems, "xbmcplugin.addDirectoryItem", "totalItems")
    STATE.directory_items.append((handle, [(url, listitem, bool(isFolder))], totalItems))
    return True


def addDirectoryItems(handle, items, totalItems=0):
    _check_handle(handle, "xbmcplugin.addDirectoryItems")
    if not isinstance(items, (list, tuple)):
        # swig vector typemap: WrongTypeException -> TypeError.
        raise TypeError(
            'The parameter "items" must be either a Tuple or a List, got '
            f"{type(items).__name__}"
        )
    need_int(totalItems, "xbmcplugin.addDirectoryItems", "totalItems")
    normalised = []
    for entry in items:
        if not isinstance(entry, (tuple, list)):
            raise TypeError(
                'The parameter "items" must be either a Tuple or a List, got '
                f"{type(entry).__name__}"
            )
        if len(entry) not in (2, 3):
            raise RuntimeError(
                "Must pass in a list of tuples of pairs of strings. One entry "
                f"in the list only has {len(entry)} elements."
            )
        url = need_str(entry[0], "xbmcplugin.addDirectoryItems", "url")
        listitem = entry[1]
        if not isinstance(listitem, ListItem):
            raise TypeError(
                "xbmcplugin.addDirectoryItems: element 1 must be an "
                f"xbmcgui.ListItem, got {type(listitem).__name__}"
            )
        isfolder = bool(entry[2]) if len(entry) == 3 else False
        if len(entry) == 3:
            need_bool(entry[2], "xbmcplugin.addDirectoryItems", "isFolder")
        normalised.append((url, listitem, isfolder))
    STATE.directory_items.append((handle, normalised, totalItems))
    return True


def endOfDirectory(handle, succeeded=True, updateListing=False, cacheToDisc=True):
    _check_handle(handle, "xbmcplugin.endOfDirectory")
    need_bool(succeeded, "xbmcplugin.endOfDirectory", "succeeded")
    need_bool(updateListing, "xbmcplugin.endOfDirectory", "updateListing")
    need_bool(cacheToDisc, "xbmcplugin.endOfDirectory", "cacheToDisc")
    STATE.end_of_directory.append(
        (handle, bool(succeeded), bool(updateListing), bool(cacheToDisc))
    )


def setResolvedUrl(handle, succeeded, listitem):
    _check_handle(handle, "xbmcplugin.setResolvedUrl")
    need_bool(succeeded, "xbmcplugin.setResolvedUrl", "succeeded")
    if not isinstance(listitem, ListItem):
        raise TypeError("xbmcplugin.setResolvedUrl: 'listitem' expects xbmcgui.ListItem")
    STATE.resolved_urls.append((handle, bool(succeeded), listitem))


def addSortMethod(handle, sortMethod, labelMask="", label2Mask=""):
    """Note the four-parameter signature: labelMask precedes label2Mask."""
    _check_handle(handle, "xbmcplugin.addSortMethod")
    need_int(sortMethod, "xbmcplugin.addSortMethod", "sortMethod")
    need_str(labelMask, "xbmcplugin.addSortMethod", "labelMask")
    need_str(label2Mask, "xbmcplugin.addSortMethod", "label2Mask")
    # ModuleXbmcplugin.cpp guards with `>= SORT_METHOD_NONE && < SORT_METHOD_MAX`
    # and silently drops anything else.
    if SORT_METHOD_NONE <= sortMethod < SORT_METHOD_MAX:
        STATE.sort_methods.append((handle, sortMethod))
    else:
        STATE.note(f"xbmcplugin.addSortMethod: {sortMethod} is out of range and was dropped")


def setContent(handle, content):
    """No allow-list in Kodi: PluginDirectory::SetContent stores any string."""
    _check_handle(handle, "xbmcplugin.setContent")
    need_str(content, "xbmcplugin.setContent", "content")
    STATE.content.append((handle, content))


def setPluginCategory(handle, category):
    _check_handle(handle, "xbmcplugin.setPluginCategory")
    need_str(category, "xbmcplugin.setPluginCategory", "category")
    STATE.categories.append((handle, category))


def setPluginFanart(handle, image=None, color1=None, color2=None, color3=None):
    _check_handle(handle, "xbmcplugin.setPluginFanart")
    if image is not None:
        need_str(image, "xbmcplugin.setPluginFanart", "image")
    STATE.plugin_fanart.append((handle, image))


def setProperty(handle, key, value):
    _check_handle(handle, "xbmcplugin.setProperty")
    need_str(key, "xbmcplugin.setProperty", "key")
    need_str(value, "xbmcplugin.setProperty", "value")


def getSetting(handle, id):  # noqa: A002
    _check_handle(handle, "xbmcplugin.getSetting")
    need_str(id, "xbmcplugin.getSetting", "id")
    return ""


def setSetting(handle, id, value):  # noqa: A002
    _check_handle(handle, "xbmcplugin.setSetting")
    need_str(id, "xbmcplugin.setSetting", "id")
    need_str(value, "xbmcplugin.setSetting", "value")
