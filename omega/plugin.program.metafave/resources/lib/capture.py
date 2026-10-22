# SPDX-License-Identifier: GPL-3.0-only
# MetaFave is authored by SEA6ULL / Claude / GPT and licensed under GPL-3.0.
"""Turn the item the context menu was opened on into a MetaFave item."""

import xbmc
import xbmcaddon
import xbmcgui

from . import artwork, metadata, targets
from .common import L, plain


def _info(name):
    return xbmc.getInfoLabel('ListItem.%s' % name)


def _is_folder(listitem):
    try:
        return bool(listitem.isFolder())
    except Exception:
        return xbmc.getCondVisibility('ListItem.IsFolder')


def source_addon_name(path):
    if not path.lower().startswith('plugin://'):
        return ''
    addon_id = path[len('plugin://'):].split('/', 1)[0].split('?', 1)[0]
    try:
        return xbmcaddon.Addon(addon_id).getAddonInfo('name')
    except Exception:
        return ''


def capture(listitem):
    """Return (item, hints) or (None, None) when the item has nothing to launch.

    Must run before any dialog opens, while the item is still focused.
    """
    path = listitem.getPath() or _info('FolderPath') or _info('FileNameAndPath')
    if not path:
        return None, None
    label = listitem.getLabel() or _info('Label')
    playable = (listitem.getProperty('IsPlayable') or _info('Property(IsPlayable)')).lower() == 'true'
    window = targets.media_window_name(xbmcgui.getCurrentWindowId())

    info = metadata.capture(listitem)
    item = {
        'label': label,
        'label2': listitem.getLabel2(),
        'target': targets.derive(path, _is_folder(listitem), playable,
                                 _info('Property(Addon.ID)'), window),
        'art': artwork.capture(listitem, info.get('mediatype', ''), info.get('dbid', 0)),
        'info': info,
        'properties': metadata.capture_properties(listitem, info.get('mediatype', ''),
                                                  info.get('dbid', 0)),
    }
    hints = {
        'container': plain(xbmc.getInfoLabel('Container.FolderName')),
        'source': plain(source_addon_name(path)),
    }
    return item, hints


def suggest_folder_name(item, hints):
    """Suggested name for a folder created while adding an item.

    Episodes and seasons suggest their show, songs and albums their artist;
    otherwise the folder the user is browsing, then the source add-on, then
    the item's own name.
    """
    info = item.get('info', {})
    mediatype = info.get('mediatype')
    if mediatype in ('episode', 'season') and info.get('tvshowtitle'):
        return plain(info['tvshowtitle'])
    if mediatype in ('song', 'album') and info.get('artist'):
        return plain(info['artist'])
    label = plain(item.get('label'))
    for candidate in (hints.get('container'), hints.get('source'), label):
        if candidate and candidate != label:
            return candidate
    return label or L(30032)
