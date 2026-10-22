# SPDX-License-Identifier: GPL-3.0-only
# MetaFave is authored by SEA6ULL / Claude / GPT and licensed under GPL-3.0.
"""Kodi's built-in favorites, read straight from favourites.xml.

The file holds each favorite's exact Kodi command, which JSON-RPC does not
expose. Files are read in Kodi's own order (system first, then the profile's),
skipping duplicate commands, so the list matches Kodi's Favorites window.
Read-only: MetaFave never changes Kodi's favorites.
"""

import os
import re
import xml.etree.ElementTree as ElementTree

import xbmc
import xbmcvfs

from . import targets
from .common import log

FOLDER_ID = 'kodi_favorites'

_EXEC = re.compile(r'^\s*([A-Za-z][\w.]*)\s*\((.*)\)\s*$', re.DOTALL)

_FILES = ('special://xbmc/system/favourites.xml', 'special://profile/favourites.xml')


def load():
    """Return Kodi favorites as MetaFave items, in Kodi's order."""
    items, seen = [], set()
    for path in _FILES:
        real_path = xbmcvfs.translatePath(path)
        if not os.path.exists(real_path):
            continue
        try:
            root = ElementTree.parse(real_path).getroot()
        except (ElementTree.ParseError, OSError) as exc:
            log('Could not read %s: %s' % (path, exc), xbmc.LOGWARNING)
            continue
        if root.tag != 'favourites':
            continue
        for element in root.findall('favourite'):
            name = element.get('name')
            command = (element.text or '').strip()
            if not name or not command or command in seen:
                continue
            seen.add(command)
            thumb = element.get('thumb') or ''
            items.append({
                'label': name,
                'label2': '',
                'target': targets.from_command(command),
                'art': {'thumb': thumb, 'icon': thumb} if thumb else {},
                'info': {},
                'properties': {},
            })
    return items


# ---------------------------------------------------------------- Go to Source

_ADDON_LISTS = (  # (add-on id prefix, window, Kodi's add-on list for that type)
    ('plugin.video.', 'videos', 'addons://sources/video/'),
    ('plugin.audio.', 'music', 'addons://sources/audio/'),
    ('plugin.image.', 'pictures', 'addons://sources/image/'),
)
_PROGRAM_ADDONS = ('programs', 'addons://sources/executable/')


def _activate(window, path):
    return 'ActivateWindow(%s,"%s",return)' % (window, path.replace('"', '\\"'))


def _addon_list(addon_id):
    for prefix, window, path in _ADDON_LISTS:
        if addon_id.startswith(prefix):
            return _activate(window, path)
    return _activate(*_PROGRAM_ADDONS)


def _parent(path):
    """Containing folder of a non-plugin path ('' when there is none)."""
    trimmed = path.rstrip('/\\')
    cut = max(trimmed.rfind('/'), trimmed.rfind('\\'))
    return trimmed[:cut + 1] if cut >= 0 else ''


def source_command(item):
    """Kodi command that opens where a favorite's original item lives, or ''.

    From there, Add to MetaFave captures the real item with all of its artwork.
    """
    target = item['target']
    kind, path = target.get('kind'), target.get('path', '')
    if kind == 'folder':
        return _location(path, target.get('window'))
    match = _EXEC.match(path) if kind == 'command' else None
    if not match:
        return ''
    name, params = match.group(1).lower(), targets.split_params(match.group(2))
    first = params[0] if params else ''
    if name == 'playmedia' and first:
        return _location(first, None)
    if name in ('runaddon', 'runscript') and first and '/' not in first and '\\' not in first:
        return _addon_list(first)
    return ''


def _location(path, window):
    low = path.lower()
    if low.startswith('plugin://'):
        addon_id = path[len('plugin://'):].split('/', 1)[0].split('?', 1)[0]
        return _activate(window or targets.guess_window(path), 'plugin://%s/' % addon_id)
    if low.startswith('pvr://'):
        return 'ActivateWindow(%s)' % ('RadioChannels' if '/radio/' in low else 'TVChannels')
    parent = _parent(path)
    if not parent:
        return ''
    return _activate(window or targets.guess_window(path), parent)
