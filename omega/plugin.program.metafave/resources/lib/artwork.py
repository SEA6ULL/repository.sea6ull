# SPDX-License-Identifier: GPL-3.0-only
# MetaFave is authored by SEA6ULL / Claude / GPT and licensed under GPL-3.0.
"""Artwork type registry and capture.

Kodi's ListItem.getArt() only answers for a named key, so plugin items are probed
against the registry below. Library items additionally get their complete art
dictionary from JSON-RPC, which includes any non-standard keys. To support a new
artwork type, add it to BASE_TYPES (or PREFIXES); nothing else needs to change.
Stored art is a plain {key: url} dict, so any key round-trips untouched.
"""

from .common import jsonrpc

BASE_TYPES = [
    ('poster', 'Poster'),
    ('fanart', 'Fanart'),
    ('landscape', 'Landscape'),
    ('thumb', 'Thumb'),
    ('icon', 'Icon'),
    ('clearlogo', 'Clear Logo'),
    ('clearart', 'Clear Art'),
    ('banner', 'Banner'),
    ('discart', 'Disc Art'),
    ('keyart', 'Key Art'),
    ('characterart', 'Character Art'),
]

PREFIXES = [
    ('tvshow', 'TV Show'),
    ('season', 'Season'),
    ('set', 'Movie Set'),
    ('album', 'Album'),
    ('artist', 'Artist'),
    ('albumartist', 'Album Artist'),
]

EXTRA_FANART = ['fanart%d' % number for number in range(1, 10)]

_BASE_NAMES = dict(BASE_TYPES)
_PREFIX_NAMES = dict(PREFIXES)

PROBE_KEYS = ([key for key, _ in BASE_TYPES] + EXTRA_FANART +
              ['%s.%s' % (prefix, key) for prefix, _ in PREFIXES for key, _ in BASE_TYPES])

IMAGE_MASK = '.png|.jpg|.jpeg|.gif|.webp|.bmp|.tbn'

_LIBRARY_METHODS = {
    'movie': ('VideoLibrary.GetMovieDetails', 'movieid', 'moviedetails'),
    'tvshow': ('VideoLibrary.GetTVShowDetails', 'tvshowid', 'tvshowdetails'),
    'season': ('VideoLibrary.GetSeasonDetails', 'seasonid', 'seasondetails'),
    'episode': ('VideoLibrary.GetEpisodeDetails', 'episodeid', 'episodedetails'),
    'musicvideo': ('VideoLibrary.GetMusicVideoDetails', 'musicvideoid', 'musicvideodetails'),
    'set': ('VideoLibrary.GetMovieSetDetails', 'setid', 'setdetails'),
    'album': ('AudioLibrary.GetAlbumDetails', 'albumid', 'albumdetails'),
    'artist': ('AudioLibrary.GetArtistDetails', 'artistid', 'artistdetails'),
    'song': ('AudioLibrary.GetSongDetails', 'songid', 'songdetails'),
}


def label(key):
    """Human-readable name for an art key, including unknown and prefixed keys."""
    if key in _BASE_NAMES:
        return _BASE_NAMES[key]
    if key.startswith('fanart') and key[6:].isdigit():
        return 'Fanart %s' % key[6:]
    prefix, _, base = key.partition('.')
    if base and prefix in _PREFIX_NAMES:
        return '%s %s' % (_PREFIX_NAMES[prefix], label(base))
    return key


def ordered_keys(art):
    order = {key: index for index, key in enumerate(PROBE_KEYS)}
    return sorted(art, key=lambda key: (order.get(key, len(order)), key))


def preview(art):
    """Best single image to represent an item in dialogs."""
    for key in ('poster', 'thumb', 'icon', 'landscape', 'fanart', 'clearlogo'):
        if art.get(key):
            return art[key]
    return next(iter(art.values()), '')


# Folder cover slot -> the art keys of an item that suit it, best first. A TV episode has no
# poster of its own but its show's poster makes a good cover, so prefixed keys follow.
# Slots not listed here only take the same key.
SLOT_SOURCES = {
    'poster': ('poster', 'tvshow.poster', 'season.poster', 'set.poster'),
    'thumb': ('thumb', 'album.thumb', 'artist.thumb', 'poster', 'tvshow.poster', 'icon'),
    'landscape': ('landscape', 'tvshow.landscape', 'season.landscape', 'set.landscape', 'fanart'),
    'fanart': ('fanart', 'tvshow.fanart', 'season.fanart', 'set.fanart', 'artist.fanart')
              + tuple(EXTRA_FANART),
    'clearlogo': ('clearlogo', 'tvshow.clearlogo', 'set.clearlogo', 'artist.clearlogo'),
    'banner': ('banner', 'tvshow.banner', 'season.banner', 'set.banner'),
}


def slot_keys(slot):
    return SLOT_SOURCES.get(slot, (slot,))


def best_match(art, slot):
    """(key, url) of the image in art that best suits a folder cover slot, or None."""
    for key in slot_keys(slot):
        if art.get(key):
            return key, art[key]
    return None


def library_art(dbtype, dbid):
    method = _LIBRARY_METHODS.get(dbtype)
    if not method or dbid <= 0:
        return {}
    name, id_param, result_key = method
    result = jsonrpc(name, {id_param: dbid, 'properties': ['art']})
    if not result:
        return {}
    return (result.get(result_key) or {}).get('art') or {}


# Library items whose alternative artwork Kodi can list (the scraper results stored in the
# library, as shown by Kodi's own "Choose art" dialog): media type -> (namespace, id field).
_AVAILABLE_ART = {
    'movie': ('VideoLibrary', 'movieid'), 'tvshow': ('VideoLibrary', 'tvshowid'),
    'season': ('VideoLibrary', 'seasonid'), 'episode': ('VideoLibrary', 'episodeid'),
    'set': ('VideoLibrary', 'setid'), 'musicvideo': ('VideoLibrary', 'musicvideoid'),
    'artist': ('AudioLibrary', 'artistid'), 'album': ('AudioLibrary', 'albumid'),
}


def library_source(info):
    """(namespace, item) identifying the library item behind a saved item, or None."""
    mediatype, dbid = info.get('mediatype', ''), info.get('dbid', 0)
    if mediatype not in _AVAILABLE_ART or not isinstance(dbid, int) or dbid <= 0:
        return None
    namespace, id_field = _AVAILABLE_ART[mediatype]
    return namespace, {id_field: dbid}


def available_art_types(source):
    """Art types the library has alternatives for (Kodi's 'Add art type' list)."""
    if not source:
        return []
    namespace, item = source
    result = jsonrpc('%s.GetAvailableArtTypes' % namespace, {'item': item}) or {}
    return [key for key in result.get('availablearttypes') or [] if key]


def available_art(source, art_type):
    """Remote alternatives for one art type: [(url, preview_url), ...] in library order."""
    if not source:
        return []
    namespace, item = source
    result = jsonrpc('%s.GetAvailableArt' % namespace, {'item': item, 'arttype': art_type}) or {}
    choices, seen = [], set()
    for entry in result.get('availableart') or []:
        url = entry.get('url')
        if url and url not in seen:
            seen.add(url)
            choices.append((url, entry.get('previewurl') or url))
    return choices


def capture(listitem, dbtype='', dbid=0):
    art = {}
    for key in PROBE_KEYS:
        value = listitem.getArt(key)
        if value:
            art[key] = value
    if 'fanart' not in art:
        legacy = listitem.getProperty('fanart_image')
        if legacy:
            art['fanart'] = legacy
    for key, value in library_art(dbtype, dbid).items():
        if value:
            art.setdefault(key, value)  # what the user saw wins; the library fills gaps
    return art
