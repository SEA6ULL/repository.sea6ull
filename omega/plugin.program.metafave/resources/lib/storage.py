# SPDX-License-Identifier: GPL-3.0-only
# MetaFave is authored by SEA6ULL / Claude / GPT and licensed under GPL-3.0.
"""Collection storage: one JSON file holding the folder tree and all items.

Layout (schema 1):

    {
      "schema": 1,
      "folders": {
        "root": {"name": "", "parent": null, "folders": [ids...], "items": [ids...]},
        "<id>": {..., "content": "",       content type override; "" = automatic
                 "order": [ids...],        display order of subfolders and items together
                 "art": {"thumb": ...}}    folder covers chosen by the user
        "<id>": {...}
      },
      "kodi_favorites": {"content": ""},
      "items": {
        "<id>": {
          "label": "...", "label2": "...",
          "target": {"kind": "folder|item|addon|script|command", "path": "...",
                     "window": "videos", "playable": false},
          "art": {"poster": "...", "fanart": "...", ...},
          "info": {"mediatype": "movie", "title": "...", ...},
          "properties": {"key": "value"}
        }
      }
    }

"target" is launch behavior; label/label2/art/info/properties are presentation.
Editing one never touches the other.
"""

import copy
import json
import os
import shutil
import time
import uuid

import xbmc
import xbmcvfs

from .common import PROFILE_PATH, L, log, notify

SCHEMA = 1
ROOT = 'root'
DATA_FILE = os.path.join(PROFILE_PATH, 'collections.json')
BACKUP_FILE = DATA_FILE + '.bak'

_cache = {'stamp': None, 'data': None}


# ---------------------------------------------------------------- file access

def _new_folder(name, parent, content='', art=None, page_size=None):
    return {'name': name, 'parent': parent, 'folders': [], 'items': [], 'content': content,
            'order': [], 'art': dict(art or {}), 'page_size': page_size}


def _empty():
    return {'schema': SCHEMA, 'folders': {ROOT: _new_folder('', None)}, 'items': {},
            'kodi_favorites': {'content': ''}}


def normalize_item(item):
    item.setdefault('label', '')
    item.setdefault('label2', '')
    target = item.setdefault('target', {})
    target.setdefault('kind', 'item')
    target.setdefault('path', '')
    item.setdefault('art', {})
    item.setdefault('info', {})
    item.setdefault('properties', {})
    return item


def validate(data):
    if not isinstance(data, dict):
        raise ValueError('collection root is not an object')
    folders = data.setdefault('folders', {})
    items = data.setdefault('items', {})
    data.setdefault('kodi_favorites', {}).setdefault('content', '')
    folders.setdefault(ROOT, _new_folder('', None))
    for folder in folders.values():
        folder.setdefault('name', '')
        folder.setdefault('parent', None)
        folder.setdefault('content', '')
        folder.setdefault('page_size', None)
        if not isinstance(folder.get('art'), dict):
            folder['art'] = {}
        folder['folders'] = [fid for fid in folder.get('folders', []) if fid in folders]
        folder['items'] = [iid for iid in folder.get('items', []) if iid in items]
        members = set(folder['folders']) | set(folder['items'])
        folder['order'] = [eid for eid in folder.get('order', []) if eid in members]
    for item in items.values():
        normalize_item(item)
    data['schema'] = SCHEMA
    return data


def _stamp():
    try:
        stat = os.stat(DATA_FILE)
    except OSError:
        return None
    return stat.st_mtime_ns, stat.st_size


def _read(path):
    with open(path, 'r', encoding='utf-8') as handle:
        return json.load(handle)


def _load_from_disk():
    if os.path.exists(DATA_FILE):
        try:
            return validate(_read(DATA_FILE))
        except Exception as exc:  # corrupt or unreadable file
            log('Could not read %s: %s' % (DATA_FILE, exc), xbmc.LOGERROR)
            # Set the damaged file aside so a later save can't overwrite the good backup.
            try:
                os.replace(DATA_FILE, '%s.corrupt-%d' % (DATA_FILE, int(time.time())))
            except OSError:
                pass
    if os.path.exists(BACKUP_FILE):
        try:
            data = validate(_read(BACKUP_FILE))
        except Exception as exc:
            log('Could not read %s: %s' % (BACKUP_FILE, exc), xbmc.LOGERROR)
            return _empty()
        save(data)  # make the restored collection the main file again
        notify(L(30073))
        return data
    return _empty()


def load():
    """Return a cached, read-only snapshot. Do not mutate the result."""
    stamp = _stamp()
    if stamp is None or stamp != _cache['stamp'] or _cache['data'] is None:
        _cache['data'] = _load_from_disk()
        _cache['stamp'] = _stamp()
    return _cache['data']


def load_for_edit():
    """Return a private copy that can be changed and passed to save()."""
    return copy.deepcopy(load())


def save(data):
    xbmcvfs.mkdirs(PROFILE_PATH)
    temp_file = DATA_FILE + '.tmp'
    with open(temp_file, 'w', encoding='utf-8') as handle:
        json.dump(data, handle, ensure_ascii=False, indent=1)
    if os.path.exists(DATA_FILE):
        try:
            shutil.copyfile(DATA_FILE, BACKUP_FILE)
        except OSError as exc:
            log('Backup failed: %s' % exc, xbmc.LOGWARNING)
    os.replace(temp_file, DATA_FILE)
    _cache['data'] = copy.deepcopy(data)
    _cache['stamp'] = _stamp()


# ------------------------------------------------------- collection operations

def new_id():
    return uuid.uuid4().hex[:12]


# --- order ----------------------------------------------------------------------
# folder['folders'] and folder['items'] say what a folder contains; folder['order'] is the
# display order of both together. Entries missing from 'order' (collections saved before
# it existed) follow, subfolders first, so older collections look exactly as they did.

def entries(data, folder_id):
    """[(kind, id), ...] in display order; kind is 'folder' or 'item'."""
    folder = data['folders'][folder_id]
    members = dict.fromkeys(folder['folders'], 'folder')
    members.update(dict.fromkeys(folder['items'], 'item'))
    ordered = [(members[eid], eid) for eid in dict.fromkeys(folder.get('order', []))
               if eid in members]
    placed = {eid for _, eid in ordered}
    ordered += [('folder', fid) for fid in folder['folders'] if fid not in placed]
    ordered += [('item', iid) for iid in folder['items'] if iid not in placed]
    return ordered


def _set_order(data, folder_id, ordered):
    data['folders'][folder_id]['order'] = [eid for _, eid in ordered]


def _insert(data, folder_id, kind, entry_id, at_end):
    """Add an entry to the order: at the end, or after the last subfolder."""
    ordered = [entry for entry in entries(data, folder_id) if entry[1] != entry_id]
    index = len(ordered)
    if not at_end:
        index = max((i + 1 for i, (k, _) in enumerate(ordered) if k == 'folder'), default=0)
    ordered.insert(index, (kind, entry_id))
    _set_order(data, folder_id, ordered)


def _detach(data, folder_id, kind, entry_id):
    folder = data['folders'][folder_id]
    members = folder['folders' if kind == 'folder' else 'items']
    if entry_id in members:
        members.remove(entry_id)
    if entry_id in folder.get('order', []):
        folder['order'].remove(entry_id)


# --- folders and items -----------------------------------------------------------

def create_folder(data, parent_id, name, content='', at_end=False, art=None, page_size=None):
    folder_id = new_id()
    data['folders'][folder_id] = _new_folder(name, parent_id, content, art, page_size)
    data['folders'][parent_id]['folders'].append(folder_id)
    _insert(data, parent_id, 'folder', folder_id, at_end)
    return folder_id


def rename_folder(data, folder_id, name):
    data['folders'][folder_id]['name'] = name


def add_item(data, folder_id, item):
    item_id = new_id()
    data['items'][item_id] = normalize_item(copy.deepcopy(item))
    data['folders'][folder_id]['items'].append(item_id)
    _insert(data, folder_id, 'item', item_id, at_end=True)
    return item_id


def add_items(data, folder_id, items):
    """Append a staged list in linear time, preserving the existing mixed order."""
    ordered = entries(data, folder_id)
    folder = data['folders'][folder_id]
    added = []
    for item in items:
        item_id = new_id()
        data['items'][item_id] = normalize_item(copy.deepcopy(item))
        folder['items'].append(item_id)
        ordered.append(('item', item_id))
        added.append(item_id)
    _set_order(data, folder_id, ordered)
    return added


def copy_item(data, item_id, dest_id):
    return add_item(data, dest_id, data['items'][item_id])


def find_item_folder(data, item_id):
    for folder_id, folder in data['folders'].items():
        if item_id in folder['items']:
            return folder_id
    return None


def delete_item(data, item_id):
    folder_id = find_item_folder(data, item_id)
    if folder_id:
        _detach(data, folder_id, 'item', item_id)
    data['items'].pop(item_id, None)


def iter_tree(data, folder_id=ROOT, depth=0):
    """Depth-first (folder_id, depth) pairs in display order, starting with folder_id itself."""
    yield folder_id, depth
    for kind, child_id in entries(data, folder_id):
        if kind == 'folder':
            yield from iter_tree(data, child_id, depth + 1)


def subtree_ids(data, folder_id):
    return [fid for fid, _ in iter_tree(data, folder_id)]


CONTENTS_ART_LIMIT = 200


def contents_art(data, folder_id, limit=CONTENTS_ART_LIMIT):
    """[(label, art), ...] for entries inside folder_id that have artwork, in display order.

    Subfolders contribute their own covers, then their contents, so a folder holding only
    subfolders still has something to choose from. At most 'limit' entries are returned,
    which keeps very large imported folders quick.
    """
    found, seen = [], set()

    def walk(fid):
        seen.add(fid)
        for kind, entry_id in entries(data, fid):
            if len(found) >= limit:
                return
            if kind == 'item':
                item = data['items'][entry_id]
                if item.get('art'):
                    found.append((item['label'], item['art']))
            elif entry_id not in seen:
                sub = data['folders'][entry_id]
                if sub.get('art'):
                    found.append((sub['name'], sub['art']))
                walk(entry_id)

    walk(folder_id)
    return found


def count_contents(data, folder_id):
    """(subfolders, items) inside folder_id, counted recursively."""
    subfolders = items = 0
    for fid, _ in iter_tree(data, folder_id):
        if fid != folder_id:
            subfolders += 1
        items += len(data['folders'][fid]['items'])
    return subfolders, items


def delete_folder(data, folder_id):
    if folder_id == ROOT or folder_id not in data['folders']:
        return
    doomed = subtree_ids(data, folder_id)
    parent_id = data['folders'][folder_id]['parent']
    if parent_id in data['folders']:
        _detach(data, parent_id, 'folder', folder_id)
    for fid in doomed:
        for item_id in data['folders'][fid]['items']:
            data['items'].pop(item_id, None)
    for fid in doomed:
        data['folders'].pop(fid, None)


def copy_folder_contents(data, source_id, dest_id):
    """Copy a folder's entire contents (items and subfolders, in order) into dest_id."""
    def snapshot(fid):
        folder = data['folders'][fid]
        return {
            'name': folder['name'],
            'content': folder.get('content', ''),
            'art': dict(folder.get('art') or {}),
            'page_size': folder.get('page_size'),
            'entries': [('item', copy.deepcopy(data['items'][eid])) if kind == 'item'
                        else ('folder', snapshot(eid)) for kind, eid in entries(data, fid)],
        }

    def fill(node, fid):
        for kind, value in node['entries']:
            if kind == 'item':
                add_item(data, fid, value)
            else:
                fill(value, create_folder(data, fid, value['name'], value['content'],
                                          at_end=True, art=value['art'], page_size=value['page_size']))

    fill(snapshot(source_id), dest_id)  # snapshot first, so nothing copies itself twice


# --- moving ----------------------------------------------------------------------

MOVES = ('top', 'up', 'down', 'bottom')


def move(data, parent_id, entry_id, where):
    """Reorder an entry within its folder. Returns False when nothing changed."""
    ordered = entries(data, parent_id)
    index = next((i for i, (_, eid) in enumerate(ordered) if eid == entry_id), None)
    if index is None or where not in MOVES:
        return False
    target = {'top': 0, 'up': index - 1, 'down': index + 1, 'bottom': len(ordered) - 1}[where]
    if not 0 <= target < len(ordered) or target == index:
        return False
    ordered.insert(target, ordered.pop(index))
    _set_order(data, parent_id, ordered)
    return True


def move_item_to(data, item_id, dest_id):
    """Move an item (same id, same everything) to the end of another folder."""
    source_id = find_item_folder(data, item_id)
    if not source_id or source_id == dest_id or dest_id not in data['folders']:
        return False
    _detach(data, source_id, 'item', item_id)
    data['folders'][dest_id]['items'].append(item_id)
    _insert(data, dest_id, 'item', item_id, at_end=True)
    return True


def move_folder_to(data, folder_id, dest_id):
    """Move a folder, with everything in it, into another folder or the top level."""
    if folder_id == ROOT or dest_id not in data['folders'] or dest_id in subtree_ids(data, folder_id):
        return False  # never into itself or one of its own subfolders
    source_id = data['folders'][folder_id]['parent']
    if source_id == dest_id:
        return False
    if source_id in data['folders']:
        _detach(data, source_id, 'folder', folder_id)
    data['folders'][folder_id]['parent'] = dest_id
    data['folders'][dest_id]['folders'].append(folder_id)
    _insert(data, dest_id, 'folder', folder_id, at_end=False)
    return True


def folder_trail(data, folder_id):
    names, seen = [], set()
    while folder_id and folder_id != ROOT and folder_id not in seen:
        seen.add(folder_id)
        folder = data['folders'].get(folder_id)
        if not folder:
            break
        names.append(folder['name'])
        folder_id = folder['parent']
    return list(reversed(names))
