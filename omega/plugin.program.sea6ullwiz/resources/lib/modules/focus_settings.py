import xbmc, xbmcaddon


# addonId is the Addon ID
# id1 is the Category (Tab) offset (0=first, 1=second, 2...etc)
# id2 is the Setting (Control) offset (0=first, 1=second, 2...etc)
# Example: OpenAddonSettings('plugin.video.name', 2, 3)
# This will open settings dialog focusing on fourth setting (control) inside the third category (tab)

def openAddonSettings(addonId, id1=None, id2=None):
    xbmc.executebuiltin('Addon.OpenSettings(%s)' % addonId)
    if id1 != None and id2 != None:
        xbmc.executebuiltin('SetFocus(%i)' % (id1))
    if id2 != None:
        xbmc.executebuiltin('SetFocus(%i)' % (id2)) 

def tmdbh_mdblist_api():
        openAddonSettings('plugin.video.themoviedb.helper', -96, -75)
        
def rurl_settings_rd():
        openAddonSettings('script.module.resolveurl', -98, -67)

def rurl_settings_pm():
        openAddonSettings('script.module.resolveurl', -98, -78)

def rurl_settings_ad():
        openAddonSettings('script.module.resolveurl', -99, -78)

def am_auth_nondebrid():
        openAddonSettings('script.module.accountmgr', -100, -44)
        
def am_auth_meta():
        openAddonSettings('script.module.accountmgr', -100, -32)

def am_supported_furk():
        openAddonSettings('script.module.accountmgr', -96, -60)

def am_supported_easy():
        openAddonSettings('script.module.accountmgr', -96, -59)

def am_supported_file():
        openAddonSettings('script.module.accountmgr', -96, -58)

def am_supported_meta():
        openAddonSettings('script.module.accountmgr', -96, -61)

def am_supported_trakt():
        openAddonSettings('script.module.accountmgr', -96, -62)
        
def am_supported_debrid():
        openAddonSettings('script.module.accountmgr', -96, -63)

def am_manage():
        openAddonSettings('script.module.accountmgr', -99, 0)
        
def am_backup():
        openAddonSettings('script.module.accountmgr', -98, 0)


