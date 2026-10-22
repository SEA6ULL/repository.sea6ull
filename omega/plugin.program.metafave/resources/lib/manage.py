# SPDX-License-Identifier: GPL-3.0-only
# MetaFave is authored by SEA6ULL / Claude / GPT and licensed under GPL-3.0.
"""Management actions run from rows and context menus inside MetaFave.

Each action reloads the collection, changes it, saves once and refreshes the
MetaFave view it was started from. Widgets are left to the skin.
"""

import xbmc
import xbmcgui

from . import editor, kodifav, metadata, storage
from .common import L, Settings, confirm, notify, plain, refresh_container
from .dialogs import folder_label, resolve_destination, text


def dispatch(action, params):
    handler = _ACTIONS.get(action)
    if handler and handler(params):
        refresh_container()


def _folder(data, params, key='folder'):
    folder_id = params.get(key, '')
    if folder_id not in data['folders']:
        notify(L(30071))
        return None
    return folder_id


def _item(data, params):
    item_id = params.get('id', '')
    if item_id not in data['items']:
        notify(L(30074))
        return None
    return item_id


# ------------------------------------------------------------------- folders

def new_folder(params):
    data = storage.load_for_edit()
    parent_id = _folder(data, params, 'parent')
    if not parent_id:
        return False
    name = text(L(30014), L(30032))
    if not name:
        return False
    storage.create_folder(data, parent_id, name, Settings().default_content)
    storage.save(data)
    return True


def rename_folder(params):
    data = storage.load_for_edit()
    folder_id = _folder(data, params)
    if not folder_id or folder_id == storage.ROOT:
        return False
    name = text(L(30008), data['folders'][folder_id]['name'])
    if not name or name == data['folders'][folder_id]['name']:
        return False
    storage.rename_folder(data, folder_id, name)
    storage.save(data)
    return True


def copy_folder(params):
    """Copy to Folder for a folder: its whole contents go into a new or existing folder."""
    data = storage.load_for_edit()
    folder_id = _folder(data, params)
    if not folder_id or folder_id == storage.ROOT:
        return False
    name = data['folders'][folder_id]['name']
    existing = set(data['folders'])
    dest, _ = resolve_destination(data, L(30005), L(30029) % name,
                                  exclude=storage.subtree_ids(data, folder_id))
    if not dest:
        return False
    if dest not in existing:  # a folder made for this copy takes the original's covers
        data['folders'][dest]['art'] = dict(data['folders'][folder_id].get('art') or {})
    storage.copy_folder_contents(data, folder_id, dest)
    storage.save(data)
    confirm(L(30030) % folder_label(data, dest))
    return True


def edit_folder_art(params):
    """Edit Artwork for a folder: covers chosen with Browse, a path or URL."""
    data = storage.load_for_edit()
    folder_id = _folder(data, params)
    if not folder_id or folder_id == storage.ROOT:
        return False
    folder = data['folders'][folder_id]
    art = editor.edit_folder_artwork(folder['name'], folder.get('art') or {},
                                     storage.contents_art(data, folder_id))
    if art is None:
        return False
    data = storage.load_for_edit()  # keep changes made elsewhere while the editor was open
    if folder_id not in data['folders']:
        notify(L(30071))
        return False
    data['folders'][folder_id]['art'] = art
    storage.save(data)
    confirm(L(30072))
    return True


def use_as_folder_art(params):
    """Use as Artwork for <folder>: covers for the folder being viewed, from one of its entries.

    The entry is an item (id=...) or a subfolder (kind=folder, folder=...). Its image URLs
    are copied into the folder's covers, so they stay even if the entry is later removed.
    """
    data = storage.load_for_edit()
    if params.get('kind') == 'folder':
        source_id = _folder(data, params)
        if not source_id or source_id == storage.ROOT:
            return False
        source_art = data['folders'][source_id].get('art') or {}
        parent_id = data['folders'][source_id]['parent']
    else:
        item_id = _item(data, params)
        if not item_id:
            return False
        source_art = data['items'][item_id]['art']
        parent_id = storage.find_item_folder(data, item_id)
    if parent_id == storage.ROOT:
        return False  # the top level has no covers of its own
    if parent_id not in data['folders']:
        notify(L(30071))
        return False
    folder = data['folders'][parent_id]
    changes = editor.choose_folder_art_from(folder['name'], folder.get('art') or {}, source_art)
    if not changes:
        return False
    data = storage.load_for_edit()  # keep changes made elsewhere while the dialog was open
    if parent_id not in data['folders']:
        notify(L(30071))
        return False
    data['folders'][parent_id]['art'].update(changes)
    storage.save(data)
    confirm(L(30072))
    return True


def delete_folder(params):
    data = storage.load_for_edit()
    folder_id = _folder(data, params)
    if not folder_id or folder_id == storage.ROOT:
        return False
    name = data['folders'][folder_id]['name']
    subfolders, items = storage.count_contents(data, folder_id)
    scope = L(30025) % (subfolders, items) if subfolders or items else L(30026)
    message = '%s[CR]%s[CR][CR]%s' % (L(30024) % name, scope, L(30027))
    if not xbmcgui.Dialog().yesno(L(30007), message, yeslabel=L(30007)):
        return False
    storage.delete_folder(data, folder_id)
    storage.save(data)
    confirm(L(30031))
    return True


def set_content(params):
    """Content Type: which skin views a folder offers. Automatic follows its items."""
    data = storage.load_for_edit()
    folder_id = params.get('folder', '')
    if folder_id == kodifav.FOLDER_ID:
        settings, name = data['kodi_favorites'], L(30002)
    else:
        folder_id = _folder(data, params)
        if not folder_id or folder_id == storage.ROOT:
            return False
        settings, name = data['folders'][folder_id], data['folders'][folder_id]['name']
    current = settings.get('content', '')
    keys = [key for key, _ in metadata.CONTENT_TYPES]
    labels = [L(sid) for _, sid in metadata.CONTENT_TYPES]
    index = xbmcgui.Dialog().select('%s: %s' % (L(30083), name), labels,
                                    preselect=keys.index(current) if current in keys else 0)
    if index < 0 or keys[index] == current:
        return False
    settings['content'] = keys[index]
    storage.save(data)
    return True


# --------------------------------------------------------------------- items

def edit_item(params):
    return _edit(params, editor.edit_item)


def edit_art(params):
    return _edit(params, editor.edit_artwork)


def _edit(params, edit):
    data = storage.load_for_edit()
    item_id = _item(data, params)
    if not item_id:
        return False
    edited = edit(data['items'][item_id])
    if edited is None:
        return False
    # Re-read so changes made elsewhere while the editor was open are kept.
    data = storage.load_for_edit()
    if item_id not in data['items']:
        notify(L(30074))
        return False
    data['items'][item_id] = storage.normalize_item(edited)
    storage.save(data)
    confirm(L(30072))
    return True


def copy_item(params):
    data = storage.load_for_edit()
    item_id = _item(data, params)
    if not item_id:
        return False
    dest, _ = resolve_destination(data, L(30005), L(30032))
    if not dest:
        return False
    storage.copy_item(data, item_id, dest)
    storage.save(data)
    confirm(L(30030) % folder_label(data, dest))
    return True


def kodi_source(params):
    """Go to Source: open where a Kodi favorite's original item lives."""
    favorites = kodifav.load()
    name = params.get('name', '')
    try:
        index = int(params.get('index', -1))
    except ValueError:
        index = -1
    if not 0 <= index < len(favorites) or favorites[index]['label'] != name:
        # Favorites changed since the list was shown; fall back to the name.
        index = next((i for i, fav in enumerate(favorites) if fav['label'] == name), -1)
    if index < 0:
        notify(L(30094))
        return False
    command = kodifav.source_command(favorites[index])
    if not command:
        notify(L(30096))
        return False
    xbmc.executebuiltin(command)
    return False  # navigating away; nothing to refresh


def delete_item(params):
    data = storage.load_for_edit()
    item_id = _item(data, params)
    if not item_id:
        return False
    label = plain(data['items'][item_id]['label'])
    message = '%s[CR][CR]%s' % (L(30023) % label, L(30027))
    if not xbmcgui.Dialog().yesno(L(30006), message, yeslabel=L(30006)):
        return False
    storage.delete_item(data, item_id)
    storage.save(data)
    return True


def move(params):
    """Move to Top / Up / Down / Bottom within the folder being viewed."""
    data = storage.load_for_edit()
    parent_id = _folder(data, params, 'parent')
    if not parent_id:
        return False
    entry_id = params.get('folder') if params.get('kind') == 'folder' else params.get('id')
    if not storage.move(data, parent_id, entry_id, params.get('to', '')):
        return False
    storage.save(data)
    return True


def move_item(params):
    """Move to Folder for an item: same item, new folder (never the top level)."""
    data = storage.load_for_edit()
    item_id = _item(data, params)
    if not item_id:
        return False
    current = storage.find_item_folder(data, item_id)
    dest, _ = resolve_destination(data, L(30132), L(30032), exclude=(current,))
    if not dest or not storage.move_item_to(data, item_id, dest):
        return False
    storage.save(data)
    confirm(L(30134) % folder_label(data, dest))
    return True


def move_folder(params):
    """Move to Folder for a folder: it moves with everything inside it."""
    data = storage.load_for_edit()
    folder_id = _folder(data, params)
    if not folder_id or folder_id == storage.ROOT:
        return False
    own = storage.subtree_ids(data, folder_id)  # itself and its subfolders
    current = data['folders'][folder_id]['parent']
    dest, _ = resolve_destination(data, L(30132), L(30032), exclude=own + [current],
                                  include_top_level=True, exclude_parents=own)
    if not dest or not storage.move_folder_to(data, folder_id, dest):
        return False
    storage.save(data)
    confirm(L(30134) % folder_label(data, dest))
    return True


def import_list(params):
    data = storage.load()
    item_id = _item(data, params)
    if item_id and data['items'][item_id]['target'].get('kind') == 'folder':
        from . import listcopy
        listcopy.run(data['items'][item_id])
        return True
    return False


def set_pagination(params):
    data = storage.load_for_edit()
    folder_id = _folder(data, params)
    if not folder_id:
        return False
    folder = data['folders'][folder_id]
    choice = xbmcgui.Dialog().select(L(30323), [L(30324), L(30325), L(30326)])
    if choice < 0:
        return False
    value = None
    if choice == 1:
        value = 0
    elif choice == 2:
        raw = text(L(30321), folder.get('page_size') or Settings().items_per_page, numeric=True)
        if raw is None:
            return False
        from .paging import positive
        value = positive(raw, None)
        if value is None:
            notify(L(30327))
            return False
    latest = storage.load_for_edit()
    if folder_id not in latest['folders']:
        notify(L(30071))
        return False
    latest['folders'][folder_id]['page_size'] = value
    storage.save(latest)
    return True


_ACTIONS = {
    'new_folder': new_folder,
    'rename_folder': rename_folder,
    'copy_folder': copy_folder,
    'delete_folder': delete_folder,
    'edit_item': edit_item,
    'edit_art': edit_art,
    'copy_item': copy_item,
    'delete_item': delete_item,
    'move': move,
    'move_item': move_item,
    'move_folder': move_folder,
    'edit_folder_art': edit_folder_art,
    'use_as_folder_art': use_as_folder_art,
    'set_content': set_content,
    'set_pagination': set_pagination,
    'import_list': import_list,
    'kodi_source': kodi_source,
}
