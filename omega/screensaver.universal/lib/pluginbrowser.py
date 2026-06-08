# Plugin path browser for Universal Screensaver
# Shows ONLY video addon plugin:// paths — no network shares, no local drives.
# Navigate with d-pad. "Use This Folder" saves the path to addon settings.

import json
import xbmc
import xbmcgui
import xbmcaddon
from lib.utils import save_plugin_path, load_plugin_path

ADDON   = xbmcaddon.Addon()
ADDONID = ADDON.getAddonInfo('id')

# Control IDs in plugin-browser.xml
CONTROL_LIST     = 50
CONTROL_USE      = 51
CONTROL_BACK     = 52
CONTROL_PATH_LBL = 53
CONTROL_HEADING  = 54


def _log(txt):
    xbmc.log('%s: [browser] %s' % (ADDONID, txt), xbmc.LOGDEBUG)


def _rpc(method, params):
    """Execute a JSON-RPC call and return the parsed result dict."""
    req = json.dumps({'jsonrpc': '2.0', 'method': method, 'params': params, 'id': 1})
    raw = xbmc.executeJSONRPC(req)
    try:
        return json.loads(raw)
    except Exception:
        return {}


def _get_video_addons():
    """Return installed enabled video addons as browser items."""
    resp = _rpc('Addons.GetAddons', {
        'type': 'xbmc.addon.video',
        'enabled': True,
        'properties': ['name', 'addonid']
    })
    items = []
    for a in resp.get('result', {}).get('addons', []):
        items.append({
            'label':    a.get('name', a.get('addonid', '')),
            'path':     'plugin://%s/' % a.get('addonid', ''),
            'isdir':    True,
        })
    return sorted(items, key=lambda x: x['label'].lower())


def _get_dir(path):
    """
    List a plugin:// directory via Files.GetDirectory.
    Returns list of {'label', 'path', 'isdir'} dicts, dirs first.
    """
    resp = _rpc('Files.GetDirectory', {
        'directory': path,
        'sort': {'method': 'label', 'order': 'ascending'}
    })
    raw = resp.get('result', {}).get('files', [])
    dirs  = []
    files = []
    for item in raw:
        entry = {
            'label': item.get('label', ''),
            'path':  item.get('file', ''),
            'isdir': item.get('filetype') == 'directory',
        }
        if entry['isdir']:
            dirs.append(entry)
        else:
            files.append(entry)
    return dirs + files


class PluginBrowser(xbmcgui.WindowXMLDialog):
    """
    Remote-navigable browser restricted to video plugin:// paths.
    Top level = installed video addons.
    OK/Select on a folder = enter it.
    Back key / Back button = go up.
    Use This Folder = save current path to plugin_path setting and close.
    """

    def __init__(self, *args, **kwargs):
        self._stack = []          # list of (path, scroll_pos) tuples for history
        self._path  = None        # current path; None = top level
        self._items = []          # current listing

    def onInit(self):
        try:
            self.getControl(CONTROL_HEADING).setLabel(
                ADDON.getAddonInfo('name') + '  –  ' +
                ADDON.getLocalizedString(30041)
            )
        except Exception:
            pass
        self._show('')

    # ------------------------------------------------------------------
    # Core navigation
    # ------------------------------------------------------------------

    def _show(self, path):
        """Load and display the listing for path ('' = addon root)."""
        self._path  = path
        self._items = _get_video_addons() if path == '' else _get_dir(path)

        _log('showing path=%r  items=%d' % (path, len(self._items)))

        # Update path label
        try:
            lbl = path if path else ADDON.getLocalizedString(30060)
            self.getControl(CONTROL_PATH_LBL).setLabel(lbl)
        except Exception:
            pass

        # Populate list control
        ctrl = self.getControl(CONTROL_LIST)
        ctrl.reset()
        for item in self._items:
            li = xbmcgui.ListItem(label=item['label'])
            if item['isdir']:
                li.setLabel2(u'\u25ba')   # ►
            ctrl.addItem(li)

        # Back button visible only when we have history
        try:
            self.getControl(CONTROL_BACK).setVisible(bool(self._stack))
        except Exception:
            pass

        # Focus
        if self._items:
            self.setFocus(ctrl)
            ctrl.selectItem(0)
        else:
            try:
                self.setFocus(self.getControl(CONTROL_USE))
            except Exception:
                pass

    def _enter(self, idx):
        """Descend into the directory at index idx."""
        item = self._items[idx]
        if not item['isdir']:
            return
        ctrl = self.getControl(CONTROL_LIST)
        self._stack.append((self._path, ctrl.getSelectedPosition()))
        self._show(item['path'])

    def _back(self):
        """Go up one level."""
        if self._stack:
            prev_path, prev_pos = self._stack.pop()
            self._show(prev_path)
            try:
                self.getControl(CONTROL_LIST).selectItem(prev_pos)
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Event handlers
    # ------------------------------------------------------------------

    def onClick(self, control_id):
        if control_id == CONTROL_LIST:
            idx = self.getControl(CONTROL_LIST).getSelectedPosition()
            if 0 <= idx < len(self._items):
                self._enter(idx)

        elif control_id == CONTROL_USE:
            if self._path:
                save_plugin_path(self._path)
                _log('plugin_path saved: %s' % self._path)
            self.close()

        elif control_id == CONTROL_BACK:
            self._back()

    def onAction(self, action):
        aid = action.getId()
        # Standard Kodi back/escape action IDs
        if aid in (92, 10, 9, 110):   # Back, PreviousMenu, ParentDir, NavBack
            if self._stack:
                self._back()
            else:
                self.close()
