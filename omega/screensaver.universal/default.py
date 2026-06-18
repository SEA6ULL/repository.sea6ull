import sys
import xbmc
import xbmcaddon
import xbmcgui
import xbmcvfs

ADDON        = xbmcaddon.Addon()
CWD          = xbmcvfs.translatePath(ADDON.getAddonInfo('path'))
ADDONID      = ADDON.getAddonInfo('id')
ADDONVERSION = ADDON.getAddonInfo('version')

# When the addon is updated in place while Kodi is still running, Python may
# keep the pre-update copies of our own modules cached in sys.modules. A freshly
# pressed button re-runs this file, but `import` would then reuse the stale
# library code. Drop our own modules from the cache so the new code is loaded.
for _m in [m for m in list(sys.modules) if m == 'lib' or m.startswith('lib.')]:
    del sys.modules[_m]

from lib.utils import *
from lib.utils import _clear_plugin_cache

if __name__ == '__main__':
    log('script version %s started' % ADDONVERSION)

    args = sys.argv[1:] if len(sys.argv) > 1 else []

    if args and args[0] == 'rebuild_cache':
        log('rebuild_cache triggered')
        try:
            # RunScript launches this file as a separate process from the GUI
            # that holds the settings dialog. A value the user changes in the
            # dialog lives only in that dialog's in-memory buffer until Kodi
            # commits it to settings.xml — which it does when the dialog closes.
            # The rebuild button therefore uses <close>true</close> so the dialog
            # commits before this script runs. We give the write a brief moment
            # to land on disk, then read the freshly-committed values.
            xbmc.sleep(300)
            addon = xbmcaddon.Addon()
            slideshow_type = addon.getSettingInt('type')
            if slideshow_type == 3:
                plugin_path = load_plugin_path()
                # Don't cache the default addons:// root path — only real plugin:// paths
                if plugin_path and plugin_path.startswith('plugin://'):
                    # Fire the "rebuilding" notification immediately so the user
                    # always gets visible feedback that the button did something.
                    xbmcgui.Dialog().notification(
                        addon.getAddonInfo('name'),
                        addon.getLocalizedString(30055),
                        xbmcgui.NOTIFICATION_INFO, 3000
                    )
                    cache_size = addon.getSettingInt('cache_size')
                    # Manual rebuild does a full wipe first: delete every cached
                    # image and the URL map, then build fresh. This guarantees a
                    # clean cache with no leftovers from older naming schemes.
                    _clear_plugin_cache(full=True)
                    count = build_plugin_cache(
                        plugin_path, cache_size, startup_delay=False)
                    xbmcgui.Dialog().notification(
                        addon.getAddonInfo('name'),
                        addon.getLocalizedString(30056) + ' (%d images)' % count,
                        xbmcgui.NOTIFICATION_INFO, 3000
                    )
                else:
                    xbmcgui.Dialog().ok(
                        addon.getAddonInfo('name'),
                        'No plugin path set. Please browse and select a folder inside a video plugin first.'
                    )
            else:
                xbmcgui.Dialog().ok(
                    addon.getAddonInfo('name'),
                    'Source is not set to Video Plugin. Switch source type first.'
                )
        except Exception as e:
            # Never fail silently — surface the error to the user and the log.
            log('rebuild_cache error: %s' % str(e))
            import traceback
            log(traceback.format_exc())
            try:
                xbmcgui.Dialog().notification(
                    ADDON.getAddonInfo('name'),
                    'Cache rebuild failed: %s' % str(e),
                    xbmcgui.NOTIFICATION_ERROR, 5000
                )
            except Exception:
                pass

    else:
        log('screensaver launch')
        from lib import gui
        screensaver_gui = gui.Screensaver('script-python-slideshow.xml', CWD, 'default')
        screensaver_gui.doModal()
        del screensaver_gui

log('script stopped')
