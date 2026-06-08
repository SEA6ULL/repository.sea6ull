import os
import xbmc
import xbmcgui
import xbmcvfs
import xbmcaddon
from .downloader import Downloader
import json
from datetime import datetime
import time
import sqlite3
from zipfile import ZipFile
from .save_data import save_backup_restore
from .maintenance import fresh_start, clean_backups, truncate_tables
from .addonvar import dp, dialog, zippath, addon_name, addon_icon, addon_id, home, setting_set, local_string, addons_db
from pathlib import Path
import shutil
from .colors import colors

addons_path = Path(xbmcvfs.translatePath('special://home/addons'))
user_data = Path(xbmcvfs.translatePath('special://userdata'))
color1 = colors.color_text1
color2 = colors.color_text2
    

def build_install(name, name2, version, url):
    # Ready to install, Cancel, Continue
    if not dialog.yesno(color2(name), color2(local_string(30028)), nolabel=local_string(30029), yeslabel=local_string(30030)):
        return
    
    download_build(name, url)
    save_backup_restore('backup')
    fresh_start()
    extract_build()
    save_backup_restore('restore')
    clean_backups()
    setting_set('buildname', name2)
    setting_set('buildversion', version)
    setting_set('update_passed', 'false')
    setting_set('firstrun', 'true')
    enable_wizard()
    xbmcgui.Dialog().notification(addon_name, 'Finishing up install, please wait!', addon_icon, 6000)
    if name2 == 'ELEMico':
        dialog.ok(addon_name, local_string(30036))  # Install Complete
        os._exit(1)
    else:
        truncate_tables()
        dialog.ok(addon_name, local_string(30036))  # Install Complete
        os._exit(1)

def download_build(name, url):
    if os.path.exists(zippath):
        os.unlink(zippath)
    d = Downloader(url)
    if 'dropbox' in url:
        if not xbmc.getCondVisibility('System.HasAddon(script.module.requests)'):
            xbmc.executebuiltin('InstallAddon(script.module.requests)')
            dialog.ok(color2(name), color2(local_string(30033)))  # Installing Requests
            return
        d.download_build(name, zippath, meth='requests')
    else:
        d.download_build(name, zippath, meth='urllib')

def extract_build():
    if os.path.exists(zippath):
        dp.create(addon_name, local_string(30034))  # Extracting files
        counter = 1
        with ZipFile(zippath, 'r') as z:
            files = z.infolist()
            for file in files:
                filename = file.filename
                filename_path = os.path.join(home, filename)
                progress_percentage = int(counter/len(files)*100)
                try:
                    if not os.path.exists(filename_path) or 'Addons33.db' in filename:
                        z.extract(file, home)
                except Exception as e:
                    xbmc.log(f'Error extracting {filename} - {e}', xbmc.LOGINFO)
                dp.update(progress_percentage, f'{local_string(30034)}...\n{progress_percentage}%\n{filename}')
                counter += 1
        dp.update(100, local_string(30035))  # Done Extracting
        xbmc.sleep(500)
        dp.close()
        os.unlink(zippath)

def enable_wizard():
    try:
        timestamp = str(datetime.now())[:-7]

        con = sqlite3.connect(addons_db)
        cursor = con.cursor()
        cursor.execute('INSERT or IGNORE into installed (addonID , enabled, installDate) VALUES (?,?,?)', (addon_id, 1, timestamp,))

        cursor.execute('UPDATE installed SET enabled = ? WHERE addonID = ? ', (1, addon_id,))
        con.commit()
    except sqlite3.Error as e:
        xbmc.log('There was an error writing to the database - %s' %e, xbmc.LOGINFO)
        return
    finally:
        try:
            if con:
                con.close()
        except UnboundLocalError as e:
            xbmc.log('%s: There was an error connecting to the database - %s' % (xbmcaddon.Addon().getAddonInfo('name'), e), xbmc.LOGINFO)

def repo_rollback():
    import sqlite3
    db = user_data / 'Database' / 'Addons33.db'
    try:
        con = sqlite3.connect(db)
        cursor = con.cursor()
        cursor.execute(
            """UPDATE repo SET version = 0 WHERE addonID = "repository.xbmc.org";
""",
        )
        con.commit()
    except sqlite3.Error as e:
        xbmc.log(f"Failed to write data to the sqlite table: {e}", xbmc.LOGINFO)
    finally:
        if con:
            con.close()

def repo_rollback_profile():
    import sqlite3
    db = user_data / 'profiles' / 'Sports Night' / 'Database' / 'Addons33.db'
    try:
        con = sqlite3.connect(db)
        cursor = con.cursor()
        cursor.execute(
            """UPDATE repo SET version = 0 WHERE addonID = "repository.xbmc.org";
""",
        )
        con.commit()
    except sqlite3.Error as e:
        xbmc.log(f"Failed to write data to the sqlite table: {e}", xbmc.LOGINFO)
    finally:
        if con:
            con.close()
