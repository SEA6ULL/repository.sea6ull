import xbmcgui
import xbmcaddon
from urllib.request import Request, urlopen
from uservar import notify_url

addon_name = xbmcaddon.Addon().getAddonInfo('name')


def get_notify() -> list:
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:58.0) Gecko/20100101 Firefox/58.0'}
    req = Request(notify_url, headers=headers)
    response = urlopen(req).read().decode('utf-8')
    try:
        split_response = response.split('|||')
        notify_version = int(split_response[0])
        message = split_response[1]
    except:
        notify_version = 0
        message = 'Improper Notifications format. Please check the Notifications text.'
    return [notify_version, message]


def notification(message: str) -> None:
    """Show the notice in Kodi's own text viewer so it picks up the fonts,
    colours and layout of whatever skin the user is running."""
    xbmcgui.Dialog().textviewer(addon_name, message)
