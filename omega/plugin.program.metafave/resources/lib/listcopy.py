# SPDX-License-Identifier: GPL-3.0-only
"""One-time snapshots of Kodi directories; never a sync or a media resolver."""
import copy
import json
import re
from urllib.parse import parse_qs, unquote, urlsplit

import xbmc
import xbmcgui

from . import metadata, storage, targets
from .common import ADDON_ID, L, Settings, log, notify, plain
from .dialogs import folder_label, resolve_destination

_FIELDS = [field for field in metadata.FIELD_TYPES
           if field not in ('mediatype', 'dbid', 'duration', 'tracknumber', 'tvshowtitle')
           and not field.endswith('_id')]
_FIELDS += ['art', 'thumbnail', 'fanart', 'uniqueid', 'runtime', 'duration', 'track',
            'showtitle', 'customproperties']
_PAGE_KEYS = ('page', 'new_page', 'page_no', 'pagenum', 'pagenumber', 'offset', 'cursor', 'next_cursor')
_PRESENTATION_KEYS = {'name', 'label', 'exit_list_params'}
_VIDEO = {'movie', 'tvshow', 'season', 'episode', 'musicvideo'}
_STATUS = {'playcount', 'watched', 'resumetime', 'totaltime', 'percentplayed', 'watchedprogress',
           'watchedepisodes', 'unwatchedepisodes', 'totalepisodes', 'numepisodes',
           'watchedepisodepercent', 'inprogress', 'startoffset'}
MAX_PAGES, MAX_ITEMS = 500, 100000


class CopyError(Exception):
    pass


class Cancelled(Exception):
    pass


def read_page(path):
    """Kodi runs the source add-on and returns its existing playable destinations."""
    request = {'jsonrpc': '2.0', 'id': 1, 'method': 'Files.GetDirectory',
               'params': {'directory': path, 'media': 'files', 'properties': _FIELDS,
                          'sort': {'method': 'none'}}}
    try:
        response = json.loads(xbmc.executeJSONRPC(json.dumps(request)))
        rows = response['result']['files']
        if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
            raise ValueError()
        return rows
    except Exception:
        # Errors may contain authenticated source URLs. Never log the payload.
        log('List copy could not read a source directory.', xbmc.LOGWARNING)
        raise CopyError(L(30310)) from None


def _has_media(row):
    return row.get('type') in metadata.MEDIA_TYPES or row.get('type') == 'picture'


def next_marker(row):
    if _has_media(row):
        return False
    label = plain(row.get('label', '')).casefold().strip(' <>»›→')
    if re.match(r'^next(?:\s+page)?(?:\b|\s|[>(])', label):
        return True
    art = row.get('art') or {}
    if not isinstance(art, dict):
        art = {}
    for value in list(art.values()) + [row.get('thumbnail', '')]:
        path = unquote(str(value)).lower().split('?', 1)[0].rstrip('/')
        if re.search(r'(?:/|%2f)(?:item_next|nextpage|next_page)\.(?:png|jpg|webp)', path):
            return True
    return False


def navigation(row):
    if _has_media(row):
        return False
    label = plain(row.get('label', '')).casefold().strip(' <>«»‹›←→')
    return (next_marker(row) or row.get('file') in ('..', '../')
            or bool(re.match(r'^(?:previous(?:\s+page)?|parent\s+folder|back|jump\s+to|go\s+to\s+page)(?:\b|\s|[<(])', label)))


def continuation(current, candidate):
    """Follow only a marked next-page link within the same source and list route.

    Paging tokens may change. Existing list identity parameters must be retained;
    harmless name/exit hints may be added. No hard-coded source-addon adapters.
    """
    try:
        old, new = urlsplit(current), urlsplit(candidate)
        if (old.scheme != 'plugin' or new.scheme != 'plugin'
                or old.netloc != new.netloc or old.path != new.path
                or new.netloc == ADDON_ID or new.fragment):
            return False
        before, after = parse_qs(old.query, keep_blank_values=True), parse_qs(new.query, keep_blank_values=True)
        identity_before = {k: v for k, v in before.items() if k not in _PAGE_KEYS and k not in _PRESENTATION_KEYS}
        identity_after = {k: v for k, v in after.items() if k not in _PAGE_KEYS and k not in _PRESENTATION_KEYS}
        if identity_before != identity_after:
            return False
        changed = [k for k in _PAGE_KEYS if after.get(k) != before.get(k) and k in after]
        if len(changed) != 1:
            return False
        key = changed[0]
        values = after[key]
        if len(values) != 1 or not values[0]:
            return False
        if key in ('cursor', 'next_cursor'):
            return True
        initial = 0 if key == 'offset' else 1
        previous = int(before.get(key, [initial])[0])
        following = int(values[0])
        return following > previous and (key == 'offset' or following == previous + 1)
    except (ValueError, TypeError, IndexError):
        return False


def next_page(rows, current):
    marked = [row for row in rows if next_marker(row)]
    supported = [row.get('file') for row in marked
                 if row.get('filetype') == 'directory' and continuation(current, row.get('file', ''))]
    if len(marked) > 1 or len(supported) > 1:
        return None, bool(marked)
    return (supported[0] if supported else None), bool(marked and not supported)


def _properties(row):
    value = row.get('customproperties') or {}
    if isinstance(value, list):
        value = {entry['name']: entry.get('value', '') for entry in value
                 if isinstance(entry, dict) and isinstance(entry.get('name'), str)}
    if not isinstance(value, dict):
        return {}
    return {k: str(v) for k, v in value.items() if k.lower() not in _STATUS and v is not None}


def item_from_row(row, window=None):
    path = row.get('file')
    if not isinstance(path, str) or not path or navigation(row):
        return None
    label = row.get('label') or row.get('title')
    if not isinstance(label, str) or not label.strip():
        return None
    media = row.get('type', '')
    info = {k: copy.deepcopy(row[k]) for k in metadata.FIELD_TYPES if k in row and row[k] not in (None, '', [], {})}
    if media in metadata.MEDIA_TYPES:
        info['mediatype'] = media
    # Raw source IDs are external IDs, not necessarily Kodi database IDs.
    try:
        dbid = int(row.get('id', 0))
    except (ValueError, TypeError):
        dbid = 0
    if not path.startswith('plugin://') and dbid > 0:
        info['dbid'] = dbid
    duration = row.get('runtime') or row.get('duration')
    if duration:
        info['duration'] = duration
    if row.get('showtitle'):
        info['tvshowtitle'] = row['showtitle']
    if row.get('track'):
        info['tracknumber'] = row['track']
    if isinstance(info.get('artist'), list):
        info['artist'] = ' / '.join(str(artist) for artist in info['artist'])
    ids = row.get('uniqueid') or {}
    if isinstance(ids, dict) and media in ('movie', 'tvshow'):
        for provider in ('imdb', 'tmdb', 'tvdb'):
            value = ids.get(provider)
            if value and (provider != 'tvdb' or media == 'tvshow'):
                info['imdbnumber' if provider == 'imdb' else provider + '_id'] = str(value)
    props = _properties(row)
    if media == 'episode':
        for scope, providers in (('episode', ('tmdb', 'tvdb')), ('tvshow', ('imdb', 'tmdb'))):
            for provider in providers:
                if props.get('%s.%s_id' % (scope, provider)):
                    info['%s_%s_id' % (scope, provider)] = props['%s.%s_id' % (scope, provider)]
    artwork = row.get('art') or {}
    art = {k: v for k, v in artwork.items() if isinstance(v, str) and v} if isinstance(artwork, dict) else {}
    for field, kind in (('thumbnail', 'thumb'), ('fanart', 'fanart')):
        if row.get(field):
            art.setdefault(kind, row[field])
    is_folder = row.get('filetype') == 'directory'
    playable = not is_folder and (media in _VIDEO | {'song'} or props.get('IsPlayable', '').lower() == 'true')
    return {'label': label, 'label2': row.get('label2', ''), 'info': info, 'art': art,
            'properties': props, 'target': targets.derive(path, is_folder, playable, '', window)}


def identities(item):
    from .mdblist import item_keys
    keys = set(item_keys(item))
    target = item.get('target') or {}
    if target.get('path'):
        keys.add('target:' + json.dumps([target.get('kind'), target['path'], target.get('window')], separators=(',', ':')))
    return keys


def add_unique(data, destination, items, skip_duplicates=True):
    seen = set()
    for item_id in data['folders'][destination]['items']:
        seen.update(identities(data['items'][item_id]))
    pending, skipped = [], 0
    for item in items:
        keys = identities(item)
        if skip_duplicates and keys & seen:
            skipped += 1
            seen.update(keys)
            continue
        pending.append(item)
        seen.update(keys)
    storage.add_items(data, destination, pending)
    return len(pending), skipped


def collect(first, path, window, follow, progress, reader=None):
    reader = reader or read_page
    rows, current, pages, items, skipped, visited, signatures = first, path, 0, [], 0, set(), set()
    while True:
        if progress.iscanceled():
            raise Cancelled()
        if current in visited or pages >= MAX_PAGES:
            raise CopyError(L(30313))
        visited.add(current)
        signature = tuple(row.get('file', '') for row in rows if not navigation(row))
        if signature in signatures:
            raise CopyError(L(30313))
        signatures.add(signature)
        pages += 1
        for index, row in enumerate(rows):
            if index % 250 == 0 and progress.iscanceled():
                raise Cancelled()
            item = item_from_row(row, window)
            if item is None:
                skipped += 1
            else:
                items.append(item)
        if len(items) > MAX_ITEMS:
            raise CopyError(L(30313))
        progress.update(0, L(30309) % (pages, len(items)))
        following, unsupported = next_page(rows, current)
        if not follow:
            return items, skipped
        if unsupported:
            raise CopyError(L(30312))
        if not following:
            return items, skipped
        if progress.iscanceled():
            raise Cancelled()
        if pages >= MAX_PAGES:
            raise CopyError(L(30313))
        current, rows = following, reader(following)


def run(captured):
    path = captured['target']['path']
    if path.startswith('plugin://%s' % ADDON_ID):
        notify(L(30310))
        return
    progress = xbmcgui.DialogProgress()
    try:
        progress.create(L(30300), L(30308))
        first = read_page(path)
        if progress.iscanceled():
            return
    except CopyError as exc:
        notify(str(exc))
        return
    finally:
        progress.close()
    following, unsupported = next_page(first, path)
    follow = False
    if following:
        choice = xbmcgui.Dialog().select(L(30300), [L(30302), L(30303)])
        if choice < 0:
            return
        follow = choice == 1
    elif unsupported:
        if not xbmcgui.Dialog().yesno(L(30300), L(30312) + '[CR][CR]' + L(30314),
                                      yeslabel=L(30302), nolabel=L(30036)):
            return
    progress = xbmcgui.DialogProgress()
    try:
        progress.create(L(30300), L(30308))
        items, ignored = collect(first, path, captured['target'].get('window'), follow, progress)
        if progress.iscanceled():
            return
    except Cancelled:
        return
    except CopyError as exc:
        notify(str(exc))
        return
    finally:
        progress.close()
    if not items:
        notify(L(30315))
        return
    # No folders or items have been written during reads or cancellation.
    data = storage.load_for_edit()
    old_folders = set(data['folders'])
    destination, _ = resolve_destination(data, L(30304), plain(captured['label']))
    if not destination:
        return
    latest = storage.load_for_edit()
    if destination not in old_folders:
        folder = data['folders'][destination]
        if folder['parent'] not in latest['folders']:
            notify(L(30071))
            return
        latest['folders'][destination] = folder
        latest['folders'][folder['parent']]['folders'].append(destination)
        storage._insert(latest, folder['parent'], 'folder', destination, at_end=False)
    if destination not in latest['folders']:
        notify(L(30071))
        return
    added, duplicates = add_unique(latest, destination, items, Settings().import_skip_duplicates)
    if added:
        storage.save(latest)
    # Navigation/invalid rows and duplicates are reported separately.
    xbmcgui.Dialog().ok(L(30300), L(30311) % (added, duplicates, ignored, folder_label(latest, destination)))
