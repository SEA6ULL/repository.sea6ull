# SPDX-License-Identifier: GPL-3.0-only
# MetaFave is authored by SEA6ULL / Claude / GPT and licensed under GPL-3.0.
"""Dialogs shared by the context menu and the management view.

Only Kodi's stock dialogs are used, so everything follows the user's skin.
"""

import xbmcgui

from . import artwork, storage
from .common import ICON_CREATE, ICON_FOLDER, L, Settings, icon_art, plain


def row(label, label2='', image=''):
    listitem = xbmcgui.ListItem(label, label2, offscreen=True)
    if image:
        listitem.setArt(icon_art(image))
    return listitem


def text(heading, default='', numeric=False):
    """Text entry. Returns None on cancel or an empty result."""
    kind = xbmcgui.INPUT_NUMERIC if numeric else xbmcgui.INPUT_ALPHANUM
    value = xbmcgui.Dialog().input(heading, defaultt=str(default or ''), type=kind)
    value = (value or '').strip()
    return value or None


def folder_label(data, folder_id):
    if folder_id == storage.ROOT:
        return L(30012)
    return ' / '.join(storage.folder_trail(data, folder_id))


def choose_folder(data, heading, allow_create=False, include_top_level=False, extra=None,
                  exclude=()):
    """Pick a user folder.

    The top level is only offered when choosing where a folder goes; items always
    live inside a user folder. extra is a list of (key, label, image) rows shown
    after the folders. Returns ('folder', id), ('create', None), (extra_key, None)
    or None.
    """
    choices, rows = [], []
    if allow_create:
        choices.append(('create', None))
        rows.append(row(L(30001), image=ICON_CREATE))
    for folder_id, _ in storage.iter_tree(data):
        if folder_id in exclude:
            continue
        if folder_id == storage.ROOT:
            if include_top_level:
                choices.append(('folder', folder_id))
                rows.append(row(L(30012), image=ICON_FOLDER))
            continue
        count = len(data['folders'][folder_id]['items'])
        choices.append(('folder', folder_id))
        rows.append(row(folder_label(data, folder_id), L(30070) % count, ICON_FOLDER))
    for key, label, image in extra or []:
        choices.append((key, None))
        rows.append(row(label, image=image))
    if not rows:
        return None
    index = xbmcgui.Dialog().select(heading, rows, useDetails=True)
    return choices[index] if index >= 0 else None


def choose_folder_parent(data, heading, exclude=()):
    """Where a new folder goes. Skips the question when the top level is the only choice."""
    if all(fid == storage.ROOT or fid in exclude for fid in data['folders']):
        return storage.ROOT
    choice = choose_folder(data, heading, include_top_level=True, exclude=exclude)
    return choice[1] if choice else None


def resolve_destination(data, heading, suggested_name, extra=None, exclude=(),
                        include_top_level=False, exclude_parents=()):
    """Choose a destination folder, or create one (in memory; caller saves).

    The top level is offered only when a folder is being placed (include_top_level).
    exclude hides destinations; exclude_parents hides where a newly created folder may go
    (a folder being moved must not end up inside itself).
    Returns (folder_id, None), (None, extra_key) or (None, None) when cancelled.
    """
    choice = choose_folder(data, heading, allow_create=True, extra=extra, exclude=exclude,
                           include_top_level=include_top_level)
    if not choice:
        return None, None
    key, folder_id = choice
    if key == 'folder':
        return folder_id, None
    if key != 'create':
        return None, key
    name = text(L(30014), suggested_name)
    if not name:
        return None, None
    parent_id = choose_folder_parent(data, L(30017), exclude=exclude_parents)
    if not parent_id:
        return None, None
    return storage.create_folder(data, parent_id, name, Settings().default_content), None


def pick_item(data, folder_id, heading):
    item_ids = data['folders'][folder_id]['items']
    rows = []
    for item_id in item_ids:
        item = data['items'][item_id]
        rows.append(row(item['label'], plain(item['target'].get('path', '')),
                        artwork.preview(item['art'])))
    index = xbmcgui.Dialog().select(heading, rows, useDetails=True) if rows else -1
    return item_ids[index] if index >= 0 else None
