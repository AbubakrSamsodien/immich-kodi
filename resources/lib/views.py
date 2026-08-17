"""Every listing the addon can produce.

Views are registered by `@route` and receive the per-invocation `Request`. They
either emit a directory through `request.add_items` or, for the RunPlugin
actions, perform a side effect and return without touching the handle.
"""

from __future__ import annotations

from datetime import date

import xbmc
import xbmcgui
import xbmcplugin

from api import ImmichError, parse_datetime
from kodiutils import localise, log, notify, open_settings
from listing import (
    asset_label,
    folder_item,
    format_month,
    menu_item,
    photo_item,
    video_item,
)
from router import route

# Registered before the items, first entry wins as the listing default.
PHOTO_SORTS = (
    xbmcplugin.SORT_METHOD_NONE,
    xbmcplugin.SORT_METHOD_DATE_TAKEN,
    xbmcplugin.SORT_METHOD_LABEL,
)
FOLDER_SORTS = (
    xbmcplugin.SORT_METHOD_NONE,
    xbmcplugin.SORT_METHOD_LABEL,
    xbmcplugin.SORT_METHOD_DATE,
)


# ---------------------------------------------------------------- root menu


def _search_page(request, **filters) -> tuple:
    """Fetch just the page being rendered, not the whole result set."""
    size = request.settings.page_size
    page = request.int_param("page", 0) + 1
    return request.client.search_metadata_page(page, size, **filters)


def _count(value) -> int:
    """Coerce a server-supplied count for `%d` formatting.

    A key present with a null value survives `.get(key, 0)`, and `"%d" % None`
    raises, which would take down the entire listing over one cosmetic label.
    """
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _slideshow_menu(request, target_url: str) -> list:
    """Context menu entry that plays a listing as a slideshow."""
    return [
        (
            localise(30049),
            f"RunPlugin({request.url(action='slideshow', target=target_url)})",
        )
    ]


@route("")
def root(request):
    features = request.client.features(request.cache)

    # (label, icon, help, params, offer a slideshow). Slideshow is only offered
    # where the target resolves to media without prompting: running it on the
    # search entry would pop an input dialog inside the slideshow window.
    entries = [
        (30092, "photos", 30093, {"action": "all"}, True),
        (30002, "timeline", 30071, {"action": "timeline"}, True),
        (30015, "videos", 30073, {"action": "videos"}, True),
        (30003, "albums", 30074, {"action": "albums"}, True),
        (30052, "favourites", 30075, {"action": "favourites"}, True),
    ]
    if features.get("facialRecognition", True):
        entries.append((30053, "people", 30076, {"action": "people"}, True))
    entries.extend(
        [
            (30054, "places", 30077, {"action": "places"}, True),
            (30055, "tags", 30078, {"action": "tags"}, True),
            (30056, "memories", 30079, {"action": "memories"}, True),
            (30057, "search", 30080, {"action": "search"}, False),
            (30058, "random", 30081, {"action": "random"}, True),
        ]
    )

    items = []
    for label_id, icon, help_id, params, sliddable in entries:
        url = request.url(**params)
        item = menu_item(localise(label_id), icon, localise(help_id))
        if sliddable:
            item.addContextMenuItems(_slideshow_menu(request, url))
        items.append((url, item, True))

    items.append(
        (
            request.url(action="settings"),
            menu_item(localise(30059), "settings", localise(30082), is_folder=False),
            False,
        )
    )

    request.add_items(items, content="files")


@route("all")
def all_media(request):
    """Everything in one chronological listing, newest first.

    Kodi builds a slideshow from the current directory only
    (CGUIWindowPictures::ShowPicture iterates m_vecItems), so next and previous
    can never cross a folder boundary. Month folders therefore always stop at
    the end of the month. This view has no folders, so a page of it flows
    straight across month and year boundaries.
    """
    assets, more = _search_page(request, order="desc")
    _emit_assets(request, assets, category=localise(30092),
                 prefetched=True, has_more=more)


@route("videos")
def videos(request):
    """Every video, newest first.

    Deliberately not a filtered timeline. The timeline endpoints take no asset
    type filter (reference section 2), so month folders built from them list
    every photo-only month, each opening empty. Search does take
    `type=VIDEO`, so this is one accurate listing instead of a misleading tree.
    """
    assets, more = _search_page(request, type="VIDEO")
    _emit_assets(request, assets, category=localise(30015),
                 prefetched=True, has_more=more)


# ------------------------------------------------------------------ timeline


def _timeline_filters(request) -> dict:
    """Filters shared by the month list and the assets inside a month."""
    return {
        "personId": request.param("personId"),
        "tagId": request.param("tagId"),
        "albumId": request.param("albumId"),
        "isFavorite": request.param("favorite") == "1",
        "withPartners": request.settings.include_partners,
        "visibility": "timeline",
    }


def _passthrough(request) -> dict:
    """The subset of params a child listing must inherit."""
    keep = {}
    for name in ("video", "personId", "tagId", "albumId", "favorite"):
        value = request.param(name)
        if value:
            keep[name] = value
    return keep


def _month_bounds(when):
    """First and last instant of the month `when` falls in."""
    start = when.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    if start.month == 12:
        end = start.replace(year=start.year + 1, month=1)
    else:
        end = start.replace(month=start.month + 1)
    return start, end


def _month_preview(request, when):
    """Thumbnail of one asset from a month, or None.

    Costs one small request per month, so it is opt-in: the buckets endpoint
    returns only {timeBucket, count} and there is no per-bucket cover image, and
    a ten-year library would otherwise turn a two-request menu into 120.
    """
    start, end = _month_bounds(when)
    try:
        assets, _more = request.client.search_metadata_page(
            1, 1,
            takenAfter=start.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
            takenBefore=end.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
            order="desc",
            withExif=False,
        )
    except ImmichError as error:
        log(f"month preview failed for {when:%Y-%m}: {error}")
        return None
    return request.client.thumbnail_url(assets[0].id) if assets else None


@route("timeline")
def timeline(request):
    # DEPRECATED, advisory, no removal date: `?action=timeline&video=1` was the
    # Videos entry in 1.0.0 and 2.0.0, so it is saved in users' Kodi favourites
    # and cannot simply stop working. It is adapted onto the videos route rather
    # than merely tolerated, because the old implementation listed every
    # photo-only month with a full asset count and each one opened empty.
    # Removable once no favourite can plausibly still point here.
    if request.param("video") == "1":
        return videos(request)

    buckets = request.client.timeline_buckets(**_timeline_filters(request))
    inherited = _passthrough(request)
    previews = request.settings.month_previews

    items = []
    for bucket in buckets:
        when = parse_datetime(bucket.get("timeBucket"))
        if when is None:
            continue
        url = request.url(action="bucket", id=bucket["timeBucket"], **inherited)
        label = format_month(when)
        item = folder_item(
            label,
            thumb=_month_preview(request, when) if previews else None,
            icon_name="timeline",
            date=when,
            label2=localise(30085) % _count(bucket.get("count")),
        )
        item.addContextMenuItems(_slideshow_menu(request, url))
        items.append((url, item, True))

    category = request.param("title") or localise(30002)
    request.add_items(items, content="files", category=category, sort=(xbmcplugin.SORT_METHOD_NONE,))


@route("bucket")
def bucket(request):
    assets = request.client.timeline_bucket(
        request.param("id"), **_timeline_filters(request)
    )
    when = parse_datetime(request.param("id"))
    # The preference is "show videos in the timeline", so it applies to the
    # plain timeline only. Favourites, a person and a tag all reach this route
    # too, and silently dropping videos from a listing the user asked for by
    # name is not what the setting says.
    plain_timeline = not any(
        request.param(name)
        for name in ("personId", "tagId", "albumId", "favorite")
    )
    _emit_assets(
        request,
        assets,
        category=format_month(when) if when else request.param("id", ""),
        timeline_filters=plain_timeline,
    )


# -------------------------------------------------------------------- albums


@route("albums")
def albums(request):
    entries = request.client.albums(shared_only=request.settings.shared_only)

    items = []
    for album in entries:
        if not album.get("id"):
            continue
        url = request.url(action="album", id=album["id"],
                          title=album.get("albumName", ""),
                          order=album.get("order") or "")
        thumbnail = album.get("albumThumbnailAssetId")
        item = folder_item(
            album.get("albumName") or localise(30003),
            thumb=request.client.thumbnail_url(thumbnail) if thumbnail else None,
            icon_name="albums",
            date=parse_datetime(album.get("startDate")),
            label2=localise(30085) % _count(album.get("assetCount")),
        )
        item.addContextMenuItems(_slideshow_menu(request, url))
        items.append((url, item, True))

    request.add_items(items, content="files", category=localise(30003), sort=FOLDER_SORTS)


@route("album")
def album(request):
    assets, more = request.client.album_assets_page(
        request.param("id"),
        request.int_param("page", 0) + 1,
        request.settings.page_size,
        order=request.param("order") or None,
    )
    _emit_assets(request, assets, category=request.param("title", ""),
                 prefetched=True, has_more=more)


# ---------------------------------------------------------------- favourites


@route("favourites")
def favourites(request):
    request.params["favorite"] = "1"
    request.params["title"] = localise(30052)
    timeline(request)


# -------------------------------------------------------------------- people


@route("people")
def people(request):
    data = request.client.people()
    items = []
    for person in data.get("people") or []:
        if person.get("isHidden"):
            continue
        name = person.get("name") or ""
        if not name:
            continue
        url = request.url(action="timeline", personId=person["id"], title=name)
        item = folder_item(
            name,
            thumb=request.client.person_thumbnail_url(person["id"]),
            icon_name="people",
        )
        items.append((url, item, True))

    request.add_items(items, content="files", category=localise(30053), sort=FOLDER_SORTS)


# -------------------------------------------------------------------- places


@route("places")
def places(request):
    representatives = request.client.cities()
    items = []
    seen = set()
    for asset in representatives:
        city = asset.city
        if not city or city in seen:
            continue
        seen.add(city)
        url = request.url(action="place", city=city, title=city)
        item = folder_item(
            city,
            thumb=request.client.thumbnail_url(asset.id),
            icon_name="places",
            label2=asset.country or "",
        )
        items.append((url, item, True))

    request.add_items(items, content="files", category=localise(30054), sort=FOLDER_SORTS)


@route("place")
def place(request):
    assets, more = _search_page(request, city=request.param("city"))
    _emit_assets(request, assets, category=request.param("title", ""),
                 prefetched=True, has_more=more)


# ---------------------------------------------------------------------- tags


@route("tags")
def tags(request):
    items = []
    for tag in request.client.tags():
        label = tag.get("value") or tag.get("name") or ""
        if not label:
            continue
        url = request.url(action="timeline", tagId=tag["id"], title=label)
        items.append((url, folder_item(label, icon_name="tags"), True))

    request.add_items(items, content="files", category=localise(30055), sort=FOLDER_SORTS)


# ------------------------------------------------------------------ memories


@route("memories")
def memories(request):
    today = date.today()
    entries = request.client.memories(for_date=today.strftime("%Y-%m-%d"))

    items = []
    for memory in entries:
        year = (memory.get("data") or {}).get("year")
        assets = memory.get("assets") or []
        if not assets:
            continue
        label = str(year) if year else localise(30069)
        url = request.url(action="memory", id=memory["id"])
        item = folder_item(
            label,
            thumb=request.client.thumbnail_url(assets[0]["id"]),
            icon_name="memories",
            label2=localise(30085) % len(assets),
        )
        item.addContextMenuItems(_slideshow_menu(request, url))
        items.append((url, item, True))

    if not items:
        notify(localise(30056), localise(30066))
    request.add_items(items, content="files", category=localise(30056), sort=FOLDER_SORTS)


@route("memory")
def memory(request):
    entry = request.client.memory(request.param("id"))
    _emit_assets(
        request,
        request.client.memory_assets(entry),
        category=str((entry.get("data") or {}).get("year") or localise(30056)),
    )


# -------------------------------------------------------------------- search


@route("search")
def search(request):
    features = request.client.features(request.cache)
    items = [
        (
            request.url(action="search_text"),
            menu_item(localise(30060), "search", localise(30080)),
            True,
        )
    ]
    if features.get("smartSearch", True):
        items.append(
            (
                request.url(action="search_smart"),
                menu_item(localise(30061), "memories", localise(30062)),
                True,
            )
        )
    request.add_items(items, content="files", category=localise(30057))


def _ask(heading_id: int) -> str:
    return xbmcgui.Dialog().input(localise(heading_id), type=xbmcgui.INPUT_ALPHANUM).strip()


@route("search_text")
def search_text(request):
    query = request.param("q") or _ask(30060)
    if not query:
        request.fail()
        return
    # Recorded on the request so the next-page URL carries it. Without this,
    # page two re-opens the keyboard instead of paging the same search.
    request.params["q"] = query
    # Paging re-invokes this view, so a second fallback query would double the
    # cost of every page. One field, chosen because it is the one users type.
    assets, more = _search_page(request, originalFileName=query)
    _emit_assets(request, assets, category=query, prefetched=True, has_more=more)


@route("search_smart")
def search_smart(request):
    query = request.param("q") or _ask(30061)
    if not query:
        request.fail()
        return
    request.params["q"] = query
    assets = request.client.search_smart(query)
    _emit_assets(request, assets, category=query)


@route("random")
def random_assets(request):
    assets = request.client.search_random(size=min(500, request.settings.page_size))
    _emit_assets(request, assets, category=localise(30058), paged=False)


# ------------------------------------------------------------------- actions


@route("settings")
def settings(request):
    open_settings()
    request.fail()


@route("slideshow")
def slideshow(request):
    """Hand a plugin path to Kodi's own slideshow window.

    The builtin re-enumerates the target through the plugin, so the listing is
    produced exactly as browsing would produce it.
    """
    target = request.param("target")
    # Plugin URLs are reachable from favourites, keymaps, .strm files and other
    # addons, so the target is confirmed to be one of ours before it is handed
    # to a builtin. Without this the action launches a slideshow over any path
    # the caller names, under this addon's identity.
    if not target or not target.startswith(request.base_url):
        log(f"refusing slideshow for a foreign target: {target!r}")
        return
    # SplitParams treats commas as argument separators, so the path is quoted.
    escaped = target.replace("\\", "\\\\").replace('"', '\\"')
    # Argument order follows the builtin's documented signature:
    # SlideShow(dir[,random|notrandom][,recursive][,pause][,beginslide=...])
    xbmc.executebuiltin(f'SlideShow("{escaped}",notrandom,recursive)')


@route("test_connection")
def test_connection(request):
    try:
        user = request.client.me()
        version = request.client.detect_version()
    except Exception as error:  # noqa: BLE001 - reported to the user verbatim
        log(f"connection test failed: {error!r}")
        xbmcgui.Dialog().ok(localise(30007), str(error))
        return
    name = user.get("name") or user.get("email") or ""
    lines = [localise(30068) % (name, version)]

    # Naming the missing scope beats Kodi's bare "playback failed".
    can_download = request.client.can_download_originals()
    if request.settings.prefer_original_video and can_download is False:
        lines.append("")
        lines.append(localise(30096))
    elif can_download is False:
        lines.append("")
        lines.append(localise(30097))

    xbmcgui.Dialog().ok(localise(30067), "\n".join(lines))


# ------------------------------------------------------------------- helpers


def _emit_assets(
    request,
    assets,
    category: str = "",
    paged: bool = True,
    timeline_filters: bool = False,
    prefetched: bool = False,
    has_more: bool = False,
):
    """Render a list of assets, splitting large sets into pages.

    A month with several thousand photos would otherwise cross the Python to
    C++ boundary as one enormous list and stall the UI while Kodi builds it.

    `timeline_filters` gates the videos-in-timeline preference, which must not
    silently hide videos from an album or a search result the user asked for by
    name.
    """
    video_only = request.param("video") == "1"
    if video_only:
        assets = [asset for asset in assets if asset.is_video]
    elif timeline_filters and not request.settings.show_videos_in_timeline:
        assets = [asset for asset in assets if not asset.is_video]

    page = request.int_param("page", 0)
    size = request.settings.page_size
    total = len(assets)
    if prefetched:
        # The server already returned exactly this page.
        window = assets
    else:
        window = assets[page * size : (page + 1) * size] if paged and total > size else assets

    quality = request.settings.image_quality
    name_mode = request.settings.asset_name
    original_video = request.settings.prefer_original_video
    client = request.client

    items = []
    for asset in window:
        thumb = client.thumbnail_url(asset.id)
        # Kodi only fetches fanart for the focused item, so a full preview is
        # affordable here and looks far better than a stretched 250px thumb.
        backdrop = client.image_url(asset.id, "preview")
        label = asset_label(asset, name_mode)
        if asset.is_video:
            url = client.video_url(asset.id, prefer_original=original_video)
            item = video_item(asset, label, url, thumb, fanart=backdrop)
        else:
            url = client.image_url(asset.id, quality)
            item = photo_item(asset, label, url, thumb, fanart=backdrop)
        items.append((url, item, False))

    more = has_more if prefetched else (paged and (page + 1) * size < total)
    if more:
        remaining = 0 if prefetched else total - (page + 1) * size
        items.append(
            (
                request.url(**dict(request.params, page=page + 1)),
                folder_item(
                    localise(30064),
                    icon_name="next",
                    label2="" if prefetched else localise(30085) % remaining,
                ),
                True,
            )
        )

    # Content is derived from what the listing actually holds, not from the
    # legacy `video=1` param. Kodi picks the view modes, the sort options and
    # the info dialog from this, so the dedicated videos route reporting
    # "images" presented a videos-only listing as pictures.
    all_video = bool(window) and all(asset.is_video for asset in window)
    content = "videos" if (video_only or all_video) else "images"
    request.add_items(items, content=content, category=category, sort=PHOTO_SORTS)
