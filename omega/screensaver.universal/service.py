import os
import re
import xbmc
import xbmcaddon
import xbmcgui
import xbmcvfs

ADDON    = xbmcaddon.Addon()
ADDONID  = ADDON.getAddonInfo('id')
PROFILE  = xbmcvfs.translatePath(ADDON.getAddonInfo('profile'))
PATHFILE = os.path.join(PROFILE, 'plugin_path.txt')
SETTINGSFILE = os.path.join(PROFILE, 'settings.xml')

def log(txt):
    xbmc.log('%s: [service] %s' % (ADDONID, txt), xbmc.LOGDEBUG)

def read_trigger_from_file():
    try:
        if not xbmcvfs.exists(SETTINGSFILE):
            return False
        f = xbmcvfs.File(SETTINGSFILE)
        content = f.read()
        f.close()
        match = re.search(r'<setting id="plugin_browse_trigger">(.*?)</setting>', content)
        if match:
            return match.group(1).strip().lower() == 'true'
    except Exception as e:
        log('read_trigger error: %s' % str(e))
    return False

def clear_trigger():
    try:
        xbmcaddon.Addon().setSettingBool('plugin_browse_trigger', False)
    except Exception as e:
        log('clear_trigger error: %s' % str(e))

def save_path(path):
    if not xbmcvfs.exists(PROFILE):
        xbmcvfs.mkdir(PROFILE)
    f = xbmcvfs.File(PATHFILE, 'w')
    f.write(path)
    f.close()

def do_browse():
    ADDON2 = xbmcaddon.Addon()
    log('opening browse dialog')
    # Close settings via UI thread message and wait for it to process
    xbmc.executebuiltin('Dialog.Close(AddonSettings,true)')
    xbmc.sleep(1000)
    result = xbmcgui.Dialog().browse(
        type=0,
        heading=ADDON2.getLocalizedString(30041),
        shares='files',
        defaultt='addons://sources/video/',
        enableMultiple=False
    )
    log('browse result: %s' % str(result))
    clear_trigger()

    if result and result.startswith('plugin://'):
        save_path(result)
        log('plugin_path saved: %s' % result)
        xbmcgui.Dialog().notification(
            ADDON2.getAddonInfo('name'),
            'Building image cache...',
            xbmcgui.NOTIFICATION_INFO, 3000
        )
        try:
            import sys
            sys.path.insert(0, xbmcvfs.translatePath(
                'special://home/addons/screensaver.universal'))
            from lib.utils import build_plugin_cache
            cache_size = ADDON2.getSettingInt('cache_size')
            count = build_plugin_cache(result, cache_size, startup_delay=False)
            log('cache built: %d images' % count)
            if count > 0:
                xbmcgui.Dialog().notification(
                    ADDON2.getAddonInfo('name'),
                    'Cache ready: %d images' % count,
                    xbmcgui.NOTIFICATION_INFO, 4000
                )
            else:
                xbmcgui.Dialog().notification(
                    ADDON2.getAddonInfo('name'),
                    'No 1920px-wide images found from plugin',
                    xbmcgui.NOTIFICATION_WARNING, 5000
                )
        except Exception as e:
            log('cache build error: %s' % str(e))

if __name__ == '__main__':
    log('service started v%s' % ADDON.getAddonInfo('version'))
    monitor = xbmc.Monitor()

    while not monitor.abortRequested():
        if read_trigger_from_file():
            log('file trigger=true detected')
            do_browse()

        if monitor.waitForAbort(1):
            break

    log('service stopped')
