# SPDX-License-Identifier: GPL-3.0-only
# MetaFave is authored by SEA6ULL / Claude / GPT and licensed under GPL-3.0.
"""Item editor. Every stored value of an item can be changed here.

Editors work on a copy and return the edited item, or None when nothing
should be saved.
"""

import copy

import xbmc
import xbmcgui

from . import artwork, metadata, targets
from .common import ICON_ADD, L, notify, plain
from .dialogs import row, text


def _select(heading, rows):
    return xbmcgui.Dialog().select(heading, rows, useDetails=True)


def _finish(work, original, heading=None):
    if work == original:
        return None
    if xbmcgui.Dialog().yesno(heading or L(30003), L(30034) % plain(work['label']),
                              nolabel=L(30036), yeslabel=L(30035)):
        return work
    return None


# ------------------------------------------------------------------ main menu

def edit_item(item):
    original, work = item, copy.deepcopy(item)
    while True:
        target = work['target']
        kind = target.get('kind', 'item')
        kind_names = dict(targets.KINDS)
        preview = artwork.preview(work['art'])
        rows = [
            ('label', row(L(30037), work['label'], preview)),
            ('label2', row(L(30038), work.get('label2', ''))),
            ('kind', row(L(30039), L(kind_names.get(kind, 30051)))),
            ('path', row(L(30040), target.get('path', ''))),
        ]
        if kind == 'folder':
            rows.append(('window', row(L(30041), _window_name(target.get('window')))))
        if kind == 'item':
            rows.append(('playable', row(L(30042), L(30057) if target.get('playable') else L(30058))))
        rows += [
            ('art', row(L(30004), L(30045) % len(work['art']))),
            ('info', row(L(30043), L(30046) % len(work['info']))),
            ('properties', row(L(30044), L(30081) % len(work['properties']))),
            ('save', row(L(30035))),
        ]
        index = _select('%s: %s' % (L(30003), plain(work['label'])), [r for _, r in rows])
        if index < 0:
            return _finish(work, original)
        key = rows[index][0]
        if key == 'save':
            return work if work != original else None
        if key == 'label2' and work.get('label2'):
            action = xbmcgui.Dialog().contextmenu([L(30077), L(30069)])
            if action == 1:
                work['label2'] = ''
                continue
            if action != 0:
                continue
        if key in ('label', 'label2'):
            value = text(L(30037) if key == 'label' else L(30038), work.get(key, ''))
            if value is not None:
                work[key] = value
        elif key == 'kind':
            _edit_kind(target)
        elif key == 'path':
            value = text(L(30040), target.get('path', ''))
            if value is not None:
                target['path'] = value
        elif key == 'window':
            _edit_window(target)
        elif key == 'playable':
            target['playable'] = not target.get('playable')
        elif key == 'art':
            edit_artwork_in_place(work)
        elif key == 'info':
            _edit_info(work)
        elif key == 'properties':
            _edit_properties(work)


def _window_name(window):
    names = {key: L(sid) for key, sid in targets.WINDOWS}
    return names.get(str(window or '').lower(), str(window or ''))


def _edit_kind(target):
    rows = [row(L(sid)) for _, sid in targets.KINDS]
    index = xbmcgui.Dialog().select(L(30039), rows)
    if index < 0:
        return
    kind = targets.KINDS[index][0]
    target['kind'] = kind
    if kind == 'folder' and not target.get('window'):
        target['window'] = targets.guess_window(target.get('path', ''))


def _edit_window(target):
    rows = [row(L(sid)) for _, sid in targets.WINDOWS] + [row(L(30056))]
    index = xbmcgui.Dialog().select(L(30041), rows)
    if index < 0:
        return
    if index < len(targets.WINDOWS):
        target['window'] = targets.WINDOWS[index][0]
    else:
        value = text(L(30078), target.get('window', ''))
        if value:
            target['window'] = value


# ------------------------------------------------------------------- artwork

def edit_artwork(item):
    """Standalone artwork editor (Edit Artwork). Returns the edited item or None."""
    work = copy.deepcopy(item)
    edit_artwork_in_place(work)
    return _finish(work, item, heading=L(30004))


def _core(string_id):
    """Kodi's own strings, so labels match its "Choose art" dialog in every language."""
    return xbmc.getLocalizedString(string_id)


# Cover types offered for folders even before any is set.
FOLDER_ART_TYPES = ('thumb', 'poster', 'landscape', 'fanart', 'clearlogo', 'banner')


def edit_folder_artwork(name, art, contents=()):
    """Edit Artwork for a folder. Returns the new art dict, or None when unchanged/cancelled.

    contents is [(label, art), ...] from storage.contents_art: what the folder holds, offered
    as "From Items in This Folder" for every cover type.
    """
    work = {'label': name, 'art': dict(art), 'info': {}}
    edit_artwork_in_place(work, suggested=FOLDER_ART_TYPES, contents=contents)
    edited = _finish(work, {'label': name, 'art': dict(art), 'info': {}}, heading=L(30004))
    return edited['art'] if edited else None


# Cover types ticked in advance by "Use as Artwork for", when the folder doesn't have them yet.
FOLDER_ART_DEFAULT_PICKS = ('poster', 'fanart', 'clearlogo')


def choose_folder_art_from(folder_name, folder_art, source_art):
    """Use as Artwork for <folder>: tick which of an entry's images become the folder's covers.

    Each folder cover type the entry has a suitable image for is listed with that image.
    Poster, fanart and clear logo start ticked unless the folder already has them, so a
    cover chosen earlier is only replaced on purpose. Returns {slot: url} of the changes,
    or None when cancelled, nothing was ticked or nothing would change.
    """
    offers = []
    for slot in FOLDER_ART_TYPES:
        match = artwork.best_match(source_art, slot)
        if match:
            offers.append((slot,) + match)
    if not offers:
        notify(L(30144))
        return None
    rows, preselect = [], []
    for index, (slot, key, url) in enumerate(offers):
        detail = artwork.label(key)
        current = folder_art.get(slot)
        if current == url:
            detail = L(30143) % detail  # already in use
        elif current:
            detail = L(30142) % detail  # replaces current
        elif slot in FOLDER_ART_DEFAULT_PICKS:
            preselect.append(index)
        rows.append(row(artwork.label(slot), detail, url))
    chosen = xbmcgui.Dialog().multiselect(L(30141) % plain(folder_name), rows,
                                          preselect=preselect, useDetails=True)
    if not chosen:
        return None
    changes = {offers[i][0]: offers[i][2] for i in chosen
               if folder_art.get(offers[i][0]) != offers[i][2]}
    return changes or None


def _contents_choices(key, contents, current):
    """Images from a folder's contents for one cover type: [(url, label, art key), ...].

    Images that suit the type come first (an episode's show poster for Poster, say), then
    every other image, so a different shape can still be chosen deliberately. Each image
    appears once, under the first entry that has it.
    """
    wanted = artwork.slot_keys(key)
    seen = {current} if current else set()
    fitting, other = [], []
    for label, art in contents:
        for art_key in wanted:
            url = art.get(art_key)
            if url and url not in seen:
                seen.add(url)
                fitting.append((url, label, art_key))
    for label, art in contents:
        for art_key in artwork.ordered_keys(art):
            url = art[art_key]
            if url and url not in seen:
                seen.add(url)
                other.append((url, label, art_key))
    return fitting + other


def _choose_from_contents(key, choices):
    heading = '%s: %s' % (L(30140), artwork.label(key))
    rows = [row(plain(label), artwork.label(art_key), url) for url, label, art_key in choices]
    index = _select(heading, rows)
    return choices[index][0] if index >= 0 else None


def edit_artwork_in_place(work, suggested=(), contents=()):
    """Art types first, then a chooser for the selected type - as Kodi's "Choose art" works.

    For library items the library's alternatives are offered too: types it has
    artwork for appear even when not set, and each type lists its remote art.
    'suggested' types are listed as "Not set" as well (used for folder covers), and
    'contents' adds "From Items in This Folder" (also for folder covers).
    """
    art = work['art']
    source = artwork.library_source(work['info'])
    library_types = list(dict.fromkeys(list(suggested) + artwork.available_art_types(source)))
    while True:
        keys = artwork.ordered_keys(art)
        unset = [key for key in library_types if key not in art]
        rows = [row(artwork.label(key), art[key], art[key]) for key in keys]
        rows += [row(artwork.label(key), L(30097)) for key in unset]
        rows.append(row(_core(13516), image=ICON_ADD))  # Add art type
        index = _select('%s: %s' % (L(30004), plain(work['label'])), rows)
        if index < 0:
            return
        if index < len(keys):
            _set_artwork(art, keys[index], source, contents)
        elif index < len(keys) + len(unset):
            _set_artwork(art, unset[index - len(keys)], source, contents)
        else:
            key = _choose_new_art_type(art, library_types)
            if key:
                _set_artwork(art, key, source, contents)


def _choose_new_art_type(art, library_types):
    known = list(library_types) + [key for key in artwork.PROBE_KEYS if key not in library_types]
    missing = [key for key in known if key not in art]
    rows = [row(artwork.label(key), key) for key in missing] + [row(L(30064))]
    index = _select(_core(13516), rows)
    if index < 0:
        return None
    if index < len(missing):
        return missing[index]
    value = text(L(30065))
    return value.strip().lower().replace(' ', '') if value else None


def _set_artwork(art, key, source, contents=()):
    """Kodi's order: Current art, Remote art..., Browse; then MetaFave's extras, No art last."""
    heading = '%s: %s' % (_core(13511), artwork.label(key))  # Choose art: Poster
    current = art.get(key, '')
    others = [other for other in artwork.ordered_keys(art) if other != key and art[other]]
    from_contents = _contents_choices(key, contents, current) if contents else []
    choices = []
    if current:
        choices.append((('current', None), row(_core(13512), image=current)))  # Current art
    for url, preview in artwork.available_art(source, key):
        if url != current:
            choices.append((('remote', url), row(_core(13513), image=preview)))  # Remote art
    choices.append((('browse', None), row(_core(1024))))  # Browse
    if from_contents:
        choices.append((('contents', None),
                        row(L(30140), L(30145) % len(from_contents), from_contents[0][0])))
    if others:
        choices.append((('reuse', None), row(L(30060))))
    choices.append((('enter', None), row(L(30062))))
    if current:
        choices.append((('remove', None), row(_core(13515))))  # No art
    index = _select(heading, [r for _, r in choices])
    if index < 0:
        return
    action, payload = choices[index][0]
    value = None
    if action == 'remote':
        value = payload
    elif action == 'contents':
        value = _choose_from_contents(key, from_contents)
    elif action == 'reuse':
        rows = [row(artwork.label(other), art[other], art[other]) for other in others]
        choice = _select(L(30080), rows)
        if choice >= 0:
            value = art[others[choice]]
    elif action == 'browse':
        default = current if current and '://' not in current else ''
        chosen = xbmcgui.Dialog().browse(2, heading, 'files', artwork.IMAGE_MASK, True, False, default)
        if chosen and chosen != default:
            value = chosen
    elif action == 'enter':
        value = text(heading, current)
    elif action == 'remove':
        art.pop(key, None)
    if value:
        art[key] = value


# ------------------------------------------------------------------ metadata

def _edit_info(work):
    info = work['info']
    while True:
        known = [key for key, _, _, _ in metadata.FIELDS if key in info]
        extra = sorted(key for key in info if key not in metadata.FIELD_TYPES)
        keys = known + extra
        rows = [row(_field_name(key), metadata.display(info[key])) for key in keys]
        rows.append(row(L(30066), image=ICON_ADD))
        index = _select('%s: %s' % (L(30043), plain(work['label'])), rows)
        if index < 0:
            return
        if index == len(keys):
            missing = [key for key, _, _, _ in metadata.FIELDS if key not in info]
            if not missing:
                continue
            choice = xbmcgui.Dialog().select(L(30066), [row(_field_name(k)) for k in missing])
            if choice >= 0:
                _edit_field(info, missing[choice])
            continue
        key = keys[index]
        action = xbmcgui.Dialog().contextmenu([L(30077), L(30069)])
        if action == 0:
            _edit_field(info, key)
        elif action == 1:
            info.pop(key, None)


def _field_name(key):
    sid = metadata.FIELD_LABELS.get(key)
    return L(sid) if sid else key


def _edit_field(info, key):
    kind = metadata.FIELD_TYPES.get(key, 'str')
    if key == 'mediatype':
        choices = list(metadata.MEDIA_TYPES)
        index = xbmcgui.Dialog().select(_field_name(key), choices)
        if index >= 0:
            info[key] = choices[index]
        return
    heading = _field_name(key)
    if kind == 'list':
        heading = '%s (%s)' % (heading, L(30076))
    raw = text(heading, metadata.display(info.get(key)), numeric=(kind == 'int'))
    value = metadata.parse(kind, raw)
    if value is not None:
        info[key] = value


# ---------------------------------------------------------------- properties

def _edit_properties(work):
    props = work['properties']
    while True:
        keys = sorted(props)
        rows = [row(key, str(props[key])) for key in keys] + [row(L(30067), image=ICON_ADD)]
        index = _select('%s: %s' % (L(30044), plain(work['label'])), rows)
        if index < 0:
            return
        if index == len(keys):
            name = text(L(30068))
            if name:
                value = text(name, props.get(name, ''))
                if value is not None:
                    props[name] = value
            continue
        key = keys[index]
        action = xbmcgui.Dialog().contextmenu([L(30077), L(30069)])
        if action == 0:
            value = text(key, props[key])
            if value is not None:
                props[key] = value
        elif action == 1:
            props.pop(key, None)
