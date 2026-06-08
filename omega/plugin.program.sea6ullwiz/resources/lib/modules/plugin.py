import xbmc
import xbmcplugin
import sys
import os
from .params import Params
from .utils import play_video
from .menus import main_menu, build_menu, submenu_maintenance, backup_restore, restore_gui_skin
from .build_install import build_install
from .maintenance import fresh_start, clear_packages, clear_thumbnails, advanced_settings_k20, advanced_settings_k21
from .whitelist import get_whitelist
from .addonvar import addon
from .save_data import restore_gui, restore_skin, backup_gui_skin_user, restore_gui_user, restore_skin_user
from .backup_restore import backup_build, restore_menu, restore_build, get_backup_folder, reset_backup_folder
from .focus_settings import tmdbh_mdblist_api, rurl_settings_rd, rurl_settings_pm, rurl_settings_ad, am_auth_nondebrid, am_auth_meta, am_supported_furk, am_supported_easy, am_supported_file, am_supported_meta, am_supported_trakt, am_supported_debrid, am_manage, am_backup

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
        clear_thumbnails()
    
    elif mode == 8:
        advanced_settings_k20()
    
    elif mode == 9:
        addon.openSettings()
    
    elif mode == 11:
        get_whitelist()
    
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

    elif mode == 19:
        restore_gui_skin()

    elif mode == 20:
        restore_gui()

    elif mode == 21:
        restore_skin()

    elif mode == 24:
        xbmc.executebuiltin(url)
    
    elif mode == 25:
        from .quick_log import log_viewer
        log_viewer()

    elif mode == 26:
        advanced_settings_k21()

    elif mode == 27:
        backup_gui_skin_user()

    elif mode == 28:
        restore_gui_user()
        
    elif mode == 29:
        restore_skin_user()
        
# Focus Add-on settings
    elif mode == 50:
        tmdbh_mdblist_api()
    elif mode == 51:
        rurl_settings_rd()
    elif mode == 52:
        rurl_settings_pm()
    elif mode == 53:
        rurl_settings_ad()
    elif mode == 54:
        am_auth_nondebrid()
    elif mode == 55:
        am_auth_meta()
    elif mode == 56:
        am_supported_furk()
    elif mode == 57:
        am_supported_easy()
    elif mode == 58:
        am_supported_file()
    elif mode == 59:
        am_supported_meta()
    elif mode == 60:
        am_supported_trakt()
    elif mode == 61:
        am_supported_debrid()
    elif mode == 62:
        am_manage()
    elif mode == 63:
        am_backup()
        
    elif mode == 100:
        from resources.lib.GUIcontrol import notify
        message = notify.get_notify()[1]
        notify.notification(message)
        
    xbmcplugin.endOfDirectory(handle)
