import xbmc
import xbmcgui
import xbmcaddon
import json
import base64
import xbmcvfs
import shutil
import xml.etree.ElementTree as ET
from .maintenance import clear_packages_startup
from uservar import buildfile, notify_url
from resources.lib.modules import addonvar
from .addonvar import setting, setting_set, addon_name, isBase64, headers, dialog, local_string, addon_id

current_build = setting('buildname')
try:
    current_version = setting('buildversion') 
except:
    current_version = 0.0

class Startup:
    
    def seren_check(self):
        if '21' in str(xbmc.getInfoLabel("System.BuildVersion")[:4]) and xbmcvfs.exists(addonvar.seren):
            with open(addonvar.seren_glbs, encoding="utf8") as f:
                if addonvar.chk_glbs not in f.read():
                    try:
                        shutil.copyfile(addonvar.seren_fix, addonvar.seren_glbs)
                    except:
                        pass
        else:
            pass
            
    def check_updates(self):
           if current_build == 'No Build Installed':
               nobuild = dialog.yesnocustom(addon_name, 'There is currently no build installed.\nWould you like to install one now?', 'Remind Later')
               if nobuild == 1:
                   xbmc.executebuiltin(f'ActivateWindow(10001, "plugin://{addon_id}/?mode=1",return)')
               elif nobuild == 0:
                   setting_set('buildname', 'No Build')
               else:
                   return
           try:
               response = self.get_page(buildfile)
           except:
               return
           version = 0.0
           try:
               builds = json.loads(response)['builds']
               for build in builds:
                       if build.get('name') == current_build:
                           version = str(build.get('version'))
                           break
           except:
               builds = ET.fromstring(response)
               for tag in builds.findall('build'):
                       if tag.find('name').text == current_build:
                           version = str(tag.find('version').text)
                           break
           # 3 decimal fix
           
           current_bump = 0
           version_bump = 0
           update = False
           
           try:
               current = str(current_version)
               version = str(version)
               c_splitted = current.split('.')
               v_splitted = version.split('.')
        
               if '.' in current:
                   current = float(f'{c_splitted[0]}.{c_splitted[1]}')
                   if len(c_splitted) == 3:
                       current_bump = int(c_splitted[2])
               if '.' in version:
                   version = float(f'{v_splitted[0]}.{v_splitted[1]}')
                   if len(v_splitted) == 3:
                       version_bump = int(v_splitted[2])
               if float(version) > float(current):
                   update = True
               elif float(version) == float(current) and version_bump > current_bump:
                   update = True
               else:
                   update = False
           
           except ValueError as e:
               print(f'Invalid Version Number. It must be numeric and no more than 3 decimals. Error Details - {e}')
               update = False
           
           if update and setting('update_passed') != 'true':
               update_available = xbmcgui.Dialog().yesnocustom(addon_name, local_string(30047) + ' ' + current_build +' ' + local_string(30048) + '\n' + local_string(30049) + ' ' + str(current_version) + '\n' + local_string(30050) + ' ' + str(version) + '\n' + local_string(30051), 'Remind Later')
               if update_available == 1:
                   xbmc.executebuiltin(f'ActivateWindow(10001, "plugin://{addon_id}/?mode=1",return)')
               elif update_available == 0:
                   setting_set('update_passed', 'true')
               else:
                   return
           else:
               return

    def file_check(self, bfile):
        if isBase64(bfile):
            return base64.b64decode(bfile).decode('utf8')
        else:
            return bfile
            
    def get_page(self, url):
           from urllib.request import Request,urlopen
           req = Request(self.file_check(url), headers = headers)
           return urlopen(req).read()
        
    def save_menu(self):
        save_items = []
        choices = ["Trakt & Debrid", "YouTube API Keys", "Favourites", "Advanced Settings", "Sources"]
        save_select = dialog.multiselect(addon_name + ' - ' + local_string(30052),choices, preselect=[])  # Select Save Items
        if save_select == None:
            return
        else:
            for index in save_select:
                save_items.append(choices[index])
                
        if 'Trakt & Debrid' in save_items:
            setting_set('savedata','true')
        else:
            setting_set('savedata','false')
            
        if 'YouTube API Keys' in save_items:
            setting_set('saveyoutube','true')
        else:
            setting_set('saveyoutube','false')
            
        if 'Favourites' in save_items:
            setting_set('savefavs','true')
        else:
            setting_set('savefavs','false')
            
        if 'Advanced Settings' in save_items:
            setting_set('saveadvanced','true')
        else:
            setting_set('saveadvanced','false')
        
        if 'Sources' in save_items:
            setting_set('savesources', 'true')
        else:
            setting_set('savesources', 'false')
  
        setting_set('firstrunSave', 'true')

    def notify_check(self):
        from ..GUIcontrol import notify
        info = notify.get_notify()
        current_notify = int(setting('notifyversion'))
        notify_version = info[0]
        message = info[1]
        if not setting('firstrunNotify')=='true' or notify_version > current_notify:
            notify.notification(message)
            setting_set('firstrunNotify', 'true')
            setting_set('notifyversion', str(notify_version))    

    def run_startup(self):
        self.seren_check()
        if setting('firstrun') == 'true':
            if current_build == 'Xlite Switch':
                from .save_data import backup_gui_skin
                xbmc.executebuiltin('UpdateAddonRepos')
                xbmc.sleep(2000)
                xbmc.executebuiltin('UpdateLocalAddons')
                backup_gui_skin()
                setting_set('firstrun', 'false')
            else:
                from resources.lib.modules.addons_enable import enable_addons
                from .save_data import backup_gui_skin
                enable_addons()
                backup_gui_skin()
                setting_set('firstrun', 'false')
        else:
            if setting('autoclearpackages')=='true':
                clear_packages_startup()
            xbmc.sleep(2000)
            self.notify_check()
            xbmc.sleep(3000)      #Delay Build Update Notification
            self.check_updates()
            
