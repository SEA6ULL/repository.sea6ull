import xbmc
import xbmcplugin
import sys
import os
from .params import Params
from .utils import play_video
from .menus import main_menu, build_menu, submenu_maintenance, backup_restore
from .build_install import build_install
from .maintenance import fresh_start, clear_packages, clear_cache_files
from .addonvar import addon
from .backup_restore import backup_build, restore_menu, restore_build, delete_backup, get_backup_folder, reset_backup_folder
from .focus_settings import tmdbh_mdblist_api

handle = int(sys.argv[1])

def router(paramstring):
    p = Params(paramstring)
    xbmc.log(str(p.get_params()),xbmc.LOGDEBUG)
    
    name = p.get_name()
    name2 = p.get_name2()
    version = p.get_version()
    url = p.get_url()
    mode = p.get_mode()
    icon = p.get_icon()
    fanart = p.get_fanart()
    description = p.get_description()
    
    if handle >= 0:
        xbmcplugin.setContent(handle, 'files')

    if mode is None:
        main_menu()
    
    elif mode == 1:
        build_menu()
    
    elif mode == 2:
        play_video(name, url, icon, description)
    
    elif mode == 3:
        build_install(name, name2, version, url)
    
    elif mode == 4:
        fresh_start(standalone=True)
    
    elif mode == 5:
        submenu_maintenance()
    
    elif mode == 6:
        clear_packages()
    
    elif mode == 7:
        clear_cache_files()
    
    
    elif mode == 9:
        addon.openSettings()
    
    
    elif mode == 12:
        backup_restore()
    
    elif mode == 13:
        backup_build()
    
    elif mode == 14:
        restore_menu()
    
    elif mode == 15:
        restore_build(url)
    
    elif mode == 16:
        get_backup_folder()
    
    elif mode == 17:
        reset_backup_folder()

    elif mode == 18:
        os._exit(1)

    elif mode == 24:
        xbmc.executebuiltin(url)
    
    elif mode == 25:
        from .quick_log import log_viewer
        log_viewer()

        

    elif mode == 30:
        delete_backup(url)
        
# Focus Add-on settings
    elif mode == 50:
        tmdbh_mdblist_api()
        
    elif mode == 100:
        from resources.lib.GUIcontrol import notify
        message = notify.get_notify()[1]
        notify.notification(message)

    # Actions fired from a context menu run via RunPlugin() and get handle -1.
    # Calling endOfDirectory() on those logs an error, so only close real listings.
    if handle >= 0:
        xbmcplugin.endOfDirectory(handle)
