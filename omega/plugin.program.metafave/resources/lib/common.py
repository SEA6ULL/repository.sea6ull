# SPDX-License-Identifier: GPL-3.0-only
# MetaFave is authored by SEA6ULL / Claude / GPT and licensed under GPL-3.0.
"""Shared constants and small helpers. Kept import-light for fast widget loads."""

import json
import os
import re
from urllib.parse import urlencode

import xbmc
import xbmcaddon
import xbmcgui
import xbmcvfs

ADDON = xbmcaddon.Addon('plugin.program.metafave')
ADDON_ID = ADDON.getAddonInfo('id')
ADDON_NAME = ADDON.getAddonInfo('name')
ADDON_ICON = ADDON.getAddonInfo('icon')
ADDON_FANART = ADDON.getAddonInfo('fanart')
ADDON_PATH = xbmcvfs.translatePath(ADDON.getAddonInfo('path'))
PROFILE_PATH = xbmcvfs.translatePath(ADDON.getAddonInfo('profile'))
PLUGIN_URL = 'plugin://%s/' % ADDON_ID

_ICON_DIR = os.path.join(ADDON_PATH, 'resources', 'media', 'icons')
ICON_FOLDER = os.path.join(_ICON_DIR, 'folder-simple.png')
ICON_CREATE = os.path.join(_ICON_DIR, 'folder-plus.png')
ICON_KODI_FAVORITES = os.path.join(_ICON_DIR, 'folder-simple-star.png')
ICON_ADD = os.path.join(_ICON_DIR, 'plus-circle.png')
ICON_REPAIR = os.path.join(_ICON_DIR, 'wrench.png')
ICON_NEXT = os.path.join(_ICON_DIR, 'next-page.png')
ICON_PREVIOUS = os.path.join(_ICON_DIR, 'previous-page.png')

_BUILTIN_ICONS = frozenset((ICON_FOLDER, ICON_CREATE, ICON_KODI_FAVORITES, ICON_ADD,
                            ICON_REPAIR, ICON_NEXT, ICON_PREVIOUS))


def icon_art(icon):
    """Use a crop-safe square in every built-in slot; skins may prefer poster
    even in square layouts. User-selected artwork is handled by the caller.
    """
    art = {'icon': icon, 'thumb': icon}
    if icon in _BUILTIN_ICONS:
        art['poster'] = icon
    return art



_FORMAT_TAGS = re.compile(
    r'\[/?(?:COLOR|B|I|LIGHT|UPPERCASE|LOWERCASE|CAPITALIZE|CR|TABS)[^\]]*\]', re.IGNORECASE)


def L(string_id):
    return ADDON.getLocalizedString(string_id)


def log(message, level=xbmc.LOGDEBUG):
    xbmc.log('[%s] %s' % (ADDON_ID, message), level)


def plugin_url(**params):
    params = {key: value for key, value in params.items() if value is not None}
    return PLUGIN_URL + ('?' + urlencode(params) if params else '')


def plain(text):
    """Strip Kodi label formatting tags (for headings and suggestions)."""
    return _FORMAT_TAGS.sub('', text or '').strip()


def notify(message, heading=None, time_ms=3000):
    """Always shown; used for errors."""
    xbmcgui.Dialog().notification(heading or ADDON_NAME, message, ADDON_ICON, time_ms, False)


class Settings(object):
    """Live settings. Read through a fresh Addon object, because with the reused
    language invoker an object created at import time can hold stale values."""

    def __init__(self):
        self._addon = xbmcaddon.Addon('plugin.program.metafave')

    def _bool(self, key, default):
        try:
            return self._addon.getSettingBool(key)
        except Exception:
            return default

    @property
    def fanart_fallback(self):
        return self._bool('fanart_fallback', True)

    @property
    def show_kodi_favorites(self):
        return self._bool('show_kodi_favorites', True)

    @property
    def library_indicators(self):
        return self._bool('library_indicators', True)

    @property
    def mdblist_indicators(self):
        return self._bool('mdblist_indicators', False)

    @property
    def mdblist_api_key(self):
        return self._addon.getSetting('mdblist_api_key').strip()

    @property
    def paginate_folders(self):
        return self._bool('paginate_folders', True)

    @property
    def items_per_page(self):
        from .paging import positive
        return positive(self._addon.getSetting('items_per_page'), 20)

    @property
    def import_skip_duplicates(self):
        return self._bool('import_skip_duplicates', True)

    @property
    def notifications(self):
        return self._bool('notifications', True)

    @property
    def default_content(self):
        value = self._addon.getSetting('default_content')
        return '' if value in ('', 'auto') else value


def confirm(message):
    """Confirmation notification, shown only when enabled in settings."""
    if Settings().notifications:
        notify(message)


def refresh_container():
    xbmc.executebuiltin('Container.Refresh')


def jsonrpc(method, params=None):
    request = {'jsonrpc': '2.0', 'id': 1, 'method': method}
    if params:
        request['params'] = params
    try:
        response = json.loads(xbmc.executeJSONRPC(json.dumps(request)))
    except (TypeError, ValueError) as exc:
        log('JSON-RPC %s failed: %s' % (method, exc), xbmc.LOGWARNING)
        return None
    if 'error' in response:
        log('JSON-RPC %s error: %s' % (method, response['error']), xbmc.LOGDEBUG)
        return None
    return response.get('result')
