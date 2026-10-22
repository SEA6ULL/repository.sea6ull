# SPDX-License-Identifier: GPL-3.0-only
# MetaFave is authored by SEA6ULL / Claude / GPT and licensed under GPL-3.0.
"""Metadata schema, capture from the focused item, and application to ListItems."""

import re

import xbmc

from .common import jsonrpc

# key, value type, ListItem info label used for capture (None = special), string id
FIELDS = [
    ('title', 'str', 'Title', 30100),
    ('originaltitle', 'str', 'OriginalTitle', 30101),
    ('tvshowtitle', 'str', 'TVShowTitle', 30102),
    ('plot', 'str', 'Plot', 30103),
    ('plotoutline', 'str', 'PlotOutline', 30104),
    ('tagline', 'str', 'Tagline', 30105),
    ('year', 'int', 'Year', 30106),
    ('genre', 'list', 'Genre', 30107),
    ('studio', 'list', 'Studio', 30108),
    ('director', 'list', 'Director', 30109),
    ('writer', 'list', 'Writer', 30110),
    ('mpaa', 'str', 'Mpaa', 30111),
    ('rating', 'float', 'Rating', 30112),
    ('votes', 'int', 'Votes', 30113),
    ('duration', 'int', None, 30114),
    ('season', 'int', 'Season', 30115),
    ('episode', 'int', 'Episode', 30116),
    ('artist', 'str', 'Artist', 30117),
    ('album', 'str', 'Album', 30118),
    ('tracknumber', 'int', 'TrackNumber', 30119),
    ('trailer', 'str', 'Trailer', 30120),
    ('imdbnumber', 'str', 'IMDBNumber', 30121),
    ('mediatype', 'str', None, 30122),
    ('dbid', 'int', 'DBID', 30123),
    ('tmdb_id', 'str', None, 30124),
    ('episode_tmdb_id', 'str', None, 30125),
    ('episode_tvdb_id', 'str', None, 30126),
    ('tvshow_imdb_id', 'str', None, 30127),
    ('tvshow_tmdb_id', 'str', None, 30128),
    ('tvdb_id', 'str', None, 30135),
]
FIELD_TYPES = {key: kind for key, kind, _, _ in FIELDS}
FIELD_LABELS = {key: sid for key, _, _, sid in FIELDS}

VIDEO_TYPES = ('video', 'movie', 'tvshow', 'season', 'episode', 'musicvideo', 'set')
MUSIC_TYPES = ('song', 'album', 'artist')
MEDIA_TYPES = VIDEO_TYPES + MUSIC_TYPES

CONTENT_FOR_MEDIATYPE = {
    'movie': 'movies', 'tvshow': 'tvshows', 'season': 'seasons', 'episode': 'episodes',
    'musicvideo': 'musicvideos', 'set': 'sets', 'video': 'videos',
    'song': 'songs', 'album': 'albums', 'artist': 'artists',
}

# Folder content types a user can choose ('' = automatic, from the items' media types).
CONTENT_TYPES = [
    ('', 30084), ('movies', 30085), ('tvshows', 30086), ('seasons', 30087),
    ('episodes', 30088), ('musicvideos', 30089), ('videos', 30052), ('songs', 30090),
    ('albums', 30091), ('artists', 30092), ('files', 30093),
]

_LIST_SPLIT = re.compile(r'\s*/\s*')


def parse(kind, raw):
    """Convert text to the field's type. Returns None when the text holds no value."""
    raw = (raw or '').strip()
    if not raw:
        return None
    try:
        if kind == 'int':
            digits = re.sub(r'[^\d-]', '', raw)
            return int(digits) if digits not in ('', '-') else None
        if kind == 'float':
            return float(raw.replace(',', '.'))
        if kind == 'list':
            values = [value for value in _LIST_SPLIT.split(raw) if value]
            return values or None
    except ValueError:
        return None
    return raw


def display(value):
    if isinstance(value, list):
        return ' / '.join(str(entry) for entry in value)
    return '' if value is None else str(value)


def capture(listitem):
    """Read metadata for the focused item. Only non-empty values are kept."""
    info = {}
    for key, kind, infolabel, _ in FIELDS:
        if infolabel:
            value = parse(kind, xbmc.getInfoLabel('ListItem.%s' % infolabel))
            if value is not None and not (kind == 'int' and value < 0):
                info[key] = value
    mediatype = xbmc.getInfoLabel('ListItem.DBTYPE')
    tag = None
    try:
        tag = listitem.getVideoInfoTag()
        mediatype = mediatype or tag.getMediaType()
    except Exception:
        pass
    if mediatype in MUSIC_TYPES:
        try:
            tag = listitem.getMusicInfoTag()
        except Exception:
            tag = None
    if mediatype:
        info['mediatype'] = mediatype
    try:
        duration = int(tag.getDuration()) if tag else 0
        if duration > 0:
            info['duration'] = duration
    except Exception:
        pass
    # Scalar fields stay editable. Movie unique IDs have a clear meaning.
    if mediatype in ('movie', 'tvshow') and tag:
        for provider in ('imdb', 'tmdb', 'tvdb'):
            try:
                value = tag.getUniqueID(provider)
                if value and (provider != 'tvdb' or mediatype == 'tvshow'):
                    info['imdbnumber' if provider == 'imdb' else provider + '_id'] = str(value)
            except Exception:
                pass
    elif mediatype == 'episode':
        # Source add-ons often put the SHOW's IDs into an episode's native
        # unique IDs. Only explicitly scoped properties are safe to capture.
        for scope, providers in (('episode', ('tmdb', 'tvdb')), ('tvshow', ('imdb', 'tmdb'))):
            for provider in providers:
                value = listitem.getProperty('%s.%s_id' % (scope, provider))
                if value:
                    info['%s_%s_id' % (scope, provider)] = value
    if info.get('dbid', 0) <= 0 or mediatype not in MEDIA_TYPES:
        info.pop('dbid', None)
    return info


# ---------------------------------------------------------------- properties

# Artist and album details are list item properties in Kodi, not info labels
# (CMusicDatabase::SetPropertiesFromArtist / SetPropertiesFromAlbum); skins read them
# as ListItem.Property(Artist_Description) and so on, and music add-ons use the same
# names. Kodi's Python API cannot list an item's properties, so they are read by name.
# The *_array variants hold lists the Python API cannot read and skins do not use.
CAPTURED_PROPERTIES = (
    'artist_description', 'artist_born', 'artist_formed', 'artist_died', 'artist_disbanded',
    'artist_yearsactive', 'artist_genre', 'artist_style', 'artist_mood', 'artist_instrument',
    'artist_type', 'artist_gender', 'artist_disambiguation', 'artist_sortname',
    'album_description', 'album_title', 'album_artist', 'album_genre', 'album_style',
    'album_mood', 'album_theme', 'album_type', 'album_label', 'album_releasetype',
    'album_rating', 'album_userrating', 'album_votes', 'album_duration', 'album_isboxset',
    'album_totaldiscs',
)

# Library lookups for the same details: JSON-RPC field -> Kodi property name.
_LIBRARY_PROPERTIES = {
    'artist': ('AudioLibrary.GetArtistDetails', 'artistid', 'artistdetails', {
        'description': 'artist_description', 'born': 'artist_born', 'formed': 'artist_formed',
        'died': 'artist_died', 'disbanded': 'artist_disbanded',
        'yearsactive': 'artist_yearsactive', 'genre': 'artist_genre', 'style': 'artist_style',
        'mood': 'artist_mood', 'instrument': 'artist_instrument', 'type': 'artist_type',
        'gender': 'artist_gender', 'disambiguation': 'artist_disambiguation',
        'sortname': 'artist_sortname'}),
    'album': ('AudioLibrary.GetAlbumDetails', 'albumid', 'albumdetails', {
        'description': 'album_description', 'title': 'album_title',
        'displayartist': 'album_artist', 'genre': 'album_genre', 'style': 'album_style',
        'mood': 'album_mood', 'theme': 'album_theme', 'type': 'album_type',
        'albumlabel': 'album_label', 'releasetype': 'album_releasetype',
        'rating': 'album_rating', 'userrating': 'album_userrating', 'votes': 'album_votes',
        'albumduration': 'album_duration', 'isboxset': 'album_isboxset',
        'totaldiscs': 'album_totaldiscs'}),
}


def _property_text(value):
    """Library value as Kodi would show it; '' when there is nothing to show."""
    if isinstance(value, bool):
        return 'true' if value else ''
    if isinstance(value, list):
        return ' / '.join(str(entry) for entry in value if entry)
    if isinstance(value, (int, float)):
        return '' if value == 0 else ('%g' % value)
    return str(value or '').strip()


def library_properties(mediatype, dbid):
    method = _LIBRARY_PROPERTIES.get(mediatype)
    if not method or dbid <= 0:
        return {}
    name, id_param, result_key, fields = method
    result = jsonrpc(name, {id_param: dbid, 'properties': list(fields)})
    details = (result or {}).get(result_key) or {}
    properties = {}
    for field, key in fields.items():
        text = _property_text(details.get(field))
        if text:
            properties[key] = text
    return properties


def capture_properties(listitem, mediatype='', dbid=0):
    """Artist/album details from the focused item, with the library filling gaps."""
    properties = {}
    for key in CAPTURED_PROPERTIES:
        value = listitem.getProperty(key) or xbmc.getInfoLabel('ListItem.Property(%s)' % key)
        if value:
            properties[key] = value
    for key, value in library_properties(mediatype, dbid).items():
        properties.setdefault(key, value)  # what the user saw wins; the library fills gaps
    return properties


def _call(setter, *args):
    try:
        setter(*args)
    except Exception as exc:
        xbmc.log('[plugin.program.metafave] metadata setter failed: %s' % exc, xbmc.LOGDEBUG)


def apply(listitem, info):
    if not info:
        return
    mediatype = info.get('mediatype', '')
    if mediatype in MUSIC_TYPES:
        _apply_music(listitem, info, mediatype)
    else:
        _apply_video(listitem, info, mediatype)


def _apply_video(listitem, info, mediatype):
    tag = listitem.getVideoInfoTag()
    unique = ({'tmdb': info.get('tmdb_id'), 'tvdb': info.get('tvdb_id')}
              if mediatype in ('movie', 'tvshow') else
              {'tmdb': info.get('episode_tmdb_id'), 'tvdb': info.get('episode_tvdb_id')}
              if mediatype == 'episode' else {})
    for provider, value in unique.items():
        if value:
            _call(tag.setUniqueID, str(value), provider)
    simple = {
        'title': tag.setTitle, 'originaltitle': tag.setOriginalTitle,
        'tvshowtitle': tag.setTvShowTitle, 'plot': tag.setPlot,
        'plotoutline': tag.setPlotOutline, 'tagline': tag.setTagLine, 'year': tag.setYear,
        'genre': tag.setGenres, 'studio': tag.setStudios, 'director': tag.setDirectors,
        'writer': tag.setWriters, 'mpaa': tag.setMpaa, 'duration': tag.setDuration,
        'season': tag.setSeason, 'episode': tag.setEpisode, 'album': tag.setAlbum,
        'trailer': tag.setTrailer, 'imdbnumber': tag.setIMDBNumber,
    }
    for key, setter in simple.items():
        if key in info:
            _call(setter, info[key])
    if 'artist' in info:
        _call(tag.setArtists, [info['artist']])
    if 'rating' in info:
        _call(tag.setRating, float(info['rating']), int(info.get('votes', 0)))
    elif 'votes' in info:
        _call(tag.setVotes, int(info['votes']))
    if mediatype in VIDEO_TYPES:
        _call(tag.setMediaType, mediatype)
        if info.get('dbid', 0) > 0:
            _call(tag.setDbId, int(info['dbid']))


def _apply_music(listitem, info, mediatype):
    tag = listitem.getMusicInfoTag()
    simple = {
        'title': tag.setTitle, 'artist': tag.setArtist, 'album': tag.setAlbum,
        'genre': tag.setGenres, 'year': tag.setYear, 'duration': tag.setDuration,
        'tracknumber': tag.setTrack, 'plot': tag.setComment,
    }
    for key, setter in simple.items():
        if key in info:
            _call(setter, info[key])
    if 'rating' in info:
        _call(tag.setRating, float(info['rating']))
    _call(tag.setMediaType, mediatype)
    if info.get('dbid', 0) > 0:
        _call(tag.setDbId, int(info['dbid']), mediatype)
