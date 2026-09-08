import xbmcaddon
import xbmcvfs
import xbmcgui
import re

addon_name = xbmcaddon.Addon().getAddonInfo('name')
log_path = xbmcvfs.translatePath('special://logpath/')
text_view = xbmcgui.Dialog().textviewer
addons_path = xbmcvfs.translatePath('special://home/addons')
select_three = xbmcgui.Dialog().yesnocustom


def get_log():
    pattern = re.compile('EXCEPTION Thrown(.+?)-->End of Python script error report<--', re.MULTILINE | re.DOTALL)
    log = ''
    choice = select_three(addon_name, 'Select Log Type:', 'Kodi.log', nolabel='Error Log', yeslabel='Kodi.old')
    if choice == 2 or choice == 0:
        path = log_path + 'kodi.log'
    elif choice == 1:
        path = log_path + 'kodi.old.log'
    else:
        return

    with open(path, 'r', encoding='utf-8', errors='ignore') as f:
        log = f.read()
    if choice == 0:
        errors = pattern.findall(log)
        if errors:
            string = '\n'.join('***Error***\n\n%s\n***End of Error Report***\n' % error for error in errors)
        else:
            string = 'No Errors Found'
    else:
        string = log
    return string.replace(addons_path, addons_path + '\n')


def log_viewer() -> None:
    """Hand the log to Kodi's own text viewer. usemono gives a fixed width
    font, which keeps log columns lined up, and the skin supplies everything
    else - background, text colour and scrollbar."""
    message = get_log()
    if not message:
        return
    text_view(addon_name, message, True)
