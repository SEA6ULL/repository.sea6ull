import os
import re
import shutil
import sqlite3
import xbmc
import xbmcaddon
import xbmcgui
from .skinSwitch import swapSkins
from .save_data import save_backup_restore
from .utils import log
from .cache_paths import tmdbh_cache_dirs
from .addonvar import currSkin, user_path, db_path, data_path, addon_name, textures_db, dialog, dp, xbmcPath, packages, setting_set, addon_icon, local_string, addons_db, addon_id
from uservar import excludes

# Folders and files a wipe must leave alone. Matched by name anywhere under the
# Kodi path. EXCLUDES_INSTALL applies when fresh_start runs as part of a build
# install; EXCLUDES_FRESH applies to the standalone Fresh Start menu item.
EXCLUDES_FRESH = [addon_id, 'Addons33.db', 'kodi.log', 'script.module.certifi',
                  'script.module.chardet', 'script.module.idna',
                  'script.module.requests', 'script.module.urllib3', 'repository.709']
EXCLUDES_INSTALL = excludes + [addon_id, 'Addons33.db', 'packages', 'backups', 'repository.709']

def purge_db(db):
    if os.path.exists(db):
        try:
            conn = sqlite3.connect(db)
            cur = conn.cursor()
        except Exception as e:
            xbmc.log("DB Connection Error: %s" % str(e), xbmc.LOGDEBUG)
            return False
    else: 
        xbmc.log('%s not found.' % db, xbmc.LOGINFO)
        return False
    cur.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    for table in cur.fetchall():
        if table[0] == 'version': 
            xbmc.log('Data from table `%s` skipped.' % table[0], xbmc.LOGDEBUG)
        else:
            try:
                cur.execute("DELETE FROM %s" % table[0])
                conn.commit()
                xbmc.log('Data from table `%s` cleared.' % table[0], xbmc.LOGDEBUG)
            except Exception as e:
                xbmc.log("DB Remove Table `%s` Error: %s" % (table[0], str(e)), xbmc.LOGERROR)
    conn.close()
    xbmc.log('%s DB Purging Complete.' % db, xbmc.LOGINFO)

####----- Clear Cache Files -----####

def human_size(num):
    for unit in ('B', 'KB', 'MB', 'GB'):
        if num < 1024 or unit == 'GB':
            return '%d %s' % (num, unit) if unit == 'B' else '%.1f %s' % (num, unit)
        num /= 1024.0


def path_size(path):
    if os.path.isfile(path):
        try:
            return os.path.getsize(path)
        except OSError:
            return 0
    total = 0
    for root, dirs, files in os.walk(path):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(root, name))
            except OSError:
                pass
    return total


def find_cache_targets():
    """Build the list of cache entries that currently exist on this device.
    An entry can cover more than one path when they only make sense together."""
    targets = []

    # Thumbnails and Textures13.db are two halves of the same cache: the folder
    # holds the images, the database indexes them. Clearing one without the
    # other leaves Kodi showing blank artwork, so they are offered as one item.
    thumbnails = os.path.join(user_path, 'Thumbnails')
    thumb_paths = []
    if os.path.isdir(thumbnails):
        thumb_paths.append({'path': thumbnails, 'db': False})
    if os.path.exists(textures_db):
        thumb_paths.append({'path': textures_db, 'db': True})
    if thumb_paths:
        targets.append({'name': 'Thumbnails', 'paths': thumb_paths})

    for full_path in tmdbh_cache_dirs(data_path, logger=lambda m: xbmc.log(m, xbmc.LOGINFO)):
        targets.append({'name': 'TMDb Helper: %s' % os.path.basename(full_path),
                        'paths': [{'path': full_path, 'db': False}]})
    return targets


def target_size(target):
    return sum(path_size(item['path']) for item in target['paths'])


def wipe_path(item):
    """Empty a single cache location. Folders are kept in place so the owning
    add-on does not have to recreate them. True when nothing is left behind."""
    path = item['path']
    if not os.path.exists(path):
        return True
    if os.path.isdir(path):
        shutil.rmtree(path, ignore_errors=True)
        if not os.path.exists(path):
            try:
                os.makedirs(path)
            except OSError as e:
                xbmc.log('Unable to recreate %s. Reason: %s' % (path, e), xbmc.LOGINFO)
        try:
            return not os.listdir(path)
        except OSError:
            return False
    try:
        os.unlink(path)
        return True
    except Exception as e:
        xbmc.log('Failed to delete %s. Reason: %s' % (path, e), xbmc.LOGINFO)
        if item['db']:
            # File is locked (Kodi has it open), so empty the tables instead.
            purge_db(path)
            return True
        return False


def wipe_target(target):
    """Clear every path belonging to one checklist entry."""
    results = [wipe_path(item) for item in target['paths']]
    return all(results)


def clear_cache_files():
    # Finding the entries is cheap, but measuring them means walking every file,
    # which is slow on a big Thumbnails folder. Show real progress while sizing.
    targets = find_cache_targets()
    if not targets:
        dialog.ok(addon_name, local_string(30107))  # Nothing found to clear
        return

    dp.create(addon_name, local_string(30113))  # Scanning for cache files...
    try:
        for count, target in enumerate(targets):
            if dp.iscanceled():
                return
            dp.update(int(count * 100 / len(targets)), '%s[CR]%s' % (local_string(30113), target['name']))
            target['size'] = target_size(target)
        dp.update(100, local_string(30113))
    finally:
        dp.close()

    # Kodi's own multiselect is used rather than a bundled window, so the
    # checklist is drawn by the active skin and matches the rest of Kodi.
    # The size travels in the label because a compact select list is the only
    # layout every skin is guaranteed to have.
    labels = ['%s  [%s]' % (t['name'], human_size(t['size'])) for t in targets]
    selection = dialog.multiselect(local_string(30108), labels,
                                   preselect=list(range(len(targets))))
    if not selection:
        return

    chosen = [targets[i] for i in selection]
    total_size = sum(t['size'] for t in chosen)
    warning = local_string(30109) % human_size(total_size)  # Are you sure?
    if not dialog.yesno(addon_name, warning, nolabel=local_string(30032), yeslabel=local_string(30110)):  # No, Delete
        return

    dp.create(addon_name, local_string(30111))  # Deleting cache files...
    cleared = 0
    freed = 0
    for count, target in enumerate(chosen):
        if dp.iscanceled():
            break
        dp.update(int(count * 100 / len(chosen)), '%s[CR]%s' % (local_string(30111), target['name']))
        if wipe_target(target):
            cleared += 1
            freed += target['size']
    dp.update(100, local_string(30111))
    xbmc.sleep(500)
    dp.close()
    if not cleared:
        dialog.ok(addon_name, local_string(30118))  # Nothing could be deleted
        return
    dialog.ok(addon_name, local_string(30112) % (cleared, len(chosen), human_size(freed)))  # Cleared x of y
    os._exit(1)


# Kept so any older menu item or shortcut pointing at the previous name still works.
clear_thumbnails = clear_cache_files

def fresh_start(standalone=False):
    if standalone:
        exceptions = EXCLUDES_FRESH
        yesFresh = dialog.yesno(local_string(30012), local_string(30042), nolabel=local_string(30032), yeslabel=local_string(30012))  # Are you sure?
        if not yesFresh:
            quit()
    else:
        exceptions = EXCLUDES_INSTALL
    if not currSkin() in ['skin.estuary']:
        swapSkins('skin.estuary')
        x = 0
        xbmc.sleep(100)
        while not xbmc.getCondVisibility("Window.isVisible(yesnodialog)") and x < 150:
            x += 1
            xbmc.sleep(100)
            xbmc.executebuiltin('SendAction(Select)')
        if xbmc.getCondVisibility("Window.isVisible(yesnodialog)"):
            xbmc.executebuiltin('SendClick(11)')
        else: 
            xbmc.log('Fresh Install: Skin Swap Timed Out!', xbmc.LOGINFO)
            return False
        xbmc.sleep(100)
    if not currSkin() in ['skin.estuary']:
        xbmc.log('Fresh Install: Skin Swap failed.', xbmc.LOGINFO)
        return
    dp.create(addon_name, local_string(30043))  # Deleting files and folders...
    xbmc.sleep(100)
    dp.update(30, local_string(30043))
    xbmc.sleep(100)
    for root, dirs, files in os.walk(xbmcPath, topdown=True):
        dirs[:] = [d for d in dirs if d not in exceptions]
        for name in files:
            if name not in exceptions:
                try:
                    os.remove(os.path.join(root, name))
                except:
                    xbmc.log('Unable to delete ' + name, xbmc.LOGINFO)
    dp.update(60, local_string(30043))
    xbmc.sleep(100)    
    for root, dirs, files in os.walk(xbmcPath,topdown=True):
        dirs[:] = [d for d in dirs if d not in exceptions]
        for name in dirs:
            if name not in ['addons', 'userdata', 'Database', 'addon_data', 'backups', 'temp']:
                try:
                    shutil.rmtree(os.path.join(root,name),ignore_errors=True, onerror=None)
                except:
                    xbmc.log('Unable to delete ' + name, xbmc.LOGINFO)
    dp.update(60, local_string(30043))
    xbmc.sleep(100)
    if not os.path.exists(packages):
        os.mkdir(packages)
    dp.update(100, local_string(30044))  # Done Deleting Files
    xbmc.sleep(1000)
    if standalone is True:
        setting_set('firstrun', 'true')
        setting_set('buildname', 'No Build Installed')
        setting_set('buildversion', '0')
        truncate_tables()
        dialog.ok(addon_name, local_string(30045))  # Fresh Start Complete
        os._exit(1)
    else:
        return

def clean_backups():
    for filename in os.listdir(packages):
        file_path = os.path.join(packages, filename)
        try:
            os.unlink(file_path)
        except OSError:
            shutil.rmtree(file_path)

def clear_packages_startup():
    packages_dir = os.listdir(packages)
    if len(packages_dir) == 0:
        pass
    else:
        clear_packages()

def clear_packages():
    file_count = len([name for name in os.listdir(packages)])
    for filename in os.listdir(packages):
        file_path = os.path.join(packages, filename)
        try:
               if os.path.isfile(file_path) or os.path.islink(file_path):
                   os.unlink(file_path)
               elif os.path.isdir(file_path):
                   shutil.rmtree(file_path)
        except Exception as e:
            xbmc.log('Failed to delete %s. Reason: %s' % (file_path, e), xbmc.LOGINFO)
    xbmcgui.Dialog().notification(addon_name, str(file_count)+' ' + local_string(30046), addon_icon, 5000, sound=False)  # Packages Cleared

def truncate_tables():
    try:
        con = sqlite3.connect(addons_db)
        cursor = con.cursor()
        cursor.execute('DELETE FROM addonlinkrepo;',)
        cursor.execute('DELETE FROM addons;',)
        cursor.execute('DELETE FROM package;',)
        cursor.execute('DELETE FROM repo;',)
        cursor.execute('DELETE FROM update_rules;',)
        cursor.execute('DELETE FROM version;',)
        con.commit()
    except sqlite3.Error as e:
        xbmc.log('There was an error reading the database - %s' %e, xbmc.LOGINFO)
        return ''
    finally:
        try:
            if con:
                con.close()
        except UnboundLocalError as e:
            xbmc.log('%s: There was an error connecting to the database - %s' % (xbmcaddon.Addon().getAddonInfo('name'), e), xbmc.LOGINFO)
    try:
        con = sqlite3.connect(addons_db)
        cursor = con.cursor()
        cursor.execute('VACUUM;',)
        con.commit()
    except sqlite3.Error as e:
        xbmc.log(f"Failed to vacuum data from the sqlite table: {e}", xbmc.LOGINFO)
    finally:
        if con:
            con.close()
