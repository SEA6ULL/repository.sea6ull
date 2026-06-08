import sys
import xbmc
import xbmcaddon
import xbmcgui
import xbmcvfs

ADDON        = xbmcaddon.Addon()
CWD          = xbmcvfs.translatePath(ADDON.getAddonInfo('path'))
ADDONID      = ADDON.getAddonInfo('id')
ADDONVERSION = ADDON.getAddonInfo('version')

from lib.utils import *

if __name__ == '__main__':
    log('script version %s started' % ADDONVERSION)

    args = sys.argv[1:] if len(sys.argv) > 1 else []

    if args and args[0] == 'rebuild_cache':
        log('rebuild_cache triggered')
        slideshow_type = ADDON.getSettingInt('type')
        if slideshow_type == 3:
            plugin_path = load_plugin_path()
            # Don't cache the default addons:// root path — only real plugin:// paths
            if plugin_path and plugin_path.startswith('plugin://'):
                xbmcgui.Dialog().notification(
                    ADDON.getAddonInfo('name'),
                    ADDON.getLocalizedString(30055),
                    xbmcgui.NOTIFICATION_INFO, 3000
                )
                cache_size = ADDON.getSettingInt('cache_size')
                # Full rebuild: wipe existing cache including URL map
                _clear_plugin_cache(full=True)
                count = build_plugin_cache(plugin_path, cache_size, startup_delay=False)
                xbmcgui.Dialog().notification(
                    ADDON.getAddonInfo('name'),
                    ADDON.getLocalizedString(30056) + ' (%d images)' % count,
                    xbmcgui.NOTIFICATION_INFO, 3000
                )
            else:
                xbmcgui.Dialog().ok(
                    ADDON.getAddonInfo('name'),
                    'No plugin path set. Please browse and select a folder inside a video plugin first.'
                )
        else:
            xbmcgui.Dialog().ok(
                ADDON.getAddonInfo('name'),
                'Source is not set to Video Plugin. Switch source type first.'
            )

    else:
        log('screensaver launch')
        from lib import gui
        screensaver_gui = gui.Screensaver('script-python-slideshow.xml', CWD, 'default')
        screensaver_gui.doModal()
        del screensaver_gui

log('script stopped')
