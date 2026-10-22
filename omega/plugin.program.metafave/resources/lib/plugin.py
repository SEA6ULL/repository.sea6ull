# SPDX-License-Identifier: GPL-3.0-only
# MetaFave is authored by SEA6ULL / Claude / GPT and licensed under GPL-3.0.
"""Plugin routing and directory listings.

    plugin://plugin.program.metafave/                         top level
    plugin://plugin.program.metafave/?folder=<id>             a user folder (widget source)
    plugin://plugin.program.metafave/?folder=kodi_favorites   Kodi's favorites (read-only)

The top level holds folders only, plus the Create New Folder and Kodi Favorites
rows. A folder lists a page of its subfolders and items, with navigation rows when needed.
The same URL serves the add-on and widgets; all=1 explicitly requests the full folder. Management actions live in each entry's context
menu. Create New Folder makes a folder inside the folder being viewed; Content
Type is set on a folder's own entry (or the Kodi Favorites row).
"""

from urllib.parse import parse_qsl

import xbmc
import xbmcgui
import xbmcplugin

from . import kodifav, metadata, storage, targets, watchstate, paging
from .common import (ADDON_FANART, ICON_CREATE, ICON_FOLDER, ICON_KODI_FAVORITES,
                     ICON_NEXT, ICON_PREVIOUS, L, Settings, icon_art, log, notify, plain,
                     plugin_url)

_MEDIA_WINDOWS = (10025, 10502, 10001, 10002)


def run(argv):
    handle = int(argv[1]) if len(argv) > 1 and argv[1].lstrip('-').isdigit() else -1
    params = dict(parse_qsl(argv[2].lstrip('?'))) if len(argv) > 2 else {}
    action = params.get('action')

    if not action:
        folder_id = params.get('folder', storage.ROOT)
        if folder_id == kodifav.FOLDER_ID:
            list_kodi_favorites(handle, params.get('page', '1'), params.get('all') == '1')
        else:
            list_folder(handle, folder_id, params.get('page', '1'), params.get('all') == '1')
    elif action == 'launch':
        launch(handle, params.get('id'))
    elif action == 'run':
        _execute(handle, params.get('cmd', ''))
    else:
        from . import manage  # only imported when an action runs
        manage.dispatch(action, params)


# ------------------------------------------------------------- context menus

def _plugin_call(**params):
    return 'RunPlugin(%s)' % plugin_url(**params)


def _create_here(folder_id):
    return (L(30001), _plugin_call(action='new_folder', parent=folder_id))


def _content_type_for(folder_id):
    return (L(30083), _plugin_call(action='set_content', folder=folder_id))


def _use_as_folder_art(parent_id, parent, art, **target):
    """'Use as Artwork for <folder>' for an entry with artwork inside a user folder."""
    if parent_id == storage.ROOT or not parent or not any(art.values()):
        return []
    return [(L(30141) % plain(parent['name']),
             _plugin_call(action='use_as_folder_art', **target))]


# ------------------------------------------------------------------- listings

def _finish(handle, entries, content, label, fanart):
    log('Listed %s: %d entries' % (label, len(entries)), xbmc.LOGINFO)
    xbmcplugin.addDirectoryItems(handle, entries, len(entries))
    if fanart:
        xbmcplugin.setPluginFanart(handle, fanart)
    xbmcplugin.setContent(handle, content)
    xbmcplugin.addSortMethod(handle, xbmcplugin.SORT_METHOD_NONE)
    xbmcplugin.endOfDirectory(handle, succeeded=True, cacheToDisc=False)


def _fallback_fanart(settings):
    return ADDON_FANART if settings.fanart_fallback else ''


def list_folder(handle, folder_id, page=1, show_all=False):
    settings = Settings()
    fanart = _fallback_fanart(settings)
    data = storage.load()
    folder = data['folders'].get(folder_id)
    entries = []
    if folder is not None and (folder.get('art') or {}).get('fanart'):
        fanart = folder['art']['fanart']  # the folder's own fanart is the background inside it

    if folder is not None:
        at_top = folder_id == storage.ROOT
        if at_top:
            entries.append(_action_row(L(30001), ICON_CREATE,
                                       plugin_url(action='new_folder', parent=storage.ROOT), fanart))
            if settings.show_kodi_favorites:
                entries.append(_kodi_favorites_row(fanart))
        else:
            xbmcplugin.setPluginCategory(handle, folder['name'])
        current_window = xbmcgui.getCurrentWindowId()
        ordered = storage.entries(data, folder_id)
        visible, offset, current_page, pages = paging.slice_entries(
            ordered, page, paging.size(settings, folder, show_all))
        if current_page > 1:
            entries.append(_page_entry(folder_id, current_page - 1, pages, False, fanart))
        status = watchstate.ListingStatus(
            [data['items'][entry_id] for kind, entry_id in visible if kind == 'item'],
            settings.library_indicators)
        for position, (kind, entry_id) in enumerate(visible, offset):
            moves = _move_menu(kind, entry_id, folder_id, position, len(ordered))
            if kind == 'folder':
                entries.append(_folder_entry(data, folder_id, entry_id, moves, fanart))
                continue
            # Items saved at the top level by older versions are still shown so they can be moved out.
            item = data['items'][entry_id]

            def call(action, **extra):
                return _plugin_call(action=action, id=entry_id, **extra)
            menu = [
                _create_here(folder_id),
                (L(30003), call('edit_item')),
                (L(30004), call('edit_art')),
            ] + _use_as_folder_art(folder_id, folder, item['art'], id=entry_id)
            if item['target'].get('kind') == 'folder':
                menu.append((L(30300), call('import_list')))
            menu += [
                (L(30005), call('copy_item')),
                (L(30132), call('move_item')),
            ] + moves + [
                (L(30006), call('delete_item')),
            ]
            entries.append(_item_entry(item, plugin_url(action='launch', id=entry_id),
                                       menu, current_window, fanart, status))
        if current_page < pages:
            entries.append(_page_entry(folder_id, current_page + 1, pages, True, fanart))

    _finish(handle, entries, _content_type(data, folder), 'folder %s' % folder_id, fanart)


def list_kodi_favorites(handle, page=1, show_all=False):
    settings = Settings()
    fanart = _fallback_fanart(settings)
    xbmcplugin.setPluginCategory(handle, L(30002))
    current_window = xbmcgui.getCurrentWindowId()
    favorites = kodifav.load()
    visible, offset, current_page, pages = paging.slice_entries(
        favorites, page, paging.size(settings, show_all=show_all))
    status = watchstate.ListingStatus(visible, settings.library_indicators)
    entries = []
    if current_page > 1:
        entries.append(_page_entry(kodifav.FOLDER_ID, current_page - 1, pages, False, fanart))
    for index, item in enumerate(visible, offset):
        menu = [(L(30095), _plugin_call(action='kodi_source', index=index, name=item['label']))]
        command = targets.command(item['target'])
        entries.append(_item_entry(item, plugin_url(action='run', cmd=command), menu,
                                   current_window, fanart, status))
    if current_page < pages:
        entries.append(_page_entry(kodifav.FOLDER_ID, current_page + 1, pages, True, fanart))
    content = storage.load()['kodi_favorites'].get('content') or 'files'
    _finish(handle, entries, content, 'Kodi Favorites', fanart)


def _content_type(data, folder):
    if not folder:
        return 'files'
    if folder.get('content'):
        return folder['content']
    if not folder['items']:
        return 'files'
    mediatypes = {data['items'][iid]['info'].get('mediatype', '') for iid in folder['items']}
    if len(mediatypes) == 1:
        return metadata.CONTENT_FOR_MEDIATYPE.get(mediatypes.pop(), 'files')
    if mediatypes & set(metadata.VIDEO_TYPES):
        return 'videos'
    return 'files'


def _art(icon, fanart):
    art = icon_art(icon)
    if fanart:
        art['fanart'] = fanart
    return art


def _action_row(label, icon, url, fanart):
    listitem = xbmcgui.ListItem(label, offscreen=True)
    listitem.setArt(_art(icon, fanart))
    listitem.setProperty('SpecialSort', 'top')
    return url, listitem, False


def _page_entry(folder_id, page, pages, forward, fanart):
    label = L(30328 if forward else 30329) % (page, pages)
    listitem = xbmcgui.ListItem(label, offscreen=True)
    listitem.setArt(_art(ICON_NEXT if forward else ICON_PREVIOUS, fanart))
    listitem.setProperty('SpecialSort', 'bottom' if forward else 'top')
    return plugin_url(folder=folder_id, page=page), listitem, True


def _kodi_favorites_row(fanart):
    listitem = xbmcgui.ListItem(L(30002), offscreen=True)
    listitem.setArt(_art(ICON_KODI_FAVORITES, fanart))
    listitem.setProperty('SpecialSort', 'top')
    listitem.addContextMenuItems([_content_type_for(kodifav.FOLDER_ID)])
    return plugin_url(folder=kodifav.FOLDER_ID), listitem, True


def _move_menu(kind, entry_id, parent_id, position, count):
    """Move to Top / Up / Down / Bottom, leaving out moves that would do nothing."""
    target = {'folder': entry_id} if kind == 'folder' else {'id': entry_id}

    def call(to):
        return _plugin_call(action='move', kind=kind, parent=parent_id, to=to, **target)
    menu = []
    if position > 0:
        menu += [(L(30130), call('top')), (L(30010), call('up'))]
    if position < count - 1:
        menu += [(L(30011), call('down')), (L(30131), call('bottom'))]
    return menu


def _folder_entry(data, parent_id, folder_id, moves, fanart):
    folder = data['folders'][folder_id]
    listitem = xbmcgui.ListItem(folder['name'], offscreen=True)
    art = _art(ICON_FOLDER, fanart)
    custom = {key: value for key, value in (folder.get('art') or {}).items() if value}
    art.update(custom)
    # A user-selected thumbnail remains the poster fallback. Otherwise the
    # new built-in poster would hide a cover that worked in earlier versions.
    if not custom.get('poster') and (custom.get('thumb') or custom.get('icon')):
        art['poster'] = custom.get('thumb') or custom['icon']
    listitem.setArt(art)  # covers chosen with Edit Artwork replace the defaults

    def call(action, **extra):
        return _plugin_call(action=action, folder=folder_id, **extra)
    listitem.addContextMenuItems([
        _create_here(parent_id),
        # Inside this folder - the way to start a subfolder in an empty folder.
        (L(30133), _plugin_call(action='new_folder', parent=folder_id)),
        (L(30008), call('rename_folder')),
        (L(30004), call('edit_folder_art')),
    ] + _use_as_folder_art(parent_id, data['folders'].get(parent_id), custom,
                           kind='folder', folder=folder_id) + [
        (L(30005), call('copy_folder')),
        (L(30132), call('move_folder')),
        _content_type_for(folder_id),
        (L(30323), call('set_pagination')),
        (L(30325), 'Container.Update(%s)' % plugin_url(folder=folder_id, all=1)),
    ] + moves + [
        (L(30007), call('delete_folder')),
    ])
    return plugin_url(folder=folder_id), listitem, True


def build_listitem(item, status=None):
    """ListItem carrying the item's saved presentation (no path)."""
    listitem = xbmcgui.ListItem(item['label'], item.get('label2', ''), offscreen=True)
    if item['art']:
        listitem.setArt(item['art'])
    metadata.apply(listitem, item['info'])
    for key, value in item['properties'].items():
        listitem.setProperty(key, str(value))
    if status is not None:
        status.apply(listitem, item)
    return listitem


def _item_entry(item, launch_url, menu, current_window, fanart, status=None):
    """A directory entry for a saved item or a Kodi favorite.

    Folders and playable items keep their native paths; everything else, and folders
    belonging to a different media window, goes through launch_url.

    A widget opens a folder in the widget's own target window (Programs for a widget made
    from MetaFave under Program add-ons), which is wrong for a library folder such as a
    music artist: the Programs window lists it without Kodi's music artwork. Kodi checks a
    per-item 'node.target' before the widget's target (CDirectoryProvider::GetTarget), so
    each folder carries the window it was saved from.
    """
    target = item['target']
    kind, path = target.get('kind'), target.get('path', '')
    listitem = build_listitem(item, status)
    if fanart and not item['art'].get('fanart'):
        listitem.setArt({'fanart': fanart})  # instead of the skin's unrelated fallback
    is_folder = False

    if kind == 'folder' and path:
        wanted = targets.window_id(target.get('window'))
        if current_window in _MEDIA_WINDOWS and wanted != current_window:
            url = launch_url  # open it in its own window
        else:
            url, is_folder = path, True
            listitem.setProperty('node.target',
                                 str(target.get('window') or targets.guess_window(path)))
    elif kind == 'item' and path:
        url = path
        if target.get('playable'):
            listitem.setProperty('IsPlayable', 'true')
    else:
        url = launch_url
    listitem.setPath(url)
    listitem.addContextMenuItems(menu)
    return url, listitem, is_folder


# --------------------------------------------------------------------- launch

def _execute(handle, command):
    """Run a shortcut's Kodi command.

    From a widget Kodi starts non-folder items with PlayMedia, so it hands over a handle and
    waits for something to play. Tell it there is nothing to play first, then run the
    command. Clicks inside MetaFave arrive without a handle (-1) and skip this.
    """
    if handle >= 0:
        xbmcplugin.setResolvedUrl(handle, False, xbmcgui.ListItem(offscreen=True))
    if command:
        xbmc.executebuiltin(command)


def launch(handle, item_id):
    item = storage.load()['items'].get(item_id or '')
    if not item:
        if handle >= 0:
            xbmcplugin.setResolvedUrl(handle, False, xbmcgui.ListItem(offscreen=True))
        notify(L(30074))
        return
    _execute(handle, targets.command(item['target']))
