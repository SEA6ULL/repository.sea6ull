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
