import sys
import json
import xbmc
import xbmcplugin
from .addonvar import addon_name
from .utils import add_dir
from .parser import Parser
from .dropbox import DownloadFile
from uservar import buildfile
from .addonvar import addon_icon, addon_fanart, local_string, build_file
from .colors import colors

HANDLE = int(sys.argv[1])
COLOR1 = colors.color_text1
COLOR2 = colors.color_text2

def main_menu():
    xbmcplugin.setPluginCategory(HANDLE, COLOR1('Main Menu'))
    
    add_dir(COLOR1(f'{addon_name}'), '', '', addon_icon, addon_fanart, COLOR1(f'{addon_name}'), isFolder=False) 
    
    add_dir(COLOR2(local_string(30010)), '', 1, addon_icon, addon_fanart, COLOR2(local_string(30001)), isFolder=True)  # Build Menu
    
    add_dir(COLOR2(local_string(30011)), '', 5, addon_icon, addon_fanart, COLOR2(local_string(30002)), isFolder=True)  # Maintenance
    
    add_dir(COLOR2(local_string(30013)), '', 100, addon_icon, addon_fanart, COLOR2(local_string(30014)), isFolder=False)  # Notification
    
    add_dir(COLOR2(local_string(30015)), '', 9, addon_icon, addon_fanart, COLOR2(local_string(30016)), isFolder=False)  # Settings

def build_menu():
    xbmc.executebuiltin('Dialog.Close(busydialog)')
    xbmcplugin.setPluginCategory(HANDLE, local_string(30010))
    if buildfile.startswith('https://www.dropbox.com'):
        DownloadFile(buildfile, build_file)
        try:
            builds = json.load(open(build_file,'r')).get('builds')
        except:
            xml = Parser(build_file)
            builds = json.loads(xml.get_list2())['builds']
    elif not buildfile.endswith('.xml') and not buildfile.endswith('.json'):
        add_dir(local_string(30017),'','',addon_icon,addon_fanart,local_string(30017),isFolder=False)  # Invalid Build URL
        return
    else:
        p = Parser(buildfile)
        builds = json.loads(p.get_list())['builds']
    
    for build in builds:
        name = (build.get('name', local_string(30018)))  # Unknown Name
        version = (build.get('version', '0'))
        url = (build.get('url', ''))
        icon = (build.get('icon', addon_icon))
        fanart = (build.get('fanart', addon_fanart))
        description = (build.get('description', local_string(30019)))  # No Description Available.
        preview = (build.get('preview',None))
        
        if url.endswith('.xml') or url.endswith('.json'):
            add_dir(COLOR2(name),url,1,icon,fanart,COLOR2(description),name2=name,version=version,isFolder=True)
        add_dir(COLOR2(f'{name} {local_string(30020)} {version}'), url, 3, icon, fanart, description, name2=name, version=version, isFolder=False)  # Version
        if preview is not None:
            add_dir(COLOR1(local_string(30021) + ' ' + name + ' ' + local_string(30020) + ' ' + version), preview, 2, icon, fanart, COLOR2(description), name2=name, version=version, isFolder=False)  # Video Preview

def submenu_maintenance():
    kodi_ver = str(xbmc.getInfoLabel("System.BuildVersion")[:4])
    xbmcplugin.setPluginCategory(HANDLE, COLOR1(local_string(30022)))  # Maintenance
    add_dir(COLOR1('[B]Maintenance[/B]'),'','',addon_icon,addon_fanart, COLOR1('Maintenance Menu'),isFolder=False)
    add_dir(COLOR2(local_string(30023)),'',6,addon_icon,addon_fanart,COLOR1(local_string(30005)),isFolder=False)  # Clear Packages
    add_dir(COLOR2(local_string(30024)),'',7,addon_icon,addon_fanart,COLOR2(local_string(30008)),isFolder=False)  # Clear Thumbnails
    add_dir(COLOR2(local_string(30012)), '', 4, addon_icon, addon_fanart, COLOR2(local_string(30003)), isFolder=False)  # Fresh Start
    if '20' in kodi_ver:
        add_dir(COLOR2(local_string(30025)),'',8,addon_icon,addon_fanart,COLOR2(local_string(30009)),isFolder=False)  # Advanced Settings K20
    if '21' in kodi_ver:
        add_dir(COLOR2(local_string(30106)),'',26,addon_icon,addon_fanart,COLOR2(local_string(30009)),isFolder=False)  # Advanced Settings K21
    add_dir(COLOR2(local_string(30064)),'',11,addon_icon,addon_fanart,COLOR2(local_string(30064)), isFolder=False)  # Edit Whitelist
    add_dir(COLOR2('Backup/Restore Build'),'',12,addon_icon,addon_fanart, COLOR2('Backup and Restore Build'))  # Backup Build
    add_dir(COLOR2('Backup/Restore GUI & Skin Settings'),'',19,addon_icon,addon_fanart,COLOR2('Restore GUI and Skin Settings'))
    add_dir(COLOR2('Force Close'),'', 18, addon_icon,addon_fanart,COLOR2('Force Close Kodi'))
    add_dir(COLOR2('View Log'),'', 25, addon_icon,addon_fanart,COLOR2('View Log'), isFolder=False)

def backup_restore():
    xbmcplugin.setPluginCategory(HANDLE, COLOR1('Backup/Restore'))
    add_dir(COLOR1('[B]Backup/Restore[/B]'),'','',addon_icon,addon_fanart, COLOR1('Backup/Restore Menu'), isFolder=False)
    add_dir(COLOR2('Backup Build'),'',13,addon_icon,addon_fanart, COLOR2('Backup Build'), isFolder=False)  # Backup Build
    add_dir(COLOR2('Restore Backup'),'',14, addon_icon,addon_fanart, COLOR2('Restore Backup'))  # Restore Backup
    add_dir(COLOR2('Change Backups Folder Location'),'',16,addon_icon,addon_fanart, COLOR2('Change the location where backups will be stored and accessed.'), isFolder=False)  # Backup Location
    add_dir(COLOR2('Reset Backups Folder Location'),'',17,addon_icon,addon_fanart, COLOR2('Set the backup location to its default.'), isFolder=False)  # Reset Backup Location

def restore_gui_skin():
    add_dir(COLOR1('[B]Backup/Restore GUI & Skin Settings[/B]'),'','',addon_icon,addon_fanart, COLOR1('Restore GUI/Skin Settings Menu'), isFolder=False)
    add_dir(COLOR2('Backup GUI & Skin Settings'),'',27,addon_icon,addon_fanart,COLOR2('Backup GUI & Skin Settings'), isFolder=False)
    add_dir(COLOR2('Restore GUI Settings'),'',28, addon_icon,addon_fanart, COLOR2('Restore Your GUI Settings'), isFolder=False)
    add_dir(COLOR2('Restore Skin Settings'),'',29, addon_icon,addon_fanart, COLOR2('Restore Your Skin Settings'), isFolder=False)
    add_dir(COLOR2('Restore Build Default GUI Settings'),'',20,addon_icon,addon_fanart,COLOR2('Restore GUI Settings'), isFolder=False)  
    add_dir(COLOR2('Restore Build Default Skin Settings'),'',21, addon_icon,addon_fanart, COLOR2('Restore Skin Settings'), isFolder=False)
