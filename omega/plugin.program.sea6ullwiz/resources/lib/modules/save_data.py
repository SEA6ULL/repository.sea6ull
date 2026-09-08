import xbmc
import xbmcvfs	
import os
import shutil
import json
from .addonvar import user_path, data_path, setting, addon_id, packages, addon_path

user_path = xbmcvfs.translatePath('special://userdata/')	
data_path = os.path.join(user_path, 'addon_data/')
text_path = os.path.join(addon_path, 'resources', 'texts')

def backup(path, file):
    if os.path.exists(os.path.join(path, file)):
        try:
            if os.path.isfile(os.path.join(path, file)):
                xbmcvfs.copy(os.path.join(path, file), os.path.join(packages, file))   #Backup your Kodi specifics (favourites, sources etc...)
            elif os.path.isdir(os.path.join(path, file)):
                shutil.copytree(os.path.join(path, file), os.path.join(packages, file), dirs_exist_ok=True)   #Backup an add-on data folder (e.g. YouTube)
        except Exception as e:
            xbmc.log('Failed to backup %s. Reason: %s' % (os.path.join(packages, file), e), xbmc.LOGINFO)

def restore(path, file):
    if os.path.exists(os.path.join(packages, file)):
        try:
            if os.path.isfile(os.path.join(packages, file)):
                if os.path.exists(os.path.join(user_path, file)):
                    os.unlink(os.path.join(path, file))   #Remove Kodi specifics (favourites, sources etc...) included with new install
                shutil.move(os.path.join(packages, file), os.path.join(path, file))   #Restore your backed up Kodi specifics (favourites, sources etc...)
            elif os.path.isdir(os.path.join(packages, file)):
                shutil.copytree(os.path.join(packages, file), os.path.join(path, file), dirs_exist_ok=True)   #Restore an add-on data folder (e.g. YouTube)
        except Exception as e:
            xbmc.log('Failed to restore %s. Reason: %s' % (os.path.join(path, file), e), xbmc.LOGINFO)

def save_backup_restore(_type: str) -> None:
    """Carry the user's own settings across a wipe.

    Each entry in backup_restore.json names either a single file in userdata or an
    add-on data folder plus the files worth keeping from it.
    """
    try:
        with open(os.path.join(text_path, 'backup_restore.json'), 'r',
                  encoding='utf-8', errors='ignore') as f:
            item_list = json.loads(f.read())
    except (OSError, ValueError) as e:
        xbmc.log('Unable to read backup_restore.json: %s' % e, xbmc.LOGINFO)
        return

    for item, meta in item_list.items():
        try:
            if setting(meta['setting']) != 'true':
                continue
            path = user_path if meta['path'] == 'user_path' else data_path
            files = meta.get('files')

            if _type == 'backup':
                if files:
                    for name in files:
                        backup(path, '%s/%s' % (item, name))
                else:
                    backup(path, item)
            elif _type == 'restore':
                restore(path, item)
        except Exception as e:
            xbmc.log('Error handling %s: %s' % (item, e), xbmc.LOGINFO)
            continue
