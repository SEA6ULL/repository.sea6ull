# -*- coding: utf-8 -*-
#
# Rotation — plugin.audio.rotation
# Copyright (C) 2019 L2501
# Modernised and extended by SEA6ULL / GPT (2026)
#
# Logging note: xbmc.log() only — Python's standard logging module
# produces zero output in Kodi addon context.

import concurrent.futures
import csv
import datetime
import hashlib
import io
import json
import math
import os
import random
import re
import sys
import threading
import time
import unicodedata
from urllib.parse import parse_qs, quote, unquote, urlparse, urlsplit, urlunsplit

import requests
from routing import Plugin
import xbmc
import xbmcgui
import xbmcaddon
import xbmcplugin
import xbmcvfs

from resources.lib.storage import FavoritesStore
from resources.lib.shared_sync import SharedFavoritesStore, shared_favorites_path
from resources.lib.user_playlists import UserPlaylistStore
from resources.lib.playlist_import import parse_playlist, HELP as PLAYLIST_IMPORT_HELP
from resources.lib.artwork import ArtworkProvider
from resources.lib.library import (LibraryIndex, library_artists, library_artist_art,
                                   library_artist_fanart, library_artist_logo,
                                   library_song_count, norm_artist, norm_title,
                                   primary_artist, track_identity_key, _similar)
from resources.lib.playlists import PlaylistSource
from resources.lib.provider_health import ProviderError, ProviderHealth
from resources.lib.musicmp3 import musicMp3

try:
    from xbmcvfs import translatePath
except ImportError:
    from xbmc import translatePath

# --------------------------------------------------------------------------- #
# Add-on setup
# --------------------------------------------------------------------------- #

addon  = xbmcaddon.Addon()
plugin = Plugin()
plugin.name = addon.getAddonInfo("name")

USER_DATA_DIR = translatePath(addon.getAddonInfo("profile"))
MEDIA_DIR     = os.path.join(translatePath(addon.getAddonInfo("path")), "resources", "media")
# Kodi caches textures by path, not by content. Replacing this image while
# keeping the filename means Kodi keeps serving the old one from
# Textures13.db - clear the texture cache after swapping it out.
FANART        = os.path.join(MEDIA_DIR, "fanart.jpg")
DATA_DIR      = os.path.join(USER_DATA_DIR, "data")
PLAYLIST_DIR  = os.path.join(USER_DATA_DIR, "playlists")
MUSICMP3_DIR = os.path.join(USER_DATA_DIR, "musicmp3")
USER_PLAYLIST_ART_DIR = os.path.join(USER_DATA_DIR, "user_playlist_art")
PLAYBACK_QUEUE_FILE = os.path.join(USER_DATA_DIR, "playback-lookahead.json")

# --------------------------------------------------------------------------- #
# Icons — bundled 1024px Phosphor Duotone tiles remain legible in artwork-led
# views such as Arctic Vibe's List Media instead of relying on skin textures.
# --------------------------------------------------------------------------- #

def _icon(name):
    # One stable asset directory keeps the package clean; Kodi's texture cache
    # can be cleared when the bundled artwork itself changes.
    return os.path.join(MEDIA_DIR, "icons", name + ".png")

ICON_ARTISTS = _icon("artists")
ICON_ALBUMS = _icon("albums")
ICON_TOP_ALBUMS = _icon("top-albums")
ICON_NEW_ALBUMS = _icon("new-albums")
ICON_SONGS = _icon("songs")
ICON_FAVORITES = _icon("favorites")
ICON_SHUFFLE = _icon("shuffle")
ICON_SEARCH = _icon("search")
ICON_GENRES = _icon("genres")
ICON_COMPILATIONS = _icon("compilations")
ICON_SOUNDTRACKS = _icon("soundtracks")
ICON_MAINTENANCE = _icon("maintenance")
ICON_NEXT_PAGE = _icon("next-page")
ICON_PLAYLISTS = _icon("playlists")
ICON_TOP_SONGS = _icon("top-songs")
ICON_RADIO = _icon("radio")
FALLBACK_ALBUM = os.path.join(MEDIA_DIR, "fallback-album.jpg")
FALLBACK_ARTIST = os.path.join(MEDIA_DIR, "fallback-artist.png")
UNAVAILABLE_AUDIO = os.path.join(MEDIA_DIR, "unavailable.mp3")

# --------------------------------------------------------------------------- #
# Artwork policy
#
# Album covers on the source site are layout-sized thumbnails (~200-300px).
# Using one as fanart makes Kodi upscale it to the full screen height, which
# is a 4-8x blow-up on a 1080p/4K display and looks badly pixelated.
#
# COVER_AS_FANART = False  -> playback + track items use the addon's own
#                             fallback fanart as the background. The cover still
#                             shows as thumb/icon at its native size, where
#                             it looks fine.
# COVER_AS_FANART = True   -> restores the old behaviour.
# --------------------------------------------------------------------------- #

COVER_AS_FANART = False


def _art(cover, fanart=None, fallback=FALLBACK_ALBUM, clearlogo=""):
    """
    Build a setArt() dict from a cover image and an optional background.

    thumb/icon always get the cover. When no cover resolved, icon falls back
    to a Kodi default texture so the row shows the skin's own placeholder
    rather than an empty tile. The background prefers a real landscape fanart
    when one was resolved (fanart.tv), falls back to the cover only when
    COVER_AS_FANART is on, and otherwise uses the addon's own fallback fanart.
    """
    cover = cover or ""
    display = cover or fallback
    if fanart:
        background = fanart
    elif COVER_AS_FANART and cover:
        background = cover
    else:
        background = FANART
    # Artwork-led skins commonly read thumb without falling back to icon.
    # Populate both so a missing cover consistently shows Rotation's tile
    # instead of a skin-specific blank/default-album texture.
    result = {"thumb": display, "icon": display, "fanart": background}
    if clearlogo:
        # clearlogo is Kodi's standard artist-logo key.  The logo alias also
        # helps older/custom skins which still query ListItem.Art(logo).
        result["clearlogo"] = clearlogo
        result["logo"] = clearlogo
    return result

if not os.path.exists(DATA_DIR):
    os.makedirs(DATA_DIR)
if not os.path.exists(MUSICMP3_DIR):
    os.makedirs(MUSICMP3_DIR)


def _notify_sync_error_once(message):
    """Explain a shared-folder failure without repeating it on every listing."""
    window = xbmcgui.Window(10000)
    key = "Rotation.SharedSyncError"
    if window.getProperty(key):
        return
    window.setProperty(key, "1")
    xbmcgui.Dialog().notification(
        plugin.name,
        message or "Shared sync needs read/write access to the Kodi music source.",
        xbmcgui.NOTIFICATION_ERROR, 8000)


def _favorites():
    local = FavoritesStore(DATA_DIR)
    if addon.getSetting("shared_favorites_sync") != "true":
        return local
    path = shared_favorites_path(USER_DATA_DIR)
    if not path:
        xbmc.log("[rotation] Shared favorites enabled, but Rotation could not "
                 "identify one shared Kodi music source.", xbmc.LOGWARNING)
        return local
    try:
        return SharedFavoritesStore(path, local)
    except Exception as exc:
        xbmc.log("[rotation] Shared favorites unavailable: %s" % exc,
                 xbmc.LOGWARNING)
        _notify_sync_error_once(str(exc))
        return local


def _user_playlists():
    """Use shared music storage when sync is enabled, otherwise local data."""
    local_directory = os.path.join(USER_DATA_DIR, "user_playlists")
    directory = local_directory
    if addon.getSetting("shared_favorites_sync") == "true":
        favorites_path = shared_favorites_path(USER_DATA_DIR)
        if favorites_path:
            try:
                SharedFavoritesStore(favorites_path, FavoritesStore(DATA_DIR))
                directory = (favorites_path.replace("\\", "/").rsplit("/", 1)[0]
                             + "/playlists")
            except (IOError, OSError) as exc:
                xbmc.log("[rotation] Shared playlists unavailable: %s" % exc,
                         xbmc.LOGWARNING)
                _notify_sync_error_once(str(exc))
    store = UserPlaylistStore(directory)
    if directory != local_directory:
        try:
            if not store.list():
                for row in UserPlaylistStore(local_directory).list():
                    store.save(row)
        except (IOError, OSError) as exc:
            xbmc.log("[rotation] Shared playlists unavailable: %s" % exc,
                     xbmc.LOGWARNING)
            _notify_sync_error_once(str(exc))
            return UserPlaylistStore(local_directory)
    return store


def _playlist_cover_source(source):
    """Read one local/VFS/HTTP artwork source into bytes for Pillow."""
    source = str(source or "").strip()
    if not source:
        return b""
    if source.startswith("image://"):
        source = unquote(source[8:]).rstrip("/")
    try:
        if source.startswith(("http://", "https://")):
            response = requests.get(source, timeout=8)
            response.raise_for_status()
            return response.content
        handle = __import__("xbmcvfs").File(source)
        data = handle.readBytes()
        handle.close()
        return data
    except Exception as exc:
        xbmc.log("[rotation] Playlist cover source failed for %s: %s" %
                 (source, exc), xbmc.LOGDEBUG)
        return b""


def _user_playlist_cover(row, regenerate=False):
    """Return a cached Spotify-style 2x2 mosaic for a user playlist."""
    try:
        from PIL import Image, ImageOps
    except ImportError:
        return ICON_PLAYLISTS
    os.makedirs(USER_PLAYLIST_ART_DIR, exist_ok=True)
    custom = str(row.get("cover_path") or "")
    if row.get("cover_mode") == "custom" and custom:
        try:
            if __import__("xbmcvfs").exists(custom): return custom
        except Exception: pass
    playlist_id = str(row.get("id") or "")
    modified = float(row.get("modified") or 0)
    # Imports contain artist/title only. Use the same learned metadata and
    # album cache as the song listing, rather than requiring persisted covers.
    cache = _load_availability()
    provider = _artwork_for_listing()
    sources = []
    for track in row.get("tracks", []):
        source = _playlist_display_cover(track, cache, provider)
        if source and source not in sources:
            sources.append(source)
        if len(sources) == 4:
            break
    # Older saved rows can have a playable file but no persisted cover. Fill
    # only the missing mosaic slots from Kodi's cached library index.
    if len(sources) < 4:
        try:
            index = _library()
            index.build()
            for track in row.get("tracks", []):
                song = index.resolve(
                    track.get("artist", ""), track.get("title", ""),
                    album=track.get("album", ""),
                    threshold=_match_threshold(),
                    allow_any_artist=_allow_any_artist())
                source = ((song or {}).get("thumb") or
                          ((song or {}).get("source") or {}).get("cover") or "")
                if source and source not in sources:
                    sources.append(source)
                if len(sources) == 4:
                    break
        except Exception as exc:
            xbmc.log("[rotation] Playlist cover library fallback failed: %s" % exc,
                     xbmc.LOGDEBUG)
    # Include the actual sources in the filename: Kodi must see a new texture
    # path when a partial mosaic gains artwork, even if no songs were edited.
    signature = hashlib.sha1(json.dumps(sources, ensure_ascii=False).encode(
        "utf-8")).hexdigest()[:16]
    revision = "%s-%s" % (max(0, int(modified * 1000)), signature)
    target = os.path.join(
        USER_PLAYLIST_ART_DIR, "%s-%s.jpg" % (playlist_id, revision))
    if os.path.exists(target) and not regenerate:
        return target
    images = []
    try:
        resize_filter = Image.Resampling.LANCZOS
    except AttributeError:
        resize_filter = (getattr(Image, "LANCZOS", None) or
                         getattr(Image, "ANTIALIAS", Image.BICUBIC))
    for source in sources:
        data = _playlist_cover_source(source)
        if not data:
            continue
        try:
            image = Image.open(io.BytesIO(data)).convert("RGB")
            images.append(ImageOps.fit(
                image, (300, 300), method=resize_filter))
        except Exception:
            continue
    if not images:
        return ICON_PLAYLISTS
    canvas = Image.new("RGB", (600, 600))
    if len(images) == 1:
        canvas.paste(ImageOps.fit(images[0], (600, 600), method=resize_filter), (0, 0))
    elif len(images) == 2:
        for image, position in zip(images, ((0, 0), (300, 0))): canvas.paste(ImageOps.fit(image, (300, 600), method=resize_filter), position)
    elif len(images) == 3:
        canvas.paste(ImageOps.fit(images[0], (300, 600), method=resize_filter), (0, 0)); canvas.paste(images[1], (300, 0)); canvas.paste(images[2], (300, 300))
    else:
        for image, position in zip(images[:4], ((0, 0), (300, 0), (0, 300), (300, 300))): canvas.paste(image, position)
    temporary = target + ".tmp"
    try:
        canvas.save(temporary, "JPEG", quality=92, optimize=True)
        os.replace(temporary, target)
        prefix = playlist_id + "-"
        for filename in os.listdir(USER_PLAYLIST_ART_DIR):
            if (filename.startswith(prefix) and filename.endswith(".jpg") and
                    not filename.endswith("-custom.jpg") and
                    filename != os.path.basename(target)):
                try:
                    os.remove(os.path.join(USER_PLAYLIST_ART_DIR, filename))
                except OSError:
                    pass
        return target
    except OSError as exc:
        xbmc.log("[rotation] Could not save user playlist cover: %s" % exc,
                 xbmc.LOGWARNING)
        return ICON_PLAYLISTS


def _musicmp3_enabled():
    """Whether MusicMP3.ru may be used after local-library matching fails."""
    return addon.getSetting("musicmp3_enabled") == "true"


def _make_musicmp3(timeout_override=None):
    try:
        timeout = int(addon.getSetting("request_timeout") or 15)
    except (ValueError, TypeError):
        timeout = 15
    if timeout_override is not None:
        timeout = max(1, min(timeout, int(timeout_override)))
    return musicMp3(MUSICMP3_DIR, timeout=timeout, cache_hours=6)


def _make_playlists(quiet=False):
    """Build a PlaylistSource from current settings."""
    try:
        ttl = int(addon.getSetting("playlist_cache_hours"))
    except (ValueError, TypeError):
        ttl = 12
    try:
        timeout = int(addon.getSetting("request_timeout"))
    except (ValueError, TypeError):
        timeout = 15

    return PlaylistSource(
        cache_dir=PLAYLIST_DIR,
        ttl_hours=ttl,
        timeout=timeout,
        lastfm_key=addon.getSetting("lastfm_apikey"),
        prefer_lastfm=addon.getSetting("playlist_prefer_lastfm") == "true",
        country=addon.getSetting("lastfm_country").strip() or "united states",
        interactive=not quiet,
    )












# The index is expensive to build and cheap to keep. reuselanguageinvoker is
# on for this addon, so module-level state survives between directory loads
# within the same Python instance and the index is built once per Kodi
# session rather than once per click.
_LIBRARY_INDEX = None


def _library():
    """Return the shared LibraryIndex, building it on first use."""
    global _LIBRARY_INDEX
    if _LIBRARY_INDEX is None:
        try:
            ttl = int(addon.getSetting("library_index_hours"))
        except (ValueError, TypeError):
            ttl = 24
        _LIBRARY_INDEX = LibraryIndex(PLAYLIST_DIR, ttl_hours=ttl)
    return _LIBRARY_INDEX


def _use_kodi_library():
    """Whether local Kodi music matching is enabled by the user."""
    return addon.getSetting("use_kodi_library") != "false"


def _match_threshold():
    """
    Fuzzy-title cutoff, stored as an integer percentage.

    Kodi's float slider is fussy about step/precision in settings format 1;
    an integer percentage slider is reliable and reads better in the UI.
    """
    try:
        return max(50, min(int(addon.getSetting("playlist_match_threshold")), 100)) / 100.0
    except (ValueError, TypeError):
        return 0.86


def _playlist_limit():
    try:
        return max(10, min(int(addon.getSetting("playlist_length")), 200))
    except (ValueError, TypeError):
        return 100


def _allow_any_artist():
    return addon.getSetting("playlist_loose_match") == "true"


def _unavailable_report_enabled():
    return addon.getSetting("playlist_unavailable_report") == "true"


def _unavailable_csv_enabled():
    value = addon.getSetting("playlist_unavailable_csv")
    return value != "false"


def _show_not_in_library_labels():
    """New installs default on; preserve that default if Kodi returns empty."""
    return addon.getSetting("show_not_in_library") != "false"


def _show_artist_album_years():
    """Append reliable release years to artist discography labels."""
    return addon.getSetting("show_artist_album_years") != "false"


def _hide_explicit_content():
    """Unknown metadata remains allowed; only a positive flag is filtered."""
    return addon.getSetting("explicit_content") == "hide"


def _end_action():
    """
    Close out an action route that was entered as a directory.

    Home screen actions are added with isFolder=True, so Kodi calls
    GetDirectory and shows DialogBusy until a directory is returned.
    Without this the spinner stays up until Kodi gives up on its own.
    """
    if plugin.handle >= 0:
        xbmcplugin.endOfDirectory(
            plugin.handle, succeeded=False, updateListing=False,
            cacheToDisc=False)
    folder = xbmc.getInfoLabel("Container.FolderPath") or ""
    if folder:
        _safe_background_refresh(folder, "action complete", cooldown=2)


def _page_size():
    try:
        return int(addon.getSetting("page_size"))
    except (ValueError, TypeError):
        return 40


def _artwork_enabled():
    return addon.getSetting("artwork_enabled") == "true"


def _artwork_listings_enabled():
    return _artwork_enabled() and addon.getSetting("artwork_listings") == "true"


def _artwork_async_enabled():
    return _artwork_enabled() and addon.getSetting("artwork_async") == "true"


def _artwork_artists_enabled():
    return _artwork_enabled() and addon.getSetting("artwork_artists") == "true"


def _artwork_backgrounds_enabled():
    return _artwork_artists_enabled() and addon.getSetting("artwork_backgrounds") == "true"


def _artwork_budget():
    """Seconds a grid listing may spend resolving artwork before giving up."""
    try:
        return float(addon.getSetting("artwork_budget"))
    except (ValueError, TypeError):
        return 5.0


def _make_artwork():
    """
    Build an ArtworkProvider, or None when external lookup is switched off.

    Returning None rather than a disabled object keeps the call sites
    explicit — every one of them has to decide what to do without artwork,
    which is the same thing it did before this feature existed.
    """
    if not _artwork_enabled():
        return None
    try:
        timeout = int(addon.getSetting("artwork_timeout"))
    except (ValueError, TypeError):
        timeout = 6
    try:
        if addon.getSetting("artwork_theaudiodb") == "true":
            audiodb_key = addon.getSetting("theaudiodb_apikey").strip() or "123"
        else:
            audiodb_key = ""
        return ArtworkProvider(
            cache_dir=DATA_DIR,
            fanarttv_key=addon.getSetting("fanarttv_apikey"),
            timeout=timeout,
            want_backgrounds=addon.getSetting("artwork_backgrounds") == "true",
            audiodb_key=audiodb_key,
        )
    except Exception as exc:
        xbmc.log("[rotation] Artwork provider unavailable: %s" % exc,
                 xbmc.LOGWARNING)
        return None


def _resolve_art(artist, album, site_cover):
    """
    Return (thumb, fanart) for an album, preferring external artwork.

    site_cover is the small cover scraped from the source site and is used
    whenever external lookup is off, fails, or finds nothing.
    """
    provider = _make_artwork()
    if not provider:
        return site_cover, ""

    try:
        found = provider.album_art(artist, album)
    except Exception as exc:
        xbmc.log("[rotation] Artwork lookup failed: %s" % exc, xbmc.LOGWARNING)
        return site_cover, ""

    return (found.get("thumb") or site_cover), found.get("fanart", "")


# Keys already handed to a background worker in this interpreter. Kodi keeps
# the Python process alive between invocations (reuselanguageinvoker), so
# this survives across navigations and stops a directory that never resolves
# from spawning a worker - and a refresh - every single time it is opened.
_ASYNC_STARTED = set()
_ASYNC_LOCK = threading.Lock()


def _current_plugin_path():
    """The plugin:// URL for the directory currently being built."""
    try:
        return sys.argv[0] + sys.argv[2]
    except IndexError:
        return ""


def _spawn_art_worker(artist, album, path):
    """
    Resolve artwork off the UI thread, then refresh the directory.

    The directory is returned immediately with fallback art so navigation
    is instant. When the worker finishes, it refreshes the container only
    if the user is still looking at it - refreshing a page they have
    already left would yank them backwards.
    """
    marker = (artist or "", album or "")
    with _ASYNC_LOCK:
        if marker in _ASYNC_STARTED:
            return
        _ASYNC_STARTED.add(marker)

    def _work():
        provider = None
        try:
            provider = _make_artwork()
            if not provider:
                return
            provider.album_art(artist, album)
        except Exception as exc:
            xbmc.log("[rotation] Background artwork worker failed: %s" % exc,
                     xbmc.LOGWARNING)
            return
        finally:
            if provider is not None:
                provider.close()
            with _ASYNC_LOCK:
                _ASYNC_STARTED.discard(marker)

        # Let the directory finish rendering before refreshing it
        xbmc.sleep(300)

        if not path:
            return
        current = xbmc.getInfoLabel("Container.FolderPath")
        if current == path:
            xbmc.log("[rotation] Artwork ready, refreshing %s" % path,
                     xbmc.LOGINFO)
            _safe_background_refresh(path, "album artwork")
        else:
            xbmc.log("[rotation] Artwork ready but user navigated away",
                     xbmc.LOGDEBUG)

    thread = threading.Thread(target=_work, name="rotation-artwork")
    thread.daemon = True
    thread.start()


def _resolve_art_nonblocking(artist, album, site_cover):
    """
    Return (thumb, fanart) without ever waiting on the network.

    Anything already cached is used immediately. Anything missing is left
    to a background worker and the caller falls back to site art plus the
    addon fanart, which is replaced on refresh once the worker finishes.
    """
    if not _artwork_async_enabled():
        return _resolve_art(artist, album, site_cover)

    provider = _make_artwork()
    if not provider or not artist or not album:
        return site_cover, ""

    try:
        cover = provider.album_art_cached(artist, album)
        background = provider.artist_background_cached(artist)
    except Exception:
        return site_cover, ""

    if cover is not None and background is not None:
        return (cover.get("thumb") or site_cover), (background or "")

    _spawn_art_worker(artist, album, _current_plugin_path())

    return (
        (cover.get("thumb") if cover else "") or site_cover,
        background or "",
    )


# Grid listings reuse one provider for the whole directory rather than
# building a new one per row.
_ARTWORK_SINGLETON = []
_GRID_ART_ACTIVE = set()
_GRID_ART_LOCK = threading.Lock()
_PLAYLIST_COVER_ACTIVE = set()
_PLAYLIST_COVER_LOCK = threading.Lock()
_PLAYLIST_COVER_TRIED = set()
_PLAYLIST_METADATA_ACTIVE = set()
_PLAYLIST_METADATA_LOCK = threading.Lock()


def _artwork_for_listing():
    if not _ARTWORK_SINGLETON:
        _ARTWORK_SINGLETON.append(_make_artwork())
    return _ARTWORK_SINGLETON[0]


def _bulk_covers_for(pairs):
    """
    Resolve covers for a list of (artist, album) pairs, concurrently.

    Returns {(artist, album): thumb_url}. Runs under a wall-clock budget,
    so a full 40-album page costs a second or two rather than 40 sequential
    round trips. Anything unresolved when the budget expires is simply
    absent from the dict and the caller falls back to site artwork.
    """
    if not _artwork_listings_enabled():
        return {}

    provider = _artwork_for_listing()
    if not provider:
        return {}

    pairs = [(a, b) for a, b in pairs if a and b]
    if not pairs:
        return {}

    try:
        found = provider.album_art_bulk(pairs, budget=_artwork_budget())
    except Exception as exc:
        xbmc.log("[rotation] Bulk artwork lookup failed: %s" % exc,
                 xbmc.LOGWARNING)
        return {}

    covers = {
        key: value.get("thumb", "")
        for key, value in found.items()
        if value and value.get("thumb")
    }
    pending = []
    for artist, album in dict.fromkeys(pairs):
        try:
            cover = provider.album_art_cached(artist, album)
            background = (provider.artist_background_cached(artist)
                          if provider.want_backgrounds else "")
            if cover is None or background is None:
                pending.append((artist, album))
        except Exception as exc:
            xbmc.log("[rotation] Grid artwork cache check failed: %s" % exc,
                     xbmc.LOGWARNING)
    if pending:
        _spawn_grid_art_worker(pending, _current_plugin_path())
    return covers


def _spawn_grid_art_worker(pairs, path):
    """Fill bulk misses through the album page's full lookup chain."""
    with _GRID_ART_LOCK:
        if path in _GRID_ART_ACTIVE:
            return
        _GRID_ART_ACTIVE.add(path)

    def _work():
        provider = None
        changed = 0
        refreshed = 0
        try:
            xbmc.sleep(500)  # Let the first grid finish loading.
            provider = _make_artwork()
            if not provider:
                return
            monitor = xbmc.Monitor()
            for artist, album in pairs:
                if monitor.abortRequested():
                    break
                try:
                    before_cover = provider.album_art_cached(artist, album) or {}
                    before_background = provider.artist_background_cached(artist) or ""
                    resolved = provider.album_art(artist, album)
                    if ((resolved.get("thumb") and
                         resolved["thumb"] != before_cover.get("thumb")) or
                        (resolved.get("fanart") and
                         resolved["fanart"] != before_background)):
                        changed += 1
                except Exception as exc:
                    xbmc.log("[rotation] Grid art failed for %r / %r: %s" %
                             (artist, album, exc), xbmc.LOGWARNING)
                if changed - refreshed >= 5:
                    _refresh_grid_art(path)
                    refreshed = changed
            if changed > refreshed:
                _refresh_grid_art(path)
        except Exception as exc:
            xbmc.log("[rotation] Grid artwork worker failed: %s" % exc,
                     xbmc.LOGWARNING)
        finally:
            if provider:
                provider.close()
            with _GRID_ART_LOCK:
                _GRID_ART_ACTIVE.discard(path)

    thread = threading.Thread(target=_work, name="rotation-grid-art")
    thread.daemon = True
    thread.start()


def _refresh_grid_art(path):
    return _safe_background_refresh(path, "grid artwork")


def _same_plugin_directory(current, expected):
    """Match a Kodi container to a plugin route despite URL formatting.

    Container.FolderPath is not guaranteed to preserve the query encoding or
    trailing slash used in sys.argv.  The route is enough for this guard: it
    prevents a Similar Artists worker from refreshing an artist detail page,
    while allowing Kodi's equivalent forms of /artists/similar to match.
    """
    def _route(value):
        value = unquote(value or "").split("?", 1)[0]
        return value.rstrip("/").casefold()
    return bool(current and expected and _route(current) == _route(expected))


def _visible_playlist_location():
    """Read this directory's marker before Kodi's skin-dependent folder label."""
    marker = xbmc.getInfoLabel("Container.Property(Rotation.PlaylistLocation)")
    return marker or xbmc.getInfoLabel("Container.FolderPath") or ""


def _same_plugin_location(current, expected):
    """Compare a full plugin location, including its decoded query values."""
    def _location(value):
        parsed = urlparse(value or "")
        route = parsed.path.rstrip("/").casefold()
        query = tuple(sorted(
            (key.casefold(), tuple(item.casefold() for item in values))
            for key, values in parse_qs(
                parsed.query, keep_blank_values=True).items()))
        return parsed.netloc.casefold(), route, query
    return bool(current and expected and _location(current) == _location(expected))


_PENDING_REFRESH_PROPERTY = "Rotation.PendingDirectoryRefresh"
_DEFERRED_ALARM_PROPERTY = "Rotation.DeferredRefreshAlarmUntil"
_DEFERRED_REFRESH_MAX_AGE = 30 * 60


def _queue_background_refresh(path, reason, service_only=False):
    """Remember one refresh and schedule a fallback independent of service.py."""
    if not path:
        return
    window = xbmcgui.Window(10000)
    now = time.time()
    try:
        previous = json.loads(window.getProperty(
            _PENDING_REFRESH_PROPERTY) or "{}")
    except (TypeError, ValueError):
        previous = {}
    created = (float(previous.get("created") or now)
               if _same_plugin_directory(previous.get("path"), path) else now)
    if now - created > _DEFERRED_REFRESH_MAX_AGE:
        window.clearProperty(_PENDING_REFRESH_PROPERTY)
        window.clearProperty(_DEFERRED_ALARM_PROPERTY)
        return
    payload = {"path": path, "reason": reason, "created": created}
    if service_only:
        payload["not_before"] = now + 5
    window.setProperty(
        _PENDING_REFRESH_PROPERTY,
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
    if service_only:
        # Run the service script in a separate invocation even after an update
        # stopped Kodi's startup service. Never re-enter default.py to redraw.
        service_path = os.path.join(translatePath(addon.getAddonInfo("path")),
                                    "resources", "lib", "artwork_refresh_1_0_18.py")
        xbmc.executebuiltin('RunScript("%s",refresh-only)' % service_path)
        return
    # Kodi can leave the old service process stopped after an in-place add-on
    # update. A silent RunPlugin alarm gives every queued refresh a second,
    # self-contained path without opening a directory busy dialog.
    try:
        alarm_until = float(window.getProperty(_DEFERRED_ALARM_PROPERTY) or 0)
    except (TypeError, ValueError):
        alarm_until = 0
    if alarm_until <= now:
        window.setProperty(_DEFERRED_ALARM_PROPERTY, str(now + 5))
        url = plugin.url_for(deferred_directory_refresh)
        xbmc.executebuiltin(
            "AlarmClock(RotationDeferredRefresh,RunPlugin(%s),00:00:04,silent,false)"
            % url)


def _clear_queued_refresh(path):
    window = xbmcgui.Window(10000)
    try:
        pending = json.loads(window.getProperty(_PENDING_REFRESH_PROPERTY) or "{}")
    except (TypeError, ValueError):
        pending = {}
    if _same_plugin_directory(pending.get("path"), path):
        window.clearProperty(_PENDING_REFRESH_PROPERTY)


def _safe_background_refresh(path, reason="background update", cooldown=15,
                             allow_navigation=False, allow_audio=False):
    """Refresh cached artwork/metadata only when it cannot race user actions.

    Kodi 21 exits the application when two busy dialogs overlap. Background
    RunPlugin/Container.Refresh operations used to race a context-menu Play or
    Shuffle action. Cosmetic updates now yield whenever Kodi is not idle.
    """
    if not path:
        return False
    if not _same_plugin_location(_visible_playlist_location(), path):
        _queue_background_refresh(path, reason)
        return False
    if xbmc.Player().isPlayingAudio() and not allow_audio:
        _queue_background_refresh(path, reason)
        return False
    # Kodi window aliases differ between platforms/skins. Numeric IDs are
    # stable and getCurrentWindowDialogId also catches dialogs a skin creates.
    try:
        dialog_active = (xbmc.getCondVisibility("System.HasActiveModalDialog") or
                         xbmcgui.getCurrentWindowDialogId() in (10106, 10138, 10160, 12000))
        player_window = xbmcgui.getCurrentWindowId() in (12005, 12006)
    except (AttributeError, RuntimeError):
        dialog_active = xbmc.getCondVisibility("System.HasActiveModalDialog")
        player_window = False
    try:
        play_action_active = time.time() < float(
            xbmcgui.Window(10000).getProperty("Rotation.PlayActionUntil") or 0)
    except (TypeError, ValueError):
        play_action_active = False
    if dialog_active or player_window or play_action_active:
        xbmc.log("[rotation] Skipped %s refresh while a dialog is active" % reason,
                 xbmc.LOGDEBUG)
        _queue_background_refresh(path, reason)
        return False
    if (not allow_navigation and
            not xbmc.getCondVisibility("System.IdleTime(3)")):
        xbmc.log("[rotation] Skipped %s refresh during user activity" % reason,
                 xbmc.LOGDEBUG)
        _queue_background_refresh(path, reason)
        return False
    window = xbmcgui.Window(10000)
    key = "Rotation.SafeRefresh.%s" % hashlib.sha1(
        path.encode("utf-8")).hexdigest()[:16]
    try:
        if time.time() - float(window.getProperty(key) or 0) < cooldown:
            _queue_background_refresh(path, reason)
            return False
    except (TypeError, ValueError):
        pass
    window.setProperty(key, str(time.time()))
    _clear_queued_refresh(path)
    xbmc.log("[rotation] Safe directory refresh: %s" % reason, xbmc.LOGINFO)
    xbmc.executebuiltin("Container.Refresh")
    return True


@plugin.route("/refresh/deferred")
def deferred_directory_refresh():
    """Retry one queued redraw safely even when the service was not restarted."""
    window = xbmcgui.Window(10000)
    window.clearProperty(_DEFERRED_ALARM_PROPERTY)
    try:
        pending = json.loads(window.getProperty(
            _PENDING_REFRESH_PROPERTY) or "{}")
    except (TypeError, ValueError):
        pending = {}
    if pending.get("not_before"):
        return
    path = pending.get("path") or ""
    created = float(pending.get("created") or 0)
    if not path or time.time() - created > _DEFERRED_REFRESH_MAX_AGE:
        window.clearProperty(_PENDING_REFRESH_PROPERTY)
        return
    _safe_background_refresh(
        path, pending.get("reason") or "deferred update", cooldown=3,
        allow_navigation=True, allow_audio=True)


def _refresh_artist_listing_when_visible(path, timeout_ms=5000):
    """Refresh once Kodi has committed the artist directory being built.

    Fast providers can finish before endOfDirectory() replaces the previous
    container.  A single immediate FolderPath check then misses the refresh,
    leaving the newly cached artwork invisible until the user reopens the
    page.  Wait briefly for the requested path to become active, but never
    refresh a different directory.
    """
    if not path:
        return False
    monitor = xbmc.Monitor()
    waited = 0
    while waited <= timeout_ms and not monitor.abortRequested():
        if _same_plugin_directory(
                xbmc.getInfoLabel("Container.FolderPath"), path):
            return _safe_background_refresh(path, "artist artwork")
        xbmc.sleep(100)
        waited += 100
    return False


def _bulk_covers(entries):
    """Convenience wrapper for scraped album entries (artist + title keys)."""
    return _bulk_covers_for(
        [(e.get("artist", ""), e.get("title", "")) for e in entries]
    )


# How many uncached artists one visit to a listing will resolve.
#
# This was 12 because portraits came from fanart.tv, which needs a
# MusicBrainz ID first, and MusicBrainz allows one request per second
# globally - so 12 artists meant a 12-second wait and a 50-artist page took
# five visits to fill. Deezer needs no MBID and no throttle, so a full page
# now fits in one pass.
ARTIST_BATCH = 50


def _artist_thumbs(names, queue_missing=True, refresh_path=None):
    """
    Cached artist portraits for a listing, plus a worker for the rest.

    Returns {artist: thumb_url} for artists already resolved. Anything
    uncached is handed to a background thread, which resolves up to
    ARTIST_BATCH of them and refreshes the directory when it finishes.
    Nothing here waits on the network.
    """
    if not _artwork_artists_enabled():
        xbmc.log("[rotation] artist portraits are switched off "
                 "(Settings > Artwork)", xbmc.LOGINFO)
        return {}

    provider = _artwork_for_listing()
    if not provider:
        return {}

    thumbs, pending, misses = {}, [], 0
    for name in names:
        if not name:
            continue
        try:
            cached = provider.artist_thumb_cached(name)
            logo = provider.artist_logo_cached(name)
        except Exception:
            continue
        if cached is None or logo is None:
            pending.append(name)
        elif cached:
            thumbs[name] = cached
        else:
            misses += 1

    # Without this line the whole feature can do nothing and leave no trace
    # in kodi.log, which is exactly how the missing-thumbs bug hid.
    xbmc.log("[rotation] artist portraits: %d cached, %d recorded as having "
             "none, %d to look up (resolving %d now)"
             % (len(thumbs), misses, len(pending),
                min(len(pending), ARTIST_BATCH)),
             xbmc.LOGINFO)

    if pending and queue_missing:
        _spawn_artist_worker(
            pending[:ARTIST_BATCH], refresh_path or _current_plugin_path())

    return thumbs


def _queue_artist_backgrounds(names, path):
    """Resolve uncached non-library fanart/logos even when a thumb exists."""
    if not _artwork_artists_enabled():
        return
    provider = _artwork_for_listing()
    if not provider:
        return
    pending = []
    for name in names:
        if not name:
            continue
        try:
            background_done = (bool(library_artist_fanart(name)) or
                               provider.artist_background_cached(name) is not None)
            logo_done = (bool(library_artist_logo(name)) or
                         provider.artist_logo_cached(name) is not None)
            if not background_done or not logo_done:
                pending.append(name)
        except Exception:
            continue
    if pending:
        _spawn_artist_worker(pending[:ARTIST_BATCH], path)


def _ensure_artist_art(artist):
    """
    Make sure one artist's portrait reaches the cache.

    Opening an artist's page is the strongest signal there is that this
    artist matters, but the album grid resolves covers through
    album_art_bulk(), which only ever touches album rows. So an artist
    could be opened a dozen times and still have no portrait on the
    listing you came from - covers filled in, thumb still blank.
    """
    if not artist or not _artwork_artists_enabled():
        return
    provider = _artwork_for_listing()
    if not provider:
        return
    try:
        if (provider.artist_thumb_cached(artist) is not None and
                provider.artist_logo_cached(artist) is not None):
            return
    except Exception:
        return
    # Empty path: nothing on this screen shows the artist portrait, so the
    # worker has no reason to refresh it when it finishes.
    _spawn_artist_worker([artist], "")


def _spawn_artist_worker(names, path):
    """
    Resolve a batch of artist portraits off the UI thread, then refresh.

    Runs concurrently. This used to be a sequential loop, on the reasoning
    that the MusicBrainz throttle serialised the calls anyway - true when
    every portrait came from fanart.tv, and no longer true now that Deezer
    answers most of them without an MBID. When a fanart.tv key is set the
    throttle still serialises that half; it is a global lock inside the
    provider, so the pool waits on it rather than working around it.
    """
    marker = ("__artists__", tuple(sorted(names)))
    with _ASYNC_LOCK:
        if marker in _ASYNC_STARTED:
            return
        _ASYNC_STARTED.add(marker)

    def _work():
        provider = None
        resolved = 0
        try:
            provider = _make_artwork()
            if not provider:
                return

            def _one(name):
                try:
                    return bool(provider.artist_art(name).get("thumb"))
                except Exception as exc:
                    xbmc.log("[rotation] Artist art failed for %s: %s"
                             % (name, exc), xbmc.LOGWARNING)
                    return False

            with concurrent.futures.ThreadPoolExecutor(
                max_workers=min(8, max(1, len(names)))
            ) as pool:
                resolved = sum(1 for hit in pool.map(_one, names) if hit)
        finally:
            if provider is not None:
                provider.close()

        xbmc.log("[rotation] artist worker: %d of %d resolved"
                 % (resolved, len(names)), xbmc.LOGINFO)

        if not resolved:
            return

        xbmc.sleep(300)
        if _refresh_artist_listing_when_visible(path):
            xbmc.log("[rotation] %d artist portraits ready, refreshing"
                     % resolved, xbmc.LOGINFO)
        elif path:
            xbmc.log("[rotation] Artist artwork ready but listing is no "
                     "longer visible", xbmc.LOGDEBUG)

    thread = threading.Thread(target=_work, name="rotation-artists")
    thread.daemon = True
    thread.start()


def _favorite_pair(favorite, kind):
    """
    The (artist, album) pair to look artwork up by for a saved favorite.

    Albums store the album name in "label"; songs store it in "album".
    Artist favorites have no album at all, so they get an empty pair and
    are skipped by the lookup.
    """
    artist = favorite.get("artist", "")
    if kind == "album":
        return (artist, favorite.get("label", ""))
    if kind == "song":
        return (artist, favorite.get("album", ""))
    return ("", "")


def _favorite_artist_name(favorite, kind):
    """
    The artist a saved favorite belongs to, for portrait and background
    lookup.

    Artist favorites keep the name in "label" and leave "artist" empty -
    the save action had nothing else to put in the label, so the artist
    column was never filled. Album and song favorites do carry it.
    """
    if kind == "artist":
        return favorite.get("artist") or favorite.get("label", "")
    return favorite.get("artist", "")


def _cached_background(artist):
    """
    Artist background from cache only - never blocks a listing.

    Used by favorites, where waiting on fanart.tv for every row would
    make the screen crawl. Uncached artists simply show the fallback until
    their album page has been opened once.
    """
    if not artist:
        return ""
    local = library_artist_fanart(artist)
    if local:
        return local
    provider = _artwork_for_listing()
    if not provider:
        return ""
    try:
        return provider.artist_background_cached(artist) or ""
    except Exception:
        return ""


def _cached_logo(artist):
    """Library-first artist clearlogo lookup without blocking the UI."""
    if not artist:
        return ""
    local = library_artist_logo(artist)
    if local:
        return local
    provider = _artwork_for_listing()
    if not provider:
        return ""
    try:
        return provider.artist_logo_cached(artist) or ""
    except Exception:
        return ""


_PLAYLIST_ART_ACTIVE = set()
_PLAYLIST_ART_LOCK = threading.Lock()
_PLAYLIST_PROGRESS_ACTIVE = set()
_PLAYLIST_PROGRESS_LOCK = threading.Lock()


def _playlist_art_key(path):
    parsed = urlparse(path)
    location = (parsed.netloc.casefold(), parsed.path.rstrip("/").casefold(),
                sorted((key.casefold(), sorted(v.casefold() for v in values))
                       for key, values in parse_qs(
                           parsed.query, keep_blank_values=True).items()))
    return "rotation_playlist_art_" + hashlib.sha1(
        json.dumps(location, ensure_ascii=False).encode("utf-8")).hexdigest()


def _playlist_art_state_key(path, suffix):
    return _playlist_art_key(path) + "_" + suffix


def _playlist_scan_done_key(folder):
    return _playlist_scan_key(folder) + "_done"


def _show_playlist_progress(path, folder):
    """Keep one nonmodal progress indicator open while either lookup runs."""
    if not path:
        return
    window = xbmcgui.Window(10000)
    art_key = _playlist_art_key(path)
    scan_key = _playlist_scan_key(folder) if folder else ""
    if not window.getProperty(art_key) and not (scan_key and window.getProperty(scan_key)):
        return
    # Module locks do not cover Kodi's separate Python invocations. All
    # directory rebuilds must share one native progress-dialog owner.
    owner_key = art_key + "_progress_owner"
    with _PLAYLIST_PROGRESS_LOCK:
        if art_key in _PLAYLIST_PROGRESS_ACTIVE:
            return
        try:
            if time.time() - float(window.getProperty(owner_key).rsplit("|", 1)[-1]) < 10:
                return
        except (TypeError, ValueError):
            pass
        owner_token = str(time.time())
        window.setProperty(owner_key, owner_token + "|" + str(time.time()))
        _PLAYLIST_PROGRESS_ACTIVE.add(art_key)

    def _work():
        dialog = None
        last = None
        away_since = None
        seen_playlist = False
        was_scanning = False
        last_scan = (0, 0, 0)
        last_art_total = 0
        phase_finished_since = None
        monitor = xbmc.Monitor()
        try:
            while not monitor.abortRequested():
                if not window.getProperty(owner_key).startswith(owner_token + "|"):
                    break
                window.setProperty(owner_key, owner_token + "|" + str(time.time()))
                current = _visible_playlist_location()
                at_playlist = _same_plugin_location(current, path)
                if not at_playlist:
                    if away_since is None:
                        away_since = time.monotonic()
                    # A refresh can briefly clear FolderPath; tolerate that.
                    if time.monotonic() - away_since >= (3 if seen_playlist else 30):
                        xbmc.log("[rotation] Playlist progress left directory: expected=%r visible=%r" %
                                 (path, current), xbmc.LOGINFO)
                        break
                else:
                    seen_playlist = True
                    away_since = None

                scan = window.getProperty(scan_key) if scan_key else ""
                art = window.getProperty(art_key)
                completed_scan = window.getProperty(_playlist_scan_done_key(folder)) if folder else ""
                scan_done = bool(completed_scan)
                if not scan and "/" in completed_scan:
                    try:
                        last_scan = tuple(int(n) for n in completed_scan.split("/"))
                    except ValueError:
                        pass
                if not scan and not art:
                    # Availability completion and artwork reservation occur
                    # in different invocations. Keep the same dialog through
                    # that short handoff rather than close and reopen it.
                    if phase_finished_since is None:
                        phase_finished_since = time.monotonic()
                    if time.monotonic() - phase_finished_since >= 2:
                        break
                    if monitor.waitForAbort(0.1):
                        break
                    continue
                phase_finished_since = None
                # Text states reserve the worker but contain no real counts.
                # Never turn them into a synthetic Songs 0/0 display.  The
                # availability worker will publish numeric counts after the
                # first track has actually been checked.
                scan_waiting = scan in ("pending", "building")
                if scan_waiting and not art:
                    if monitor.waitForAbort(0.1):
                        break
                    continue
                if scan_waiting:
                    scan = ""
                was_scanning = was_scanning or bool(scan)
                if art and (scan or last_scan[2]) and not scan_done:
                    heading = "Updating Artwork and Song Availability"
                elif art:
                    heading = "Finishing Artwork Update" if (was_scanning or scan_done) else "Updating Artwork"
                else:
                    heading = "Checking Song Availability"
                try:
                    if scan:
                        last_scan = tuple(int(n) for n in scan.split("/"))
                    s_checked, _, s_total = last_scan
                    if scan_done:
                        s_checked = s_total
                    a_checked, a_total = (int(n) for n in art.split("/")) if art else (0, 0)
                except ValueError:
                    s_checked = s_total = a_checked = a_total = 0
                if art:
                    last_art_total = a_total
                elif scan and last_art_total:
                    a_checked = a_total = last_art_total
                progress = round(100.0 * (s_checked + a_checked) / max(1, s_total + a_total))
                details = []
                if (scan and last_scan[2]) or (art and last_scan[2]):
                    details.append("Songs %d/%d" % (s_checked, s_total))
                if art or (scan and last_art_total):
                    details.append("Artists %d/%d" % (a_checked, a_total))
                state = (heading, " · ".join(details), progress)
                if state != last and at_playlist:
                    if dialog is None:
                        dialog = xbmcgui.DialogProgressBG()
                        dialog.create(plugin.name, heading)
                        xbmc.log("[rotation] Playlist progress opened: %s" % path,
                                 xbmc.LOGINFO)
                    dialog.update(progress, heading, state[1])
                    last = state
                if monitor.waitForAbort(0.4):
                    break
        finally:
            if dialog:
                dialog.close()
            # Release only after native dialog.close has returned, so the
            # independent redraw never overlaps progress-dialog teardown.
            if window.getProperty(owner_key).startswith(owner_token + "|"):
                window.clearProperty(owner_key)
            with _PLAYLIST_PROGRESS_LOCK:
                _PLAYLIST_PROGRESS_ACTIVE.discard(art_key)

    thread = threading.Thread(target=_work, name="rotation-playlist-progress")
    thread.daemon = True
    thread.start()


def _playlist_backgrounds(artists, path, folder="", refresh=True, background=True):
    """Fill missing artist backgrounds without delaying playlist browsing.

    The artwork provider retains its normal fanart.tv vote ranking. Work from
    the full provider list so refreshes cannot change the progress total.
    """
    provider = _artwork_for_listing()
    if not provider or not path or not _artwork_artists_enabled():
        return
    names = [name for name in dict.fromkeys(artists) if name]
    window = xbmcgui.Window(10000)
    art_key = _playlist_art_key(path)
    running_key = _playlist_art_state_key(path, "running")
    done_key = _playlist_art_state_key(path, "done")
    fingerprint = hashlib.sha1("\n".join(names).encode("utf-8")).hexdigest()
    pending = []
    for artist in names:
        if not artist:
            continue
        try:
            background_missing = (provider.want_backgrounds and
                                  not library_artist_fanart(artist) and
                                  provider.artist_background_cached(artist) is None)
            thumb_missing = (not library_artist_art(artist).get("thumb") and
                             provider.artist_thumb_cached(artist) is None)
            logo_missing = (not library_artist_logo(artist) and
                            provider.artist_logo_cached(artist) is None)
            if background_missing or thumb_missing or logo_missing:
                pending.append(artist)
        except Exception as exc:
            xbmc.log("[rotation] Playlist artwork cache check failed for %r: %s"
                     % (artist, exc), xbmc.LOGWARNING)
    if not pending:
        return

    with _PLAYLIST_ART_LOCK:
        if path in _PLAYLIST_ART_ACTIVE:
            return
        try:
            prior, timestamp = window.getProperty(done_key).split(":", 1)
            if prior == fingerprint and time.time() - float(timestamp) < 1800:
                return
        except (ValueError, TypeError):
            pass
        try:
            if time.time() - float(window.getProperty(running_key)) < 120:
                return
        except (ValueError, TypeError):
            pass
        _PLAYLIST_ART_ACTIVE.add(path)
        window.setProperty(running_key, str(time.time()))
        window.clearProperty(done_key)
    window.setProperty(art_key, "0/%d" % len(pending))

    def _refresh():
        _safe_background_refresh(path, "playlist artist artwork")

    def _work():
        worker = None
        changed = 0
        backgrounds = 0
        completed = False
        try:
            worker = _make_artwork()
            if not worker:
                return
            monitor = xbmc.Monitor()
            for checked, artist in enumerate(pending, 1):
                if monitor.abortRequested():
                    break
                try:
                    art = worker.artist_art(artist)
                    if art.get("fanart"):
                        backgrounds += 1
                    if any(art.get(key) for key in ("thumb", "fanart", "clearlogo")):
                        changed += 1
                except Exception as exc:
                    xbmc.log("[rotation] Playlist background failed for %r: %s"
                             % (artist, exc), xbmc.LOGWARNING)
                window.setProperty(art_key, "%d/%d" % (checked, len(pending)))
                window.setProperty(running_key, str(time.time()))
            completed = not monitor.abortRequested()
            xbmc.log("[rotation] Playlist artist backgrounds: %d of %d found"
                     % (backgrounds, len(pending)), xbmc.LOGINFO)
        finally:
            if completed:
                window.setProperty(done_key, "%s:%s" % (fingerprint, time.time()))
            if background:
                window.clearProperty(art_key)
            window.clearProperty(running_key)
            if worker:
                worker.close()
            with _PLAYLIST_ART_LOCK:
                _PLAYLIST_ART_ACTIVE.discard(path)
            # Publish completion before the new plugin invocation can begin.
            # Even thumbnail-only hits need a redraw; absent fanart is normal.
            if changed and refresh:
                _refresh()

        return bool(changed)

    if not background:
        return _work()

    thread = threading.Thread(target=_work, name="rotation-playlist-art")
    thread.daemon = True
    thread.start()


def _grid_cover(covers, artist, album, site_cover):
    """Pick the resolved cover for a grid row, else the site thumbnail."""
    return covers.get((artist, album)) or site_cover


def _genre_icon(genre_name):
    """
    Icon for a genre row.

    Kodi's default set has one genre icon, not one per genre, so every row
    gets the same guitar and the label carries the distinction. Compilations
    and Soundtracks are the two names that have a better match available.
    """
    if genre_name == "Compilations":
        return ICON_COMPILATIONS
    if genre_name == "Soundtracks":
        return ICON_SOUNDTRACKS
    return ICON_GENRES


def _sub_genre_icon(parent_name, sub_name):
    return _genre_icon(sub_name)


def _set_view(content_type):
    """Describe the content and let the active skin choose its own view."""
    xbmcplugin.setContent(plugin.handle, content_type)



def _link_url(route_func, link):
    return plugin.url_for(route_func) + "?link=" + quote(link, safe="")


def _get_link():
    return unquote(plugin.args.get("link", [""])[0])


def _set_music_tag(li, title="", artist="", album="", year="", genre="",
                   duration=0, tracknumber=0, description=""):
    """Populate a ListItem MusicInfoTag using the Kodi 19+ API.

    setAlbumArtist() is called alongside setArtist() whenever an artist is
    present. When content type is 'albums', Kodi and most skins (including
    Aeon Nox Silvo) display albumartist — not artist — as the subtitle on
    album ListItems. setArtist() alone populates the track-level field, which
    skins only read in song/track context. Both fields must be set so the
    artist name appears correctly in both album listings and track listings.
    """
    tag = li.getMusicInfoTag()
    if title:  tag.setTitle(title)
    if artist:
        tag.setArtist(artist)
        tag.setAlbumArtist(artist)
    if album:       tag.setAlbum(album)
    if year:
        try:        tag.setYear(int(str(year)[:4]))
        except (ValueError, TypeError): pass
    if genre:
        tag.setGenres([g.strip() for g in genre.split(",")] if "," in genre else [genre])
    if duration:
        try:        tag.setDuration(int(float(duration)))
        except (ValueError, TypeError): pass
    if tracknumber: tag.setTrack(tracknumber)
    if description: tag.setComment(description)


def _kind_label(kind):
    """Map kind key to a human-readable list name shown in context menus."""
    return {"song": "My Songs", "album": "My Albums", "artist": "My Artists",
            "playlist": "Followed Playlists",
            "radio": "Favorite Radio"}.get(kind, "My List")


def _favorite_add_action(kind, url, label, thumb="", artist="", album=""):
    """Build the RunPlugin URL string for the Save to <list> action."""
    # Provider metadata occasionally contains explicit JSON null values (most
    # often for album artwork). urllib.quote accepts text/bytes only, so make
    # every optional field safe before constructing context menu actions.
    encode = lambda value: quote(str(value or ""), safe="")
    action = (
        plugin.url_for(favorite_add)
        + "?kind="  + encode(kind)
        + "&url="   + encode(url)
        + "&label=" + encode(label)
        + "&thumb=" + encode(thumb)
        + "&artist="+ encode(artist)
        + "&album=" + encode(album)
    )
    return "RunPlugin({0})".format(action)


def _favorite_remove_action(url):
    """Build the RunPlugin URL string for the Remove from <list> action."""
    action = (plugin.url_for(favorite_remove) + "?url="
              + quote(str(url or ""), safe=""))
    return "RunPlugin({0})".format(action)


def _favorite_reorder_action(kind):
    action = (plugin.url_for(favorite_reorder)
              + "?kind=" + quote(kind, safe=""))
    return "RunPlugin({0})".format(action)


def _favorite_display_label(favorite, kind):
    if kind in ("album", "song") and favorite.get("artist"):
        return "{0} — {1}".format(favorite["artist"], favorite["label"])
    return favorite["label"]


def _favorite_context(api, kind, url, label, thumb="", artist="", album=""):
    """
    Return context menu entry for albums and artists (Save OR Remove, not both).
    Shows whichever action is relevant based on current saved state.
    Label is kind-specific: My Albums, My Artists.
    """
    kl = _kind_label(kind)
    if kind == "radio":
        action_label = (label.rsplit(" Radio", 1)[0]
                        if album.startswith("library_radio:") else
                        (artist or label.replace(" Radio", "")))
        if api.is_favorite(url):
            return [("✕  Remove {0} Radio from Favorites".format(action_label),
                     _favorite_remove_action(url))]
        return [("★  Add {0} Radio to Favorites".format(action_label),
                 _favorite_add_action(kind, url, label, thumb, artist, album))]
    if api.is_favorite(url):
        return [("✕  Remove from {0}".format(kl), _favorite_remove_action(url))]
    return [("★  Save to {0}".format(kl), _favorite_add_action(kind, url, label, thumb, artist, album))]


def _song_favorite_context(api, kind, url, label, thumb="", artist="", album=""):
    """
    Return BOTH Save and Remove context menu entries for song items.
    Both are always visible so you can save or remove without re-opening the menu.
    Label is kind-specific: My Songs.
    """
    kl = _kind_label(kind)
    return [
        ("★  Save to {0}".format(kl),      _favorite_add_action(kind, url, label, thumb, artist, album)),
        ("✕  Remove from {0}".format(kl),  _favorite_remove_action(url)),
    ]


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def _notify_error(msg):
    """Show a visible error toast in Kodi and log it."""
    xbmc.log("[rotation] error: %s" % msg, xbmc.LOGWARNING)
    xbmcgui.Dialog().notification(
        plugin.name, msg, xbmcgui.NOTIFICATION_ERROR, 5000
    )


def _country_name():
    """Human-readable name for the configured local chart country."""
    value = (addon.getSetting("lastfm_country") or "").strip()
    if not value:
        return "Your Country"
    if len(value) <= 3 and value.isalpha():
        return value.upper()
    return " ".join(word.capitalize() for word in value.split())


# --------------------------------------------------------------------------- #
# Routes
# --------------------------------------------------------------------------- #

@plugin.route("/")
def index():
    _reconcile_imported_unavailable()
    items = [
        ("For You",                 plugin.url_for(for_you_root),               ICON_FAVORITES),
        ("My Music",                plugin.url_for(my_music_root),               ICON_PLAYLISTS),
        ("Artists",                 plugin.url_for(artists_root),               ICON_ARTISTS),
        ("Discover",                plugin.url_for(playlists_root),             ICON_SEARCH),
        ("Maintenance",             plugin.url_for(maintenance),                ICON_MAINTENANCE),
    ]
    for label, url, icon in items:
        li = xbmcgui.ListItem(label)
        li.setArt({"fanart": FANART, "icon": icon})
        xbmcplugin.addDirectoryItem(plugin.handle, url, li, True)
    xbmcplugin.endOfDirectory(plugin.handle, cacheToDisc=False)


def _lastfm_username():
    return (addon.getSetting("lastfm_username") or "").strip()


@plugin.route("/my_music")
def my_music_root():
    items = [
        ("My Playlists", plugin.url_for(user_playlists_root), ICON_PLAYLISTS),
        ("Followed Playlists", plugin.url_for(favorites, "playlist"), ICON_FAVORITES),
        ("Favorite Radio", plugin.url_for(favorites, "radio"), ICON_RADIO),
        ("Favorite Artists", plugin.url_for(favorites, "artist"), ICON_ARTISTS),
    ]
    if _use_kodi_library():
        items.append(("Library Radio", plugin.url_for(library_radio_root), ICON_RADIO))
    for label, url, icon in items:
        li = xbmcgui.ListItem(label)
        li.setArt({"thumb": icon, "icon": icon, "fanart": FANART})
        xbmcplugin.addDirectoryItem(plugin.handle, url, li, True)
    xbmcplugin.endOfDirectory(plugin.handle, cacheToDisc=False)


@plugin.route("/for_you")
def for_you_root():
    username = _lastfm_username()
    if not username:
        li = xbmcgui.ListItem("Connect Last.fm")
        li.setLabel2("Enter your username to personalize Rotation")
        li.setArt({"thumb": ICON_FAVORITES, "icon": ICON_FAVORITES,
                   "fanart": FANART})
        xbmcplugin.addDirectoryItem(
            plugin.handle, plugin.url_for(for_you_configure), li, False)
        xbmcplugin.endOfDirectory(plugin.handle, cacheToDisc=False)
        return
    items = [
        ("Recommended Radio", "recommended", ICON_RADIO, True),
        ("Your Top Tracks", "top_tracks", ICON_TOP_SONGS, True),
        ("Your Top Artists", "top_artists", ICON_ARTISTS, True),
        ("Recently Played", "recent", ICON_TOP_SONGS, True),
        ("Rediscover Favorites", "rediscover", ICON_FAVORITES, True),
        ("New Albums from Artists You Play", "new_albums", ICON_NEW_ALBUMS, True),
        ("Forgotten Favorites", "forgotten", ICON_SHUFFLE, True),
    ]
    for label, mode, icon, is_folder in items:
        route_mode = mode
        if mode == "recommended":
            # Put the session directly in the folder URL. Kodi can open this
            # like every other directory; no redirecting launcher is needed.
            token = hashlib.sha1((
                username + str(time.time()) + str(random.random())
            ).encode("utf-8")).hexdigest()[:16]
            route_mode = "recommended@%s" % token
        li = xbmcgui.ListItem(label)
        li.setLabel2("Personalized for %s" % username)
        li.setArt({"thumb": icon, "icon": icon, "fanart": FANART})
        url = plugin.url_for(for_you_browse, route_mode)
        xbmcplugin.addDirectoryItem(plugin.handle, url, li, is_folder)
    xbmcplugin.endOfDirectory(plugin.handle, cacheToDisc=False)


@plugin.route("/for_you/configure")
def for_you_configure():
    username = xbmcgui.Dialog().input(
        "Last.fm Username", defaultt=_lastfm_username(),
        type=xbmcgui.INPUT_ALPHANUM).strip()
    if username:
        addon.setSetting("lastfm_username", username)
        xbmcgui.Dialog().notification(
            plugin.name, "For You connected to %s" % username,
            xbmcgui.NOTIFICATION_INFO, 3000)
        xbmc.executebuiltin("Container.Refresh")
    _end_action()


def _for_you_source():
    source = _make_playlists()
    if not source.lastfm.enabled:
        raise ProviderError("Last.fm", "configuration", "API key is not configured")
    return source


def _for_you_recommended(source, username):
    limit = _playlist_limit()
    top = source.lastfm.user_top_tracks(username, "3month", limit=50)
    recent = source.lastfm.user_recent_tracks(username, limit=100)
    recent_keys = {track_identity_key(row.get("artist", ""), row.get("title", ""))
                   for row in recent}

    # A new Last.fm profile can have recent scrobbles before its rolling Top
    # Tracks charts have been calculated.  Start with Top Tracks, supplement
    # them with recent scrobbles, and finally use Kodi play history so
    # Recommended Radio is useful immediately instead of returning an empty
    # directory while Last.fm builds the user's profile.
    seeds = _dedupe_track_entries(top[:20] + recent[:40], 20)
    seed_source = "Last.fm top/recent tracks"
    if len(seeds) < 8:
        index = _library()
        index.build()
        library_seeds = sorted(
            index.songs,
            key=lambda song: (
                int(song.get("playcount") or 0),
                song.get("lastplayed", "") or "",
            ),
            reverse=True,
        )
        library_seeds = [
            {"artist": song.get("artist", ""),
             "title": song.get("title", "")}
            for song in library_seeds
            if song.get("artist") and song.get("title")
        ]
        seeds = _dedupe_track_entries(seeds + library_seeds, 20)
        seed_source = "Last.fm plus Kodi library history"

    xbmc.log(
        "[rotation] Recommended Radio: %d top, %d recent, %d seeds from %s"
        % (len(top), len(recent), len(seeds), seed_source),
        xbmc.LOGINFO,
    )
    random.shuffle(seeds)
    suggestions = []
    for seed in seeds[:8]:
        suggestions.extend(source.lastfm.similar_tracks(
            seed.get("artist", ""), seed.get("title", ""), limit=12))
    suggestions = [row for row in _dedupe_track_entries(suggestions)
                   if track_identity_key(row.get("artist", ""),
                                         row.get("title", "")) not in recent_keys]
    random.shuffle(suggestions)
    familiar = [row for row in top
                if track_identity_key(row.get("artist", ""),
                                      row.get("title", "")) not in recent_keys]
    familiar = familiar[:max(5, int(limit * .15))]
    result = _dedupe_track_entries(familiar + suggestions, limit)
    xbmc.log(
        "[rotation] Recommended Radio: %d familiar + %d similar -> %d tracks"
        % (len(familiar), len(suggestions), len(result)),
        xbmc.LOGINFO,
    )
    return result


def _for_you_rediscover(source, username, forgotten=False):
    limit = _playlist_limit()
    top = source.lastfm.user_top_tracks(username, "overall", limit=200)
    recent = source.lastfm.user_recent_tracks(username, limit=200)
    recent_keys = {track_identity_key(row.get("artist", ""), row.get("title", ""))
                   for row in recent}
    pool = [row for row in top
            if track_identity_key(row.get("artist", ""), row.get("title", ""))
            not in recent_keys]
    if forgotten:
        pool = pool[max(10, len(pool) // 3):]
    random.shuffle(pool)
    return pool[:limit]


def _for_you_recommended_cache(token):
    if not token:
        return ""
    safe = re.sub(r"[^A-Za-z0-9_-]", "", token)
    return os.path.join(DATA_DIR, "recommended-radio-%s.json" % safe)


def _for_you_entries(mode):
    base_mode, _, token = mode.partition("@")
    username = _lastfm_username()
    source = _for_you_source()
    if base_mode == "recommended":
        cache_path = _for_you_recommended_cache(token)
        if cache_path:
            try:
                with open(cache_path, "r", encoding="utf-8") as stream:
                    snapshot = json.load(stream)
                if (snapshot.get("username") == username and
                        isinstance(snapshot.get("entries"), list)):
                    xbmc.log(
                        "[rotation] Recommended Radio: reusing %d-track session %s"
                        % (len(snapshot["entries"]), token), xbmc.LOGINFO)
                    return snapshot["entries"]
            except (OSError, ValueError, TypeError, AttributeError):
                pass
        entries = _for_you_recommended(source, username)
        if cache_path and entries:
            try:
                os.makedirs(DATA_DIR, exist_ok=True)
                with open(cache_path, "w", encoding="utf-8") as stream:
                    json.dump({"username": username, "created": time.time(),
                               "entries": entries}, stream, ensure_ascii=False)
                xbmc.log(
                    "[rotation] Recommended Radio: saved %d-track session %s"
                    % (len(entries), token), xbmc.LOGINFO)
            except OSError as exc:
                xbmc.log("[rotation] Recommended Radio cache failed: %s" % exc,
                         xbmc.LOGWARNING)
        return entries
    if base_mode == "recent":
        return source.lastfm.user_recent_tracks(username, limit=_playlist_limit())
    if base_mode == "rediscover":
        return _for_you_rediscover(source, username)
    if base_mode == "forgotten":
        return _for_you_rediscover(source, username, True)
    return source.lastfm.user_top_tracks(
        username, "3month", limit=_playlist_limit())


@plugin.route("/for_you/<mode>")
def for_you_browse(mode):
    base_mode = mode.partition("@")[0]
    username = _lastfm_username()
    if not username:
        _notify_error("Enter your Last.fm username in Rotation settings first.")
        xbmcplugin.endOfDirectory(plugin.handle, succeeded=False)
        return
    try:
        source = _for_you_source()
        if base_mode == "top_artists":
            _render_artists(source.lastfm.user_top_artists(
                username, "3month", limit=50), heading="Your Top Artists")
            return
        if base_mode == "new_albums":
            artists = source.lastfm.user_top_artists(username, "3month", limit=12)
            albums = []
            with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
                futures = []
                for artist in artists:
                    found = source.deezer.find_artist(artist.get("name", ""))
                    if found and found.get("id"):
                        futures.append(pool.submit(
                            source.deezer.artist_albums, found["id"], 25))
                for future in concurrent.futures.as_completed(futures):
                    try:
                        albums.extend(future.result())
                    except Exception as exc:
                        xbmc.log("[rotation] personalized album lookup failed: %s" % exc,
                                 xbmc.LOGWARNING)
            unique_albums = {}
            for album in albums:
                release_type = (album.get("record_type") or "").lower()
                if release_type in ("single", "ep") or (
                        not release_type and int(album.get("track_count") or 0) < 7):
                    continue
                key = (norm_artist(album.get("artist", "")),
                       norm_title(album.get("title", ""), False))
                current = unique_albums.get(key)
                if (not current or int(album.get("track_count") or 0) >
                        int(current.get("track_count") or 0)):
                    unique_albums[key] = album
            albums = list(unique_albums.values())
            albums.sort(key=lambda row: row.get("release_date", ""), reverse=True)
            cutoff = "%04d-01-01" % (datetime.date.today().year - 2)
            recent_albums = [row for row in albums
                             if (row.get("release_date") or "") >= cutoff]
            _render_deezer_album_choices((recent_albums or albums)[:50],
                                         "New Albums for You")
            return
        headings = {
            "recommended": "Recommended Radio", "recent": "Recently Played",
            "rediscover": "Rediscover Favorites",
            "forgotten": "Forgotten Favorites", "top_tracks": "Your Top Tracks"}
        entries = _for_you_entries(mode)
        heading = headings.get(base_mode, "For You")
        _render_playlist("for_you", mode, heading, entries=entries)
    except ProviderError as exc:
        _notify_error(str(exc))
        xbmcplugin.endOfDirectory(plugin.handle, succeeded=False)


# --------------------------------------------------------------------------- #
# Artists
# --------------------------------------------------------------------------- #

def _artist_page_url(name, artist_id=""):
    return (plugin.url_for(artists_page)
            + "?artist=" + quote(name, safe="")
            + "&artist_id=" + quote(str(artist_id or ""), safe=""))


def _library_artist_groups():
    """Aggregate metrics only for Kodi's configured album-artist list."""
    index = _library()
    index.build()
    groups = {}
    for artist in library_artists():
        name = (artist.get("artist") or "").strip()
        key = norm_artist(name)
        if key:
            groups[key] = {
                "name": name, "songs": [], "genres": set(), "playcount": 0,
                "dateadded": "", "lastplayed": "", "never_played": True,
            }
    for song in index.songs:
        credits = song.get("albumartists") or []
        if not credits and song.get("albumartist"):
            credits = [song["albumartist"]]
        if not credits:
            credits = [song.get("artist", "")]
        for key in {norm_artist(name) for name in credits if name}:
            row = groups.get(key)
            if not row:
                continue
            row["songs"].append(song)
            row["genres"].update(_song_genres(song))
            plays = int(song.get("playcount") or 0)
            row["playcount"] += plays
            if plays:
                row["never_played"] = False
            row["dateadded"] = max(row["dateadded"], song.get("dateadded", "") or "")
            row["lastplayed"] = max(row["lastplayed"], song.get("lastplayed", "") or "")
    return list(groups.values())


def _artist_navigation_context():
    return [
        ("⌂  Artists Home",
         "Container.Update(%s)" % plugin.url_for(artists_root)),
        ("⌂  Rotation Home",
         "Container.Update(%s)" % plugin.url_for(index)),
    ]


def _artist_context(name, url, thumb="", has_library=False):
    api = _favorites()
    radio_url = (plugin.url_for(playlists_radio_open)
                 + "?artist=" + quote(name, safe=""))
    existing = next((favorite for favorite in api.get_favorites(kind="artist")
                     if norm_artist(_favorite_artist_name(favorite, "artist")) ==
                     norm_artist(name)), None)
    if existing:
        items = [("✕  Remove from My Artists",
                  _favorite_remove_action(existing["url"]))]
    else:
        items = [("★  Save to My Artists",
                  _favorite_add_action("artist", url, name, thumb, name, ""))]
    items.append(("▶  Start Artist Radio", "RunPlugin(%s)" % radio_url))
    if has_library:
        play_url = (plugin.url_for(artists_library_play)
                    + "?artist=" + quote(name, safe=""))
        add_url = _user_playlist_add_url("artist", artist=name, label=name)
        items.extend([
            ("▶  Play All Library Songs", "RunPlugin(%s)" % play_url),
            ("🔀  Shuffle Library Songs", "RunPlugin(%s&shuffle=1)" % play_url),
            ("＋  Add Artist's Library Songs to My Playlist…",
             "RunPlugin(%s)" % add_url),
        ])
    items.extend(_artist_navigation_context())
    return items


def _render_artists(rows, heading="Artists"):
    """Render provider or library artist descriptors through one UI path."""
    cleaned = []
    seen = set()
    for row in rows:
        name = (row.get("name") or row.get("artist") or "").strip()
        key = norm_artist(name)
        if not key or key in seen:
            continue
        seen.add(key)
        item = dict(row)
        item["name"] = name
        cleaned.append(item)

    library_names = {norm_artist(row["name"]) for row in _library_artist_groups()}
    local_art = {row["name"]: library_artist_art(row["name"]) for row in cleaned}
    portrait_names = [
        row["name"] for row in cleaned
        if not local_art[row["name"]].get("thumb") and not row.get("picture")
    ]
    cached_portraits = _artist_thumbs(portrait_names, queue_missing=False)
    api = _favorites()
    for row in cleaned:
        name = row["name"]
        art = local_art[name]
        thumb = art.get("thumb") or row.get("picture", "") or cached_portraits.get(name, "")
        fanart = art.get("fanart") or _cached_background(name)
        url = _artist_page_url(name, row.get("id", ""))
        li = xbmcgui.ListItem(name)
        count = len(row.get("songs", []))
        if count:
            li.setLabel2("%d song%s in library" % (count, "" if count == 1 else "s"))
        li.setArt(_art(thumb, fanart, FALLBACK_ARTIST,
                       art.get("clearlogo") or _cached_logo(name)))
        # Match Favorite Artists: skins use the music tag's artist identity
        # to resolve library extras such as clearlogo and clearart.
        _set_music_tag(li, title=name, artist=name)
        li.addContextMenuItems(
            _artist_context(name, url, thumb,
                            norm_artist(name) in library_names),
            replaceItems=False)
        xbmcplugin.addDirectoryItem(plugin.handle, url, li, True)
    xbmcplugin.setPluginCategory(plugin.handle, "Artists")
    _set_view("artists")
    xbmcplugin.endOfDirectory(plugin.handle, cacheToDisc=False)
    refresh_path = _current_plugin_path()
    # Queue the worker only after Kodi has received the directory. This gives
    # the completed batch an exact visible target for the safe refresh.
    _artist_thumbs(portrait_names, queue_missing=True,
                   refresh_path=refresh_path)
    portrait_keys = set(portrait_names)
    _queue_artist_backgrounds(
        [row["name"] for row in cleaned if row["name"] not in portrait_keys],
        refresh_path)


@plugin.route("/artists")
def artists_root():
    items = [
        ("Favorite Artists", plugin.url_for(favorites, "artist"), ICON_FAVORITES, True),
        ("Trending Artists", plugin.url_for(artists_trending_root), ICON_TOP_SONGS, True),
        ("Similar Artists", plugin.url_for(artists_discover_root), ICON_SEARCH, True),
        ("Search for an Artist", plugin.url_for(artists_discover, "search"), ICON_SEARCH, False),
    ]
    if _use_kodi_library():
        items.insert(0, ("Library Artists", plugin.url_for(artists_library_root),
                         ICON_ARTISTS, True))
    for label, url, icon, is_folder in items:
        li = xbmcgui.ListItem(label)
        li.setArt({"thumb": icon, "icon": icon, "fanart": FANART})
        xbmcplugin.addDirectoryItem(plugin.handle, url, li, is_folder)
    xbmcplugin.endOfDirectory(plugin.handle, cacheToDisc=False)


@plugin.route("/artists/library")
def artists_library_root():
    items = [
        ("All Library Artists", "all", ICON_ARTISTS, True),
        ("Recently Added Artists", "recent_added", ICON_NEW_ALBUMS, True),
        ("Recently Played Artists", "recent_played", ICON_TOP_SONGS, True),
        ("Most Played Artists", "most_played", ICON_TOP_SONGS, True),
        ("Never Played Artists", "never_played", ICON_ARTISTS, True),
        ("Artists by Genre", "genre", ICON_GENRES, True),
        ("Random Artist", "random", ICON_SHUFFLE, False),
    ]
    for label, mode, icon, is_folder in items:
        url = plugin.url_for(artists_library, mode)
        li = xbmcgui.ListItem(label)
        li.setArt({"thumb": icon, "icon": icon, "fanart": FANART})
        xbmcplugin.addDirectoryItem(plugin.handle, url, li, is_folder)
    xbmcplugin.endOfDirectory(plugin.handle, cacheToDisc=False)


@plugin.route("/artists/library/<mode>")
def artists_library(mode):
    rows = _library_artist_groups()
    if mode == "genre":
        genres = sorted({genre for row in rows for genre in row["genres"]}, key=str.casefold)
        choice = xbmcgui.Dialog().select("Choose Genre", genres)
        if choice < 0:
            xbmcplugin.endOfDirectory(plugin.handle, succeeded=False)
            return
        wanted = genres[choice].casefold()
        rows = [row for row in rows
                if any(genre.casefold() == wanted for genre in row["genres"])]
        rows.sort(key=lambda row: row["name"].casefold())
    elif mode == "random":
        if rows:
            _open_artist_after_selection(random.choice(rows)["name"])
        elif plugin.handle >= 0:
            xbmcplugin.endOfDirectory(plugin.handle, succeeded=False)
        return
    elif mode == "recent_added":
        rows.sort(key=lambda row: row["dateadded"], reverse=True)
    elif mode == "recent_played":
        rows = [row for row in rows if row["lastplayed"]]
        rows.sort(key=lambda row: row["lastplayed"], reverse=True)
    elif mode == "most_played":
        rows.sort(key=lambda row: (row["playcount"], row["name"].casefold()), reverse=True)
    elif mode == "never_played":
        rows = [row for row in rows if row["never_played"]]
        rows.sort(key=lambda row: row["name"].casefold())
    else:
        rows.sort(key=lambda row: row["name"].casefold())
    _render_artists(rows)


@plugin.route("/artists/trending")
def artists_trending_root():
    items = [
        ("Trending Worldwide", "world", ICON_TOP_SONGS, True),
        ("Trending in %s" % _country_name(), "country", ICON_TOP_SONGS, True),
        ("Trending by Genre", "genres", ICON_GENRES, True),
    ]
    for label, mode, icon, is_folder in items:
        li = xbmcgui.ListItem(label)
        li.setArt({"thumb": icon, "icon": icon, "fanart": FANART})
        url = (plugin.url_for(artists_trending_genres) if mode == "genres" else
               plugin.url_for(artists_trending, mode))
        xbmcplugin.addDirectoryItem(
            plugin.handle, url, li, is_folder)
    xbmcplugin.endOfDirectory(plugin.handle, cacheToDisc=False)


@plugin.route("/artists/trending/<mode>")
def artists_trending(mode):
    source = _make_playlists()
    if mode == "country":
        rows = source.country_artists(limit=50)
    else:
        rows = source.top_artists(limit=50)
    _render_artists(rows)


@plugin.route("/artists/genres")
def artists_trending_genres():
    """Browse Deezer genres as artwork-led folders instead of a modal picker."""
    for genre in _make_playlists().genres():
        url = (plugin.url_for(artists_trending_genre, str(genre["id"]))
               + "?name=" + quote(genre["name"], safe=""))
        li = xbmcgui.ListItem(genre["name"])
        li.setArt(_art(genre.get("picture", ""), FANART, ICON_GENRES))
        xbmcplugin.addDirectoryItem(plugin.handle, url, li, True)
    _set_view("albums")
    xbmcplugin.endOfDirectory(plugin.handle, cacheToDisc=False)


@plugin.route("/artists/trending/genre/<genre_id>")
def artists_trending_genre(genre_id):
    name = unquote(plugin.args.get("name", [""])[0])
    rows = _make_playlists().genre_artists(genre_id, name, limit=50)
    _render_artists(rows, heading=name or "Trending Artists")


@plugin.route("/artists/discover")
def artists_discover_root():
    items = [
        ("Similar to a Library Artist", "library", ICON_ARTISTS),
        ("Similar to a Favorite Artist", "favorite", ICON_FAVORITES),
        ("Search for an Artist", "search", ICON_SEARCH),
        ("Artists Related to Recently Played", "recent", ICON_TOP_SONGS),
        ("Rediscover Your Library", "rediscover", ICON_SHUFFLE),
    ]
    if not _use_kodi_library():
        items = [row for row in items if row[1] not in ("library", "rediscover")]
    for label, mode, icon in items:
        li = xbmcgui.ListItem(label)
        li.setArt({"thumb": icon, "icon": icon, "fanart": FANART})
        xbmcplugin.addDirectoryItem(
            plugin.handle, plugin.url_for(artists_discover, mode), li,
            mode in ("recent", "rediscover"))
    xbmcplugin.endOfDirectory(plugin.handle, cacheToDisc=False)


def _open_artist_after_selection(artist):
    """Leave the current directory intact while dismissing an action dialog."""
    if plugin.handle >= 0:
        xbmcplugin.endOfDirectory(plugin.handle, succeeded=False, cacheToDisc=False)
    opener = plugin.url_for(artists_open_selected) + "?artist=" + quote(artist, safe="")
    opener += _navigation_origin_query()
    _schedule_playlist_action(opener, "RotationArtistOpen")


@plugin.route("/artists/open_selected")
def artists_open_selected():
    if not _navigation_request_is_current():
        return
    artist = unquote(plugin.args.get("artist", [""])[0]).strip()
    if artist:
        xbmc.executebuiltin("Container.Update(%s)" % _artist_page_url(artist))


@plugin.route("/artists/discover/<mode>")
def artists_discover(mode):
    if mode in ("library", "rediscover") and not _use_kodi_library():
        xbmcplugin.endOfDirectory(plugin.handle, succeeded=False)
        return
    library_rows = _library_artist_groups()
    if mode == "rediscover":
        rows = sorted(library_rows,
                      key=lambda row: (row["playcount"], row["lastplayed"] or ""))
        _render_artists(rows)
        return
    if mode == "search":
        seed = xbmcgui.Dialog().input("Search for an Artist", type=xbmcgui.INPUT_ALPHANUM)
        if seed:
            _open_artist_after_selection(seed)
        elif plugin.handle >= 0:
            xbmcplugin.endOfDirectory(plugin.handle, succeeded=False)
        return
    if mode == "favorite":
        favorites_rows = _favorites().get_favorites(kind="artist")
        names = [_favorite_artist_name(row, "artist") for row in favorites_rows]
    elif mode == "recent":
        recent = sorted((row for row in library_rows if row["lastplayed"]),
                        key=lambda row: row["lastplayed"], reverse=True)
        names = [row["name"] for row in recent[:1]]
    else:
        names = sorted((row["name"] for row in library_rows), key=str.casefold)
    if not names:
        _notify_error("No artists are available for this selection.")
        xbmcplugin.endOfDirectory(plugin.handle, succeeded=False)
        return
    if mode == "recent":
        seed = names[0]
    else:
        choice = xbmcgui.Dialog().select("Choose Artist", names)
        if choice < 0:
            xbmcplugin.endOfDirectory(plugin.handle, succeeded=False)
            return
        seed = names[choice]
    names = _make_playlists().related_artists(seed, limit=50)
    _render_artists([{"name": name} for name in names])


@plugin.route("/artists/similar")
def artists_similar():
    seed = unquote(plugin.args.get("artist", [""])[0])
    if not seed:
        xbmcplugin.endOfDirectory(plugin.handle, succeeded=False)
        return
    names = _make_playlists().related_artists(seed, limit=50)
    _render_artists([{"name": name} for name in names])


@plugin.route("/artists/artist")
def artists_page():
    name = unquote(plugin.args.get("artist", [""])[0])
    artist_id = unquote(plugin.args.get("artist_id", [""])[0])
    if not name:
        xbmcplugin.endOfDirectory(plugin.handle, succeeded=False)
        return
    local_song_count = len(_artist_local_songs(name))
    art = dict(library_artist_art(name))
    if not artist_id:
        found = _make_playlists().deezer.find_artist(name)
        artist_id = str((found or {}).get("id") or "")
        if not art.get("thumb"):
            art["thumb"] = (found or {}).get("picture", "")
    fanart = art.get("fanart") or _cached_background(name)
    albums_url = ((plugin.url_for(playlists_deezer_artist, artist_id)
                   + "?artist=" + quote(name, safe="")) if artist_id else
                  plugin.url_for(playlists_artist) + "?artist=" + quote(name, safe=""))
    items = [
        ("Albums", albums_url, ICON_ALBUMS, True),
        ("Top Tracks", plugin.url_for(artists_top_tracks) + "?artist=" + quote(name, safe="")
         + "&artist_id=" + quote(artist_id, safe=""), ICON_TOP_SONGS, True),
        ("Songs in Your Library", plugin.url_for(artists_library_songs)
         + "?artist=" + quote(name, safe=""), ICON_SONGS, True),
        ("Artist Radio", _playlist_folder_url("radio", name), ICON_RADIO, True),
        ("Similar Artists", plugin.url_for(artists_similar)
         + "?artist=" + quote(name, safe=""), ICON_ARTISTS, True),
        ("Artists Home", plugin.url_for(artists_root), ICON_ARTISTS, True),
        ("Rotation Home", plugin.url_for(index), ICON_FAVORITES, True),
    ]
    if not _use_kodi_library():
        items = [row for row in items if row[0] != "Songs in Your Library"]
    for label, url, icon, is_folder in items:
        li = xbmcgui.ListItem(label)
        if label == "Songs in Your Library":
            count = local_song_count
            li.setLabel2("%d song%s" % (count, "" if count == 1 else "s"))
        li.setArt(_art(art.get("thumb") or icon, fanart, icon,
                       art.get("clearlogo") or _cached_logo(name)))
        _set_music_tag(li, title=label, artist=name)
        li.addContextMenuItems(_artist_navigation_context(), replaceItems=False)
        xbmcplugin.addDirectoryItem(plugin.handle, url, li, is_folder)
    # A single space prevents skins from falling back to the add-on name
    # while keeping the artist page free of a redundant category heading.
    xbmcplugin.setPluginCategory(plugin.handle, " ")
    xbmcplugin.endOfDirectory(plugin.handle)


def _artist_local_songs(name):
    wanted = norm_artist(name)
    index = _library()
    index.build()
    result = []
    for song in index.songs:
        album_credits = song.get("albumartists") or []
        if not album_credits and song.get("albumartist"):
            album_credits = [song["albumartist"]]
        track_credits = song.get("artists") or []
        if not track_credits and song.get("artist"):
            track_credits = [song["artist"]]
        keys = {norm_artist(credit) for credit in album_credits + track_credits
                if credit}
        keys.update(primary_artist(credit) for credit in track_credits if credit)
        keys.discard("")
        if wanted in keys:
            result.append(song)
    return result


@plugin.route("/artists/library_songs")
def artists_library_songs():
    name = unquote(plugin.args.get("artist", [""])[0])
    songs = sorted(_artist_local_songs(name),
                   key=lambda song: (song.get("album", "").casefold(),
                                     int(song.get("track") or 0),
                                     song.get("title", "").casefold()))
    for position, song in enumerate(songs, 1):
        li = _playlist_listitem(song, position)
        li.addContextMenuItems(
            _user_playlist_track_context({}, song), replaceItems=False)
        xbmcplugin.addDirectoryItem(plugin.handle, song["file"], li, False)
    _set_view("songs")
    xbmcplugin.endOfDirectory(plugin.handle)


@plugin.route("/artists/library_play")
def artists_library_play():
    name = unquote(plugin.args.get("artist", [""])[0])
    songs = _artist_local_songs(name)
    if plugin.args.get("shuffle", ["0"])[0] == "1":
        random.shuffle(songs)
    playlist = xbmc.PlayList(xbmc.PLAYLIST_MUSIC)
    playlist.clear()
    for position, song in enumerate(songs, 1):
        playlist.add(song["file"], _playlist_listitem(song, position))
    if playlist.size():
        xbmc.Player().play(playlist)


@plugin.route("/artists/top_tracks")
def artists_top_tracks():
    name = unquote(plugin.args.get("artist", [""])[0])
    artist_id = unquote(plugin.args.get("artist_id", [""])[0])
    entries = _artist_top_entries(name, artist_id)
    _render_playlist("artist_top", name, "%s — Top Tracks" % name, entries=entries)


def _dedupe_track_entries(entries, limit=None):
    """Preserve ranking while removing provider aliases of one recording."""
    result = []
    seen = set()
    for entry in entries or []:
        key = track_identity_key(entry.get("artist", ""), entry.get("title", ""))
        if not all(key) or key in seen:
            continue
        seen.add(key)
        result.append(entry)
        if limit and len(result) >= int(limit):
            break
    return result


def _artist_top_entries(name, artist_id=""):
    """Fetch one artist's ranked tracks consistently for browsing and actions."""
    source = _make_playlists()
    target = min(50, _playlist_limit())
    # Ask for extra rows so title variants do not leave a short list after
    # deduplication, while never displaying more than 50 Top Tracks.
    request_limit = min(100, max(target, target * 2))
    lastfm_failure = None
    try:
        entries = (source.lastfm.artist_top(name, limit=request_limit)
                   if source.lastfm.enabled else [])
    except ProviderError as exc:
        lastfm_failure = exc
        xbmc.log("[rotation] %s Using Deezer for artist top tracks." % exc,
                 xbmc.LOGWARNING)
        entries = []
    if not entries:
        try:
            if not artist_id:
                artist_id = str(
                    (source.deezer.find_artist(name) or {}).get("id") or "")
            entries = (source.deezer.artist_top(
                artist_id, limit=request_limit) if artist_id else [])
        except ProviderError as exc:
            if lastfm_failure:
                raise ProviderError(
                    "Last.fm and Deezer", "unreachable",
                    "%s; %s" % (lastfm_failure.detail, exc.detail))
            raise
    if lastfm_failure and entries:
        xbmcgui.Dialog().notification(
            "Last.fm unavailable", "Using Deezer top tracks",
            xbmcgui.NOTIFICATION_WARNING, 4500)
    return _dedupe_track_entries(entries, target)


@plugin.route("/maintenance")
def maintenance():
    """Administrative actions kept out of ordinary music browsing menus."""
    items = [
        ("System Status", plugin.url_for(system_status), False),
        ("Changelog", plugin.url_for(maintenance_changelog), False),
        ("Refresh & Repair", plugin.url_for(maintenance_repair), True),
    ]
    if _unavailable_report_enabled():
        items.insert(0, ("Music Missing from Library", plugin.url_for(unavailable_root), True))
    for label, url, is_folder in items:
        li = xbmcgui.ListItem(label)
        li.setArt({"fanart": FANART, "icon": ICON_MAINTENANCE})
        xbmcplugin.addDirectoryItem(plugin.handle, url, li, is_folder)
    xbmcplugin.endOfDirectory(plugin.handle, cacheToDisc=False)


@plugin.route("/maintenance/changelog")
def maintenance_changelog():
    """Display the release history bundled with this installed version."""
    path = os.path.join(translatePath(addon.getAddonInfo("path")),
                        "CHANGELOG.md")
    try:
        with open(path, "r", encoding="utf-8") as stream:
            content = stream.read().strip()
    except OSError as exc:
        xbmc.log("[rotation] Could not read changelog: %s" % exc,
                 xbmc.LOGWARNING)
        _notify_error("The bundled changelog could not be opened.")
        _end_action()
        return
    # Kodi's text viewer is plain text. Remove Markdown heading markers while
    # retaining bullets and spacing from the distributable source file.
    content = re.sub(r"(?m)^#{1,3}\s*", "", content)
    xbmcgui.Dialog().textviewer("%s Changelog" % plugin.name, content)
    _end_action()






@plugin.route("/maintenance/repair")
def maintenance_repair():
    """Cache and index actions that are useful but not part of daily use."""
    items = [
        ("Rebuild Library Index", plugin.url_for(playlists_rebuild)),
        ("Clear Playlist Cache", plugin.url_for(playlists_clear_cache)),
        ("Clear Artwork Cache", plugin.url_for(rotation_clear_artwork)),
    ]
    for label, url in items:
        li = xbmcgui.ListItem(label)
        li.setArt({"fanart": FANART, "icon": ICON_MAINTENANCE})
        xbmcplugin.addDirectoryItem(plugin.handle, url, li, False)
    xbmcplugin.endOfDirectory(plugin.handle, cacheToDisc=False)


# --------------------------------------------------------------------------- #
# Provider health and album-identification helpers
# --------------------------------------------------------------------------- #



















def _status_deezer(timeout):
    health = ProviderHealth("Deezer")
    try:
        response = requests.get("https://api.deezer.com/chart/0/tracks",
                                params={"limit": 1}, timeout=min(timeout, 10))
        if response.status_code == 429:
            raise health.failure("rate_limited", "HTTP 429")
        response.raise_for_status()
        payload = response.json()
        if payload.get("error"):
            raise health.failure("service_error", payload["error"])
        if not isinstance(payload.get("data"), list):
            raise health.failure("malformed", "unexpected response")
        health.success()
        return "Connected"
    except ProviderError:
        raise
    except (requests.Timeout, requests.ConnectionError) as exc:
        raise health.failure("unreachable", exc)
    except (requests.RequestException, TypeError, ValueError) as exc:
        raise health.failure("service_error", exc)


def _status_lastfm(api_key, timeout):
    health = ProviderHealth("Last.fm")
    try:
        response = requests.get(
            "https://ws.audioscrobbler.com/2.0/",
            params={"method": "chart.getTopTracks", "api_key": api_key,
                    "format": "json", "limit": 1}, timeout=min(timeout, 10))
        if response.status_code == 429:
            raise health.failure("rate_limited", "HTTP 429")
        response.raise_for_status()
        payload = response.json()
        if payload.get("error"):
            category = ("configuration" if str(payload.get("error")) in
                        ("4", "9", "10", "26") else "service_error")
            raise health.failure(category, payload.get("message") or "API error")
        health.success()
        return "Connected"
    except ProviderError:
        raise
    except (requests.Timeout, requests.ConnectionError) as exc:
        raise health.failure("unreachable", exc)
    except (requests.RequestException, TypeError, ValueError) as exc:
        raise health.failure("service_error", exc)


def _status_musicmp3(timeout):
    health = ProviderHealth("MusicMP3.ru")
    try:
        response = requests.get("https://musicmp3.ru/", timeout=min(timeout, 10))
        response.raise_for_status()
        health.success()
        return "Reachable"
    except (requests.Timeout, requests.ConnectionError) as exc:
        raise health.failure("unreachable", exc)
    except requests.RequestException as exc:
        raise health.failure("service_error", exc)


def _configured_music_sources():
    """Return the music paths Kodi already owns; no duplicate addon path."""
    requests_to_try = [
        ("AudioLibrary.GetSources", {"properties": ["file", "paths"]}),
        ("Files.GetSources", {"media": "music"}),
    ]
    for method, params in requests_to_try:
        try:
            payload = json.loads(xbmc.executeJSONRPC(json.dumps({
                "jsonrpc": "2.0", "id": 1, "method": method,
                "params": params,
            })))
            sources = (payload.get("result") or {}).get("sources") or []
            paths = []
            for source in sources:
                candidates = source.get("paths") or [source.get("file")]
                paths.extend(str(path).strip() for path in candidates if path)
            if paths:
                return list(dict.fromkeys(paths))
        except (TypeError, ValueError):
            pass
    return []


@plugin.route("/maintenance/status")
def system_status():
    """Run the standard edition's useful connection checks."""
    try:
        timeout = max(5, int(addon.getSetting("request_timeout") or 15))
    except (TypeError, ValueError):
        timeout = 15
    rows = []
    try:
        discovery_cache = any(name.endswith(".json") for name in os.listdir(PLAYLIST_DIR))
    except OSError:
        discovery_cache = False
    try:
        streaming_cache = bool(os.listdir(MUSICMP3_DIR))
    except OSError:
        streaming_cache = False
    try:
        library_index = _library()
        library_index.build()
        rows.append(("Kodi music library", "ok", "%d indexed songs" % library_index.size))
    except Exception as exc:
        rows.append(("Kodi music library", "error", str(exc)))
    music_sources = _configured_music_sources()
    rows.append(("Kodi music sources", "ok" if music_sources else "error",
                 "%d configured" % len(music_sources) if music_sources else "None configured"))
    checks = [("Deezer", lambda: _status_deezer(timeout))]
    lastfm_key = addon.getSetting("lastfm_apikey").strip()
    if lastfm_key:
        checks.append(("Last.fm", lambda: _status_lastfm(lastfm_key, timeout)))
    else:
        rows.append(("Last.fm", "skip", "Not configured"))
    if _musicmp3_enabled():
        checks.append(("MusicMP3.ru", lambda: _status_musicmp3(timeout)))
    else:
        rows.append(("MusicMP3.ru", "skip", "Disabled"))
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(3, len(checks))) as executor:
        futures = [(name, executor.submit(check)) for name, check in checks]
        for name, future in futures:
            try:
                detail = future.result()
                cached = discovery_cache if name in ("Deezer", "Last.fm") else streaming_cache
                if cached:
                    detail += " — cached results available"
                rows.append((name, "ok", detail))
            except ProviderError as exc:
                cached = discovery_cache if name in ("Deezer", "Last.fm") else streaming_cache
                detail = exc.message + (" Cached results remain available." if cached else "")
                rows.append((name, "error", detail))
            except Exception as exc:
                rows.append((name, "error", str(exc)))
    order = {name: position for position, name in enumerate((
        "Kodi music library", "Kodi music sources", "Deezer", "Last.fm", "MusicMP3.ru"))}
    rows.sort(key=lambda row: order.get(row[0], 99))
    marker = {"ok": "✓", "error": "⚠", "skip": "—"}
    report = "[CR]".join("%s %s — %s" % (marker[state], name, detail)
                         for name, state, detail in rows)
    xbmcgui.Dialog().textviewer("Rotation System Status", report)
    _end_action()










def _route_arg(name, default=""):
    return unquote(plugin.args.get(name, [default])[0])












def _playlist_album_match(entry, source, include_lastfm=True):
    """Confidently enrich one album-less artist/title row through Deezer."""
    artist = entry.get("artist", "").strip()
    title = entry.get("title", "").strip()
    if not artist or not title:
        return None
    provider_failure = None
    try:
        rows = source.deezer.search_tracks(artist, title, limit=25)
    except ProviderError as exc:
        provider_failure = exc
        rows = []
    # Last.fm is already configured for most Rotation radio installations and
    # exposes the source album directly through track.getInfo. It is an
    # independent fallback when Deezer search is blocked, empty, or dominated
    # by covers on a particular Kodi device.
    if include_lastfm and source.lastfm.enabled:
        try:
            lastfm_match = source.lastfm.track_info(artist, title)
        except ProviderError as exc:
            if provider_failure:
                raise provider_failure
            raise exc
        if lastfm_match:
            rows.insert(0, lastfm_match)
    if provider_failure and not rows:
        raise provider_failure
    scored = []
    for row in rows:
        if not row.get("album"):
            continue
        artist_score = max(
            _similar(norm_artist(artist), norm_artist(row.get("artist", ""))),
            _similar(primary_artist(artist), primary_artist(row.get("artist", ""))))
        # Use the better of literal and version-insensitive comparisons. This
        # accepts legitimate "Remastered"/"Radio Edit" metadata while still
        # requiring the underlying song title to agree strongly.
        title_score = max(
            _similar(norm_title(title, False),
                     norm_title(row.get("title", ""), False)),
            _similar(norm_title(title), norm_title(row.get("title", ""))))
        score = title_score * 0.68 + artist_score * 0.32
        scored.append((score, title_score, artist_score, row))
    score, title_score, artist_score, match = max(
        scored, default=(0, 0, 0, None), key=lambda item: item[0])
    xbmc.log(
        "[rotation] Album match for %s — %s: %d candidate(s), title %.3f, "
        "artist %.3f, album %s" % (
            artist, title, len(scored), title_score, artist_score,
            match.get("album", "") if match else "none"),
        xbmc.LOGINFO)
    # Album releases and compilations often share the same recording. Trust
    # Deezer's relevance order only after both identity fields match strongly;
    # otherwise omitting the row is safer than opening the wrong album.
    exact_identity = title_score >= 0.985 and artist_score >= 0.90
    strong_identity = score >= 0.875 and title_score >= 0.88 and artist_score >= 0.84
    if not match or not (exact_identity or strong_identity):
        return None
    enriched = dict(entry)
    for field in ("album", "album_id", "cover", "duration", "artist_id",
                  "artist_cover", "explicit"):
        if match.get(field) and not enriched.get(field):
            enriched[field] = match[field]
    return enriched




























# --------------------------------------------------------------------------- #
# Favorites
# --------------------------------------------------------------------------- #

@plugin.route("/favorites/<kind>")
def favorites(kind):
    """Browse saved favorites. kind: album | artist | song | playlist | radio."""
    api   = _favorites()
    items = api.get_favorites(kind=kind)

    if not items:
        xbmcgui.Dialog().notification(
            plugin.name, "No favorites saved yet.", xbmcgui.NOTIFICATION_INFO, 3000
        )
        xbmcplugin.endOfDirectory(plugin.handle, succeeded=True, cacheToDisc=False)
        return

    if kind in ("playlist", "radio"):
        directory_items = []
        reorder_item = (u"\u2195  Reorder {0}\u2026".format(_kind_label(kind)),
                        _favorite_reorder_action(kind))
        radio_library_art = {}
        radio_portraits = {}
        if kind == "radio":
            station_artists = [
                f.get("artist") or f["label"].replace(" Radio", "")
                for f in items if not _library_radio_favorite_spec(f)
            ]
            for favorite in items:
                spec = _library_radio_favorite_spec(favorite)
                if spec and spec[0] == "artist" and spec[1]:
                    station_artists.append(spec[1])
            radio_library_art = {
                artist: library_artist_art(artist) for artist in station_artists
            }
            radio_portraits = _artist_thumbs([
                artist for artist in station_artists
                if not radio_library_art[artist].get("thumb")
            ])
        for f in items:
            li = xbmcgui.ListItem(f["label"])
            library_radio = (_library_radio_favorite_spec(f)
                             if kind == "radio" else None)
            station_artist = ((library_radio[1] if library_radio[0] == "artist" else "")
                              if library_radio else
                              (f.get("artist") or f["label"].replace(" Radio", "")))
            local_art = radio_library_art.get(station_artist, {})
            thumb = (local_art.get("thumb") or
                     radio_portraits.get(station_artist) or f["thumb"])
            fanart = local_art.get("fanart") or _cached_background(station_artist)
            li.setArt(_art(thumb, fanart,
                           FALLBACK_ARTIST if kind == "radio" else ICON_PLAYLISTS))
            remove_url = plugin.url_for(favorite_remove) + "?url=" + quote(f["url"], safe="")
            if kind == "playlist":
                playlist_id = f["url"].rsplit("/", 1)[-1]
                actions = (_playlist_play_context("editorial", playlist_id, f["label"])
                           if playlist_id.isdigit() else [])
                list_name = "Followed Playlists"
            else:
                if library_radio:
                    mode, value = library_radio
                    actions = _library_radio_context(
                        mode, value, f["label"], include_favorite=False)
                else:
                    actions = _playlist_play_context(
                        "radio", station_artist, f["label"])
                list_name = "Favorite Radio"
            li.addContextMenuItems(actions + [
                ("✕  Remove from %s" % list_name, "RunPlugin(%s)" % remove_url),
                reorder_item,
            ])
            directory_items.append((f["url"], li, True))
        xbmcplugin.addDirectoryItems(plugin.handle, directory_items, len(directory_items))
        _set_view("albums")
        xbmcplugin.endOfDirectory(plugin.handle, cacheToDisc=False)
        return

    provider_songs = _library() if kind == "song" else None
    if provider_songs:
        provider_songs.build()

    # Stored thumbs are whatever the site served when the favorite was
    # saved, which may predate artwork lookup entirely. Re-resolve here so
    # favorites match the rest of the addon rather than freezing whatever
    # was current at save time.
    # Artist favorites have no album to look a cover up by, so the album
    # bulk pass cannot do anything for them - _favorite_pair returns an
    # empty pair and every row gets filtered out. They need the artist
    # portrait path instead, the same one the artist listings use, so a
    # portrait resolved there already shows up here with no extra lookup.
    if kind == "artist":
        _favorite_covers = {}
        favorite_names = [_favorite_artist_name(f, kind) for f in items]
        _favorite_library_art = {
            name: library_artist_art(name) for name in favorite_names
        }
        _favorite_portraits = _artist_thumbs([
            name for name in favorite_names
            if not _favorite_library_art[name].get("thumb")
        ])
    else:
        _favorite_covers = _bulk_covers_for(
            [_favorite_pair(f, kind) for f in items])
        _favorite_portraits = {}
        _favorite_library_art = {}

    directory_items = []
    for f in items:
        _favorite_label = (
            "{0} — {1}".format(f["artist"], f["label"])
            if kind == "album" and f.get("artist") else f["label"]
        )
        li = xbmcgui.ListItem(_favorite_label)
        # setLabel2 carries the artist name into skins that show it as a subtitle.
        # kind=="artist" is intentionally excluded: the artist IS the primary label.
        if kind in ("album", "song"):
            li.setLabel2(f["artist"])
        _favorite_name  = _favorite_artist_name(f, kind)
        if kind == "artist":
            _favorite_cover = (
                _favorite_library_art.get(_favorite_name, {}).get("thumb") or
                _favorite_portraits.get(_favorite_name) or f["thumb"])
        else:
            _favorite_cover = _favorite_covers.get(_favorite_pair(f, kind)) or f["thumb"]
        _favorite_fallback = {
            "album":  FALLBACK_ALBUM,
            "artist": FALLBACK_ARTIST,
            "song":   FALLBACK_ALBUM,
        }.get(kind, FALLBACK_ALBUM)
        li.setArt(_art(_favorite_cover, _cached_background(_favorite_name),
                       fallback=_favorite_fallback,
                       clearlogo=(
                           _favorite_library_art.get(_favorite_name, {}).get("clearlogo")
                           if kind == "artist" else "") or
                       _cached_logo(_favorite_name)))
        _set_music_tag(li, title=f["label"], artist=f["artist"], album=f["album"])
        remove_url = plugin.url_for(favorite_remove) + "?url=" + quote(f["url"], safe="")
        li.addContextMenuItems([
            ("✕  Remove from {0}".format(_kind_label(kind)),
             "RunPlugin({0})".format(remove_url)),
            (u"\u2195  Reorder {0}\u2026".format(_kind_label(kind)),
             _favorite_reorder_action(kind)),
        ])
        if kind == "song":
            li.addContextMenuItems(_user_playlist_track_context({
                "artist": f.get("artist", ""), "title": f.get("label", ""),
                "album": f.get("album", ""), "cover": _favorite_cover,
                "file": f.get("url", ""),
            }), replaceItems=False)

        if kind == "album":
            directory_items.append((f["url"], li, True))
        elif kind == "artist":
            # Route old and new favorites through the common artist page.
            # It resolves the current provider id and keeps all artist entry
            # points consistent without requiring favorites to be recreated.
            artist_url = _artist_page_url(_favorite_name)
            directory_items.append((artist_url, li, True))
        elif kind == "song":
            li.setProperty("IsPlayable", "true")
            local = provider_songs.resolve(
                f["artist"], f["label"], album=f["album"],
                threshold=_match_threshold(), allow_any_artist=_allow_any_artist()
            ) if provider_songs.size else None
            directory_items.append((local["file"] if local else f["url"], li, False))

    xbmcplugin.addDirectoryItems(plugin.handle, directory_items, len(directory_items))
    _set_view("songs" if kind == "song" else "")
    xbmcplugin.endOfDirectory(plugin.handle, cacheToDisc=False)


@plugin.route("/favorite/add")
def favorite_add():
    kind   = unquote(plugin.args.get("kind",   [""])[0])
    url    = unquote(plugin.args.get("url",    [""])[0])
    label  = unquote(plugin.args.get("label",  [""])[0])
    thumb  = unquote(plugin.args.get("thumb",  [""])[0])
    artist = unquote(plugin.args.get("artist", [""])[0])
    album  = unquote(plugin.args.get("album",  [""])[0])
    api = _favorites()
    api.add_favorite(kind, url, label, thumb=thumb, artist=artist, album=album)
    xbmcgui.Dialog().notification(
        plugin.name, u"\u2605  Saved to {0}: {1}".format(_kind_label(kind), label),
        xbmcgui.NOTIFICATION_INFO, 2500
    )


@plugin.route("/favorite/remove")
def favorite_remove():
    url = unquote(plugin.args.get("url", [""])[0])
    api = _favorites()
    api.remove_favorite(url)
    xbmcgui.Dialog().notification(
        plugin.name, "Removed from saved list.", xbmcgui.NOTIFICATION_INFO, 2000
    )
    xbmc.executebuiltin("Container.Refresh")


@plugin.route("/favorite/reorder")
def favorite_reorder():
    """Interactively reorder one favorites section and persist each change."""
    kind = unquote(plugin.args.get("kind", [""])[0])
    if kind not in ("album", "artist", "song", "playlist", "radio"):
        return

    api = _favorites()
    heading = "Reorder {0}".format(_kind_label(kind))
    changed = False
    while True:
        items = api.get_favorites(kind=kind)
        if len(items) < 2:
            xbmcgui.Dialog().notification(
                plugin.name, "Add at least two favorites before reordering.",
                xbmcgui.NOTIFICATION_INFO, 3000)
            break

        labels = [_favorite_display_label(item, kind) for item in items]
        choice = xbmcgui.Dialog().select(
            heading, [u"\u2713  Done Reordering"] + labels)
        if choice <= 0:
            break

        moving_index = choice - 1
        moving = items[moving_index]
        destinations = [
            "Position {0} — {1}".format(index + 1, label)
            for index, label in enumerate(labels)
        ] + ["Move to Top", "Move to Bottom", "Sort Alphabetically"]
        destination = xbmcgui.Dialog().select(
            "Move {0}".format(labels[moving_index]), destinations)
        if destination < 0:
            continue

        if destination == len(labels) + 2:
            items.sort(key=lambda item: _favorite_display_label(item, kind).casefold())
        else:
            item = items.pop(moving_index)
            if destination == len(labels):
                target = 0
            elif destination == len(labels) + 1:
                target = len(items)
            else:
                target = min(destination, len(items))
            items.insert(target, item)

        api.set_order(kind, [item["url"] for item in items])
        changed = True

    if changed:
        xbmc.executebuiltin("Container.Refresh")


# --------------------------------------------------------------------------- #
# Shuffle Favorite Songs
# --------------------------------------------------------------------------- #

@plugin.route("/favorite/shuffle_songs")
def shuffle_favorites():
    """
    Home screen action: build a shuffled playlist from all favorite songs
    and start playing immediately.
    """
    api   = _favorites()
    items = api.get_favorites(kind="song")

    if not items:
        xbmcgui.Dialog().notification(
            plugin.name, "No favorite songs saved yet.", xbmcgui.NOTIFICATION_INFO, 3000
        )
        return

    random.shuffle(items)
    provider_songs = _library()
    if provider_songs:
        provider_songs.build()
    playlist = xbmc.PlayList(xbmc.PLAYLIST_MUSIC)
    playlist.clear()

    for f in items:
        local = provider_songs.resolve(
            f["artist"], f["label"], album=f["album"],
            threshold=_match_threshold(), allow_any_artist=_allow_any_artist()
        ) if provider_songs.size else None
        if not local:
            continue
        url = local["file"]
        li = xbmcgui.ListItem(f["label"], path=url)
        li.setArt(_art(f["thumb"], _cached_background(f["artist"]),
                       clearlogo=_cached_logo(f["artist"])))
        _set_music_tag(li, title=f["label"], artist=f["artist"], album=f["album"])
        playlist.add(url, li)

    if playlist.size() > 0:
        xbmc.Player().play(playlist)
    else:
        xbmcgui.Dialog().notification(
            plugin.name, "Could not load track data — open albums first.",
            xbmcgui.NOTIFICATION_WARNING, 4000
        )


# --------------------------------------------------------------------------- #
# Visualizer toggle
# --------------------------------------------------------------------------- #

@plugin.route("/viz/toggle")
def viz_toggle():
    """
    Toggle the Kodi visualizer on/off.
    If the visualisation window is currently active, send Back to close it.
    Otherwise open it. Works correctly whether called from a context menu
    while music is playing or from any other trigger.
    """
    if xbmc.getCondVisibility("Window.IsActive(visualisation)"):
        xbmc.executebuiltin("Action(Back)")
    else:
        xbmc.executebuiltin("ActivateWindow(Visualisation)")


# --------------------------------------------------------------------------- #
# Stereo Upmix toggle (Kodi built-in surround upmix)
# --------------------------------------------------------------------------- #

@plugin.route("/virt/toggle")
def virt_toggle():
    """
    Toggle Kodi's stereo upmix (Settings > System > Audio > Surround sound upmix).
    Reads the current state, flips it, shows a notification. Falls back silently
    if the setting is unavailable (e.g. on a device where the option doesn't exist).
    """
    import json
    try:
        raw = xbmc.executeJSONRPC(
            '{"jsonrpc":"2.0","id":1,"method":"Settings.GetSettingValue",'
            '"params":{"setting":"audiooutput.stereoupmix"}}'
        )
        current = json.loads(raw).get("result", {}).get("value", None)
        if current is None:
            xbmcgui.Dialog().notification("Stereo Upmix", "Setting not available on this device.", time=3000)
            return
        new_val = not current
        xbmc.executeJSONRPC(
            '{{"jsonrpc":"2.0","id":2,"method":"Settings.SetSettingValue",'
            '"params":{{"setting":"audiooutput.stereoupmix","value":{0}}}}}'.format(
                "true" if new_val else "false"
            )
        )
        label = "Stereo Upmix: ON" if new_val else "Stereo Upmix: OFF"
        xbmcgui.Dialog().notification(label, "", time=2500)
    except Exception as exc:
        xbmcgui.Dialog().notification("Stereo Upmix", "Error: {0}".format(exc), time=3000)


def _virt_label():
    """
    Read the current stereo upmix state once and return a context menu label
    that reflects it. Called once per directory load, not once per item.
    Falls back to a neutral label if the state can't be determined.
    """
    import json
    try:
        raw = xbmc.executeJSONRPC(
            '{"jsonrpc":"2.0","id":1,"method":"Settings.GetSettingValue",'
            '"params":{"setting":"audiooutput.stereoupmix"}}'
        )
        val = json.loads(raw).get("result", {}).get("value", None)
        if val is True:  return u"\U0001f50a  Stereo Upmix: ON  [toggle off]"
        if val is False: return u"\U0001f507  Stereo Upmix: OFF [toggle on]"
    except Exception:
        pass
    return u"\U0001f50a  Toggle Stereo Upmix"


# --------------------------------------------------------------------------- #
# Play Album / Shuffle Album
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# Playlists
#
# Track lists come from chart/similarity APIs (Deezer by default, Last.fm when
# a key is configured); playback comes from the local Kodi music library. The
# provider supplies names and ordering, library.py finds the files.
#
# Items are added with the library file path as the URL, so Kodi plays them
# directly — there is no resolver route and no plugin:// indirection in the
# playback path. Resume, ReplayGain and library playcounts all behave exactly
# as they do when playing from Music > Songs.
# --------------------------------------------------------------------------- #

def _song_genres(song):
    genres = song.get("genres")
    if isinstance(genres, list):
        return [str(g).strip() for g in genres if str(g).strip()]
    return [g.strip() for g in song.get("genre", "").split(",") if g.strip()]


_CHRISTMAS_GENRES = {"christmas", "xmas", "holiday", "holidays",
                     "holiday music", "seasonal", "yuletide"}
_CHRISTMAS_TEXT = re.compile(
    r"\b(christmas|xmas|yuletide|no[eë]l|mistletoe|"
    r"jingle\s+bells?|silent\s+night|deck\s+the\s+halls|"
    r"winter\s+wonderland|o\s+holy\s+night|little\s+drummer\s+boy|"
    r"joy\s+to\s+the\s+world|rudolph|santa\s+(baby|claus|tell\s+me))\b",
    re.IGNORECASE,
)


def _is_christmas_selection(value):
    normalized = norm_title(value, False)
    return any(term in normalized.split() for term in ("christmas", "xmas", "holiday"))


def _is_christmas_track(track):
    """Identify holiday tracks without treating every winter song as Christmas."""
    for genre in _song_genres(track):
        normalized = norm_title(genre, False)
        if normalized in _CHRISTMAS_GENRES or any(
                word in normalized.split() for word in ("christmas", "xmas", "holiday")):
            return True
    text = "%s %s" % (track.get("title", ""), track.get("album", ""))
    return bool(_CHRISTMAS_TEXT.search(text))


def _without_christmas(tracks, allow=False):
    if allow:
        return list(tracks)
    tracks = list(tracks)
    filtered = [track for track in tracks if not _is_christmas_track(track)]
    removed = len(tracks) - len(filtered)
    if removed:
        xbmc.log("[rotation] excluded %d Christmas track(s) from playlist" % removed,
                 xbmc.LOGINFO)
    return filtered


def _balanced_local_tracks(songs, limit):
    """Shuffle a local selection while spreading consecutive artists out."""
    pools = {}
    for song in songs:
        if not song.get("file"):
            continue
        key = norm_artist(song.get("artist", "")) or "unknown"
        pools.setdefault(key, []).append(song)
    for pool in pools.values():
        random.shuffle(pool)
    artists = list(pools)
    random.shuffle(artists)
    result = []
    while artists and len(result) < limit:
        next_round = []
        for artist in artists:
            pool = pools[artist]
            if pool:
                result.append(pool.pop())
                if len(result) >= limit:
                    break
            if pool:
                next_round.append(artist)
        artists = next_round
        random.shuffle(artists)
    return result


def _library_radio_tracks(mode, value=""):
    """Build a local-only radio selection from Rotation's Kodi library index."""
    index = _library()
    index.build()
    limit = _playlist_limit()
    allow_christmas = mode == "genre" and _is_christmas_selection(value)
    songs = _without_christmas(index.songs, allow=allow_christmas)
    if not songs:
        return []

    if mode == "artist":
        local = {}
        for song in songs:
            artist = song.get("artist", "")
            keys = {norm_artist(artist), primary_artist(artist)}
            for key in keys:
                if key:
                    local.setdefault(key, []).append(song)
        seed_key = norm_artist(value)
        seed_pool = list(local.get(seed_key, []))
        try:
            names = _make_playlists().related_artists(value, limit=80)
        except Exception as exc:
            xbmc.log("[rotation] Library Radio relationship lookup failed for %r: %s"
                     % (value, exc), xbmc.LOGWARNING)
            names = []
        related = []
        seen_files = {song["file"] for song in seed_pool}
        for name in names:
            for song in local.get(norm_artist(name), []):
                if song["file"] not in seen_files:
                    related.append(song)
                    seen_files.add(song["file"])

        seed_count = min(len(seed_pool), max(1, limit // 4))
        random.shuffle(seed_pool)
        head = seed_pool[:seed_count]
        result = head + _balanced_local_tracks(related, limit - len(head))
        if len(result) < limit:
            used = {song["file"] for song in result}
            remainder = [song for song in seed_pool if song["file"] not in used]
            result.extend(remainder[:limit - len(result)])
        return result[:limit]

    if mode == "genre":
        wanted = value.casefold()
        matches = [song for song in songs
                   if any(g.casefold() == wanted for g in _song_genres(song))]
        return _balanced_local_tracks(matches, limit)

    if mode == "decade":
        try:
            start = int(value)
        except (TypeError, ValueError):
            return []
        matches = [song for song in songs
                   if start <= int(song.get("year") or 0) <= start + 9]
        return _balanced_local_tracks(matches, limit)

    if mode == "rediscover":
        # Empty last-played values sort first, followed by low play counts and
        # the least recently heard tracks. A larger candidate window retains
        # variety before the artist-balancing pass chooses the final station.
        candidates = sorted(
            songs,
            key=lambda song: (int(song.get("playcount") or 0),
                              song.get("lastplayed", "") or "", random.random())
        )[:max(limit * 3, limit)]
        return _balanced_local_tracks(candidates, limit)

    if mode == "shuffle":
        return _balanced_local_tracks(songs, limit)
    return []


def _library_radio_folder_url(mode, value=""):
    return (plugin.url_for(library_radio_mix)
            + "?mode=" + quote(mode, safe="")
            + "&value=" + quote(str(value), safe=""))


def _library_radio_label(mode, value=""):
    if mode == "artist":
        return "%s Library Radio" % value
    if mode == "genre":
        return "%s Library Radio" % value
    if mode == "decade":
        return "%ss Library Radio" % value
    if mode == "rediscover":
        return "Rediscover Library Radio"
    if mode == "shuffle":
        return "Shuffle Library Radio"
    return "Library Radio"


def _library_radio_favorite_spec(favorite):
    """Return a saved Library Radio mode/value pair, or None."""
    marker = favorite.get("album", "")
    if marker.startswith("library_radio:"):
        return marker.split(":", 1)[1], favorite.get("artist", "")
    url = favorite.get("url", "")
    if "/playlists/library_radio/mix" not in url:
        return None
    query = parse_qs(urlparse(url).query)
    return (query.get("mode", [""])[0], query.get("value", [""])[0])


def _library_radio_context(mode, value, label, include_favorite=True):
    base = (plugin.url_for(library_radio_play)
            + "?mode=" + quote(mode, safe="")
            + "&value=" + quote(str(value), safe="")
            + "&label=" + quote(label, safe=""))
    context = [
        ("▶  Play All", "RunPlugin(%s)" % base),
        ("🔀  Shuffle All", "RunPlugin(%s&shuffle=1)" % base),
    ]
    if include_favorite:
        url = _library_radio_folder_url(mode, value)
        artist = str(value) if mode == "artist" else ""
        thumb = (library_artist_art(artist).get("thumb", "")
                 if artist else "")
        context += _favorite_context(
            _favorites(), "radio", url, label, thumb=thumb,
            artist=str(value), album="library_radio:%s" % mode)
    return context


def _navigation_origin_query():
    origin = xbmc.getInfoLabel("Container.FolderPath") or ""
    return "&origin=" + quote(origin, safe="")


def _navigation_request_is_current():
    """Discard a delayed redirect if Back already left its source screen."""
    origin = unquote(plugin.args.get("origin", [""])[0])
    return not origin or _same_plugin_location(
        xbmc.getInfoLabel("Container.FolderPath"), origin)


def _open_library_radio(mode, value=""):
    if plugin.handle >= 0:
        xbmcplugin.endOfDirectory(plugin.handle, succeeded=False, cacheToDisc=False)
    target = _library_radio_folder_url(mode, value)
    url = plugin.url_for(library_radio_open) + "?target=" + quote(target, safe="")
    url += _navigation_origin_query()
    _schedule_playlist_action(url, "RotationLibraryRadioOpen")


def _user_playlist_track(entry, song=None, cover=""):
    """Merge provider and resolved-library metadata into a portable row."""
    row = dict(entry or {})
    song = song or {}
    for key in ("artist", "title", "album", "album_artist", "duration"):
        if not row.get(key) and song.get(key):
            row[key] = song[key]
    row["cover"] = (cover or row.get("cover") or song.get("thumb") or
                    (song.get("source") or {}).get("cover") or "")
    if song.get("file"):
        row["file"] = song["file"]
    return row


def _user_playlist_add_url(scope, entry=None, kind="", arg="", label="",
                           artist=""):
    entry = entry or {}
    values = {
        "scope": scope, "kind": kind, "arg": str(arg), "label": label,
        "artist": artist or entry.get("artist", ""),
        "title": entry.get("title", ""), "album": entry.get("album", ""),
        "album_artist": entry.get("album_artist", ""),
        "duration": entry.get("duration", ""),
        "cover": entry.get("cover", ""), "file": entry.get("file", ""),
        "album_id": entry.get("album_id", ""), "id": entry.get("id", ""),
    }
    query = "&".join("%s=%s" % (key, quote(str(value or ""), safe=""))
                     for key, value in values.items())
    return plugin.url_for(user_playlist_add) + "?" + query


def _user_playlist_track_context(entry, song=None, cover="", playlist_id="",
                                 position=-1):
    row = _user_playlist_track(entry, song, cover)
    items = [("＋  Add to My Playlist…", "RunPlugin(%s)" %
              _user_playlist_add_url("track", row))]
    if playlist_id and position >= 0:
        base = (plugin.url_for(user_playlist_track_action)
                + "?playlist_id=" + quote(playlist_id, safe="")
                + "&position=" + str(position))
        items.extend([
            ("↑  Move Up", "RunPlugin(%s&action=up)" % base),
            ("↓  Move Down", "RunPlugin(%s&action=down)" % base),
            ("⇈  Move to Top", "RunPlugin(%s&action=top)" % base),
            ("⇊  Move to Bottom", "RunPlugin(%s&action=bottom)" % base),
            ("✕  Remove from This Playlist", "RunPlugin(%s&action=remove)" % base),
        ])
    return items


def _choose_user_playlist(store, default_name=""):
    rows = store.list()
    labels = ["Create New Playlist"] + [row.get("name") or "Untitled Playlist"
                                         for row in rows]
    choice = xbmcgui.Dialog().select("Add to My Playlist", labels)
    if choice < 0:
        return None
    if choice == 0:
        name = xbmcgui.Dialog().input("New Playlist Name", defaultt=default_name,
                                      type=xbmcgui.INPUT_ALPHANUM).strip()
        if not name:
            return None
        return store.create(name)
    return rows[choice - 1]


def _recommended_snapshot_name(store):
    today = datetime.datetime.now()
    base = "Recommended Radio — %s %d, %d" % (
        today.strftime("%B"), today.day, today.year)
    existing = {str(row.get("name") or "").casefold() for row in store.list()}
    if base.casefold() not in existing:
        return base
    number = 2
    while ("%s (%d)" % (base, number)).casefold() in existing:
        number += 1
    return "%s (%d)" % (base, number)


def _snapshot_default_name(store, kind, arg, label):
    """Suggest an editable name that reflects the playlist being captured."""
    if kind == "for_you" and str(arg).partition("@")[0] == "recommended":
        return _recommended_snapshot_name(store)
    base = re.sub(r"\[/?(?:B|I|COLOR(?:=[^]]+)?)\]", "", str(label or "")).strip()
    base = base or "Playlist Snapshot"
    today = datetime.datetime.now()
    dated = "%s — %s %d, %d" % (
        base, today.strftime("%B"), today.day, today.year)
    existing = {str(row.get("name") or "").casefold() for row in store.list()}
    # Personalized/generated lists describe a moment in time. Named provider
    # playlists retain their clean published title unless that title exists.
    candidate = dated if kind == "for_you" or base.casefold() in existing else base
    if candidate.casefold() not in existing:
        return candidate
    number = 2
    while ("%s (%d)" % (candidate, number)).casefold() in existing:
        number += 1
    return "%s (%d)" % (candidate, number)


def _album_playlist_default_name(store, entries, label="", artist=""):
    """Use the album artist rather than a guest singer for an editable name."""
    album = next((str(entry.get("album") or "").strip() for entry in entries
                  if str(entry.get("album") or "").strip()), "")
    album_artist = next((str(entry.get("album_artist") or "").strip()
                         for entry in entries
                         if str(entry.get("album_artist") or "").strip()), "")
    artist = album_artist or str(artist or "").strip() or next(
        (str(entry.get("artist") or "").strip() for entry in entries
         if str(entry.get("artist") or "").strip()), "")
    base = "%s — %s" % (artist, album) if artist and album else album or label
    return _snapshot_default_name(store, "deezer_album", "", base)


def _refresh_user_playlist(playlist_id, reason="user playlist edited"):
    # This is an explicit user mutation, not a cosmetic background update.
    # Rebuild the visible directory once Kodi has dismissed the edit/context
    # dialog. It must not depend on service.py, idle time, or artwork markers.
    xbmc.executebuiltin(
        "AlarmClock(RotationUserPlaylistRefresh,Container.Refresh,"
        "00:00:02,silent,false)")


def _refresh_user_playlists_root():
    xbmc.executebuiltin(
        "AlarmClock(RotationUserPlaylistsRootRefresh,Container.Refresh,"
        "00:00:02,silent,false)")


def _playlist_management_context(playlist_id):
    url = (plugin.url_for(user_playlist_manage)
           + "?playlist_id=" + quote(playlist_id, safe=""))
    return [("✎  Edit Playlist", "RunPlugin(%s)" % url)]

def _playlist_entries(kind, arg=""):
    """
    Fetch provider entries for a playlist spec.

    Centralised so the listing route and the Play All action resolve the same
    spec to the same tracks — the second call is a cache hit, so pressing
    Play All on a 100-track chart costs no extra HTTP.
    """
    source = _make_playlists()
    limit = _playlist_limit()

    entries = []
    if kind == "top":
        entries = source.top_tracks(limit=limit)
    elif kind == "country":
        entries = source.country_tracks(limit=limit)
    elif kind == "genre":
        entries = source.genre_tracks(arg, limit=limit)
    elif kind == "editorial":
        entries = source.playlist_tracks(arg, limit=limit)
    elif kind == "deezer_album":
        entries = source.deezer.album_tracks(arg)
    elif kind == "artist_top":
        entries = _artist_top_entries(arg)
    elif kind == "tag":
        entries = source.tag_tracks(arg, limit=limit)
    elif kind == "radio":
        snapshot = source.cache.get_stale(_radio_cache_key(arg)) or {}
        if snapshot.get("complete"):
            entries = snapshot.get("entries", [])
        else:
            entries = source.artist_radio(arg, limit=limit)
    elif kind == "user":
        entries = (_user_playlists().get(arg).get("tracks", []))
    elif kind == "for_you":
        entries = _for_you_entries(arg)
    if kind in ("artist_top", "radio"):
        entries = _dedupe_track_entries(
            entries, min(50, limit) if kind == "artist_top" else limit)
    entries = _playlist_metadata_policy(entries)
    return _without_christmas(
        entries, allow=(kind == "user" or
                        (kind == "tag" and _is_christmas_selection(arg))))


def _playlist_play_context(kind, arg, label):
    """Play All / Shuffle All context menu entries for a playlist item."""
    base = (plugin.url_for(playlists_play)
            + "?kind=" + quote(kind, safe="")
            + "&arg="  + quote(str(arg), safe="")
            + "&label=" + quote(label, safe=""))
    items = [
        ("▶  Play All",    "RunPlugin({0})".format(base)),
        ("🔀  Shuffle All", "RunPlugin({0}&shuffle=1)".format(base)),
    ]
    if kind == "user":
        items += _playlist_management_context(str(arg))
    elif kind == "deezer_album":
        save_url = _user_playlist_add_url(
            "album", kind=kind, arg=arg, label=label)
        items.append(("＋  Add Album to My Playlist…",
                      "RunPlugin(%s)" % save_url))
    else:
        save_url = _user_playlist_add_url(
            "snapshot", kind=kind, arg=arg, label=label)
        items.append(("＋  Save Snapshot to My Playlists",
                      "RunPlugin(%s)" % save_url))
    return items


def _playlist_report_missing_url(kind, arg, label):
    return (plugin.url_for(playlists_report_missing)
            + "?kind=" + quote(kind, safe="")
            + "&arg=" + quote(str(arg), safe="")
            + "&label=" + quote(label, safe=""))




def _track_album_url(entry):
    """Context action that resolves and opens the album containing a track."""
    return (plugin.url_for(track_open_album)
            + "?artist=" + quote(str(entry.get("artist") or ""), safe="")
            + "&title=" + quote(str(entry.get("title") or ""), safe="")
            + "&album=" + quote(str(entry.get("album") or ""), safe="")
            + "&album_id=" + quote(str(entry.get("album_id") or ""), safe=""))




@plugin.route("/track/open_album")
def track_open_album():
    """Open a known album directly or identify it from artist/title first."""
    artist = _route_arg("artist").strip()
    title = _route_arg("title").strip()
    album = _route_arg("album").strip()
    album_id = _route_arg("album_id").strip()
    source = _make_playlists()
    progress = None
    if not album_id:
        progress = xbmcgui.DialogProgressBG()
        progress.create("Rotation", "Finding the source album…")
        try:
            matched = _playlist_album_match(
                {"artist": artist, "title": title, "album": album}, source) or {}
            album = (matched.get("album") or album).strip()
            album_id = str(matched.get("album_id") or "").strip()
            # Last.fm can identify the album title but has no Deezer ID. Match
            # that title against Deezer so Rotation can open the normal album
            # page rather than jumping straight to a download confirmation.
            if album and not album_id:
                candidates = []
                for row in source.deezer.search_albums(
                        "%s %s" % (artist, album), limit=25):
                    title_score = _similar(
                        norm_title(album, False),
                        norm_title(row.get("title", ""), False))
                    artist_score = _similar(
                        norm_artist(artist), norm_artist(row.get("artist", "")))
                    candidates.append((title_score * 0.75 + artist_score * 0.25,
                                       title_score, artist_score, row))
                _, title_score, artist_score, found = max(
                    candidates, default=(0, 0, 0, None),
                    key=lambda item: item[0])
                if found and title_score >= 0.86 and artist_score >= 0.72:
                    album_id = str(found.get("id") or "")
                    album = found.get("title") or album
        except Exception as exc:
            xbmc.log("[rotation] Track album navigation failed: %s" % exc,
                     xbmc.LOGWARNING)
        finally:
            progress.close()
    if not album_id:
        xbmcgui.Dialog().ok(
            "Find Album",
            "Rotation could not reliably identify the album containing %s by %s."
            % (title or "this track", artist or "this artist"))
        if plugin.handle >= 0:
            _end_action()
        return
    target = plugin.url_for(playlists_deezer_album, album_id)
    if plugin.handle >= 0:
        xbmcplugin.endOfDirectory(plugin.handle, succeeded=False)
    xbmc.executebuiltin("Container.Update(%s)" % target)


def _playlist_listitem(song, position=0, cover=""):
    """Build a playable ListItem for a resolved library song."""
    artist = song.get("artist", "")
    title = song.get("title", "")
    label = "{0} — {1}".format(artist, title) if artist else title

    source = song.get("source") or {}
    thumb = song.get("thumb") or cover or source.get("cover", "")

    li = xbmcgui.ListItem(label, path=song["file"])
    li.setLabel2(artist)
    li.setArt(_art(thumb, _cached_background(artist),
                   clearlogo=_cached_logo(artist)))
    li.setProperty("IsPlayable", "true")
    li.setProperty("Rotation.Availability", "available")
    _set_music_tag(li,
        title=title,
        artist=artist,
        album=song.get("album", ""),
        year=song.get("year", ""),
        genre=song.get("genre", ""),
        duration=song.get("duration", 0),
        tracknumber=position)
    return li


def _playlist_remote_url(entry, session=""):
    """Keep provider metadata in a stable local-library playback URL."""
    url = (plugin.url_for(library_play)
           + "?artist=" + quote(str(entry.get("artist") or ""), safe="")
           + "&title=" + quote(str(entry.get("title") or ""), safe="")
           + "&album=" + quote(str(entry.get("album") or ""), safe="")
           + "&cover=" + quote(str(entry.get("cover") or ""), safe=""))
    if session:
        url += "&session=" + quote(str(session), safe="")
    return url


def _missing_track_url(entry, cover=""):
    """Use the optional stream resolver without changing directory menus."""
    if _musicmp3_enabled():
        remote = dict(entry)
        remote["cover"] = cover or entry.get("cover", "")
        return _playlist_remote_url(remote)
    return (plugin.url_for(library_unavailable)
            + "?artist=" + quote(str(entry.get("artist") or ""), safe="")
            + "&title=" + quote(str(entry.get("title") or ""), safe=""))


def _playlist_artist_url(entry):
    """Stable artist destination for both Deezer and Last.fm playlist rows."""
    artist_id = entry.get("artist_id")
    if artist_id:
        return (plugin.url_for(playlists_deezer_artist, str(artist_id))
                + "?artist=" + quote(str(entry.get("artist") or ""), safe=""))
    return (plugin.url_for(playlists_artist)
            + "?artist=" + quote(str(entry.get("artist") or ""), safe=""))


def _playlist_tracks(entries, index, allow_christmas=False):
    """Resolve locally without separating or reordering the provider's tracks."""
    for entry in entries:
        if not entry.get("artist") or not entry.get("title"):
            continue
        song = index.resolve(entry["artist"], entry["title"],
                             album=entry.get("album", ""),
                             threshold=_match_threshold(),
                             allow_any_artist=_allow_any_artist()) if index.size else None
        if song:
            song = dict(song)
            song["source"] = entry
        if not allow_christmas and (
                _is_christmas_track(entry) or
                (song is not None and _is_christmas_track(song))):
            continue
        yield song, entry


def _dedupe_resolved_tracks(tracks):
    """Remove duplicate labels and duplicate Kodi files after resolution."""
    result = []
    seen_identity = set()
    seen_files = set()
    for song, entry in tracks:
        identity = track_identity_key(entry.get("artist", ""),
                                      entry.get("title", ""))
        if identity in seen_identity:
            continue
        if song:
            source = os.path.normcase(song.get("file", "").strip())
            if source and source in seen_files:
                continue
            if source:
                seen_files.add(source)
        seen_identity.add(identity)
        result.append((song, entry))
    return result


def _availability_key(entry):
    return "%s\n%s" % (norm_artist(entry.get("artist", "")),
                        norm_title(entry.get("title", ""), False))


def _availability_path():
    return os.path.join(PLAYLIST_DIR, "rotation_availability.json")


def _load_availability():
    try:
        with open(_availability_path(), "r", encoding="utf-8") as stream:
            data = json.load(stream)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def _save_availability(data):
    os.makedirs(PLAYLIST_DIR, exist_ok=True)
    path = _availability_path()
    try:
        with open(path + ".tmp", "w", encoding="utf-8") as stream:
            json.dump(data, stream)
        os.replace(path + ".tmp", path)
    except OSError as exc:
        xbmc.log("[rotation] Availability cache could not be saved: %s" % exc,
                 xbmc.LOGWARNING)


def _cached_match(cache, entry):
    cached = cache.get(_availability_key(entry))
    if not isinstance(cached, dict):
        return None, False
    match = cached.get("match")
    ttl = 86400 if match else 3600
    if time.time() - cached.get("checked", 0) > ttl:
        return None, False
    # Positive entries written before playlist-cover enrichment lack the
    # album details needed to repair a missing Last.fm image. Recheck only
    # those rows; entries that already carry provider artwork need no migration.
    if match and not entry.get("cover") and "album" not in match:
        return None, False
    return match, True


def _cached_result_confirmed(cache, entry):
    cached = cache.get(_availability_key(entry))
    return isinstance(cached, dict) and cached.get("confirmed") is True


def _playlist_match_art(cache, entry):
    """Return album/title art learned while checking stream availability."""
    cached = cache.get(_availability_key(entry))
    match = (cached.get("metadata") or cached.get("match")
             if isinstance(cached, dict) else None)
    return match if isinstance(match, dict) else {}


def _playlist_metadata_policy(entries):
    """Merge cached provider metadata and apply the explicit-content choice."""
    cache = _load_availability()
    prepared = []
    for original in entries or []:
        entry = dict(original)
        cached = cache.get(_availability_key(entry))
        metadata = cached.get("metadata") if isinstance(cached, dict) else None
        if isinstance(metadata, dict):
            for field in ("album", "album_id", "cover", "duration",
                          "artist_id", "artist_cover", "explicit"):
                if field in metadata and not entry.get(field):
                    entry[field] = metadata[field]
        if _hide_explicit_content() and entry.get("explicit") is True:
            continue
        prepared.append(entry)
    return prepared


def _playlist_stream_metadata(entries, cache, path, refresh=True, background=True):
    """Enrich album-less streaming rows in the background through Deezer."""
    if not _musicmp3_enabled() or not path:
        return
    now = time.time()
    pending = []
    seen = set()
    for entry in entries:
        key = _availability_key(entry)
        if not key.strip() or key in seen:
            continue
        seen.add(key)
        record = cache.get(key)
        if isinstance(record, dict):
            if isinstance(record.get("metadata"), dict):
                continue
            # A miss may be a temporary Deezer quota response. Retry after a
            # short cooling-off period rather than suppressing artwork all day.
            if now - float(record.get("metadata_checked") or 0) < 900:
                continue
        if entry.get("album") and entry.get("cover") and entry.get("artist_cover"):
            continue
        pending.append(dict(entry))
    if not pending:
        return

    # A Container.Refresh launches a fresh Python plugin instance.  The
    # module-level lock below therefore cannot stop that new instance from
    # starting the same worker again.  Keep a second lock in Kodi's home
    # window, which is shared by every invocation.  Without it, two workers
    # can repeatedly overwrite the availability file from stale snapshots and
    # refresh the directory forever (busy wheel flashing and focus locked).
    window = xbmcgui.Window(10000)
    state_id = hashlib.sha1(path.encode("utf-8")).hexdigest()[:16]
    running_key = "Rotation.StreamMetadata.%s.Running" % state_id
    done_key = "Rotation.StreamMetadata.%s.Done" % state_id
    fingerprint = hashlib.sha1(
        "\n".join(sorted(_availability_key(entry) for entry in entries))
        .encode("utf-8")
    ).hexdigest()
    try:
        prior, timestamp = window.getProperty(done_key).split(":", 1)
        if prior == fingerprint and time.time() - float(timestamp) < 900:
            return
    except (ValueError, TypeError):
        pass
    try:
        if time.time() - float(window.getProperty(running_key)) < 120:
            return
    except (ValueError, TypeError):
        pass
    window.setProperty(running_key, str(time.time()))

    with _PLAYLIST_METADATA_LOCK:
        if path in _PLAYLIST_METADATA_ACTIVE:
            window.clearProperty(running_key)
            return
        _PLAYLIST_METADATA_ACTIVE.add(path)

    def _work():
        changed = 0
        completed = False
        try:
            source = _make_playlists(quiet=True)
            results = {}
            with concurrent.futures.ThreadPoolExecutor(
                    max_workers=min(3, len(pending))) as pool:
                futures = {
                    pool.submit(_playlist_album_match, entry, source, False):
                    _availability_key(entry) for entry in pending}
                for future in concurrent.futures.as_completed(futures):
                    key = futures[future]
                    try:
                        results[key] = future.result()
                    except Exception as exc:
                        xbmc.log("[rotation] Streaming metadata lookup failed: %s" % exc,
                                 xbmc.LOGWARNING)
                        results[key] = None

            latest = _load_availability()
            stamp = time.time()
            for key, match in results.items():
                record = latest.get(key)
                if not isinstance(record, dict):
                    record = {}
                record["metadata_checked"] = stamp
                if match:
                    record["metadata"] = {
                        field: match[field] for field in
                        ("album", "album_id", "cover", "duration", "artist_id",
                         "artist_cover", "explicit") if field in match}
                    changed += 1
                latest[key] = record
            _save_availability(latest)
            xbmc.log("[rotation] Streaming metadata: %d of %d tracks enriched" %
                     (changed, len(pending)), xbmc.LOGINFO)
            completed = True
            # Publish completion before refreshing.  The replacement plugin
            # invocation can begin immediately after executebuiltin returns.
            window.setProperty(done_key, "%s:%s" % (fingerprint, time.time()))
            if changed and refresh:
                _playlist_covers(pending, latest, path)
                _safe_background_refresh(path, "stream metadata")
        finally:
            if not completed:
                window.clearProperty(done_key)
            window.clearProperty(running_key)
            with _PLAYLIST_METADATA_LOCK:
                _PLAYLIST_METADATA_ACTIVE.discard(path)

        return bool(changed)

    if not background:
        return _work()

    thread = threading.Thread(target=_work, name="rotation-stream-metadata")
    thread.daemon = True
    thread.start()


def _playlist_display_cover(entry, cache, provider=None):
    """Prefer enriched album art, then provider art, then resolver art."""
    match = _playlist_match_art(cache, entry)
    artist = entry.get("artist", "")
    album = entry.get("album", "") or match.get("album", "")
    if provider and artist and album:
        try:
            cached = provider.album_art_cached(artist, album)
            if cached and cached.get("thumb"):
                return cached["thumb"]
        except Exception as exc:
            xbmc.log("[rotation] Playlist cover cache read failed for %r / %r: %s"
                     % (artist, album, exc), xbmc.LOGWARNING)
    return (entry.get("cover", "") or match.get("cover", "") or
            match.get("image", ""))


def _start_playlist_artwork(entries, tracks, path, folder="", force_refresh=False, artist=""):
    """Run all artwork stages off the UI thread and publish one final redraw."""
    if not path or not _artwork_for_listing():
        return False
    local_entries = [entry for song, entry in tracks if song]
    fingerprint = hashlib.sha1(json.dumps({
        "entries": sorted(_availability_key(entry) for entry in entries),
        "local": sorted(_availability_key(entry) for entry in local_entries),
    }, sort_keys=True).encode("utf-8")).hexdigest()
    window = xbmcgui.Window(10000)
    force_key = _playlist_art_state_key(path, "batch_refresh_needed")
    if force_refresh:
        window.setProperty(force_key, "1")
    done_key = _playlist_art_state_key(path, "batch_done")
    try:
        prior, stamp = window.getProperty(done_key).split(":", 1)
        if (prior == fingerprint and time.time() - float(stamp) < 900
                and not force_refresh):
            return True
    except (TypeError, ValueError):
        pass
    batch_key = _playlist_art_state_key(path, "batch_running")
    try:
        if time.time() - float(window.getProperty(batch_key) or 0) < 1800:
            _show_playlist_progress(path, folder)
            return True
    except (TypeError, ValueError):
        pass
    window.setProperty(batch_key, str(time.time()))
    xbmc.log("[rotation] Playlist artwork pass started: %s" % path, xbmc.LOGINFO)
    artwork_entries = (_playlist_metadata_policy(entries)
                       if _musicmp3_enabled() else local_entries)
    local_keys = {_availability_key(entry) for entry in local_entries}
    remote_entries = [entry for entry in artwork_entries
                      if _availability_key(entry) not in local_keys]
    names = [entry.get("artist", "") for entry in artwork_entries]
    names += [song.get("artist", "") for song, _ in tracks if song]
    if artist:
        names.append(artist)
    art_key = _playlist_art_key(path)
    window.setProperty(art_key, "0/%d" % max(1, len(set(names))))
    _show_playlist_progress(path, folder)

    def _work():
        changed = False
        completed = False
        monitor = xbmc.Monitor()
        try:
            # Metadata must finish first: it supplies missing album names to
            # the cover stage. No individual stage is allowed to redraw.
            changed |= bool(_playlist_stream_metadata(
                remote_entries, _load_availability(), path,
                refresh=False, background=False))
            if monitor.abortRequested():
                return
            changed |= bool(_playlist_covers(
                artwork_entries, _load_availability(), path, folder,
                refresh=False, background=False))
            if monitor.abortRequested():
                return
            changed |= bool(_playlist_backgrounds(
                names, path, folder, refresh=False, background=False))
            completed = not monitor.abortRequested()
        except Exception as exc:
            xbmc.log("[rotation] Playlist artwork pass failed: %s" % exc,
                     xbmc.LOGWARNING)
        finally:
            if completed:
                # Publish before redraw so transient misses cannot start an
                # automatic lookup/refresh loop in the replacement directory.
                window.setProperty(done_key, "%s:%s" % (fingerprint, time.time()))
            window.clearProperty(batch_key)
            window.clearProperty(art_key)
        refresh_needed = bool(window.getProperty(force_key))
        if completed:
            window.clearProperty(force_key)
        if (changed or refresh_needed) and completed:
            xbmc.log("[rotation] Playlist artwork pass complete; one redraw queued",
                     xbmc.LOGINFO)
            # Refreshing from this worker can block a reused Python invoker.
            # The independent service waits for this invocation to exit first.
            _queue_background_refresh(path, "playlist artwork complete", service_only=True)

    thread = threading.Thread(target=_work, name="rotation-playlist-artwork-pass")
    thread.daemon = True
    thread.start()
    return True


def _playlist_covers(entries, cache, path, folder="", refresh=True, background=True):
    """Resolve missing playlist album covers without delaying the directory.

    Last.fm radio rows generally have no album name.  The availability scan
    learns it from the local-library match; each partial directory refresh
    can therefore contribute another batch of artist/album pairs here.  Deezer
    bulk lookup is used because it is fast, concurrent and writes into the same
    cache counted by the Artwork Cache maintenance action.
    """
    provider = _artwork_for_listing()
    if not provider or not path:
        return

    pairs = []
    for entry in entries:
        if entry.get("cover"):
            continue
        match = _playlist_match_art(cache, entry)
        artist = entry.get("artist", "")
        album = entry.get("album", "") or match.get("album", "")
        if not artist or not album:
            continue
        try:
            found = provider.album_art_cached(artist, album)
        except Exception:
            found = None
        if (not (found and found.get("thumb"))
                and (artist, album) not in _PLAYLIST_COVER_TRIED):
            pairs.append((artist, album))
    pairs = list(dict.fromkeys(pairs))
    if not pairs:
        return

    with _PLAYLIST_COVER_LOCK:
        if path in _PLAYLIST_COVER_ACTIVE:
            return
        _PLAYLIST_COVER_ACTIVE.add(path)
        _PLAYLIST_COVER_TRIED.update(pairs)

    def _work():
        worker = None
        changed = 0
        try:
            worker = _make_artwork()
            if not worker:
                return
            monitor = xbmc.Monitor()
            # Small batches bound provider work, but refresh only once after
            # all batches. Replacing the directory per batch repeatedly steals
            # focus while a user is scrolling through a long track list.
            for offset in range(0, len(pairs), 12):
                if monitor.abortRequested():
                    break
                batch = pairs[offset:offset + 12]
                found = worker.album_art_bulk(
                    batch, budget=max(3.0, _artwork_budget()), workers=6)
                hits = sum(1 for value in found.values()
                           if value and value.get("thumb"))
                changed += hits
            xbmc.log("[rotation] Playlist album covers: %d of %d found"
                     % (changed, len(pairs)), xbmc.LOGINFO)
            if changed and refresh:
                _safe_background_refresh(path, "playlist album covers")
        except Exception as exc:
            xbmc.log("[rotation] Playlist cover worker failed: %s" % exc,
                     xbmc.LOGWARNING)
        finally:
            if worker:
                worker.close()
            with _PLAYLIST_COVER_LOCK:
                _PLAYLIST_COVER_ACTIVE.discard(path)

        return bool(changed)

    if not background:
        return _work()

    thread = threading.Thread(target=_work, name="rotation-playlist-covers")
    thread.daemon = True
    thread.start()


_REPORT_FIELDS = ("artist", "title", "album", "playlists", "first seen", "last checked")
_MOST_WANTED_FIELDS = ("rank", "artist", "title", "album",
                       "playlist count", "encounters", "playlists",
                       "last checked")
MOST_WANTED_LIMIT = 50


def _report_path():
    return os.path.join(PLAYLIST_DIR, "unavailable_tracks.csv")


def _report_data_path():
    return os.path.join(PLAYLIST_DIR, "unavailable_tracks.json")


def _most_wanted_path():
    return os.path.join(PLAYLIST_DIR, "most_wanted_tracks.csv")


def _ignored_path():
    return os.path.join(PLAYLIST_DIR, "ignored_tracks.json")


def _load_unavailable_report():
    rows = {}
    try:
        with open(_report_data_path(), "r", encoding="utf-8") as stream:
            stored = json.load(stream)
        if isinstance(stored, dict):
            return stored
    except (OSError, ValueError, TypeError):
        pass
    # One-time migration from Rotation's CSV-only report.
    try:
        with open(_report_path(), "r", encoding="utf-8", newline="") as stream:
            for row in csv.DictReader(stream):
                key = "%s\n%s" % (norm_artist(row.get("artist", "")),
                                    norm_title(row.get("title", ""), False))
                if key.strip():
                    rows[key] = row
    except (OSError, csv.Error):
        pass
    return rows


def _save_unavailable_report(rows):
    os.makedirs(PLAYLIST_DIR, exist_ok=True)
    try:
        with open(_report_data_path() + ".tmp", "w", encoding="utf-8") as stream:
            json.dump(rows, stream, ensure_ascii=False, indent=2, sort_keys=True)
        os.replace(_report_data_path() + ".tmp", _report_data_path())
    except OSError as exc:
        xbmc.log("[rotation] Unavailable catalog could not be saved: %s" % exc,
                 xbmc.LOGWARNING)
    if not _unavailable_csv_enabled():
        return
    path = _report_path()
    try:
        with open(path + ".tmp", "w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=_REPORT_FIELDS)
            writer.writeheader()
            for row in sorted(rows.values(), key=lambda r: (r["artist"].lower(), r["title"].lower())):
                writer.writerow({field: row.get(field, "") for field in _REPORT_FIELDS})
        os.replace(path + ".tmp", path)
    except OSError as exc:
        xbmc.log("[rotation] Unavailable report could not be saved: %s" % exc,
                 xbmc.LOGWARNING)


def _reconcile_unavailable_with_library(force=False):
    """Remove report rows now present in Kodi; return the current report."""
    marker = os.path.join(PLAYLIST_DIR, "reconcile-imports")
    if not force and not os.path.exists(marker):
        return _load_unavailable_report()
    try:
        index = _library()
        if os.path.exists(marker):
            index.invalidate()
            index.build(force=True)
        else:
            index.build()
        rows = _load_unavailable_report()
        changed = False
        for key, row in list(rows.items()):
            song = index.resolve(
                row.get("artist", ""), row.get("title", ""),
                album=row.get("album", ""), threshold=_match_threshold(),
                allow_any_artist=_allow_any_artist()) if index.size else None
            if song:
                del rows[key]
                changed = True
        if changed:
            _save_unavailable_report(rows)
        if os.path.exists(marker):
            os.remove(marker)
        return rows
    except Exception as exc:
        xbmc.log("[rotation] Import reconciliation deferred: %s" % exc,
                 xbmc.LOGWARNING)
        return _load_unavailable_report()


def _reconcile_imported_unavailable():
    """Backward-compatible entry point used when Rotation's root opens."""
    _reconcile_unavailable_with_library(force=False)


def _update_unavailable_report(rows, entry, heading, playable, confirmed):
    """Update only definitive results; transient failures leave prior state intact."""
    key = _availability_key(entry)
    if playable:
        return rows.pop(key, None) is not None
    if not confirmed:
        return False
    ignored = _load_ignored()
    album_key = _album_report_key(entry.get("artist", ""), entry.get("album", ""))
    if (key in ignored.get("tracks", {}) or
            norm_artist(entry.get("artist", "")) in ignored.get("artists", {}) or
            (entry.get("album") and album_key in ignored.get("albums", {}))):
        return rows.pop(key, None) is not None
    today = datetime.date.today().isoformat()
    old = rows.get(key, {})
    playlists = set(filter(None, old.get("playlists", "").split(" | ")))
    playlists.add(heading)
    rows[key] = {
        "artist": entry.get("artist", ""), "title": entry.get("title", ""),
        "album": entry.get("album", "") or old.get("album", ""),
        "playlists": " | ".join(sorted(playlists)),
        "encounters": int(old.get("encounters") or len(playlists)),
        "source counts": old.get("source counts", {}),
        "first seen": old.get("first seen", today), "last checked": today,
    }
    return rows[key] != old


def _unavailable_sources(row):
    """Distinct playlist/radio names currently missing one track."""
    return sorted(set(filter(None, row.get("playlists", "").split(" | "))),
                  key=str.casefold)


def _most_wanted_rows(rows, limit=MOST_WANTED_LIMIT):
    """Rank misses without allowing repeated scans of one source to inflate them."""
    ranked = list(rows.items())
    ranked.sort(key=lambda item: (
        -len(_unavailable_sources(item[1])),
        -(int(item[1].get("encounters") or 0)),
        -(int((item[1].get("last checked", "") or "0").replace("-", ""))
          if (item[1].get("last checked", "") or "").replace("-", "").isdigit()
          else 0),
        item[1].get("artist", "").casefold(),
        item[1].get("title", "").casefold(),
    ))
    return ranked[:max(0, int(limit))]


def _save_most_wanted_csv(rows):
    """Write the capped acquisition list separately from the full report."""
    os.makedirs(PLAYLIST_DIR, exist_ok=True)
    path = _most_wanted_path()
    try:
        with open(path + ".tmp", "w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=_MOST_WANTED_FIELDS)
            writer.writeheader()
            for rank, (_, row) in enumerate(_most_wanted_rows(rows), 1):
                sources = _unavailable_sources(row)
                writer.writerow({
                    "rank": rank,
                    "artist": row.get("artist", ""),
                    "title": row.get("title", ""),
                    "album": row.get("album", ""),
                    "playlist count": len(sources),
                    "encounters": int(row.get("encounters") or len(sources)),
                    "playlists": " | ".join(sources),
                    "last checked": row.get("last checked", ""),
                })
        os.replace(path + ".tmp", path)
        return True
    except OSError as exc:
        xbmc.log("[rotation] Most Wanted CSV could not be saved: %s" % exc,
                 xbmc.LOGWARNING)
        return False


def _reconcile_unavailable_source(rows, heading, missing_counts):
    """Replace one source's old misses with the result of its latest scan."""
    changed = False
    missing_counts = dict(missing_counts)
    for key, row in list(rows.items()):
        sources = set(_unavailable_sources(row))
        if heading not in sources and key not in missing_counts:
            continue
        source_counts = dict(row.get("source counts") or
                             {source: 1 for source in sources})
        if key in missing_counts:
            sources.add(heading)
            source_counts[heading] = max(1, int(missing_counts[key]))
        else:
            sources.discard(heading)
            source_counts.pop(heading, None)
        if sources:
            updated = dict(row)
            updated["playlists"] = " | ".join(sorted(sources, key=str.casefold))
            updated["source counts"] = source_counts
            updated["encounters"] = sum(int(source_counts.get(source) or 1)
                                        for source in sources)
            rows[key] = updated
        else:
            rows.pop(key, None)
        changed = True
    return changed


def _load_ignored():
    empty = {"tracks": {}, "artists": {}, "albums": {}}
    try:
        with open(_ignored_path(), "r", encoding="utf-8") as stream:
            data = json.load(stream)
        if not isinstance(data, dict):
            return empty
        for name in empty:
            data.setdefault(name, {})
        return data
    except (OSError, ValueError, TypeError):
        return empty


def _album_report_key(artist, album):
    return "%s\n%s" % (norm_artist(artist), norm_title(album, False))


def _resolve_report_albums(rows):
    """Lazily identify albums for the highest-value unresolved misses."""
    unresolved = [(key, row) for key, row in _most_wanted_rows(rows)
                  if not (row.get("album") or "").strip()]
    if not unresolved:
        return rows
    source = _make_playlists()
    progress = xbmcgui.DialogProgressBG()
    progress.create("Rotation", "Identifying missing albums…")
    changed = False
    try:
        workers = min(4, len(unresolved))
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(_playlist_album_match, dict(original), source): key
                       for key, original in unresolved}
            completed = 0
            for future in concurrent.futures.as_completed(futures):
                key = futures[future]
                completed += 1
                try:
                    match = future.result()
                except Exception as exc:
                    xbmc.log("[rotation] Missing album lookup failed: %s" % exc,
                             xbmc.LOGWARNING)
                    match = None
                album = (match or {}).get("album", "").strip()
                if album and key in rows:
                    rows[key]["album"] = album
                    if (match or {}).get("cover"):
                        rows[key]["cover"] = match["cover"]
                    changed = True
                progress.update(int(completed * 100 / max(1, len(unresolved))),
                                "Identifying missing albums…")
    finally:
        progress.close()
    if changed:
        _save_unavailable_report(rows)
    return rows


def _most_wanted_album_rows(rows, limit=MOST_WANTED_LIMIT):
    grouped = {}
    ignored = _load_ignored().get("albums", {})
    for track_key, row in rows.items():
        artist = (row.get("artist") or "").strip()
        album = (row.get("album") or "").strip()
        album_key = _album_report_key(artist, album)
        if not artist or not album or album_key in ignored:
            continue
        item = grouped.setdefault(album_key, {
            "artist": artist, "album": album, "cover": row.get("cover", ""),
            "tracks": [], "sources": set(), "encounters": 0})
        item["tracks"].append((track_key, row))
        item["sources"].update(_unavailable_sources(row))
        item["encounters"] += int(row.get("encounters") or 1)
        if not item.get("cover") and row.get("cover"):
            item["cover"] = row["cover"]
    ranked = list(grouped.items())
    ranked.sort(key=lambda pair: (-len(pair[1]["tracks"]),
                                  -len(pair[1]["sources"]),
                                  -pair[1]["encounters"],
                                  pair[1]["artist"].casefold(),
                                  pair[1]["album"].casefold()))
    return ranked[:max(0, int(limit))]


def _save_ignored(data):
    os.makedirs(PLAYLIST_DIR, exist_ok=True)
    with open(_ignored_path() + ".tmp", "w", encoding="utf-8") as stream:
        json.dump(data, stream, ensure_ascii=False, indent=2, sort_keys=True)
    os.replace(_ignored_path() + ".tmp", _ignored_path())


def _report_action_url(action, key="", value=""):
    return (plugin.url_for(unavailable_action)
            + "?action=" + quote(action, safe="")
            + "&key=" + quote(key, safe="")
            + "&value=" + quote(value, safe=""))


@plugin.route("/maintenance/unavailable")
def unavailable_root():
    rows = _reconcile_unavailable_with_library(force=True)
    recent = sum(1 for row in rows.values()
                 if row.get("first seen") == datetime.date.today().isoformat())
    playlists = set()
    artists = set()
    for row in rows.values():
        artists.add(row.get("artist", ""))
        playlists.update(filter(None, row.get("playlists", "").split(" | ")))
    ignored = _load_ignored()
    choices = [
        ("Most Wanted Albums", "albums"),
        ("Most Wanted Tracks (%d)" % min(len(rows), MOST_WANTED_LIMIT), "wanted"),
        ("All Missing Tracks (%d)" % len(rows), "all"),
        ("By Playlist/Radio (%d)" % len(playlists), "playlists"),
        ("By Artist (%d)" % len(set(filter(None, artists))), "artists"),
        ("Recently Discovered (%d)" % recent, "recent"),
        ("Ignored Music (%d)" % (len(ignored.get("tracks", {})) +
                                  len(ignored.get("artists", {})) +
                                  len(ignored.get("albums", {}))), "ignored"),
    ]
    for label, mode in choices:
        li = xbmcgui.ListItem(label)
        li.setArt({"icon": ICON_MAINTENANCE, "fanart": FANART})
        xbmcplugin.addDirectoryItem(plugin.handle,
            plugin.url_for(unavailable_browse, mode), li, True)
    actions = [
        ("Export Most Wanted as CSV", "export_wanted"),
        ("Clear Missing Music History", "clear"),
    ]
    if _unavailable_csv_enabled():
        actions.insert(0, ("Export/Update CSV", "export"))
    for label, action in actions:
        li = xbmcgui.ListItem(label)
        li.setArt({"icon": ICON_MAINTENANCE, "fanart": FANART})
        xbmcplugin.addDirectoryItem(plugin.handle, _report_action_url(action), li, False)
    xbmcplugin.endOfDirectory(plugin.handle)


@plugin.route("/maintenance/unavailable/<mode>")
def unavailable_browse(mode):
    # Child pages reconcile too, so an auto-refresh can update Most Wanted
    # without requiring a trip back through the Maintenance menu.
    rows = _reconcile_unavailable_with_library(force=True)
    value = unquote(plugin.args.get("value", [""])[0])
    if mode == "albums":
        rows = _resolve_report_albums(rows)
        for album_key, album_row in _most_wanted_album_rows(rows):
            artist, album = album_row["artist"], album_row["album"]
            track_count = len(album_row["tracks"])
            source_count = len(album_row["sources"])
            li = xbmcgui.ListItem("%s — %s" % (artist, album))
            li.setLabel2("%d missing track%s · %d source%s" %
                         (track_count, "s" if track_count != 1 else "",
                          source_count, "s" if source_count != 1 else ""))
            li.setArt(_art(album_row.get("cover", ""),
                           _cached_background(artist), FALLBACK_ALBUM))
            album_url = (plugin.url_for(albums_search_results)
                         + "?query=" + quote("%s %s" % (artist, album), safe=""))
            tracks_url = (plugin.url_for(unavailable_browse, "album_tracks")
                          + "?value=" + quote(album_key, safe=""))
            li.addContextMenuItems([
                ("Browse Album Search Results", "Container.Update(%s)" % album_url),
                ("View Wanted Tracks", "Container.Update(%s)" % tracks_url),
                ("View Source Playlists", "RunPlugin(%s)" %
                 _report_action_url("show_album_sources", value=album_key)),
                ("Ignore Album", "RunPlugin(%s)" %
                 _report_action_url("ignore_album", value=album_key)),
            ], replaceItems=True)
            xbmcplugin.addDirectoryItem(plugin.handle, album_url, li, True)
        _set_view("files")
        xbmcplugin.endOfDirectory(plugin.handle)
        return
    if mode in ("playlists", "artists") and not value:
        counts = {}
        for row in rows.values():
            names = (row.get("playlists", "").split(" | ") if mode == "playlists"
                     else [row.get("artist", "")])
            for name in filter(None, names):
                counts[name] = counts.get(name, 0) + 1
        for name, count in sorted(counts.items(), key=lambda item: (-item[1], item[0].lower())):
            li = xbmcgui.ListItem("%s (%d)" % (name, count))
            li.setArt({"icon": ICON_PLAYLISTS if mode == "playlists" else ICON_ARTISTS,
                       "fanart": FANART})
            url = plugin.url_for(unavailable_browse, mode) + "?value=" + quote(name, safe="")
            xbmcplugin.addDirectoryItem(plugin.handle, url, li, True)
        xbmcplugin.endOfDirectory(plugin.handle)
        return
    if mode == "ignored":
        ignored = _load_ignored()
        for key, item in sorted(ignored.get("tracks", {}).items()):
            li = xbmcgui.ListItem("%s — %s" % (item.get("artist", ""), item.get("title", "")))
            li.addContextMenuItems([("Restore", "RunPlugin(%s)" % _report_action_url("restore_track", key))])
            li.setArt(_art("", fallback=FALLBACK_ALBUM))
            xbmcplugin.addDirectoryItem(plugin.handle, "", li, False)
        for key, name in sorted(ignored.get("artists", {}).items()):
            li = xbmcgui.ListItem("Ignored artist: %s" % name)
            li.addContextMenuItems([("Restore", "RunPlugin(%s)" % _report_action_url("restore_artist", key))])
            li.setArt(_art("", fallback=FALLBACK_ARTIST))
            xbmcplugin.addDirectoryItem(plugin.handle, "", li, False)
        for key, item in sorted(ignored.get("albums", {}).items()):
            li = xbmcgui.ListItem("Ignored album: %s — %s" %
                                  (item.get("artist", ""), item.get("album", "")))
            li.addContextMenuItems([("Restore", "RunPlugin(%s)" %
                                    _report_action_url("restore_album", value=key))])
            li.setArt(_art("", fallback=FALLBACK_ALBUM))
            xbmcplugin.addDirectoryItem(plugin.handle, "", li, False)
        xbmcplugin.endOfDirectory(plugin.handle)
        return
    today = datetime.date.today().isoformat()
    if mode == "album_tracks":
        selected = []
        for album_key, album_row in _most_wanted_album_rows(rows, limit=len(rows)):
            if album_key == value:
                selected = album_row["tracks"]
                break
    elif mode == "wanted":
        selected = _most_wanted_rows(rows)
    else:
        selected = []
        for key, row in rows.items():
            if mode == "recent" and row.get("first seen") != today:
                continue
            if mode == "playlists" and value not in row.get("playlists", "").split(" | "):
                continue
            if mode == "artists" and row.get("artist", "") != value:
                continue
            selected.append((key, row))
        selected.sort(key=lambda item: (item[1].get("artist", "").lower(),
                                        item[1].get("title", "").lower()))
    for key, row in selected:
        sources = _unavailable_sources(row)
        occurrences = len(sources)
        li = xbmcgui.ListItem("%s — %s" % (row.get("artist", ""), row.get("title", "")))
        if mode == "wanted":
            li.setLabel2("Missing from %d playlist%s" %
                         (occurrences, "s" if occurrences != 1 else ""))
        else:
            li.setLabel2("%s · %d source%s" %
                         (row.get("album", ""), occurrences,
                          "s" if occurrences != 1 else ""))
        li.setArt(_art("", _cached_background(row.get("artist", "")), FALLBACK_ALBUM))
        li.setProperty("IsPlayable", "false")
        li.addContextMenuItems([
            ("Open Album for This Track", "RunPlugin(%s)" %
             _track_album_url(row)),
            ("View Source Playlists", "RunPlugin(%s)" %
             _report_action_url("show_sources", key)),
            ("Remove From Report", "RunPlugin(%s)" % _report_action_url("remove", key)),
            ("Ignore This Track", "RunPlugin(%s)" % _report_action_url("ignore_track", key)),
            ("Ignore Artist", "RunPlugin(%s)" % _report_action_url("ignore_artist", key, row.get("artist", ""))),
        ], replaceItems=True)
        xbmcplugin.addDirectoryItem(
            plugin.handle, _report_action_url("show_sources", key), li, False)
    # These are report records, not playable media. Declaring them as files
    # prevents Kodi from inventing Play, Queue and music-information actions.
    _set_view("files")
    xbmcplugin.endOfDirectory(plugin.handle)


@plugin.route("/maintenance/report_action")
def unavailable_action():
    action = unquote(plugin.args.get("action", [""])[0])
    key = unquote(plugin.args.get("key", [""])[0])
    value = unquote(plugin.args.get("value", [""])[0])
    rows = _load_unavailable_report()
    ignored = _load_ignored()
    if action == "clear":
        if xbmcgui.Dialog().yesno("Clear Missing Music History?",
                                  "Delete all tracked missing songs and reset Most Wanted?"):
            rows = {}
            _save_most_wanted_csv(rows)
    elif action == "export":
        _save_unavailable_report(rows)
        xbmcgui.Dialog().notification(plugin.name, "CSV report updated.", xbmcgui.NOTIFICATION_INFO, 2500)
        return
    elif action == "export_wanted":
        if _save_most_wanted_csv(rows):
            xbmcgui.Dialog().notification(
                plugin.name, "Most Wanted CSV exported.",
                xbmcgui.NOTIFICATION_INFO, 2500)
        else:
            _notify_error("Most Wanted CSV could not be exported.")
        return
    elif action == "show_sources" and key in rows:
        row = rows[key]
        sources = _unavailable_sources(row)
        xbmcgui.Dialog().ok(
            "Source Playlists — %s" % row.get("title", ""),
            "\n".join("• %s" % source for source in sources)
            if sources else "No source playlists recorded.")
        _end_action()
        return
    elif action == "show_album_sources":
        match = next((item for album_key, item in
                      _most_wanted_album_rows(rows, limit=len(rows))
                      if album_key == value), None)
        if match:
            sources = sorted(match["sources"], key=str.casefold)
            xbmcgui.Dialog().ok(
                "Source Playlists — %s" % match["album"],
                "\n".join("• %s" % source for source in sources)
                if sources else "No source playlists recorded.")
        _end_action()
        return
    elif action in ("remove", "ignore_track"):
        row = rows.pop(key, None)
        if action == "ignore_track" and row:
            ignored.setdefault("tracks", {})[key] = {"artist": row.get("artist", ""), "title": row.get("title", "")}
    elif action == "ignore_artist":
        artist_key = norm_artist(value)
        ignored.setdefault("artists", {})[artist_key] = value
        rows = {k: r for k, r in rows.items() if norm_artist(r.get("artist", "")) != artist_key}
    elif action == "ignore_album":
        match = next((item for album_key, item in
                      _most_wanted_album_rows(rows, limit=len(rows))
                      if album_key == value), None)
        if match:
            ignored.setdefault("albums", {})[value] = {
                "artist": match["artist"], "album": match["album"]}
            rows = {k: r for k, r in rows.items()
                    if _album_report_key(r.get("artist", ""),
                                         r.get("album", "")) != value}
    elif action == "restore_track":
        ignored.setdefault("tracks", {}).pop(key, None)
    elif action == "restore_artist":
        ignored.setdefault("artists", {}).pop(key, None)
    elif action == "restore_album":
        ignored.setdefault("albums", {}).pop(value, None)
    _save_ignored(ignored)
    _save_unavailable_report(rows)
    xbmc.executebuiltin("Container.Refresh")


def _check_playlist_tracks(entries, index, heading, on_available=None,
                           on_missing=None, on_progress=None, allow_christmas=False,
                           record_unavailable=True):
    """Verify in order, allowing playback to start on the first good song."""
    tracks = _dedupe_resolved_tracks(list(
        _playlist_tracks(entries, index, allow_christmas=allow_christmas)))
    cache = _load_availability()
    available = []
    changed = False
    report = (_load_unavailable_report()
              if record_unavailable and _unavailable_report_enabled() else None)
    report_changed = False
    missing_counts = {}
    completed = True
    monitor = xbmc.Monitor()
    for checked, (song, entry) in enumerate(tracks, 1):
        if monitor.abortRequested():
            completed = False
            break
        if not song:
            # A completed Kodi JSON-RPC library lookup is definitive. Network
            # provider errors happen before this function and never enter the
            # unavailable report.
            confirmed = True
            playable = False
            # Playback and manual rescans run after the artwork worker has
            # often enriched this same row with Deezer album/cover metadata.
            # Replacing the whole record here erased that metadata, so covers
            # vanished as soon as a playlist was played. Update only the
            # library-availability fields and retain independent enrichment.
            cache_key = _availability_key(entry)
            record = cache.get(cache_key)
            if not isinstance(record, dict):
                record = {}
            record.update({
                "checked": time.time(), "confirmed": True, "match": None})
            cache[cache_key] = record
            changed = True
            missing_key = cache_key
            missing_counts[missing_key] = missing_counts.get(missing_key, 0) + 1
        else:
            playable = True
            confirmed = True
        if report is not None:
            report_changed |= _update_unavailable_report(
                report, entry, heading, playable, confirmed)
        if playable:
            available.append((song, entry))
            if on_available:
                try:
                    on_available(song, entry, len(available))
                except Exception as exc:
                    xbmc.log("[rotation] Playlist queue could not add %r / %r: %s" %
                             (entry["artist"], entry["title"], exc), xbmc.LOGWARNING)
        elif on_missing:
            try:
                on_missing(song, entry, checked)
            except Exception as exc:
                xbmc.log("[rotation] Streaming fallback could not queue %r / %r: %s" %
                         (entry["artist"], entry["title"], exc), xbmc.LOGWARNING)
        if on_progress:
            on_progress(checked, len(tracks), len(available), cache)
    if report is not None and completed:
        report_changed |= _reconcile_unavailable_source(
            report, heading, missing_counts)
    if changed:
        _save_availability(cache)
    if report is not None and report_changed:
        _save_unavailable_report(report)
    return available


def _playlist_queue_callback(autostart=True):
    """Append verified tracks and optionally start after the queue is complete."""
    playlist = xbmc.PlayList(xbmc.PLAYLIST_MUSIC)
    playlist.clear()
    started = [False]
    session = hashlib.sha1(("%s:%s" % (time.time(), random.random()))
                           .encode("utf-8")).hexdigest()[:16]

    def append(song, entry, position):
        if song:
            url = song["file"]
            li = _playlist_listitem(song, position)
        else:
            url = _playlist_remote_url(entry, session=session)
            li = _playlist_remote_listitem(entry, position)
            li.setPath(url)
        playlist.add(url, li)
        if autostart and not started[0]:
            started[0] = True
            xbmc.Player().play(playlist)

    def start():
        if playlist.size() and not started[0]:
            started[0] = True
            xbmc.Player().play(playlist)

    append.start = start
    return append


def _write_playback_queue(payload):
    """Publish a crash-safe queue for the background one-track lookahead."""
    os.makedirs(USER_DATA_DIR, exist_ok=True)
    temporary = PLAYBACK_QUEUE_FILE + ".tmp"
    with open(temporary, "w", encoding="utf-8") as stream:
        json.dump(payload, stream, ensure_ascii=False, default=str)
    os.replace(temporary, PLAYBACK_QUEUE_FILE)


def _read_playback_queue():
    try:
        with open(PLAYBACK_QUEUE_FILE, "r", encoding="utf-8") as stream:
            return json.load(stream)
    except (OSError, ValueError, TypeError):
        return {}


def _resolved_remote_listitem(entry, url, position=0):
    """Create a playable item whose URL has already passed preflight."""
    artist, title = entry.get("artist", ""), entry.get("title", "")
    li = xbmcgui.ListItem(title, path=url)
    li.setArt(_art(entry.get("cover", ""), _cached_background(artist),
                   clearlogo=_cached_logo(artist)))
    li.setMimeType("audio/mpeg")
    li.setContentLookup(False)
    _set_music_tag(li, title=title, artist=artist,
                   album=entry.get("album", ""),
                   duration=entry.get("duration", 0),
                   tracknumber=position)
    return li


def _notify_playlist_count(available, total):
    message = ("%d of %d found in your library" % (available, total)
               if _use_kodi_library() else
               "Streaming playlist ready")
    xbmcgui.Dialog().notification(
        plugin.name, message, xbmcgui.NOTIFICATION_INFO, 4000)


def _playlist_folder_url(kind, arg):
    if kind == "user":
        return plugin.url_for(user_playlist_open, str(arg))
    if kind == "deezer_album":
        return plugin.url_for(playlists_deezer_album, str(arg))
    if kind == "radio":
        return plugin.url_for(playlists_radio_artist) + "?artist=" + quote(arg, safe="")
    if kind == "editorial":
        return plugin.url_for(playlists_editorial_tracks, str(arg))
    if kind == "genre":
        return plugin.url_for(playlists_genre, str(arg))
    if kind == "top":
        return plugin.url_for(playlists_top)
    if kind == "country":
        return plugin.url_for(playlists_country)
    if kind == "for_you":
        return plugin.url_for(for_you_browse, str(arg))
    return ""  # Search and radio routes have no stable directory URL.


def _playlist_refresh_key(folder):
    return "rotation_playlist_refresh_" + hashlib.sha1(folder.encode("utf-8")).hexdigest()


def _playlist_scan_key(folder):
    return _playlist_refresh_key(folder) + "_scan"


def _schedule_playlist_action(url, name):
    """Let Kodi close the directory busy dialog before starting playback."""
    xbmc.executebuiltin("AlarmClock(%s,RunPlugin(%s),00:00:02,silent,false)" %
                        (name, url))


def _playlist_remote_listitem(entry, position=0, cover=""):
    artist, title = entry.get("artist", ""), entry.get("title", "")
    url = _playlist_remote_url(entry)
    li = xbmcgui.ListItem("%s — %s" % (artist, title), path=url)
    li.setLabel2(artist)
    li.setArt(_art(cover or entry.get("cover", ""),
                   _cached_background(artist),
                   clearlogo=_cached_logo(artist)))
    li.setProperty("IsPlayable", "true")
    _set_music_tag(li, title=title, artist=artist,
                   album=entry.get("album", ""),
                   duration=entry.get("duration", 0), tracknumber=position)
    return li


def _playlist_unavailable_listitem(entry, position=0, cover=""):
    """A visible but non-playable provider track for artist discovery pages."""
    artist, title = entry.get("artist", ""), entry.get("title", "")
    show_marker = _show_not_in_library_labels()
    display_title = ("[x] %s" % title
                     if show_marker else title)
    label = ("%s — %s" % (artist, display_title)
             if artist else display_title)
    url = _missing_track_url(entry, cover)
    li = xbmcgui.ListItem(label, path=url)
    li.setLabel2(artist)
    li.setArt(_art(cover or entry.get("cover", ""),
                   _cached_background(artist),
                   clearlogo=_cached_logo(artist)))
    li.setProperty("Rotation.Availability", "missing")
    if _musicmp3_enabled():
        li.setProperty("IsPlayable", "true")
    # Arctic Vibe and other metadata-led skins display the music tag title
    # instead of ListItem.Label, so the availability marker must exist in
    # both places. Plain text remains readable under every focus color.
    _set_music_tag(li, title=display_title, artist=artist,
                   album=entry.get("album", ""),
                   duration=entry.get("duration", 0), tracknumber=position)
    li.addContextMenuItems([
        ("Open Album for This Track", "RunPlugin(%s)" %
         _track_album_url(entry)),
    ], replaceItems=False)
    return li


def _playlist_summary(entries):
    """Return a compact count/runtime heading without estimating missing data."""
    count = len(entries)
    count_label = "%d song%s" % (count, "" if count == 1 else "s")
    durations = []
    for entry in entries:
        try:
            duration = int(float(entry.get("duration") or 0))
        except (TypeError, ValueError):
            duration = 0
        if duration <= 0:
            return count_label
        durations.append(duration)
    minutes = int(round(sum(durations) / 60.0))
    if minutes < 60:
        runtime = "%d min%s" % (minutes, "" if minutes == 1 else "s")
    else:
        hours, remainder = divmod(minutes, 60)
        runtime = "%d hr" % hours
        if remainder:
            runtime += " %d min%s" % (remainder, "" if remainder == 1 else "s")
    return "%s • %s" % (count_label, runtime)


def _remember_playlist_summary_art(kind, arg, cover="", artist=""):
    """Remember the selected tile without changing playlist URLs or tracks."""
    if cover or artist:
        _make_playlists(quiet=True).cache.put(
            "summary-art-v1:%s:%s" % (kind, arg),
            {"cover": cover or "", "artist": artist or ""})


def _playlist_artist_summary_art(artist):
    """Read artist artwork from the library first, then existing caches."""
    local = library_artist_art(artist) or {}
    thumb = local.get("thumb", "")
    provider = _artwork_for_listing()
    if not thumb and provider:
        try:
            thumb = provider.artist_thumb_cached(artist) or ""
        except Exception as exc:
            xbmc.log("[rotation] Summary artist cache unavailable: %s" % exc, xbmc.LOGDEBUG)
    return _art(thumb, _cached_background(artist), FALLBACK_ARTIST,
                clearlogo=_cached_logo(artist))


def _playlist_summary_art(kind, arg, entries):
    """Use playlist-level art, never an arbitrary song from a mixed station."""
    if kind in ("radio", "artist_top"):
        return _playlist_artist_summary_art(str(arg))
    if kind == "user":
        row = _user_playlists().get(str(arg))
        return _art(_user_playlist_cover(row) if row else "", FANART, ICON_PLAYLISTS)
    descriptor = _make_playlists(quiet=True).cache.get_stale(
        "summary-art-v1:%s:%s" % (kind, arg)) or {}
    cover = descriptor.get("cover", "")
    artist = descriptor.get("artist", "")
    if kind == "editorial" and not cover:
        # Older followed playlists already retain their original cover, even
        # if their discovery page has not been opened in this installation.
        target = plugin.url_for(playlists_editorial_tracks, str(arg))
        for favorite in _favorites().get_favorites(kind="playlist"):
            if _same_plugin_directory(favorite.get("url", ""), target):
                cover = favorite.get("thumb", "")
                break
    if kind == "deezer_album":
        entry = entries[0] if entries else {}
        artist = artist or entry.get("album_artist") or entry.get("artist", "")
        cover = cover or _playlist_display_cover(
            entry, _load_availability(), _artwork_for_listing())
        if artist.casefold() in ("various artists", "various"):
            artist = ""
    return _art(cover, _cached_background(artist) if artist else FANART,
                ICON_PLAYLISTS if kind in ("editorial", "user") else ICON_SONGS,
                clearlogo=_cached_logo(artist) if artist else "")


def _add_playlist_summary(entries, tracks=None, availability_complete=False, artwork=None, context_menu=None):
    """Add cross-skin target and playable totals above a track listing."""
    count = len(entries)
    label = "Target: %d song%s" % (count, "" if count == 1 else "s")
    if availability_complete and tracks is not None and _use_kodi_library():
        available_entries = []
        for song, entry in tracks:
            if not song:
                continue
            available = dict(entry)
            # Local duration is the most accurate fallback for provider rows
            # that omit their runtime.
            if not available.get("duration"):
                available["duration"] = song.get("duration", 0)
            available_entries.append(available)
        label += " | Library: %s" % _playlist_summary(available_entries)
        streaming_count = max(0, len(entries) - len(available_entries))
        if _musicmp3_enabled() and streaming_count:
            label += " | Stream: Up to %d more" % streaming_count
    elif not _use_kodi_library() and _musicmp3_enabled():
        label += " | Streaming"
    summary = xbmcgui.ListItem(label)
    summary.setLabel2("Playlist information")
    summary.setArt(artwork or _art("", FANART, ICON_SONGS))
    if context_menu:
        summary.addContextMenuItems(context_menu, replaceItems=False)
    summary.setProperty("IsPlayable", "false")
    xbmcplugin.addDirectoryItem(plugin.handle, "", summary, False)


def _add_library_radio_summary(tracks, artwork=None, context_menu=None):
    """Describe a fully local mix without redundant target/availability text."""
    summary = xbmcgui.ListItem("Library Radio: %s" % _playlist_summary(tracks))
    summary.setLabel2("All tracks are in your Kodi music library")
    summary.setArt(artwork or _art("", FANART, ICON_SONGS))
    if context_menu:
        summary.addContextMenuItems(context_menu, replaceItems=False)
    summary.setProperty("IsPlayable", "false")
    xbmcplugin.addDirectoryItem(plugin.handle, "", summary, False)


def _render_playlist(kind, arg, heading, entries=None, building=False):
    """Browse without starting playback; verify uncached songs separately."""
    if entries is None:
        entries = _playlist_entries(kind, arg)
    else:
        entries = _without_christmas(
            entries, allow=(kind == "user" or
                            (kind == "tag" and _is_christmas_selection(arg))))
    if kind == "user":
        entries = [dict(entry, _user_position=index)
                   for index, entry in enumerate(entries)]
    entries = _playlist_metadata_policy(entries)
    if kind in ("artist_top", "radio"):
        entries = _dedupe_track_entries(
            entries, min(50, _playlist_limit()) if kind == "artist_top" else None)

    if not entries and not building:
        _notify_error("No tracks returned for this playlist.")
        xbmcplugin.endOfDirectory(plugin.handle, succeeded=False)
        return

    index = _library()
    index.build()
    allow_christmas = (kind == "user" or
                       (kind == "tag" and _is_christmas_selection(arg)))
    folder = _playlist_folder_url(kind, arg)
    listing_path = folder or _current_plugin_path()
    xbmcplugin.setProperty(plugin.handle, "Rotation.PlaylistLocation", listing_path)
    cache = _load_availability()
    art_provider = _artwork_for_listing()
    candidates = list(_playlist_tracks(
        entries, index, allow_christmas=allow_christmas))
    if kind in ("artist_top", "radio"):
        candidates = _dedupe_resolved_tracks(candidates)
        # Make the target count describe the actual unique rows after two
        # provider labels resolve to the same Kodi file.
        entries = [entry for _, entry in candidates]
    show_unavailable = kind in ("artist_top", "deezer_album", "user")
    if not folder:
        # Interactive radio searches have no stable URL to refresh later.
        available_tracks = _check_playlist_tracks(
            entries, index, heading, allow_christmas=allow_christmas,
            record_unavailable=not show_unavailable)
        tracks = candidates if show_unavailable else available_tracks
        needs_scan = False
    else:
        tracks = (candidates if show_unavailable or _musicmp3_enabled() else
                  [(song, entry) for song, entry in candidates
                   if song or _cached_match(cache, entry)[0]])
        needs_scan = any(not song and not _cached_match(cache, entry)[1]
                         for song, entry in candidates)

    window = xbmcgui.Window(10000)
    scan_status = window.getProperty(_playlist_scan_key(folder)) if folder else ""

    context = _playlist_play_context(kind, arg, heading)
    favorites_api = _favorites()
    radio_context = []
    if kind == "radio":
        station_url = _playlist_folder_url("radio", arg)
        station_thumb = _artist_thumbs([arg]).get(arg, "")
        radio_context = _favorite_context(
            favorites_api, "radio", station_url, "%s Radio" % arg,
            thumb=station_thumb, artist=arg)

    availability_complete = not building and not needs_scan and not scan_status
    playable_count = sum(1 for song, _ in tracks if song)

    summary_context = context + radio_context
    if kind == "user":
        summary_context += _playlist_management_context(str(arg))

    # Non-discovery playlist pages hide confirmed misses from their song rows,
    # so calculate completion from the full provider list rather than only the
    # visible rows.
    missing_entries = [entry for song, entry in candidates if not song]
    if (show_unavailable and availability_complete and missing_entries
            and _unavailable_report_enabled()):
        report = _load_unavailable_report()
        already_recorded = all(
            heading in _unavailable_sources(
                report.get(_availability_key(entry), {}))
            for entry in missing_entries)
        if not already_recorded:
            count = len(missing_entries)
            summary_context.append((
                "Add Missing Tracks to Library Report (%d)" % count,
                "RunPlugin(%s)" % _playlist_report_missing_url(kind, arg, heading)))

    _add_playlist_summary(entries, tracks, availability_complete,
                          artwork=_playlist_summary_art(kind, arg, entries),
                          context_menu=summary_context)

    if (show_unavailable and availability_complete and tracks and
            not playable_count and not _musicmp3_enabled()):
        notice_key = "Rotation.UnavailableNotice.%s" % hashlib.sha1(
            _current_plugin_path().encode("utf-8")).hexdigest()[:16]
        if not window.getProperty(notice_key):
            xbmcgui.Dialog().notification(
                plugin.name,
                "This album is not in your Kodi library.",
                xbmcgui.NOTIFICATION_WARNING, 4500)
            window.setProperty(notice_key, "1")
        li = xbmcgui.ListItem("No playable tracks found")
        li.setLabel2("The complete provider track list is shown below")
        li.setArt({"icon": ICON_SEARCH, "fanart": FANART})
        xbmcplugin.addDirectoryItem(plugin.handle, "", li, False)
    elif playable_count:
        notice_key = "Rotation.UnavailableNotice.%s" % hashlib.sha1(
            _current_plugin_path().encode("utf-8")).hexdigest()[:16]
        window.clearProperty(notice_key)

    art_entries = ([entry for _, entry in tracks]
                   if _musicmp3_enabled() else
                   [entry for song, entry in tracks if song])
    # A playlist background lookup resolves and caches the artist thumb too.
    # Avoid launching an overlapping portrait worker and a second refresh for
    # the same large list when backgrounds are enabled.
    playlist_portraits = (_artist_thumbs(
        list(dict.fromkeys(entry.get("artist", "") for entry in art_entries
                           if entry.get("artist"))),
        queue_missing=False)
        if availability_complete else {})

    for position, (song, entry) in enumerate(tracks, 1):
        cover = _playlist_display_cover(entry, cache, art_provider)
        url = (song["file"] if song else
               _missing_track_url(entry, cover)
               if show_unavailable else _playlist_remote_url(entry))
        li = (_playlist_listitem(song, position, cover) if song else
              _playlist_unavailable_listitem(entry, position, cover)
              if show_unavailable else
              _playlist_remote_listitem(entry, position, cover))
        artist = entry.get("artist", "")
        match_art = _playlist_match_art(cache, entry)
        local_artist_art = library_artist_art(artist) if artist else {}
        artist_thumb = (local_artist_art.get("thumb") or
                        playlist_portraits.get(artist, "") or
                        entry.get("artist_cover", "") or
                        match_art.get("artist_cover", ""))
        artist_fanart = (_cached_background(artist) if artist else "")
        artist_art = {}
        if artist_thumb:
            artist_art["artist.thumb"] = artist_thumb
        if artist_fanart:
            artist_art["artist.fanart"] = artist_fanart
        if artist_art:
            li.setArt(artist_art)
        artist_context = []
        album_context = []
        if kind == "artist_top":
            album_name = (entry.get("album") or "").strip()
            album_context = [(
                "View Album — %s" % album_name if album_name else
                "Find Album with This Track",
                "RunPlugin(%s)" % _track_album_url(entry))]
        if artist:
            artist_url = _playlist_artist_url(entry)
            artist_thumb = (local_artist_art.get("thumb") or
                            playlist_portraits.get(artist, "") or
                            entry.get("artist_cover", "") or
                            match_art.get("artist_cover", ""))
            artist_context = _favorite_context(
                favorites_api, "artist", artist_url, artist,
                thumb=artist_thumb, artist=artist)
        user_context = _user_playlist_track_context(
            entry, song, cover, str(arg) if kind == "user" else "",
            int(entry.get("_user_position", position - 1))
            if kind == "user" else -1)
        li.addContextMenuItems(context + user_context + album_context
                               + artist_context + radio_context,
                               replaceItems=False)
        xbmcplugin.addDirectoryItem(plugin.handle, url, li, False)

    if building or needs_scan or scan_status:
        if building:
            message = ("Building radio · %d tracks discovered…" % len(entries)
                       if entries else "Discovering Tracks…")
        elif scan_status:
            try:
                checked, found, total = map(int, scan_status.split("/"))
                message = ("%d of %d checked · %d available" %
                           (checked, total, found) if total else
                           "Checking playable tracks…")
            except ValueError:
                message = "Checking playable tracks…"
        else:
            # Do not present an initial zero-count result before the worker has
            # actually checked anything.
            message = "Checking playable tracks…"
        li = xbmcgui.ListItem("[COLOR grey]%s[/COLOR]" % message)
        li.setArt({"icon": ICON_SEARCH, "fanart": FANART})
        if radio_context:
            li.addContextMenuItems(radio_context, replaceItems=False)
        xbmcplugin.addDirectoryItem(plugin.handle, "", li, False)
    elif not tracks:
        li = xbmcgui.ListItem("No playable tracks found")
        li.setArt({"icon": ICON_SEARCH, "fanart": FANART})
        if radio_context:
            li.addContextMenuItems(radio_context, replaceItems=False)
        xbmcplugin.addDirectoryItem(plugin.handle, "", li, False)

    _set_view("songs")
    xbmcplugin.endOfDirectory(plugin.handle, cacheToDisc=False)

    # Artwork is intentionally the second phase.  Do not spend network/cache
    # work on provider entries until their local-library availability is known,
    # and never request artwork for unavailable tracks.
    if availability_complete:
        _start_playlist_artwork(entries, tracks, listing_path, folder,
                                artist=str(arg) if kind in ("radio", "artist_top") else "")

    key = _playlist_refresh_key(folder) if folder else ""
    refreshed = key and window.getProperty(key)
    if refreshed:
        window.clearProperty(key)
    if needs_scan and not scan_status and not building:
        window.clearProperty(_playlist_scan_done_key(folder))
        window.setProperty(_playlist_scan_key(folder), "pending")
        url = (plugin.url_for(playlists_scan)
               + "?kind=" + quote(kind, safe="")
               + "&arg=" + quote(str(arg), safe="")
               + "&label=" + quote(heading, safe=""))
        _schedule_playlist_action(url, "RotationPlaylistScan")
    _show_playlist_progress(listing_path, folder)


@plugin.route("/playlists")
def playlists_root():
    items = [
        ("Albums of the Week",      plugin.url_for(playlists_fresh_albums), ICON_NEW_ALBUMS, True),
        ("Top Tracks",              plugin.url_for(playlists_top_root),  ICON_TOP_SONGS,  True),
        ("Popular Playlists",       plugin.url_for(playlists_editorial), ICON_PLAYLISTS,  True),
        ("Playlists by Genre",      plugin.url_for(playlists_by_genre),  ICON_PLAYLISTS,  True),
        ("Artist Radio",            plugin.url_for(artist_radio_root), ICON_RADIO, True),
        ("Search Albums",           plugin.url_for(albums_search), ICON_ALBUMS, False),
    ]

    for label, url, icon, is_folder in items:
        li = xbmcgui.ListItem(label)
        li.setArt({"fanart": FANART, "icon": icon})
        xbmcplugin.addDirectoryItem(plugin.handle, url, li, is_folder)

    xbmcplugin.endOfDirectory(plugin.handle, cacheToDisc=False)


@plugin.route("/discover/artist_radio")
def artist_radio_root():
    items = [
        ("Search for Artist", plugin.url_for(playlists_radio_search), ICON_SEARCH),
        ("Choose Favorite Artist", plugin.url_for(playlists_radio_favorite), ICON_FAVORITES),
    ]
    if _use_kodi_library():
        items.insert(1, ("Choose Library Artist", plugin.url_for(playlists_radio_library), ICON_ARTISTS))
    for label, url, icon in items:
        li = xbmcgui.ListItem(label)
        li.setLabel2(_make_playlists(quiet=True).radio_backend)
        li.setArt({"thumb": icon, "icon": icon, "fanart": FANART})
        xbmcplugin.addDirectoryItem(plugin.handle, url, li, False)
    xbmcplugin.endOfDirectory(plugin.handle, cacheToDisc=False)


def _playlist_custom_art_path(playlist_id):
    """Versioned managed path for a user-selected cover (rename-safe)."""
    revision = "%d-%06d" % (int(time.time() * 1000), random.randint(0, 999999))
    filename = "%s-custom-%s.jpg" % (playlist_id, revision)
    if addon.getSetting("shared_favorites_sync") == "true":
        favorites_path = shared_favorites_path(USER_DATA_DIR)
        if favorites_path:
            base = favorites_path.replace("\\", "/").rsplit("/", 1)[0]
            return base + "/playlist-art/" + filename
    os.makedirs(USER_PLAYLIST_ART_DIR, exist_ok=True)
    return os.path.join(USER_PLAYLIST_ART_DIR, filename)


def _playlist_art_directories():
    """Every managed user-playlist artwork directory for this installation."""
    directories = [USER_PLAYLIST_ART_DIR]
    favorites_path = (shared_favorites_path(USER_DATA_DIR)
                      if addon.getSetting("shared_favorites_sync") == "true"
                      else "")
    if favorites_path:
        shared = (favorites_path.replace("\\", "/").rsplit("/", 1)[0]
                  + "/playlist-art")
        if shared not in directories:
            directories.append(shared)
    return directories


def _delete_user_playlist_artwork(playlist_id):
    """Delete local and shared managed covers belonging to one playlist."""
    vfs = __import__("xbmcvfs")
    removed = 0
    for directory in _playlist_art_directories():
        try:
            _, files = vfs.listdir(directory)
        except Exception:
            continue
        for filename in files:
            if (filename == playlist_id + ".jpg" or
                    filename == playlist_id + ".jpg.json" or
                    filename.startswith(playlist_id + "-")):
                path = directory.rstrip("/\\") + "/" + filename
                try:
                    if vfs.delete(path):
                        removed += 1
                except Exception as exc:
                    xbmc.log("[rotation] Could not delete playlist cover %s: %s" %
                             (path, exc), xbmc.LOGWARNING)
    return removed


def _cleanup_orphan_playlist_artwork():
    """Remove managed covers whose playlist ID no longer exists."""
    valid_ids = {str(row.get("id") or "") for row in _user_playlists().list()}
    vfs = __import__("xbmcvfs")
    removed = 0
    for directory in _playlist_art_directories():
        try:
            _, files = vfs.listdir(directory)
        except Exception:
            continue
        for filename in files:
            if not (filename.lower().endswith(".jpg") or
                    filename.lower().endswith(".jpg.json")):
                continue
            if any(filename == pid + ".jpg" or
                   filename == pid + ".jpg.json" or
                   filename.startswith(pid + "-") for pid in valid_ids):
                continue
            path = directory.rstrip("/\\") + "/" + filename
            try:
                if vfs.delete(path):
                    removed += 1
            except Exception as exc:
                xbmc.log("[rotation] Could not delete orphan playlist cover %s: %s" %
                         (path, exc), xbmc.LOGWARNING)
    return removed


def _write_playlist_cover(path, data):
    vfs = __import__("xbmcvfs")
    directory = path.replace("\\", "/").rsplit("/", 1)[0]
    vfs.mkdirs(directory)
    handle = vfs.File(path, "w")
    handle.write(data)
    handle.close()


def _apply_custom_cover(store, row, source):
    from PIL import Image, ImageOps
    data = _playlist_cover_source(source)
    if not data:
        raise IOError("Artwork could not be read")
    image = Image.open(io.BytesIO(data)).convert("RGB")
    try:
        method = Image.Resampling.LANCZOS
    except AttributeError:
        method = getattr(Image, "LANCZOS", Image.BICUBIC)
    image = ImageOps.fit(image, (600, 600), method=method)
    output = io.BytesIO()
    image.save(output, "JPEG", quality=94, optimize=True)
    previous = str(row.get("cover_path") or "")
    target = _playlist_custom_art_path(row["id"])
    _write_playlist_cover(target, output.getvalue())
    row["cover_mode"] = "custom"
    row["cover_path"] = target
    store.save(row)
    # Kodi's texture cache is keyed by path. Each edit therefore receives a
    # new filename; the superseded managed original can then be removed.
    if previous and previous != target:
        old_name = previous.replace("\\", "/").rsplit("/", 1)[-1]
        if (old_name == row["id"] + "-custom.jpg" or
                old_name.startswith(row["id"] + "-custom-")):
            try:
                __import__("xbmcvfs").delete(previous)
            except Exception:
                pass


@plugin.route("/playlists/mine/cover/<playlist_id>")
def user_playlist_cover_editor(playlist_id):
    row = _user_playlists().get(playlist_id)
    if not row:
        xbmcplugin.endOfDirectory(plugin.handle, succeeded=False)
        return
    choices = [("Automatic Mosaic", "automatic", "", ICON_PLAYLISTS),
               ("Shuffle Mosaic", "shuffle", "", ICON_SHUFFLE),
               ("Browse for Image", "browse", "", ICON_SEARCH)]
    seen = []
    for track in row.get("tracks", []):
        cover = track.get("cover") or ""
        if cover and cover not in seen:
            seen.append(cover)
            choices.append((track.get("album") or track.get("title") or
                            "Use Album Cover", "album", str(len(seen) - 1), cover))
    for label, action, value, art in choices:
        url = (plugin.url_for(user_playlist_cover_action) + "?playlist_id=" +
               quote(playlist_id, safe="") + "&action=" + action +
               "&value=" + quote(value, safe=""))
        li = xbmcgui.ListItem(label)
        li.setArt(_art(art, FANART, ICON_PLAYLISTS))
        xbmcplugin.addDirectoryItem(plugin.handle, url, li, False)
    xbmcplugin.setPluginCategory(plugin.handle, "Cover for %s" % row.get("name", "Playlist"))
    _set_view("albums")
    xbmcplugin.endOfDirectory(plugin.handle, cacheToDisc=False)


@plugin.route("/playlists/mine/cover-action")
def user_playlist_cover_action():
    playlist_id = unquote(plugin.args.get("playlist_id", [""])[0])
    action = plugin.args.get("action", [""])[0]
    value = unquote(plugin.args.get("value", [""])[0])
    store = _user_playlists()
    row = store.get(playlist_id)
    if not row:
        _end_action(); return
    try:
        if action == "automatic":
            row.pop("cover_path", None)
            row["cover_mode"] = "automatic"
            store.save(row)
            _user_playlist_cover(store.get(playlist_id), regenerate=True)
        elif action == "album":
            covers = []
            for track in row.get("tracks", []):
                cover = track.get("cover") or ""
                if cover and cover not in covers:
                    covers.append(cover)
            _apply_custom_cover(store, row, covers[int(value)])
        elif action == "browse":
            dialog = xbmcgui.Dialog()
            selected = dialog.browse(2, "Choose Playlist Cover", "files",
                                     ".jpg|.jpeg|.png|.webp", False, False, "")
            if not selected:
                _end_action(); return
            _apply_custom_cover(store, row, selected)
        elif action == "shuffle":
            shuffled = list(row.get("tracks", []))
            random.shuffle(shuffled)
            fake = dict(row)
            fake["id"] = playlist_id + "-shuffle"
            fake["tracks"] = shuffled
            fake["modified"] = time.time() + random.random()
            fake["cover_mode"] = "automatic"
            fake.pop("cover_path", None)
            source = _user_playlist_cover(fake, regenerate=True)
            _apply_custom_cover(store, row, source)
        xbmc.executebuiltin("Container.Update(%s)" %
                            plugin.url_for(user_playlists_root))
    except Exception as exc:
        xbmc.log("[rotation] Playlist cover edit failed: %s" % exc, xbmc.LOGWARNING)
        _notify_error("Rotation could not update the playlist cover.")
    _end_action()


@plugin.route("/playlists/mine")
def user_playlists_root():
    store = _user_playlists()
    create = xbmcgui.ListItem("Create New Playlist")
    create.setLabel2("Start an empty playlist")
    create.setArt({"thumb": ICON_PLAYLISTS, "icon": ICON_PLAYLISTS,
                   "fanart": FANART})
    xbmcplugin.addDirectoryItem(
        plugin.handle, plugin.url_for(user_playlist_create), create, False)
    imported = xbmcgui.ListItem("Import Playlist")
    imported.setLabel2("Choose a TXT or CSV file · Format help available")
    imported.setArt({"thumb": ICON_PLAYLISTS, "icon": ICON_PLAYLISTS,
                     "fanart": FANART})
    imported.addContextMenuItems([("Import Format Help", "RunPlugin(%s)" %
                                  plugin.url_for(user_playlist_import_help))])
    xbmcplugin.addDirectoryItem(
        plugin.handle, plugin.url_for(user_playlist_import), imported, False)
    for row in store.list():
        playlist_id = str(row["id"])
        url = plugin.url_for(user_playlist_open, playlist_id)
        li = xbmcgui.ListItem(row.get("name") or "Untitled Playlist")
        li.setLabel2(_playlist_summary(row.get("tracks", [])))
        cover = _user_playlist_cover(row)
        li.setArt(_art(cover, FANART, ICON_PLAYLISTS))
        _set_music_tag(li, title=row.get("name") or "Untitled Playlist")
        li.addContextMenuItems(
            _playlist_play_context("user", playlist_id, row.get("name") or "Playlist") +
            [("Edit Playlist Cover", "Container.Update(%s)" %
              plugin.url_for(user_playlist_cover_editor, playlist_id))],
            replaceItems=False)
        xbmcplugin.addDirectoryItem(plugin.handle, url, li, True)
    xbmcplugin.setPluginCategory(plugin.handle, "My Playlists")
    _set_view("albums")
    xbmcplugin.endOfDirectory(plugin.handle, cacheToDisc=False)


@plugin.route("/playlists/mine/import/help")
def user_playlist_import_help():
    xbmcgui.Dialog().textviewer("Import Playlist — Format Help", PLAYLIST_IMPORT_HELP)
    _end_action()


@plugin.route("/playlists/mine/import")
def user_playlist_import():
    dialog = xbmcgui.Dialog()
    action = dialog.select("Import Playlist", ["Choose File", "Format Help"])
    if action == 1:
        dialog.textviewer("Import Playlist — Format Help", PLAYLIST_IMPORT_HELP)
    if action < 0:
        _end_action(); return
    selected = dialog.browse(1, "Choose Playlist File", "files",
                             ".txt|.csv", False, False, "")
    if not selected:
        _end_action(); return
    try:
        handle = xbmcvfs.File(selected)
        try:
            if handle.size() > 5 * 1024 * 1024:
                raise ValueError("Playlist files must be smaller than 5 MB.")
            raw = handle.readBytes()
        finally:
            handle.close()
        filename = unquote(selected.replace("\\", "/").rsplit("/", 1)[-1])
        suggested, extension = os.path.splitext(filename)
        tracks, problems = parse_playlist(raw, extension)
        if not tracks:
            dialog.textviewer("No Songs to Import", "\n".join(problems) or
                              "This file contains no songs.")
            _end_action(); return
        count = len(tracks)
        if not dialog.yesno("Import Playlist", "%d song%s to import" %
                            (count, "" if count == 1 else "s"),
                            nolabel="Cancel", yeslabel="Import"):
            _end_action(); return
        name = dialog.input("Imported Playlist Name", defaultt=suggested or "Imported Playlist",
                            type=xbmcgui.INPUT_ALPHANUM).strip()
        if not name:
            _end_action(); return
        store = _user_playlists()
        row = store.create(name)
        try:
            row["tracks"] = tracks
            store.save(row)
        except Exception:
            store.delete(row["id"])
            raise
        dialog.notification(plugin.name, "Imported %d songs into %s" % (len(tracks), name),
                            xbmcgui.NOTIFICATION_INFO, 3500)
        _refresh_user_playlists_root()
    except Exception as exc:
        _notify_error("Could not import playlist: %s" % exc)
    _end_action()


@plugin.route("/playlists/mine/create")
def user_playlist_create():
    name = xbmcgui.Dialog().input("New Playlist Name",
                                  type=xbmcgui.INPUT_ALPHANUM).strip()
    if not name:
        _end_action()
        return
    try:
        row = _user_playlists().create(name)
    except (IOError, OSError):
        _notify_error("Rotation could not create the playlist.")
        _end_action()
        return
    xbmcgui.Dialog().notification(
        plugin.name, "Created %s" % row["name"],
        xbmcgui.NOTIFICATION_INFO, 2500)
    _refresh_user_playlists_root()
    _end_action()


@plugin.route("/playlists/mine/view/<playlist_id>")
def user_playlist_open(playlist_id):
    row = _user_playlists().get(playlist_id)
    if not row:
        _notify_error("This playlist could not be found.")
        xbmcplugin.endOfDirectory(plugin.handle, succeeded=False)
        return
    entries = row.get("tracks", [])
    if not entries:
        _add_playlist_summary([], artwork=_playlist_summary_art("user", playlist_id, []),
                              context_menu=_playlist_management_context(playlist_id))
        empty = xbmcgui.ListItem("This playlist is empty")
        empty.setLabel2("Use Add to My Playlist from a song, album or artist")
        empty.setArt({"icon": ICON_SONGS, "fanart": FANART})
        xbmcplugin.addDirectoryItem(plugin.handle, "", empty, False)
        _set_view("songs")
        xbmcplugin.endOfDirectory(plugin.handle, cacheToDisc=False)
        return
    _render_playlist("user", playlist_id,
                     row.get("name") or "Playlist", entries=entries)


@plugin.route("/playlists/mine/add")
def user_playlist_add():
    scope = unquote(plugin.args.get("scope", [""])[0])
    kind = unquote(plugin.args.get("kind", [""])[0])
    arg = unquote(plugin.args.get("arg", [""])[0])
    label = unquote(plugin.args.get("label", [""])[0])
    artist = unquote(plugin.args.get("artist", [""])[0])
    if scope == "track":
        entries = [{key: unquote(plugin.args.get(key, [""])[0])
                    for key in ("artist", "title", "album", "album_artist",
                                "duration", "cover", "file", "album_id", "id")}]
    elif scope == "artist":
        entries = [_user_playlist_track({}, song) for song in _artist_local_songs(artist)]
    elif scope in ("album", "snapshot"):
        entries = _playlist_entries(kind, arg)
        index = _library()
        index.build()
        entries = [_user_playlist_track(entry, song)
                   for song, entry in _playlist_tracks(
                       entries, index, allow_christmas=True)]
    else:
        entries = []
    entries = [entry for entry in entries
               if entry.get("artist") and entry.get("title")]
    if not entries:
        _notify_error("No tracks were available to add.")
        _end_action()
        return
    store = _user_playlists()
    default_name = ""
    if scope == "snapshot":
        default_name = _snapshot_default_name(store, kind, arg, label)
    elif scope == "album":
        default_name = _album_playlist_default_name(store, entries, label, artist)
    try:
        target = _choose_user_playlist(store, default_name)
    except (IOError, OSError):
        _notify_error("Rotation could not create the playlist.")
        _end_action()
        return
    if not target:
        _end_action()
        return
    try:
        added, skipped = store.add_tracks(target["id"], entries)
        if skipped and xbmcgui.Dialog().yesno(
                "Duplicate Tracks",
                "%d track%s already exist%s in %s. Add %s again?" % (
                    len(skipped), "" if len(skipped) == 1 else "s",
                    "s" if len(skipped) == 1 else "",
                    target["name"], "it" if len(skipped) == 1 else "them")):
            duplicate_added, _ = store.add_tracks(
                target["id"], skipped, allow_duplicates=True)
            added.extend(duplicate_added)
        updated = store.get(target["id"])
        if added:
            _user_playlist_cover(updated, regenerate=True)
    except (IOError, OSError, KeyError):
        _notify_error("Rotation could not update the playlist.")
        _end_action()
        return
    if added:
        xbmcgui.Dialog().notification(
            plugin.name, "Added %d track%s to %s" % (
                len(added), "" if len(added) == 1 else "s", target["name"]),
            xbmcgui.NOTIFICATION_INFO, 3000)
        _refresh_user_playlist(target["id"])
    elif skipped:
        xbmcgui.Dialog().notification(
            plugin.name, "Already in %s" % target["name"],
            xbmcgui.NOTIFICATION_INFO, 2500)
    _end_action()


def _save_user_playlist_edit(store, row, reason="user playlist edited"):
    store.save(row)
    updated = store.get(row["id"])
    _user_playlist_cover(updated, regenerate=True)
    _refresh_user_playlist(row["id"], reason)


@plugin.route("/playlists/mine/manage")
def user_playlist_manage():
    playlist_id = unquote(plugin.args.get("playlist_id", [""])[0])
    store = _user_playlists()
    row = store.get(playlist_id)
    if not row:
        _notify_error("This playlist could not be found.")
        _end_action()
        return
    actions = ["Rename", "Reorder Songs", "Remove Songs", "Remove Duplicates",
               "Sort Alphabetically", "Sort by Artist", "Delete Playlist"]
    choice = xbmcgui.Dialog().select("Edit %s" % row["name"], actions)
    if choice < 0:
        _end_action()
        return
    try:
        if choice == 0:
            name = xbmcgui.Dialog().input(
                "Rename Playlist", defaultt=row["name"],
                type=xbmcgui.INPUT_ALPHANUM).strip()
            if name:
                row["name"] = name
                _save_user_playlist_edit(store, row, "user playlist renamed")
        elif choice == 1:
            _user_playlist_reorder(store, row)
        elif choice == 2:
            _user_playlist_remove_dialog(store, row)
        elif choice == 3:
            removed = store.remove_duplicates(playlist_id)
            if removed:
                _user_playlist_cover(store.get(playlist_id), regenerate=True)
                _refresh_user_playlist(playlist_id)
            xbmcgui.Dialog().notification(
                plugin.name, "%d duplicate%s removed" %
                (removed, "" if removed == 1 else "s"),
                xbmcgui.NOTIFICATION_INFO, 2500)
        elif choice in (4, 5):
            if choice == 4:
                row["tracks"].sort(key=lambda track: (
                    str(track.get("title") or "").casefold(),
                    str(track.get("artist") or "").casefold()))
            else:
                row["tracks"].sort(key=lambda track: (
                    str(track.get("artist") or "").casefold(),
                    str(track.get("title") or "").casefold()))
            _save_user_playlist_edit(store, row)
        elif choice == 6 and xbmcgui.Dialog().yesno(
                "Delete Playlist?", "Delete %s? This cannot be undone." % row["name"]):
            store.delete(playlist_id)
            _delete_user_playlist_artwork(playlist_id)
            _refresh_user_playlists_root()
            xbmcgui.Dialog().notification(
                plugin.name, "Playlist deleted", xbmcgui.NOTIFICATION_INFO, 2500)
    except (IOError, OSError, KeyError) as exc:
        xbmc.log("[rotation] User playlist edit failed: %s" % exc, xbmc.LOGWARNING)
        _notify_error("Rotation could not update the playlist.")
    _end_action()


def _track_choice_label(track):
    return "%s — %s" % (track.get("artist", ""), track.get("title", ""))


def _user_playlist_reorder(store, row):
    changed = False
    while len(row.get("tracks", [])) > 1:
        labels = [_track_choice_label(track) for track in row["tracks"]]
        choice = xbmcgui.Dialog().select(
            "Reorder %s" % row["name"], ["Done Reordering"] + labels)
        if choice <= 0:
            break
        source = choice - 1
        destinations = ["Position %d — %s" % (index + 1, label)
                        for index, label in enumerate(labels)]
        destination = xbmcgui.Dialog().select(
            "Move %s" % labels[source], destinations +
            ["Move to Top", "Move to Bottom"])
        if destination < 0:
            continue
        track = row["tracks"].pop(source)
        if destination == len(labels):
            destination = 0
        elif destination == len(labels) + 1:
            destination = len(row["tracks"])
        row["tracks"].insert(min(destination, len(row["tracks"])), track)
        changed = True
    if changed:
        _save_user_playlist_edit(store, row)


def _user_playlist_remove_dialog(store, row):
    changed = False
    while row.get("tracks"):
        labels = [_track_choice_label(track) for track in row["tracks"]]
        choice = xbmcgui.Dialog().select(
            "Remove from %s" % row["name"], ["Done Removing"] + labels)
        if choice <= 0:
            break
        row["tracks"].pop(choice - 1)
        changed = True
    if changed:
        _save_user_playlist_edit(store, row)


@plugin.route("/playlists/mine/track_action")
def user_playlist_track_action():
    playlist_id = unquote(plugin.args.get("playlist_id", [""])[0])
    action = unquote(plugin.args.get("action", [""])[0])
    try:
        position = int(plugin.args.get("position", ["-1"])[0])
    except (TypeError, ValueError):
        position = -1
    store = _user_playlists()
    row = store.get(playlist_id)
    tracks = row.get("tracks", []) if row else []
    if not (0 <= position < len(tracks)):
        _notify_error("That playlist item is no longer available.")
        _end_action()
        return
    track = tracks.pop(position)
    if action == "up":
        tracks.insert(max(0, position - 1), track)
    elif action == "down":
        tracks.insert(min(len(tracks), position + 1), track)
    elif action == "top":
        tracks.insert(0, track)
    elif action == "bottom":
        tracks.append(track)
    elif action != "remove":
        tracks.insert(position, track)
        _end_action()
        return
    try:
        _save_user_playlist_edit(store, row)
    except (IOError, OSError):
        _notify_error("Rotation could not update the playlist.")
    _end_action()


@plugin.route("/playlists/library_radio")
def library_radio_root():
    """Browse local-only radio modes."""
    index = _library()
    index.build()
    if not index.size:
        xbmcgui.Dialog().ok(
            plugin.name,
            "Library Radio plays only music scanned into Kodi. Scan a music "
            "source into Kodi, then return here.")
        xbmcplugin.endOfDirectory(plugin.handle, succeeded=False)
        return
    backend = _make_playlists().radio_backend
    items = [
        ("By Artist", plugin.url_for(library_radio_artist), ICON_ARTISTS,
         "Local only · relationships by %s" % backend, False),
        ("By Genre", plugin.url_for(library_radio_genre), ICON_GENRES,
         "Local only", False),
        ("By Decade", plugin.url_for(library_radio_decade), ICON_TOP_SONGS,
         "Local only", False),
        ("Rediscover Your Library", _library_radio_folder_url("rediscover"),
         ICON_FAVORITES, "Least played and least recently heard", True),
        ("Shuffle All Music", _library_radio_folder_url("shuffle"),
         ICON_SHUFFLE, "Balanced across artists", True),
    ]
    for label, url, icon, detail, is_folder in items:
        li = xbmcgui.ListItem(label)
        li.setLabel2(detail)
        li.setArt({"fanart": FANART, "icon": icon})
        xbmcplugin.addDirectoryItem(plugin.handle, url, li, is_folder)
    xbmcplugin.endOfDirectory(plugin.handle)


@plugin.route("/playlists/library_radio/artist")
def library_radio_artist():
    names = [artist.get("artist", "") for artist in library_artists()
             if artist.get("artist")]
    choice = xbmcgui.Dialog().select("Choose library artist", names)
    if choice < 0:
        xbmcplugin.endOfDirectory(plugin.handle, succeeded=False)
        return
    _open_library_radio("artist", names[choice])


@plugin.route("/playlists/library_radio/genre")
def library_radio_genre():
    index = _library()
    index.build()
    counts = {}
    for song in index.songs:
        for genre in _song_genres(song):
            counts[genre] = counts.get(genre, 0) + 1
    genres = sorted(counts, key=str.casefold)
    labels = ["%s (%d tracks)" % (genre, counts[genre]) for genre in genres]
    choice = xbmcgui.Dialog().select("Choose library genre", labels)
    if choice < 0:
        xbmcplugin.endOfDirectory(plugin.handle, succeeded=False)
        return
    _open_library_radio("genre", genres[choice])


@plugin.route("/playlists/library_radio/decade")
def library_radio_decade():
    index = _library()
    index.build()
    counts = {}
    for song in index.songs:
        year = int(song.get("year") or 0)
        if year >= 1900:
            decade = (year // 10) * 10
            counts[decade] = counts.get(decade, 0) + 1
    decades = sorted(counts, reverse=True)
    labels = ["%ds (%d tracks)" % (decade, counts[decade]) for decade in decades]
    choice = xbmcgui.Dialog().select("Choose decade", labels)
    if choice < 0:
        xbmcplugin.endOfDirectory(plugin.handle, succeeded=False)
        return
    _open_library_radio("decade", str(decades[choice]))


@plugin.route("/playlists/library_radio/open")
def library_radio_open():
    if not _navigation_request_is_current():
        return
    target = unquote(plugin.args.get("target", [""])[0])
    if target:
        xbmc.executebuiltin("Container.Update(%s)" % target)


@plugin.route("/playlists/library_radio/mix")
def library_radio_mix():
    mode = unquote(plugin.args.get("mode", [""])[0])
    value = unquote(plugin.args.get("value", [""])[0])
    heading = _library_radio_label(mode, value)
    tracks = _library_radio_tracks(mode, value)
    if not tracks:
        _notify_error("No local tracks found for this Library Radio station.")
        xbmcplugin.endOfDirectory(plugin.handle, succeeded=False)
        return
    context = _library_radio_context(mode, value, heading)
    summary_art = (_playlist_artist_summary_art(value) if mode == "artist"
                   else _art("", FANART, ICON_RADIO))
    _add_library_radio_summary(tracks, artwork=summary_art, context_menu=context)
    for position, song in enumerate(tracks, 1):
        li = _playlist_listitem(song, position)
        li.addContextMenuItems(context, replaceItems=False)
        xbmcplugin.addDirectoryItem(plugin.handle, song["file"], li, False)
    _set_view("songs")
    xbmcplugin.endOfDirectory(plugin.handle, cacheToDisc=False)


@plugin.route("/playlists/library_radio/play")
def library_radio_play():
    mode = unquote(plugin.args.get("mode", [""])[0])
    value = unquote(plugin.args.get("value", [""])[0])
    shuffle = plugin.args.get("shuffle", ["0"])[0] == "1"
    tracks = _library_radio_tracks(mode, value)
    if shuffle:
        random.shuffle(tracks)
    playlist = xbmc.PlayList(xbmc.PLAYLIST_MUSIC)
    playlist.clear()
    for position, song in enumerate(tracks, 1):
        playlist.add(song["file"], _playlist_listitem(song, position))
    if playlist.size():
        xbmc.Player().play(playlist)
    else:
        _notify_error("No local tracks found for this Library Radio station.")


@plugin.route("/playlists/top_tracks")
def playlists_top_root():
    items = [
        ("Worldwide", plugin.url_for(playlists_top), ICON_TOP_SONGS),
        (_country_name(), plugin.url_for(playlists_country), ICON_TOP_SONGS),
        ("By Genre", plugin.url_for(playlists_genres), ICON_GENRES),
    ]
    for label, url, icon in items:
        li = xbmcgui.ListItem(label)
        li.setArt({"fanart": FANART, "icon": icon})
        xbmcplugin.addDirectoryItem(plugin.handle, url, li, True)
    xbmcplugin.setPluginCategory(plugin.handle, "Top Tracks")
    _set_view("files")
    xbmcplugin.endOfDirectory(plugin.handle)


@plugin.route("/playlists/top")
def playlists_top():
    _render_playlist("top", "", "Top Tracks — Worldwide")


@plugin.route("/playlists/country")
def playlists_country():
    _render_playlist("country", "", "Top Tracks — %s" % _country_name())


@plugin.route("/playlists/genres")
def playlists_genres():
    genres = _make_playlists().genres()
    if not genres:
        _notify_error("Could not load genre list.")
        xbmcplugin.endOfDirectory(plugin.handle, succeeded=False)
        return

    for genre in genres:
        _remember_playlist_summary_art("genre", genre["id"], genre.get("picture", ""))
        li = xbmcgui.ListItem(genre["name"])
        li.setArt(_art(genre.get("picture", ""), FANART, ICON_GENRES))
        li.addContextMenuItems(
            _playlist_play_context("genre", genre["id"], genre["name"]),
            replaceItems=False)
        xbmcplugin.addDirectoryItem(
            plugin.handle,
            plugin.url_for(playlists_genre, genre["id"]),
            li, True)

    _set_view("files")
    xbmcplugin.endOfDirectory(plugin.handle)


@plugin.route("/playlists/genre/<gid>")
def playlists_genre(gid):
    _render_playlist("genre", gid, "Top Tracks — Genre")


@plugin.route("/playlists/editorial")
def playlists_editorial():
    playlists = _make_playlists().editorial_playlists(limit=100)
    if not playlists:
        _notify_error("Could not load popular playlists.")
        xbmcplugin.endOfDirectory(plugin.handle, succeeded=False)
        return

    _show_deezer_playlist_choices(playlists)


def _show_deezer_playlist_choices(playlists):
    """Keep popular and genre playlists on the same playback and scan path."""
    api = _favorites()
    for entry in playlists:
        _remember_playlist_summary_art("editorial", entry["id"], entry.get("cover", ""))
        url = (plugin.url_for(playlists_editorial_tracks, entry["id"])
               + "?title=" + quote(entry["title"], safe=""))
        li = xbmcgui.ListItem(entry["title"])
        li.setLabel2("%d tracks" % entry.get("count", 0))
        li.setArt(_art(entry.get("cover", ""), FANART, ICON_PLAYLISTS))
        li.addContextMenuItems(
            _playlist_play_context("editorial", entry["id"], entry["title"])
            + _favorite_context(api, "playlist", url, entry["title"],
                           thumb=entry.get("cover", "")),
            replaceItems=False)
        xbmcplugin.addDirectoryItem(
            plugin.handle,
            url,
            li, True)

    _set_view("albums")
    xbmcplugin.endOfDirectory(plugin.handle)


@plugin.route("/playlists/by_genre")
def playlists_by_genre():
    genres = _make_playlists().genres()
    if not genres:
        _notify_error("Could not load Deezer genres.")
        xbmcplugin.endOfDirectory(plugin.handle, succeeded=False)
        return

    for genre in genres:
        li = xbmcgui.ListItem(genre["name"])
        li.setArt(_art(genre.get("picture", ""), FANART, ICON_GENRES))
        xbmcplugin.addDirectoryItem(
            plugin.handle, plugin.url_for(playlists_by_genre_items, genre["id"]),
            li, True)
    _set_view("files")
    xbmcplugin.endOfDirectory(plugin.handle)


@plugin.route("/playlists/by_genre/<gid>")
def playlists_by_genre_items(gid):
    try:
        playlists = _make_playlists().genre_playlists(gid)
    except (TypeError, ValueError) as exc:
        _notify_error("Invalid Deezer genre: %s" % exc)
        xbmcplugin.endOfDirectory(plugin.handle, succeeded=False)
        return
    if not playlists:
        _notify_error("No Deezer playlists found for this genre.")
        xbmcplugin.endOfDirectory(plugin.handle, succeeded=False)
        return
    _show_deezer_playlist_choices(playlists)


@plugin.route("/playlists/editorial/<pid>")
def playlists_editorial_tracks(pid):
    title = unquote(plugin.args.get("title", ["Popular Playlist"])[0])
    _render_playlist("editorial", pid, title or "Popular Playlist")


@plugin.route("/playlists/fresh_albums")
def playlists_fresh_albums():
    albums = _make_playlists().fresh_albums(limit=100)
    if not albums:
        _notify_error("Albums of the Week is temporarily unavailable.")
        xbmcplugin.endOfDirectory(plugin.handle, succeeded=False)
        return
    _render_deezer_album_choices(albums, "Albums of the Week")


@plugin.route("/playlists/deezer_album/<aid>")
def playlists_deezer_album(aid):
    """Play a Deezer album through the normal local-first availability scan."""
    entries = _playlist_entries("deezer_album", aid)
    if entries:
        album = entries[0].get("album", "Album")
        artist = entries[0].get("album_artist") or entries[0].get("artist", "")
        heading = "%s — %s" % (artist, album) if artist else album
    else:
        heading = "Album"
    _render_playlist("deezer_album", aid, heading, entries=entries)


def _album_search_library_map():
    """Local album counts/artwork keyed by normalized title and album artist."""
    index = _library()
    index.build()
    exact = {}
    by_title = {}
    for song in index.songs:
        title = song.get("album", "")
        if not title:
            continue
        title_keys = {norm_title(title, False), norm_title(title, True)}
        artists = song.get("albumartists") or []
        if not artists and song.get("albumartist"):
            artists = [song["albumartist"]]
        if not artists:
            artists = song.get("artists") or [song.get("artist", "")]
        artist_keys = {norm_artist(name) for name in artists if name} or {""}
        for title_key in filter(None, title_keys):
            title_row = by_title.setdefault(
                title_key, {"count": 0, "thumb": "", "artists": set()})
            title_row["count"] += 1
            title_row["artists"].update(artist_keys)
            if not title_row["thumb"] and song.get("thumb"):
                title_row["thumb"] = song["thumb"]
            for artist_key in artist_keys:
                row = exact.setdefault((title_key, artist_key),
                                       {"count": 0, "thumb": ""})
                row["count"] += 1
                if not row["thumb"] and song.get("thumb"):
                    row["thumb"] = song["thumb"]
    return exact, by_title


def _album_search_library_match(album, exact, by_title):
    artist_key = norm_artist(album.get("artist", ""))
    for title_key in (norm_title(album.get("title", ""), False),
                      norm_title(album.get("title", ""), True)):
        if not title_key:
            continue
        if (title_key, artist_key) in exact:
            return exact[(title_key, artist_key)]
        candidate = by_title.get(title_key)
        # A title-only fallback is safe when Kodi associates the title with
        # one album artist. This covers soundtrack metadata variations without
        # claiming every self-titled album is locally owned.
        if candidate and len(candidate.get("artists") or []) == 1:
            return candidate
    return {}


def _render_deezer_album_choices(albums, category):
    """Render Deezer album rows with Rotation's normal local-first actions."""
    exact, by_title = _album_search_library_map()
    api = _favorites()
    show_marker = _show_not_in_library_labels()
    seen = set()
    for album in albums:
        key = (norm_title(album.get("title", ""), False),
               norm_artist(album.get("artist", "")))
        if key in seen:
            continue
        seen.add(key)
        local = _album_search_library_match(album, exact, by_title)
        count = int(local.get("count") or 0)
        total = int(album.get("track_count") or 0)
        if count and total and count >= total:
            status, availability = "In Library", "available"
        elif count:
            status = "Partially in Library — %d track%s" % (
                count, "" if count == 1 else "s")
            availability = "partial"
        else:
            status, availability = "Not in Library", "missing"
        artist = album.get("artist", "") or "Various Artists"
        aid = str(album["id"])
        _remember_playlist_summary_art(
            "deezer_album", aid, local.get("thumb") or album.get("cover", ""), artist)
        url = plugin.url_for(playlists_deezer_album, aid)
        display_title = ("[x] %s" % album["title"]
                         if availability == "missing" and show_marker
                         else album["title"])
        li = xbmcgui.ListItem(display_title)
        details = [artist]
        if album.get("release_date"):
            details.append(str(album["release_date"])[:4])
        if availability != "missing" or not show_marker:
            details.append(status)
        li.setLabel2(" · ".join(details))
        li.setArt(_art(local.get("thumb") or album.get("cover", ""),
                       _cached_background(artist), FALLBACK_ALBUM))
        li.setProperty("Rotation.Availability", availability)
        _set_music_tag(li, title=display_title, artist=artist,
                       album=album["title"])
        actions = _playlist_play_context("deezer_album", aid, album["title"])
        actions += _favorite_context(
            api, "album", url, album["title"], thumb=album.get("cover", ""),
            artist=artist)
        li.addContextMenuItems(actions, replaceItems=False)
        xbmcplugin.addDirectoryItem(plugin.handle, url, li, True)
    xbmcplugin.setPluginCategory(plugin.handle, category)
    _set_view("albums")
    xbmcplugin.endOfDirectory(plugin.handle, cacheToDisc=False)


@plugin.route("/albums/search")
def albums_search():
    query = xbmcgui.Dialog().input("Search Albums", type=xbmcgui.INPUT_ALPHANUM)
    if not query:
        if plugin.handle >= 0:
            xbmcplugin.endOfDirectory(plugin.handle, succeeded=False)
        return
    if plugin.handle >= 0:
        xbmcplugin.endOfDirectory(plugin.handle, succeeded=False, cacheToDisc=False)
    opener = (plugin.url_for(albums_search_open)
              + "?query=" + quote(query, safe=""))
    opener += _navigation_origin_query()
    _schedule_playlist_action(opener, "RotationAlbumSearchOpen")


@plugin.route("/albums/search/open")
def albums_search_open():
    """Open results after Kodi has dismissed the non-folder search action."""
    if not _navigation_request_is_current():
        return
    query = unquote(plugin.args.get("query", [""])[0]).strip()
    if query:
        target = (plugin.url_for(albums_search_results)
                  + "?query=" + quote(query, safe=""))
        xbmc.executebuiltin("Container.Update(%s)" % target)


@plugin.route("/albums/search/results")
def albums_search_results():
    query = unquote(plugin.args.get("query", [""])[0]).strip()
    if not query:
        _notify_error("No album search was supplied.")
        xbmcplugin.endOfDirectory(plugin.handle, succeeded=False)
        return
    albums = _make_playlists().deezer.search_albums(query, limit=50)
    if not albums:
        _notify_error("No albums were found for %s." % query)
        xbmcplugin.endOfDirectory(plugin.handle, succeeded=False)
        return
    _render_deezer_album_choices(albums, "Album Search")


def _artist_album_library_map(artist):
    """Counts and representative Kodi artwork keyed by normalized album title."""
    albums = {}
    for song in _artist_local_songs(artist):
        title = song.get("album", "")
        if not title:
            continue
        for key in {norm_title(title, False), norm_title(title, True)}:
            if not key:
                continue
            item = albums.setdefault(key, {"count": 0, "thumb": ""})
            item["count"] += 1
            if not item["thumb"] and song.get("thumb"):
                item["thumb"] = song["thumb"]
    return albums


def _dedupe_artist_releases(albums):
    """Collapse exact regional/reissue duplicates, keeping the fullest row."""
    unique = {}
    for album in albums:
        key = (norm_title(album.get("title", ""), False),
               album.get("record_type") or "unknown")
        current = unique.get(key)
        if (not current or
                int(album.get("track_count") or 0) >
                int(current.get("track_count") or 0)):
            unique[key] = album
    return sorted(unique.values(), key=lambda row: (
        row.get("release_date", "") or "", row.get("title", "").casefold()),
        reverse=True)


def _artist_release_sections(albums):
    """Separate useful album browsing from Deezer's singles-heavy feed."""
    main, extras = [], []
    for album in albums:
        release_type = album.get("record_type") or ""
        tracks = int(album.get("track_count") or 0)
        if release_type in ("single", "ep") or (not release_type and tracks < 7):
            extras.append(album)
        else:
            main.append(album)
    return _dedupe_artist_releases(main), _dedupe_artist_releases(extras)


def _render_artist_releases(artist_id, albums, artist_name, include_extras=False):
    """Render one curated release section with normal library-aware actions."""
    artist_fanart = _cached_background(artist_name)
    library_albums = _artist_album_library_map(artist_name)
    api = _favorites()
    for album in albums:
        aid = str(album["id"])
        url = plugin.url_for(playlists_deezer_album, aid)
        album_artist = album.get("artist", "") or artist_name
        library_album = (library_albums.get(norm_title(album["title"], False)) or
                         library_albums.get(norm_title(album["title"], True)) or {})
        count = int(library_album.get("count") or 0)
        show_marker = _show_not_in_library_labels()
        release_year = ""
        if _show_artist_album_years() and album.get("release_date"):
            candidate = str(album["release_date"])[:4]
            if re.fullmatch(r"(?:19|20)\d{2}", candidate):
                release_year = candidate
        titled_year = ("%s (%s)" % (album["title"], release_year)
                       if release_year else album["title"])
        display_title = (titled_year if count or not show_marker else
                         "[x] %s" % titled_year)
        li = xbmcgui.ListItem(display_title)
        detail = []
        if album.get("release_date"):
            detail.append(str(album["release_date"])[:4])
        if album.get("track_count"):
            detail.append("%d tracks" % int(album["track_count"]))
        if count:
            detail.append("%d in library" % count)
        li.setLabel2(" · ".join(detail) or album_artist)
        li.setArt(_art(library_album.get("thumb") or album.get("cover", ""),
                       artist_fanart, FALLBACK_ALBUM))
        li.setProperty("Rotation.Availability", "available" if count else "missing")
        _set_music_tag(li, title=display_title, artist=album_artist,
                       album=album["title"])
        li.addContextMenuItems(
            _playlist_play_context("deezer_album", aid, album["title"])
            + _favorite_context(api, "album", url, album["title"],
                                thumb=album.get("cover", ""),
                                artist=album_artist), replaceItems=False)
        xbmcplugin.addDirectoryItem(plugin.handle, url, li, True)
    _set_view("albums")
    xbmcplugin.endOfDirectory(plugin.handle, cacheToDisc=False)


@plugin.route("/playlists/deezer_artist/<artist_id>")
def playlists_deezer_artist(artist_id):
    """Open a curated album discography, with singles kept one level deeper."""
    albums = _make_playlists().deezer.artist_albums(artist_id)
    if not albums:
        _notify_error("No Deezer albums found for this artist.")
        xbmcplugin.endOfDirectory(plugin.handle, succeeded=False)
        return
    artist_name = unquote(plugin.args.get("artist", [""])[0])
    if not artist_name:
        artist_name = next((album.get("artist", "") for album in albums
                            if album.get("artist")), "")
    main, extras = _artist_release_sections(albums)
    if extras:
        extras_url = (plugin.url_for(playlists_deezer_artist_extras, artist_id)
                      + "?artist=" + quote(artist_name, safe=""))
        li = xbmcgui.ListItem("Singles & EPs")
        li.setLabel2("%d release%s" %
                     (len(extras), "" if len(extras) == 1 else "s"))
        li.setArt(_art("", _cached_background(artist_name), ICON_ALBUMS))
        xbmcplugin.addDirectoryItem(plugin.handle, extras_url, li, True)
    _render_artist_releases(artist_id, main, artist_name)


@plugin.route("/playlists/deezer_artist/<artist_id>/extras")
def playlists_deezer_artist_extras(artist_id):
    albums = _make_playlists().deezer.artist_albums(artist_id)
    artist_name = unquote(plugin.args.get("artist", [""])[0])
    if not artist_name:
        artist_name = next((album.get("artist", "") for album in albums
                            if album.get("artist")), "")
    _, extras = _artist_release_sections(albums)
    if not extras:
        _notify_error("No singles or EPs were found for this artist.")
        xbmcplugin.endOfDirectory(plugin.handle, succeeded=False)
        return
    _render_artist_releases(artist_id, extras, artist_name, include_extras=True)


@plugin.route("/playlists/artist")
def playlists_artist():
    """Resolve an artist saved from a provider row, then show their albums."""
    artist = unquote(plugin.args.get("artist", [""])[0])
    if not artist:
        _notify_error("No artist was supplied.")
        xbmcplugin.endOfDirectory(plugin.handle, succeeded=False)
        return
    found = _make_playlists().deezer.find_artist(artist)
    if not found or not found.get("id"):
        _notify_error("Could not find albums for %s." % artist)
        xbmcplugin.endOfDirectory(plugin.handle, succeeded=False)
        return
    playlists_deezer_artist(str(found["id"]))


@plugin.route("/playlists/radio/search")
def playlists_radio_search():
    """Artist radio from a typed name — the artist need not be in the library."""
    query = xbmcgui.Dialog().input("Artist Radio - Search for Artist",
                                   type=xbmcgui.INPUT_ALPHANUM)
    if not query:
        if plugin.handle >= 0:
            xbmcplugin.endOfDirectory(plugin.handle, succeeded=False)
        return
    _open_radio_after_selection(query)


@plugin.route("/playlists/radio/library")
def playlists_radio_library():
    """
    Artist radio seeded from an artist already in the library.

    Worth having separately: seeding from a name you own guarantees at least
    the seed artist's own tracks resolve, so the station is never empty.
    """
    if not _use_kodi_library():
        xbmcplugin.endOfDirectory(plugin.handle, succeeded=False)
        return
    artists = library_artists()
    if not artists:
        xbmcgui.Dialog().ok(
            plugin.name,
            "Artist Radio - Choose Library Artist uses artists scanned into "
            "Kodi's music library. Scan a music source into Kodi, or use "
            "Artist Radio - Search for Artist without a library.")
        if plugin.handle >= 0:
            xbmcplugin.endOfDirectory(plugin.handle, succeeded=False)
        return

    names = [a.get("artist", "") for a in artists if a.get("artist")]
    choice = xbmcgui.Dialog().select("Choose Library Artist", names)
    if choice < 0:
        if plugin.handle >= 0:
            xbmcplugin.endOfDirectory(plugin.handle, succeeded=False)
        return

    name = names[choice]
    _open_radio_after_selection(name)


@plugin.route("/playlists/radio/favorite")
def playlists_radio_favorite():
    rows = _favorites().get_favorites(kind="artist")
    names = [_favorite_artist_name(row, "artist") for row in rows]
    names = [name for name in names if name]
    if not names:
        _notify_error("No Favorite Artists have been saved yet.")
        if plugin.handle >= 0:
            xbmcplugin.endOfDirectory(plugin.handle, succeeded=False)
        return
    choice = xbmcgui.Dialog().select("Choose Favorite Artist", names)
    if choice < 0:
        if plugin.handle >= 0:
            xbmcplugin.endOfDirectory(plugin.handle, succeeded=False)
        return
    _open_radio_after_selection(names[choice])


def _open_radio_after_selection(artist):
    """Open a station from an action without adding the picker to Back history."""
    if plugin.handle >= 0:
        xbmcplugin.endOfDirectory(plugin.handle, succeeded=False, cacheToDisc=False)
    url = plugin.url_for(playlists_radio_open) + "?artist=" + quote(artist, safe="")
    url += _navigation_origin_query()
    _schedule_playlist_action(url, "RotationRadioOpen")


@plugin.route("/playlists/radio/open")
def playlists_radio_open():
    if not _navigation_request_is_current():
        return
    artist = unquote(plugin.args.get("artist", [""])[0])
    if artist:
        xbmc.executebuiltin("Container.Update(%s)" % _playlist_folder_url("radio", artist))


def _radio_cache_key(artist):
    return "radio-mix-v2:%s:%s" % (artist.casefold(), _playlist_limit())


def _radio_error_key(artist):
    return "Rotation.RadioProviderError.%s" % hashlib.sha1(
        artist.casefold().encode("utf-8")).hexdigest()[:16]


def _visible_radio_artist():
    """Return the artist for the radio directory currently on screen."""
    current = xbmc.getInfoLabel("Container.FolderPath") or ""
    parsed = urlparse(current)
    expected_route = urlparse(_playlist_folder_url("radio", "artist")).path
    if parsed.path.rstrip("/").casefold() != expected_route.rstrip("/").casefold():
        return ""
    return (parse_qs(parsed.query).get("artist", [""])[0] or "").strip()


@plugin.route("/playlists/radio/refresh_complete")
def playlists_radio_refresh_complete():
    """Commit a finished radio result without refreshing another directory."""
    artist = unquote(plugin.args.get("artist", [""])[0]).strip()
    if artist and _visible_radio_artist().casefold() == artist.casefold():
        xbmc.log("[rotation][radio] displaying completed station for %r" % artist,
                 xbmc.LOGINFO)
        xbmc.executebuiltin("Container.Refresh")


@plugin.route("/playlists/radio/artist")
def playlists_radio_artist():
    """Show the station immediately and refresh as its provider discovers tracks."""
    artist = unquote(plugin.args.get("artist", [""])[0])
    if not artist:
        xbmcplugin.endOfDirectory(plugin.handle, succeeded=False)
        return
    source = _make_playlists(quiet=True)
    snapshot = source.cache.get_stale(_radio_cache_key(artist)) or {}
    folder = _playlist_folder_url("radio", artist)
    window = xbmcgui.Window(10000)
    provider_error = window.getProperty(_radio_error_key(artist))
    if provider_error:
        window.clearProperty(_radio_error_key(artist))
        xbmcgui.Dialog().notification(
            "Artist Radio", provider_error,
            xbmcgui.NOTIFICATION_WARNING, 5000)
    building = not snapshot.get("complete") and not provider_error
    _render_playlist("radio", artist, "%s Radio" % artist,
                     entries=snapshot.get("entries", []), building=building)
    if building and not window.getProperty(_playlist_scan_key(folder)):
        window.setProperty(_playlist_scan_key(folder), "building")
        url = plugin.url_for(playlists_radio_build) + "?artist=" + quote(artist, safe="")
        _schedule_playlist_action(url, "RotationRadioBuild")


@plugin.route("/playlists/radio/build")
def playlists_radio_build():
    """Build Last.fm radio off the directory thread; persist partial snapshots."""
    artist = unquote(plugin.args.get("artist", [""])[0])
    if not artist:
        return
    source = _make_playlists(quiet=True)
    key = _radio_cache_key(artist)
    folder = _playlist_folder_url("radio", artist)
    window = xbmcgui.Window(10000)
    def progress(entries):
        source.cache.put(key, {"entries": entries, "complete": False})
        _safe_background_refresh(folder, "radio discovery progress")
    try:
        entries = source.artist_radio(artist, limit=_playlist_limit(),
                                      on_progress=progress)
        source.cache.put(key, {"entries": entries, "complete": True})
        if source.last_failure:
            window.setProperty(
                _radio_error_key(artist),
                "Last.fm unavailable — built with Deezer")
        xbmc.log("[rotation][radio] built %d tracks for %r" % (len(entries), artist),
                 xbmc.LOGINFO)
    except ProviderError as exc:
        xbmc.log("[rotation] Artist radio provider failed for %r: %s" %
                 (artist, exc), xbmc.LOGWARNING)
        window.setProperty(_radio_error_key(artist), exc.message)
        # Never replace a successful radio snapshot with an outage result.
    except Exception as exc:
        xbmc.log("[rotation] Artist radio build failed for %r: %s" % (artist, exc),
                 xbmc.LOGWARNING)
        window.setProperty(_radio_error_key(artist),
                           "Artist Radio could not be created.")
    finally:
        window.clearProperty(_playlist_scan_key(folder))
        xbmc.executebuiltin("CancelAlarm(RotationRadioPartial,silent)")
        # Discovery completion is an explicit user-requested state change,
        # not a cosmetic artwork update. The idle-gated refresh path can
        # leave Android on "Discovering Tracks..." indefinitely.
        refresh_url = (plugin.url_for(playlists_radio_refresh_complete)
                       + "?artist=" + quote(artist, safe=""))
        _schedule_playlist_action(refresh_url, "RotationRadioComplete")


@plugin.route("/playlists/missing")
def playlists_missing():
    """Legacy missing-tracks view, retained for existing bookmarks."""
    kind = unquote(plugin.args.get("kind", [""])[0])
    arg = unquote(plugin.args.get("arg", [""])[0])

    entries = _playlist_entries(kind, arg)
    index = _library()
    index.build()
    _, missing = index.resolve_many(
        entries,
        threshold=_match_threshold(),
        allow_any_artist=_allow_any_artist(),
    )

    for entry in missing:
        label = "{0} — {1}".format(entry.get("artist", ""), entry.get("title", ""))
        li = xbmcgui.ListItem(label)
        li.setLabel2(entry.get("artist", ""))
        li.setArt(_art(entry.get("cover", ""),
                       _cached_background(entry.get("artist", "")), FALLBACK_ALBUM))
        _set_music_tag(li,
            title=entry.get("title", ""),
            artist=entry.get("artist", ""),
            album=entry.get("album", ""),
            duration=entry.get("duration", 0))
        url = _playlist_remote_url(entry)
        li.setPath(url)
        li.setProperty("IsPlayable", "true")
        xbmcplugin.addDirectoryItem(plugin.handle, url, li, False)

    _set_view("songs")
    xbmcplugin.endOfDirectory(plugin.handle)


@plugin.route("/playlists/play")
def playlists_play():
    """
    Resolve a provider playlist against Kodi and start its playable entries.
    """
    kind = unquote(plugin.args.get("kind", [""])[0])
    arg = unquote(plugin.args.get("arg", [""])[0])
    label = unquote(plugin.args.get("label", ["Playlist"])[0])
    shuffle = plugin.args.get("shuffle", ["0"])[0] == "1"

    if not kind:
        return

    entries = _playlist_entries(kind, arg)
    if not entries:
        xbmcgui.Dialog().notification(
            plugin.name, "No tracks found.", xbmcgui.NOTIFICATION_WARNING, 3000)
        return

    progress = xbmcgui.DialogProgress()
    progress.create("Preparing Playlist", "Locating playable sources…")

    class PreparationCancelled(Exception):
        pass

    def show_progress(current, total, available=0, cache=None):
        if progress.iscanceled():
            raise PreparationCancelled()
        percent = int((float(current) / max(1, total)) * 70)
        progress.update(percent, "Checking %d of %d tracks…" % (current, total))

    # A context-menu action can close its dialog a fraction of a second before
    # Kodi opens the Play/Shuffle busy dialog. Tell the background service not
    # to refresh the directory anywhere inside that transition window.
    action_window = xbmcgui.Window(10000)
    action_window.setProperty("Rotation.PlayActionUntil", str(time.time() + 120))

    index = _library()
    progress.update(2, "Checking your music sources…")
    index.build()
    if shuffle:
        entries = list(entries)
        random.shuffle(entries)
    allow_christmas = (kind == "user" or
                       (kind == "tag" and _is_christmas_selection(arg)))
    # Resolve library membership first, but publish streaming rows to Kodi only
    # after their URL passes a real preflight. The service keeps one verified
    # item ahead, avoiding decoder-failure and busy-dialog loops.
    queued = []

    def collect(song, entry, position):
        queued.append((song, entry, position))

    try:
        tracks = _check_playlist_tracks(
            entries, index, label, on_available=collect,
            on_missing=collect if _musicmp3_enabled() else None,
            on_progress=show_progress, allow_christmas=allow_christmas)
    except PreparationCancelled:
        progress.close()
        action_window.setProperty("Rotation.PlayActionUntil", str(time.time() + 5))
        return
    if tracks is None:
        progress.close()
        action_window.setProperty("Rotation.PlayActionUntil", str(time.time() + 5))
        return
    # Shuffle All always opens with owned music when at least one library song
    # exists. Only the first item is promoted; the rest retain shuffled order.
    if shuffle:
        local_at = next((i for i, row in enumerate(queued) if row[0]), None)
        if local_at not in (None, 0):
            queued.insert(0, queued.pop(local_at))

    session = hashlib.sha1(("%s:%s" % (time.time(), random.random()))
                           .encode("utf-8")).hexdigest()[:16]
    buffered = []
    # Resolve two real items before opening Kodi's player. With only the
    # current item in PLAYLIST_MUSIC, every skin correctly hides its Next
    # control until the background worker eventually finds another stream.
    while queued and len(buffered) < 2:
        if progress.iscanceled():
            progress.close()
            action_window.setProperty("Rotation.PlayActionUntil", str(time.time() + 5))
            return
        checked = len(entries) - len(queued) + 1
        progress.update(min(95, 70 + int(25.0 * checked / max(1, len(entries)))),
                        "Locating playable sources…")
        song, entry, position = queued.pop(0)
        if song:
            buffered.append((song["file"], _playlist_listitem(song, position)))
            continue
        try:
            url = _resolve_musicmp3_stream(entry, session=session)
            buffered.append((url, _resolved_remote_listitem(entry, url, position)))
        except Exception as exc:
            xbmc.log("[rotation] Initial stream preflight skipped %r / %r: %s" %
                     (entry.get("artist"), entry.get("title"), exc),
                     xbmc.LOGWARNING)
            if str(exc).startswith("Streaming source unavailable"):
                _musicmp3_open_cooldown(exc)
                local_rows = [row for row in queued if row[0]]
                for local_song, local_entry, local_position in local_rows:
                    buffered.append((local_song["file"],
                                     _playlist_listitem(local_song, local_position)))
                queued = [row for row in queued if not row[0]]
                _musicmp3_outage_notice(bool(local_rows) or any(
                    not url.startswith(("http://", "https://")) for url, _ in buffered),
                    bool(buffered))
                # This row was not proven unavailable; the provider failed.
                # Preserve it and the untouched queue for an automatic retry.
                queued.insert(0, (song, entry, position))
                break

    if not buffered:
        progress.close()
        action_window.setProperty("Rotation.PlayActionUntil", str(time.time() + 5))
        if not _musicmp3_cooldown_remaining():
            xbmcgui.Dialog().notification(
                plugin.name, "No playable tracks were found.",
                xbmcgui.NOTIFICATION_WARNING, 4500)
        return

    remaining = [{"song": song, "entry": entry, "position": position}
                 for song, entry, position in queued]
    _write_playback_queue({"session": session, "created": time.time(),
                           "remaining": remaining})
    window = xbmcgui.Window(10000)
    window.setProperty("Rotation.LookaheadSession", session)
    window.clearProperty("Rotation.LookaheadBusy")
    playlist = xbmc.PlayList(xbmc.PLAYLIST_MUSIC)
    playlist.clear()
    for url, listitem in buffered:
        playlist.add(url, listitem)
    progress.update(100, "Starting playback…")
    xbmc.Player().play(playlist)
    progress.close()
    action_window.setProperty("Rotation.PlayActionUntil", str(time.time() + 5))
    _notify_playlist_count(len(tracks), len(entries))
    folder = _playlist_folder_url(kind, arg)
    _start_playlist_artwork(entries, tracks, folder, folder,
                            artist=str(arg) if kind in ("radio", "artist_top") else "")


@plugin.route("/playlists/report_missing")
def playlists_report_missing():
    """Explicitly add one artist discovery page's current misses to the report."""
    kind = unquote(plugin.args.get("kind", [""])[0])
    arg = unquote(plugin.args.get("arg", [""])[0])
    label = unquote(plugin.args.get("label", ["Artist Tracks"])[0])
    if kind not in ("artist_top", "deezer_album", "user"):
        return
    if not _unavailable_report_enabled():
        _notify_error("Unavailable track reporting is turned off.")
        return
    entries = _playlist_entries(kind, arg)
    index = _library()
    index.build()
    candidates = list(_playlist_tracks(entries, index, allow_christmas=kind == "user"))
    missing_count = sum(1 for song, _ in candidates if not song)
    _check_playlist_tracks(
        entries, index, label, record_unavailable=True, allow_christmas=kind == "user")
    if missing_count:
        xbmcgui.Dialog().notification(
            plugin.name,
            "%d missing track%s added to the report." %
            (missing_count, "" if missing_count == 1 else "s"),
            xbmcgui.NOTIFICATION_INFO, 3500)
    else:
        xbmcgui.Dialog().notification(
            plugin.name, "No missing tracks to add.",
            xbmcgui.NOTIFICATION_INFO, 2500)
    xbmc.executebuiltin("Container.Refresh")


@plugin.route("/playlists/scan")
def playlists_scan():
    """Check availability without changing or starting the player's queue."""
    kind = unquote(plugin.args.get("kind", [""])[0])
    arg = unquote(plugin.args.get("arg", [""])[0])
    label = unquote(plugin.args.get("label", ["Playlist"])[0])
    if not kind:
        return
    entries = _playlist_entries(kind, arg)
    if not entries:
        return
    index = _library()
    index.build()
    folder = _playlist_folder_url(kind, arg)
    window = xbmcgui.Window(10000)
    def progress(checked, total, found, cache):
        window.setProperty(_playlist_scan_key(folder),
                           "%d/%d/%d" % (checked, found, total))
        if checked % 10 == 0 and checked < total:
            _save_availability(cache)
    try:
        allow_christmas = (kind == "user" or
                           (kind == "tag" and _is_christmas_selection(arg)))
        tracks = _check_playlist_tracks(
            entries, index, label, on_progress=progress if folder else None,
            allow_christmas=allow_christmas,
            record_unavailable=kind not in ("artist_top", "deezer_album"))
    finally:
        if folder:
            completed_counts = window.getProperty(_playlist_scan_key(folder))
            window.setProperty(_playlist_scan_done_key(folder), completed_counts or "1")
            window.clearProperty(_playlist_scan_key(folder))
    _notify_playlist_count(len(tracks), len(entries))
    # Availability and artwork publish a single finished directory together.
    # Numeric scan totals remain available to the one progress owner.
    artwork_started = _start_playlist_artwork(
        entries, tracks, folder, folder, force_refresh=True,
        artist=str(arg) if kind in ("radio", "artist_top") else "")
    if folder:
        xbmc.executebuiltin("CancelAlarm(RotationPlaylistPartial,silent)")
        if not artwork_started:
            _queue_background_refresh(folder, "playlist availability complete", service_only=True)

@plugin.route("/playlists/refresh")
def playlists_refresh():
    """Commit a completed scan only while its original directory is visible."""
    folder = unquote(plugin.args.get("folder", [""])[0])
    if folder and _same_plugin_location(
            _visible_playlist_location(), folder):
        xbmcgui.Window(10000).setProperty(_playlist_refresh_key(folder), "1")
        xbmc.log("[rotation] Displaying completed playlist availability scan",
                 xbmc.LOGINFO)
        xbmc.executebuiltin("Container.Refresh")


@plugin.route("/playlists/rebuild")
def playlists_rebuild():
    """Force a full re-read of the music library into the match index."""
    index = _library()
    index.invalidate()

    progress = xbmcgui.DialogProgressBG()
    progress.create(plugin.name, "Indexing music library…")
    try:
        index.build(force=True)
    finally:
        progress.close()

    xbmcgui.Dialog().notification(
        plugin.name, "Indexed %d songs." % index.size,
        xbmcgui.NOTIFICATION_INFO, 3000)
    _end_action()


@plugin.route("/playlists/clear_cache")
def playlists_clear_cache():
    """Drop cached provider responses so the next browse refetches charts."""
    removed = _make_playlists().clear_cache()
    try:
        os.remove(_availability_path())
    except FileNotFoundError:
        pass
    xbmcgui.Dialog().notification(
        plugin.name, "Cleared %d cached playlist responses." % removed,
        xbmcgui.NOTIFICATION_INFO, 3000)
    _end_action()


# --------------------------------------------------------------------------- #
# Utility
# --------------------------------------------------------------------------- #

def _rebuild_estimate(artist_count):
    """
    Rough wall-clock cost of re-resolving artist artwork, as text.

    MusicBrainz permits one request per second and the throttle is global,
    so artist lookups cannot be parallelised. This is the number users need
    to see before wiping the cache.
    """
    seconds = max(int(artist_count * 1.2), 1)
    if seconds < 60:
        return "{0} second{1}".format(seconds, "" if seconds == 1 else "s")
    minutes = seconds // 60
    if minutes < 60:
        return "about {0} minute{1}".format(minutes, "" if minutes == 1 else "s")
    hours = minutes / 60.0
    return "about {0:.1f} hours".format(hours)


def _confirm_artwork_clear(stats):
    """
    Ask before wiping the artwork cache, and explain the cost.

    Clearing makes the addon slower, not faster - the opposite of what
    "clear cache" usually implies. Album covers come back quickly, but
    artist portraits and backgrounds are rate-limited by MusicBrainz at one
    request per second, so a large cache can take a long time to rebuild.
    """
    # Kodi's yes/no dialog is sized by the skin, and many skins clip a
    # fixed number of lines with no scrolling. Keep this short enough to
    # survive a five-line box: the entry counts and the rebuild cost are
    # the only parts that change a user's decision. The full breakdown
    # goes to the log instead.
    message = (
        "Delete [B]{total}[/B] cached entries "
        "({covers} covers, {artists} artists)?[CR][CR]"
        "[COLOR orange]This makes browsing slower, not faster.[/COLOR][CR]"
        "Artist artwork is limited to 1 lookup per second - "
        "rebuilding takes {estimate}."
    ).format(
        total=stats["total"],
        covers=stats["covers"],
        artists=stats["artists"],
        estimate=_rebuild_estimate(stats["artists"]),
    )

    xbmc.log(
        "[rotation] Clear artwork prompt: %d entries (%d covers, "
        "%d artists, %d with images), est. rebuild %s"
        % (stats["total"], stats["covers"], stats["artists"],
           stats["artist_hits"], _rebuild_estimate(stats["artists"])),
        xbmc.LOGINFO
    )

    dialog = xbmcgui.Dialog()
    try:
        return dialog.yesno(
            "Clear Artwork Cache?",
            message,
            nolabel="Keep Cache",
            yeslabel="Clear Anyway",
            defaultbutton=xbmcgui.DLG_YESNO_NO_BTN,
        )
    except (AttributeError, TypeError):
        # defaultbutton / DLG_YESNO_NO_BTN are not available on every Kodi
        # build; fall back to the basic form rather than failing outright.
        return dialog.yesno("Clear Artwork Cache?", message,
                            nolabel="Keep Cache", yeslabel="Clear Anyway")


@plugin.route("/maintenance/clear_artwork")
def rotation_clear_artwork():
    """
    Drop every resolved artwork entry.

    Useful when cached results are stale or were recorded during a network
    outage. Covers and backgrounds are re-resolved on the next visit.
    """
    provider = _make_artwork()
    if not provider:
        orphan_covers = _cleanup_orphan_playlist_artwork()
        xbmcgui.Dialog().notification(
            plugin.name, ("Artwork lookup is turned off." if not orphan_covers else
                          "%d orphan playlist cover%s removed." %
                          (orphan_covers, "" if orphan_covers == 1 else "s")),
            xbmcgui.NOTIFICATION_INFO, 3000
        )
        _end_action()
        return

    stats = provider.stats()

    if not stats["total"]:
        orphan_covers = _cleanup_orphan_playlist_artwork()
        xbmcgui.Dialog().notification(
            plugin.name, ("Artwork cache is already empty." if not orphan_covers else
                          "%d orphan playlist cover%s removed." %
                          (orphan_covers, "" if orphan_covers == 1 else "s")),
            xbmcgui.NOTIFICATION_INFO, 3000
        )
        _end_action()
        return

    if not _confirm_artwork_clear(stats):
        _end_action()
        return

    orphan_covers = _cleanup_orphan_playlist_artwork()
    removed = provider.clear()
    with _PLAYLIST_COVER_LOCK:
        _PLAYLIST_COVER_TRIED.clear()
    xbmc.log("[rotation] Artwork cache cleared: %d entries" % removed,
             xbmc.LOGINFO)
    xbmcgui.Dialog().notification(
        plugin.name, ("Artwork cache cleared (%d entries, %d orphan cover%s)." %
                      (removed, orphan_covers,
                       "" if orphan_covers == 1 else "s")),
        xbmcgui.NOTIFICATION_INFO, 3000
    )
    _end_action()



# --------------------------------------------------------------------------- #
# Local-library playback
# --------------------------------------------------------------------------- #

@plugin.route("/library/play")
def library_play():
    """Prefer Kodi, then optionally resolve an unavailable track remotely."""
    artist = unquote(plugin.args.get("artist", [""])[0])
    title = unquote(plugin.args.get("title", [""])[0])
    album = unquote(plugin.args.get("album", [""])[0])
    cover = unquote(plugin.args.get("cover", [""])[0])
    session = unquote(plugin.args.get("session", [""])[0])
    index = _library()
    index.build()
    song = index.resolve(artist, title, album=album,
                         threshold=_match_threshold(),
                         allow_any_artist=_allow_any_artist()) if index.size else None
    if not song:
        xbmc.log("[rotation] Local track unavailable: %r / %r" %
                 (artist, title), xbmc.LOGINFO)
        if _musicmp3_enabled():
            _play_musicmp3_fallback(artist, title, album, cover, session=session)
        else:
            xbmcplugin.setResolvedUrl(plugin.handle, False, xbmcgui.ListItem())
            xbmcgui.Dialog().notification(
                plugin.name, "%s — %s is not in the Kodi library" % (artist, title),
                xbmcgui.NOTIFICATION_WARNING, 3500)
        return
    li = _playlist_listitem(song, cover=cover)
    xbmcplugin.setResolvedUrl(plugin.handle, True, li)


def _best_musicmp3_match(artist, title, results):
    """Require a credible artist and title match; never match by title alone."""
    wanted_artist = norm_artist(artist)
    wanted_primary = primary_artist(artist)
    wanted_title = norm_title(title)
    wanted_strict = norm_title(title, False)
    best, best_score = None, 0.0
    for result in results:
        if not result.get("track_id") or not result.get("rel"):
            continue
        result_artist = norm_artist(result.get("artist", ""))
        result_primary = primary_artist(result.get("artist", ""))
        artist_score = max(_similar(wanted_artist, result_artist),
                           _similar(wanted_primary, result_primary))
        title_score = max(
            _similar(wanted_title, norm_title(result.get("title", ""))),
            _similar(wanted_strict,
                     norm_title(result.get("title", ""), False)))
        score = (0.55 * title_score + 0.45 * artist_score
                 if artist_score >= 0.86 and title_score >= 0.84 else 0.0)
        if score > best_score:
            best, best_score = result, score
    return best if best_score >= 0.88 else None


_MUSICMP3_ALBUM_TRACK_CACHE = {}


def _musicmp3_ascii(value):
    """Fold provider spelling such as ADÉLA to MusicMP3.ru's Adela."""
    text = unicodedata.normalize("NFKD", str(value or ""))
    return "".join(char for char in text
                   if not unicodedata.combining(char)).strip()


def _musicmp3_album_tracks(api, entry):
    """Resolve a known album once and reuse its canonical track rows."""
    artist = entry.get("artist", "")
    album = entry.get("album", "")
    if not artist or not album:
        return []
    key = (norm_artist(artist), norm_title(album, False))
    cached = _MUSICMP3_ALBUM_TRACK_CACHE.get(key)
    if cached is not None:
        return cached

    folded_artist = _musicmp3_ascii(artist)
    folded_album = _musicmp3_ascii(album)
    queries = []
    for query in ("%s %s" % (folded_artist, folded_album), folded_album):
        query = query.strip()
        if query and query.casefold() not in {q.casefold() for q in queries}:
            queries.append(query)

    wanted_artist = norm_artist(artist)
    wanted_album = norm_title(album, False)
    best, best_score = None, 0.0
    for query in queries:
        for candidate in api.search(query, "albums", fast=True):
            candidate_artist = norm_artist(candidate.get("artist", ""))
            candidate_album = norm_title(candidate.get("title", ""), False)
            artist_score = _similar(wanted_artist, candidate_artist)
            album_score = _similar(wanted_album, candidate_album)
            score = 0.55 * album_score + 0.45 * artist_score
            if artist_score >= 0.88 and album_score >= 0.88 and score > best_score:
                best, best_score = candidate, score
        if best:
            break

    if not best or not best.get("link"):
        _MUSICMP3_ALBUM_TRACK_CACHE[key] = []
        return []
    tracks, _album_info = api.album_tracks(best["link"])
    # Album-page metadata is canonical for this source, but retain the same
    # strict artist/title validation when selecting an individual recording.
    _MUSICMP3_ALBUM_TRACK_CACHE[key] = tracks or []
    xbmc.log("[rotation] MusicMP3.ru album fallback cached %d track(s) for %r / %r" %
             (len(_MUSICMP3_ALBUM_TRACK_CACHE[key]), artist, album), xbmc.LOGINFO)
    return _MUSICMP3_ALBUM_TRACK_CACHE[key]


def _find_musicmp3_track(api, entry):
    """Search narrowly, then broaden safely and finally use the album page."""
    artist = entry.get("artist", "")
    title = entry.get("title", "")
    folded_artist = _musicmp3_ascii(artist)
    folded_title = _musicmp3_ascii(title)
    queries = []
    for query in ("%s %s" % (artist, title),
                  "%s %s" % (folded_artist, folded_title),
                  folded_title):
        query = query.strip()
        if query and query.casefold() not in {q.casefold() for q in queries}:
            queries.append(query)
    for query in queries:
        results = api.search(query, "songs", limit=150, fast=True)
        match = _best_musicmp3_match(artist, title, results)
        if match:
            xbmc.log("[rotation] MusicMP3.ru matched %r / %r with query %r" %
                     (artist, title, query), xbmc.LOGINFO)
            return match
        if api.last_error:
            return None
    return _best_musicmp3_match(
        artist, title, _musicmp3_album_tracks(api, entry))


def _musicmp3_cooldown_remaining():
    try:
        until = float(xbmcgui.Window(10000).getProperty("Rotation.MusicMP3UnavailableUntil") or 0)
        return max(0, int(math.ceil(until - time.time())))
    except (TypeError, ValueError):
        return 0


def _musicmp3_open_cooldown(exc):
    """Only real failed requests open a circuit; blocked attempts never extend it."""
    if "(cooldown)" in str(exc) or _musicmp3_cooldown_remaining():
        return False
    xbmcgui.Window(10000).setProperty("Rotation.MusicMP3UnavailableUntil", str(time.time() + 60))
    ProviderHealth("MusicMP3.ru").failure("unreachable", exc)
    return True


def _musicmp3_outage_notice(library_remaining=False, playable_remaining=False):
    window = xbmcgui.Window(10000)
    until = window.getProperty("Rotation.MusicMP3UnavailableUntil")
    key = "Rotation.MusicMP3NoticeUntil"
    if until and window.getProperty(key) == until:
        return
    if until: window.setProperty(key, until)
    if library_remaining:
        message = "Streaming temporarily unavailable. Continuing with library songs."
    elif playable_remaining:
        message = "Streaming temporarily unavailable. Continuing with buffered songs."
    else:
        message = "Streaming temporarily unavailable. No library songs remain. Try again shortly."
    xbmcgui.Dialog().notification("Streaming source unavailable", message,
                                  xbmcgui.NOTIFICATION_WARNING, 5000)


def _verified_musicmp3_track(entry):
    """Retry a genuine network failure once, never retry a missing track."""
    if _musicmp3_cooldown_remaining():
        raise RuntimeError("Streaming source unavailable (cooldown)")
    for attempt in range(2):
        api = _make_musicmp3(timeout_override=2 if attempt == 0 else 1)
        match = _find_musicmp3_track(api, entry)
        if not match:
            if not api.last_error:
                raise ValueError("No matching stream was found")
        else:
            url = api.play_url(match["track_id"], match["rel"],
                               referer_url=match.get("album_url") or None)
            if api.stream_available(url):
                window = xbmcgui.Window(10000)
                window.clearProperty("Rotation.MusicMP3UnavailableUntil")
                window.clearProperty("Rotation.MusicMP3NoticeUntil")
                ProviderHealth("MusicMP3.ru").success()
                return api, match, url
            if not api.last_error:
                raise ValueError("The stream server was unavailable")
        if attempt == 1:
            raise RuntimeError("Streaming source unavailable: %s" % api.last_error)
        xbmc.log("[rotation] MusicMP3.ru request failed; retrying once: %s" % api.last_error,
                 xbmc.LOGWARNING)


def _resolve_musicmp3_stream(entry, session=""):
    """Return one verified direct stream URL without touching Kodi's player."""
    artist = entry.get("artist", "")
    title = entry.get("title", "")
    if not artist or not title:
        raise ValueError("Missing artist or title")
    window = xbmcgui.Window(10000)
    api, match, url = _verified_musicmp3_track(entry)
    if session:
        session_key = "Rotation.StreamSession.%s" % session
        try:
            seen = set(json.loads(window.getProperty(session_key) or "[]"))
        except (ValueError, TypeError):
            seen = set()
        stream_ids = {
            "match:" + str(match.get("track_id") or match.get("rel") or ""),
            "url:" + hashlib.sha1(url.encode("utf-8")).hexdigest(),
        }
        stream_ids.discard("match:")
        if seen.intersection(stream_ids):
            raise ValueError("Duplicate stream suppressed")
        seen.update(stream_ids)
        window.setProperty(session_key, json.dumps(list(seen)[-200:]))
    return url


@plugin.route("/playlists/lookahead")
def playlists_lookahead():
    """Append the next verified item; called only by Rotation's service."""
    session = unquote(plugin.args.get("session", [""])[0])
    window = xbmcgui.Window(10000)
    try:
        state = _read_playback_queue()
        if not session or state.get("session") != session:
            return
        remaining = state.get("remaining") or []
        appended = False
        while remaining and not appended:
            # Heartbeat the single-flight lease while this worker owns it.
            window.setProperty("Rotation.LookaheadBusy", str(time.time()))
            row = remaining.pop(0)
            song = row.get("song")
            entry = row.get("entry") or {}
            position = row.get("position") or 0
            try:
                if song:
                    url = song["file"]
                    li = _playlist_listitem(song, position)
                else:
                    url = _resolve_musicmp3_stream(entry, session=session)
                    li = _resolved_remote_listitem(entry, url, position)
                xbmc.PlayList(xbmc.PLAYLIST_MUSIC).add(url, li)
                appended = True
            except Exception as exc:
                xbmc.log("[rotation] Lookahead skipped %r / %r: %s" %
                         (entry.get("artist"), entry.get("title"), exc),
                         xbmc.LOGWARNING)
                if str(exc).startswith("Streaming source unavailable"):
                    _musicmp3_open_cooldown(exc)
                    local_rows = [item for item in remaining if item.get("song")]
                    for local_row in local_rows:
                        local_song = local_row["song"]
                        xbmc.PlayList(xbmc.PLAYLIST_MUSIC).add(
                            local_song["file"], _playlist_listitem(
                                local_song, local_row.get("position") or 0))
                    remaining = [item for item in remaining if not item.get("song")]
                    appended = bool(local_rows)
                    _musicmp3_outage_notice(bool(local_rows),
                        xbmc.PlayList(xbmc.PLAYLIST_MUSIC).size() - max(0,
                            xbmc.PlayList(xbmc.PLAYLIST_MUSIC).getposition()) > 1)
                    # Put the interrupted candidate back. The background
                    # service observes the cooldown and resumes this same
                    # queue after the provider has had time to recover.
                    remaining.insert(0, row)
                    break
        state["remaining"] = remaining
        _write_playback_queue(state)
        if not remaining and not appended:
            window.clearProperty("Rotation.LookaheadSession")
    finally:
        window.clearProperty("Rotation.LookaheadBusy")


def _play_musicmp3_fallback(artist, title, album="", cover="", session=""):
    """Resolve and play one missing song without exposing source menus."""
    try:
        if not artist or not title:
            raise ValueError("Missing artist or title")
        window = xbmcgui.Window(10000)
        api, match, url = _verified_musicmp3_track({
            "artist": artist, "title": title, "album": album})
        if session:
            window = xbmcgui.Window(10000)
            session_key = "Rotation.StreamSession.%s" % session
            try:
                seen = set(json.loads(window.getProperty(session_key) or "[]"))
            except (ValueError, TypeError):
                seen = set()
            stream_ids = {
                "match:" + str(match.get("track_id") or match.get("rel") or ""),
                "url:" + hashlib.sha1(url.encode("utf-8")).hexdigest(),
            }
            stream_ids.discard("match:")
            if seen.intersection(stream_ids):
                raise ValueError("Duplicate stream suppressed")
            seen.update(stream_ids)
            window.setProperty(session_key, json.dumps(list(seen)[-200:]))
        track = api.get_track(match["rel"])
        li = xbmcgui.ListItem(track.title or title, path=url)
        # Use the same cache-only, library-first background lookup as the
        # playlist row. This never performs a network request, so playback
        # remains immediate while the player receives the fanart already
        # visible on the playlist screen.
        resolved_artist = track.artist or artist
        li.setArt(_art(cover or track.image or "",
                       _cached_background(resolved_artist),
                       clearlogo=_cached_logo(resolved_artist)))
        li.setMimeType("audio/mpeg")
        li.setContentLookup(False)
        _set_music_tag(li, title=track.title or title,
                       artist=track.artist or artist,
                       album=track.album or album,
                       duration=track.duration or 0)
        xbmcplugin.setResolvedUrl(plugin.handle, True, li)
        xbmc.log("[rotation] MusicMP3.ru stream resolved: %r / %r" %
                 (artist, title), xbmc.LOGINFO)
    except Exception as exc:
        xbmc.log("[rotation] MusicMP3.ru playback failed for %r / %r: %s" %
                 (artist, title, exc), xbmc.LOGWARNING)
        source_down = str(exc).startswith("Streaming source unavailable")
        if source_down:
            window = xbmcgui.Window(10000)
            _musicmp3_open_cooldown(exc)
        # Kodi 21 can terminate itself when a failed queued plugin:// item is
        # followed immediately by another plugin:// item: the second resolver
        # opens while Kodi's first busy dialog is still closing. Resolve this
        # bundled one-second silent MP3 successfully instead. That lets the
        # current invocation finish cleanly before PAPlayer advances, while
        # retaining the expected skip-to-next-track behaviour.
        li = xbmcgui.ListItem(title, path=UNAVAILABLE_AUDIO)
        li.setArt(_art(cover, _cached_background(artist),
                       clearlogo=_cached_logo(artist)))
        li.setMimeType("audio/mpeg")
        li.setContentLookup(False)
        _set_music_tag(li, title=title, artist=artist, album=album, duration=1)
        xbmcplugin.setResolvedUrl(plugin.handle, True, li)
        if source_down:
            # Resolve the current item first, then remove only unresolved
            # Rotation streaming rows. Local files later in a mixed playlist
            # remain playable, and Kodi cannot open a chain of plugin busy
            # dialogs while the shared source is offline.
            playlist = xbmc.PlayList(xbmc.PLAYLIST_MUSIC)
            remote_paths = []
            try:
                for position in range(playlist.size()):
                    path = playlist[position].getPath()
                    if (path.startswith("plugin://plugin.audio.rotation/") and
                            "/library/play" in path):
                        remote_paths.append(path)
                for path in remote_paths:
                    playlist.remove(path)
            except Exception as remove_exc:
                xbmc.log("[rotation] Could not remove offline streaming rows: %s"
                         % remove_exc, xbmc.LOGWARNING)
            local_remaining = False
            buffered_remaining = False
            try:
                current = max(-1, playlist.getposition())
                for position in range(current + 1, playlist.size()):
                    path = playlist[position].getPath()
                    if not path or path == UNAVAILABLE_AUDIO: continue
                    if path.startswith(("http://", "https://")):
                        buffered_remaining = True
                    elif not path.startswith("plugin://"):
                        local_remaining = True
            except Exception:
                pass
            if "(cooldown)" in str(exc):
                xbmcgui.Dialog().notification(
                    "Streaming temporarily unavailable",
                    "Streaming retry available in %d seconds" % _musicmp3_cooldown_remaining(),
                    xbmcgui.NOTIFICATION_INFO, 3000)
            else:
                _musicmp3_outage_notice(local_remaining, buffered_remaining)
        elif str(exc) != "Duplicate stream suppressed":
            xbmcgui.Dialog().notification(
                "Track unavailable", "Skipping to the next song",
                xbmcgui.NOTIFICATION_WARNING, 4000)


@plugin.route("/library/unavailable")
def library_unavailable():
    """Explain gray provider rows without attempting remote playback."""
    artist = unquote(plugin.args.get("artist", [""])[0])
    title = unquote(plugin.args.get("title", [""])[0])
    xbmc.log("[rotation] Local playback unavailable for %r / %r" %
             (artist, title), xbmc.LOGINFO)
    xbmcgui.Dialog().notification(
        "Not in Kodi library", "This track cannot be played locally",
        xbmcgui.NOTIFICATION_INFO, 3500)
    if plugin.handle >= 0:
        xbmcplugin.setResolvedUrl(plugin.handle, False, xbmcgui.ListItem())


if __name__ == "__main__":
    try:
        plugin.run(sys.argv)
    except ProviderError as exc:
        xbmc.log("[rotation] Provider action stopped safely: %s (%s)" %
                 (exc.message, exc.detail), xbmc.LOGWARNING)
        xbmcgui.Dialog().notification(
            exc.provider, exc.message,
            xbmcgui.NOTIFICATION_WARNING, 5000)
        if plugin.handle >= 0:
            try:
                xbmcplugin.endOfDirectory(plugin.handle, succeeded=False)
            except Exception:
                pass
