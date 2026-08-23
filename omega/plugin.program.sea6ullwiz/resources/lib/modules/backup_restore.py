import xbmc
import xbmcgui
import xbmcplugin
import sys
import shutil
import os
import zipfile
from datetime import datetime
from urllib.parse import quote_plus
from zipfile import ZipFile
from pathlib import Path
from .addonvar import home, addon_profile, addon_path, setting, setting_set, translatePath, xbmcPath, addon_id, dp, local_string, addon_name, addon_icon, addon_fanart
from .utils import add_dir
from .colors import colors

COLOR1 = colors.color_text1
COLOR2 = colors.color_text2

MODE_RESTORE = 15
MODE_DELETE = 30

p = Path(home)
backup_path = Path(translatePath(setting('backupfolder')))
backups = p / 'backups'
addons = p / 'addons'
media = p / 'media'
userdata = p / 'userdata'
wizard_path = Path(addon_path)
data_path = Path(addon_profile)
compression = zipfile.ZIP_DEFLATED

def log(_text, _var):
    xbmc.log(f'{_text} = {str(_var)}', xbmc.LOGINFO)

excludes = [p / 'addons/packages', p / 'addons/temp', p / 'userdata/Thumbnails', p / 'userdata/Database/Textures13.db', p / wizard_path]

def from_keyboard():
    kb = xbmc.Keyboard('', 'Enter Backup Name', False)
    kb.doModal()
    if (kb.isConfirmed()):
        return kb.getText()
    else:
        return False

def backup_build():
    backup_path.mkdir(parents=True, exist_ok=True)
    k = from_keyboard()
    if k is False:
        return xbmcgui.Dialog().ok('Backup', 'Backup Cancelled')
    else:
        backup_name = backup_path / f'{k}.zip'
        
    addons_dirs, addons_files = ([x for x in addons.iterdir() if x.is_dir() and x not in excludes]), ([x for x in addons.iterdir() if x.is_file() and x not in excludes])

    media_dirs, media_files = ([x for x in media.iterdir() if x.is_dir() and x not in excludes]), ([x for x in media.iterdir() if x.is_file() and x not in excludes])

    userdata_dirs, userdata_files = ([x for x in userdata.iterdir() if x.is_dir() and x not in excludes]), ([x for x in userdata.iterdir() if x.is_file() and x not in excludes])
    
    zip_file = ZipFile(backup_name, 'w')
    xbmcgui.Dialog().notification(addon_name, 'Backup in progress, please wait!', addon_icon, 3000)
    for x in sorted(addons_dirs):
        for z in sorted([y for y in x.rglob('*') if y not in excludes]):
            try:
                if '__pycache__' not in str(z):
                    zip_file.write(z, str(z.relative_to(p)), compress_type=compression)
            except Exception as e:
                xbmc.log(f'Unable to compress file {str(z)}: {e}', xbmc.LOGINFO)
    for x in sorted(addons_files):
        try:
            zip_file.write(x, str(x.relative_to(p)), compress_type=compression)
        except Exception as e:
            xbmc.log(f'Unable to compress file {str(x)}: {e}', xbmc.LOGINFO)
    
    for x in sorted(media_dirs):
        for z in sorted([y for y in x.rglob('*') if y not in excludes]):
            try:
                zip_file.write(z, str(z.relative_to(p)), compress_type=compression)
            except Exception as e:
                xbmc.log(f'Unable to compress file {str(z)}: {e}', xbmc.LOGINFO)
    for x in sorted(media_files):
        try:
            zip_file.write(x, str(x.relative_to(p)), compress_type=compression)
        except Exception as e:
            xbmc.log(f'Unable to compress file {str(x)}: {e}', xbmc.LOGINFO)

    for x in sorted(userdata_dirs):
        for z in sorted([y for y in x.rglob('*') if y not in excludes]):
            try:
                zip_file.write(z, str(z.relative_to(p)), compress_type=compression)
            except Exception as e:
                xbmc.log(f'Unable to compress file {str(z)}: {e}', xbmc.LOGINFO)
    for x in sorted(userdata_files):
        try:
            zip_file.write(x, str(x.relative_to(p)), compress_type=compression)
        except Exception as e:
            xbmc.log(f'Unable to compress file {str(x)}: {e}', xbmc.LOGINFO)
    
    zip_file.close()
    xbmcgui.Dialog().ok('Backup', 'Backup Complete')

excludes_freshstart = [addon_id, 'backups',  'Addons33.db', 'kodi.log']

def fresh_start_restore():
    for root, dirs, files in os.walk(xbmcPath, topdown=True):
        dirs[:] = [d for d in dirs if d not in excludes_freshstart]
        for name in files:
            if name not in excludes_freshstart:
                try:
                    os.unlink(os.path.join(root, name))
                except:
                    log('Error Deleting', name)
                    
    for root, dirs, files in os.walk(xbmcPath,topdown=True):
        dirs[:] = [d for d in dirs if d not in excludes_freshstart]
        for name in dirs:
            if name not in ['addons', 'userdata', 'Database', 'addon_data', 'backups', 'temp']:
                try:
                    shutil.rmtree(os.path.join(root,name),ignore_errors=True, onerror=None)
                except:
                    log('Error Deleting', name)

def get_backup_folder():
    dialog = xbmcgui.Dialog()
    fn = dialog.browseSingle(0, 'Kodi', 'local', '', False, False)
    setting_set('backupfolder', fn)

def reset_backup_folder():
    setting_set('backupfolder', 'special://home/backups')
    xbmcgui.Dialog().ok('Backup Folder', 'Backup Folder Location\nSet to Default')

def _handle():
    try:
        return int(sys.argv[1])
    except (IndexError, ValueError):
        return -1

def _plugin_url(mode, path):
    return f'{sys.argv[0]}?mode={mode}&url={quote_plus(str(path))}'

def _human_size(num):
    for unit in ('B', 'KB', 'MB', 'GB'):
        if abs(num) < 1024.0:
            return f'{num:.0f} {unit}' if unit == 'B' else f'{num:.1f} {unit}'
        num /= 1024.0
    return f'{num:.1f} TB'

def _backup_files():
    if not backup_path.is_dir():
        return []
    files = [x for x in backup_path.iterdir() if x.is_file() and x.suffix.lower() == '.zip']
    # newest first, so the oldest backups worth deleting sit at the bottom
    return sorted(files, key=lambda x: x.stat().st_mtime, reverse=True)

def _backup_details(build):
    try:
        stat = build.stat()
        stamp = datetime.fromtimestamp(stat.st_mtime).strftime('%d %b %Y %H:%M')
        return f'{stamp} - {_human_size(stat.st_size)}'
    except OSError:
        return ''

def _backup_label(build):
    # label2 is ignored by most skins in plugin lists, so the date/size has to go
    # into the visible label. Hex colour rather than a name: named colours depend
    # on the skin's colour theme being loaded, hex always renders.
    label = COLOR2(build.stem)
    details = _backup_details(build)
    if details:
        label = f'{label}  [COLOR FF999999]{details}[/COLOR]'
    return label

def restore_menu():
    handle = _handle()
    # Clear the content type set by the router. As a video/files list these items
    # pick up Kodi's media context menu (Play, Queue item, Mark as watched, etc.),
    # none of which make sense for a backup zip.
    xbmcplugin.setContent(handle, '')
    xbmcplugin.setPluginCategory(handle, COLOR1('Restore Backup'))

    build_backups = _backup_files()

    if not build_backups:
        liz = xbmcgui.ListItem(label=COLOR2('No Backups Found'))
        liz.setArt({'fanart': addon_fanart, 'icon': addon_icon, 'thumb': addon_icon})
        liz.setProperty('IsPlayable', 'false')
        xbmcplugin.addDirectoryItem(handle=handle, url='', listitem=liz, isFolder=False)
        return

    for build in build_backups:
        restore_url = _plugin_url(MODE_RESTORE, build)
        delete_url = _plugin_url(MODE_DELETE, build)

        liz = xbmcgui.ListItem(label=_backup_label(build))
        liz.setArt({'fanart': addon_fanart, 'icon': addon_icon, 'thumb': addon_icon})
        # No video infolabels: keeps Kodi from treating the backup as playable media.
        liz.setProperty('IsPlayable', 'false')
        liz.addContextMenuItems([
            ('Restore Backup', f'RunPlugin({restore_url})'),
            ('Delete Backup', f'RunPlugin({delete_url})'),
        ])
        xbmcplugin.addDirectoryItem(handle=handle, url=restore_url, listitem=liz, isFolder=False)

def delete_backup(zippath):
    if not zippath:
        return False

    backup = Path(zippath)
    if not backup.is_file():
        return xbmcgui.Dialog().ok('Delete Backup', 'Backup Not Found')

    heading = 'Delete Backup'
    message = f'Are you sure you wish to permanently delete\nthis backup?\n\n{backup.name}'
    try:
        confirm = xbmcgui.Dialog().yesno(heading, message, nolabel='No', yeslabel='Yes',
                                         defaultbutton=xbmcgui.DLG_YESNO_NO_BTN)
    except (AttributeError, TypeError):
        confirm = xbmcgui.Dialog().yesno(heading, message, nolabel='No', yeslabel='Yes')

    if confirm is not True:
        return False

    try:
        backup.unlink()
    except OSError as e:
        xbmc.log(f'Unable to delete backup {str(backup)}: {e}', xbmc.LOGERROR)
        return xbmcgui.Dialog().ok(heading, 'Backup Could Not Be Deleted')

    xbmcgui.Dialog().notification(addon_name, 'Backup Deleted', addon_icon, 3000)
    xbmc.executebuiltin('Container.Refresh')
    return True

def restore_build(zippath):
    restore = xbmcgui.Dialog().yesno('Restore', 'Are you sure you wish to wipe \ncurrent data and restore from backup?')
    if restore is True:
        fresh_start_restore()
        if os.path.exists(zippath):
            dp.create('Restore', local_string(30034))  # Extracting files
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
                xbmcgui.Dialog().ok('Restore', 'Restore Complete')
                setting_set('firstrun', 'true')
                os._exit(1)
        else:
            xbmcgui.Dialog().ok('Restore', 'Backup Not Found')
    else:
        return False
