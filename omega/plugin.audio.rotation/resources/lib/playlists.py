# -*- coding: utf-8 -*-
#
# Rotation — resources/lib/playlists.py
#
# Playlist metadata providers.
#
# These fetch track *lists* — artist and title pairs, plus whatever cover art
# and ordering the source supplies. Resolution to playable files happens in
# library.py exclusively against Kodi's local music database.
#
# Two providers:
#
#   Deezer  — no API key, no registration, no auth header. Public read
#             endpoints under api.deezer.com. This is the default so the
#             feature works the moment the addon is installed.
#
#   Last.fm — needs a free API key (https://www.last.fm/api/account/create).
#             Better similarity data than Deezer's radio endpoint and it has
#             genuine geo charts, so it is worth the two minutes of setup.
#             Enabled in settings; falls back to Deezer when no key is set.
#
# Every provider method returns the same shape:
#
#   [{"artist": str, "title": str, "album": str, "duration": int,
#     "cover": str, "position": int}, ...]
#
# Rate limits: Deezer allows roughly 50 requests per 5 seconds per IP, which
# this module stays far below — a playlist is one or two calls. Last.fm's
# documented ceiling is 5 requests/second averaged over 5 minutes; the radio
# builder sleeps between similar-artist lookups to respect it.
#
# Logging note: xbmc.log() only — Python's standard logging module produces
# zero output in Kodi addon context.

import hashlib
import html
import json
import re
import os
import random
import time
from urllib.parse import quote_plus

import requests
import xbmc
import xbmcgui

from resources.lib.library import track_identity_key, art_artist_name, norm_artist
from resources.lib.provider_health import ProviderError, ProviderHealth

DEEZER_API = "https://api.deezer.com"
LASTFM_API = "https://ws.audioscrobbler.com/2.0/"

USER_AGENT = "Kodi/Rotation (plugin.audio.rotation)"


def primary_top_artists(rows, limit=50):
    """Group explicit collaboration credits under the first credited artist."""
    grouped = {}
    for row in rows or []:
        name = art_artist_name(row.get("name", ""))
        key = norm_artist(name)
        if not key:
            continue
        solo = name == row.get("name", "")
        if key not in grouped:
            grouped[key] = dict(row, name=name, id="", picture=row.get("picture", "") if solo else "", playcount=0)
        target = grouped[key]
        target["playcount"] += int(row.get("playcount") or 0)
        if solo:
            target["name"] = name
            target["picture"] = row.get("picture", "")
            target["id"] = row.get("id", "")
    return sorted(grouped.values(), key=lambda row: row["playcount"], reverse=True)[:limit]


def _dedupe_tracks(entries, limit=None):
    """Keep provider order while collapsing duplicate recording labels."""
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


# --------------------------------------------------------------------------- #
# Disk cache
# --------------------------------------------------------------------------- #

class JsonCache(object):
    """
    Filesystem cache for provider responses.

    Charts move slowly — Deezer's global chart changes weekly, Last.fm's
    similarity graph barely moves at all — so a multi-hour TTL costs nothing
    in freshness and keeps browsing snappy and offline-tolerant.
    """

    def __init__(self, cache_dir, ttl_hours=12):
        self.cache_dir = cache_dir
        self.ttl = max(0, int(ttl_hours)) * 3600
        try:
            os.makedirs(cache_dir, exist_ok=True)
        except OSError:
            pass

    def _path(self, key):
        digest = hashlib.sha1(key.encode("utf-8")).hexdigest()
        return os.path.join(self.cache_dir, digest + ".json")

    def get(self, key):
        path = self._path(key)
        if not os.path.exists(path):
            return None
        try:
            if self.ttl and (time.time() - os.path.getmtime(path)) > self.ttl:
                return None
            with open(path, "r", encoding="utf-8") as handle:
                return json.load(handle)
        except Exception:                                     # noqa: BLE001
            return None

    def get_stale(self, key):
        """Return the last successful value even after its normal TTL."""
        path = self._path(key)
        if not os.path.exists(path):
            return None
        try:
            with open(path, "r", encoding="utf-8") as handle:
                return json.load(handle)
        except Exception:                                     # noqa: BLE001
            return None

    def put(self, key, value):
        try:
            tmp = self._path(key) + ".tmp"
            with open(tmp, "w", encoding="utf-8") as handle:
                json.dump(value, handle)
            os.replace(tmp, self._path(key))
        except Exception as exc:                              # noqa: BLE001
            xbmc.log("[rotation] playlist cache write failed: %s" % exc,
                     xbmc.LOGWARNING)

    def clear(self):
        removed = 0
        try:
            for name in os.listdir(self.cache_dir):
                if name.endswith(".json"):
                    os.remove(os.path.join(self.cache_dir, name))
                    removed += 1
        except OSError:
            pass
        return removed


# --------------------------------------------------------------------------- #
# HTTP
# --------------------------------------------------------------------------- #

def _get_json(url, provider, health, timeout=15, retries=1):
    """
    GET a JSON document with a short backoff.

    Returns decoded JSON or raises a classified ProviderError. Retries are
    deliberately bounded so Kodi always regains focus promptly.
    """
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}

    for attempt in range(retries + 1):
        try:
            response = requests.get(
                url, headers=headers, timeout=max(2, min(int(timeout), 10)))
            if response.status_code == 429:
                if attempt < retries:
                    time.sleep(min(2.0, 0.75 * (attempt + 1)))
                    continue
                raise health.failure("rate_limited", "HTTP 429")
            if response.status_code in (401, 403):
                raise health.failure("configuration",
                                     "HTTP %d" % response.status_code)
            response.raise_for_status()
            try:
                data = response.json()
            except (TypeError, ValueError) as exc:
                raise health.failure("malformed", exc)
            health.success()
            return data
        except ProviderError:
            raise
        except requests.Timeout as exc:
            if attempt >= retries:
                raise health.failure("unreachable", exc)
            time.sleep(0.4 * (attempt + 1))
        except requests.ConnectionError as exc:
            if attempt >= retries:
                raise health.failure("unreachable", exc)
            time.sleep(0.4 * (attempt + 1))
        except requests.RequestException as exc:
            if attempt >= retries:
                raise health.failure("service_error", exc)
            time.sleep(0.4 * (attempt + 1))
    raise health.failure("unreachable", "%s did not finish" % provider)


# --------------------------------------------------------------------------- #
# Deezer
# --------------------------------------------------------------------------- #

class DeezerProvider(object):
    """Public Deezer read endpoints. No key required."""

    name = "Deezer"

    def __init__(self, cache, timeout=15, interactive=True):
        self.cache = cache
        self.timeout = timeout
        self.interactive = interactive
        self.health = ProviderHealth(self.name)
        self._cached_notice_shown = False

    def _stale_or_raise(self, key, error):
        stale = self.cache.get_stale(key)
        if stale is None:
            raise error
        if self.interactive and not self._cached_notice_shown:
            xbmcgui.Dialog().notification(
                "Deezer unavailable", "Showing cached results",
                xbmcgui.NOTIFICATION_WARNING, 4500)
            self._cached_notice_shown = True
        xbmc.log("[rotation] Deezer unavailable; using stale cache for %s" % key,
                 xbmc.LOGWARNING)
        return stale

    def _fetch(self, path, cache_key=None):
        url = DEEZER_API + path
        key = cache_key or url

        cached = self.cache.get(key)
        if cached is not None:
            return cached

        circuit = self.health.circuit_error()
        if circuit:
            return self._stale_or_raise(key, circuit)
        try:
            data = _get_json(url, self.name, self.health, timeout=self.timeout)
        except ProviderError as exc:
            return self._stale_or_raise(key, exc)
        if data is None:
            raise self.health.failure("malformed", "Empty JSON response")
        # Deezer reports errors in-band with a 200 status.
        if isinstance(data, dict) and "error" in data and data["error"]:
            xbmc.log("[rotation] deezer error: %s" % data["error"], xbmc.LOGWARNING)
            error = data["error"] or {}
            category = ("configuration" if str(error.get("code")) in
                        ("4", "100", "200", "300") else "service_error")
            return self._stale_or_raise(
                key, self.health.failure(category, error.get("message") or error))

        self.cache.put(key, data)
        return data

    @staticmethod
    def _tracks(payload):
        """Convert a Deezer track collection into provider entries."""
        if not payload:
            return []
        rows = payload.get("data", payload) if isinstance(payload, dict) else payload
        if not isinstance(rows, list):
            return []

        entries = []
        for position, row in enumerate(rows, 1):
            if not isinstance(row, dict):
                continue
            artist_obj = row.get("artist") or {}
            artist = artist_obj.get("name", "")
            album_obj = row.get("album") or {}
            title = row.get("title_short") or row.get("title") or ""
            if not artist or not title:
                continue
            entries.append({
                "artist":   artist,
                "title":    title,
                "download_title": row.get("title") or title,
                "track_number": row.get("track_position"),
                "disc_number": row.get("disk_number"),
                "album":    album_obj.get("title", ""),
                "album_id": album_obj.get("id"),
                "artist_id": artist_obj.get("id"),
                "artist_cover": artist_obj.get("picture_big")
                                or artist_obj.get("picture_medium", ""),
                "explicit": bool(row.get("explicit_lyrics") or
                                 int(row.get("explicit_content_lyrics") or 0) > 0),
                "duration": int(row.get("duration") or 0),
                "cover":    album_obj.get("cover_big")
                            or album_obj.get("cover_medium", ""),
                "position": row.get("position") or position,
            })
        return entries

    # -- charts ------------------------------------------------------------ #

    def chart_tracks(self, limit=100, genre_id=0):
        """
        Top tracks chart. genre_id 0 is the global 'all genres' chart.

        Deezer caps a single chart page at 100 entries; ask for more and the
        extra is silently dropped rather than paged.
        """
        limit = max(1, min(int(limit), 100))
        payload = self._fetch("/chart/%d/tracks?limit=%d" % (int(genre_id), limit))
        return self._tracks(payload)

    def chart_albums(self, limit=50, genre_id=0):
        """Top albums chart, as album descriptors rather than tracks."""
        limit = max(1, min(int(limit), 100))
        payload = self._fetch("/chart/%d/albums?limit=%d" % (int(genre_id), limit))
        if not payload:
            return []
        albums = []
        for row in payload.get("data", []):
            albums.append({
                "id":     row.get("id"),
                "title":  row.get("title", ""),
                "artist": (row.get("artist") or {}).get("name", ""),
                "cover":  row.get("cover_big") or row.get("cover_medium", ""),
            })
        return albums

    def fresh_albums(self, limit=100):
        """Albums from Deezer's two weekly-release editorial modules.

        Module pages are part of Deezer's public web app rather than its
        documented API.  Parse their embedded JSON defensively, then fall
        back only to Deezer's weekly releases endpoint.  A general popularity
        chart is deliberately not used: unrelated albums are worse than a
        clear temporary-unavailable result here.
        """
        limit = max(1, min(int(limit), 100))

        def albums_from(payload):
            rows = payload.get("data", []) if isinstance(payload, dict) else payload
            albums = []
            seen = set()
            for value in rows if isinstance(rows, list) else []:
                if not isinstance(value, dict):
                    continue
                # Some editorial responses wrap the album descriptor while
                # chart/release responses return the descriptor directly.
                row = value.get("album") or value
                if not isinstance(row, dict):
                    continue
                album_id = row.get("id") or row.get("ALB_ID")
                title = row.get("title", "") or row.get("ALB_TITLE", "")
                if not album_id or not title or str(album_id) in seen:
                    continue
                seen.add(str(album_id))
                artist_obj = row.get("artist") or value.get("artist") or {}
                artist = (artist_obj.get("name", "") if isinstance(artist_obj, dict)
                          else "") or row.get("ART_NAME", "")
                picture = row.get("ALB_PICTURE", "")
                albums.append({
                    "id": album_id,
                    "title": title,
                    "artist": artist,
                    "cover": (row.get("cover_big") or
                              row.get("cover_medium") or
                              ("https://e-cdns-images.dzcdn.net/images/cover/"
                               "%s/1000x1000-000000-80-0-0.jpg" % picture
                               if picture else "")),
                    "record_type": (row.get("record_type") or "album").lower(),
                    "track_count": int(row.get("nb_tracks") or 0),
                    "release_date": row.get("release_date") or "",
                })
                if len(albums) >= limit:
                    break
            return albums

        def module_payload(locale, module_id, cache_key):
            cached = self.cache.get(cache_key)
            if cached:
                return cached
            url = "https://www.deezer.com/%s/channels/module/%s" % (locale, module_id)
            try:
                response = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=10)
                response.raise_for_status()
                page = html.unescape(response.text)
            except (requests.RequestException, ValueError) as exc:
                xbmc.log("[rotation] Deezer weekly module failed: %s" % exc,
                         xbmc.LOGWARNING)
                return []
            found = []
            # Modern and legacy Deezer pages both embed JSON in script tags.
            for text in re.findall(r"<script[^>]*>(.*?)</script>", page,
                                   flags=re.I | re.S):
                text = text.strip()
                candidates = [text]
                if "=" in text:
                    candidates.append(text.split("=", 1)[1].strip().rstrip(";"))
                for candidate in candidates:
                    if not candidate.startswith(("{", "[")):
                        continue
                    try:
                        root = json.loads(candidate)
                    except (TypeError, ValueError):
                        continue
                    stack = [root]
                    while stack:
                        node = stack.pop()
                        if isinstance(node, dict):
                            if ((node.get("id") and node.get("title") and node.get("artist"))
                                    or (node.get("ALB_ID") and node.get("ALB_TITLE"))):
                                found.append(node)
                            # The traversal stack is LIFO. Push children in
                            # reverse so Deezer's editorial order is retained.
                            stack.extend(reversed(list(node.values())))
                        elif isinstance(node, list):
                            stack.extend(reversed(node))
            payload = {"data": found}
            if albums_from(payload):
                self.cache.put(cache_key, payload)
            return payload

        attempts = (
            ("Albums of the Week", lambda: module_payload(
                "us", "64ac680b-7c84-49a3-9077-38e9b653332e",
                "deezer:albums-week:primary:v2")),
            ("Fresh Picks of the Week", lambda: module_payload(
                "en", "374e4e1e-ff60-40d1-859c-ed4ffc7df6b6",
                "deezer:albums-week:secondary:v2")),
            ("weekly releases", lambda: self._fetch(
                "/editorial/0/releases?limit=%d" % limit,
                cache_key="deezer:albums-week:releases:v1:%d" % limit)),
        )
        for label, fetch in attempts:
            payload = fetch()
            albums = albums_from(payload)
            if albums:
                xbmc.log("[rotation] Albums of the Week using %s (%d albums)" %
                         (label, len(albums)), xbmc.LOGINFO)
                return albums
            details = (sorted(payload.keys()) if isinstance(payload, dict)
                       else type(payload).__name__)
            xbmc.log("[rotation] Deezer Albums of the Week %s returned no usable "
                     "albums (response=%s)" % (label, details), xbmc.LOGWARNING)
        return []

    def chart_artists(self, limit=50, genre_id=0):
        """Popular artists from Deezer's global or genre chart."""
        limit = max(1, min(int(limit), 100))
        payload = self._fetch("/chart/%d/artists?limit=%d"
                              % (int(genre_id), limit))
        return [{
            "id": row.get("id"),
            "name": row.get("name", ""),
            "picture": row.get("picture_big") or row.get("picture_medium", ""),
        } for row in (payload or {}).get("data", [])
            if isinstance(row, dict) and row.get("name")]

    def album_tracks(self, album_id):
        payload = self._fetch("/album/%s/tracks?limit=100" % album_id)
        entries = self._tracks(payload)
        # Track objects nested under an album omit the album title.
        album = self._fetch("/album/%s" % album_id)
        if album:
            album_artist = (album.get("artist") or {}).get("name", "")
            for number, entry in enumerate(entries, 1):
                entry["track_number"] = entry.get("track_number") or number
                entry["year"] = str(album.get("release_date") or "")[:4]
                entry["genre"] = ", ".join(row.get("name", "") for row in (album.get("genres") or {}).get("data", []))
                entry["album"] = album.get("title", "")
                entry["album_artist"] = album_artist
                entry["cover"] = entry["cover"] or album.get("cover_big", "")
                if not entry["artist"]:
                    entry["artist"] = album_artist
        return entries

    def search_albums(self, query, limit=50):
        """Search Deezer's album catalog for albums, compilations and scores."""
        count = max(1, min(int(limit), 100))
        payload = self._fetch(
            "/search/album?q=%s&limit=%d" % (quote_plus((query or "").strip()), count),
            cache_key="deezer:album-search:v1:%s:%d" % (
                (query or "").strip().casefold(), count))
        return [{
            "id": row.get("id"),
            "title": row.get("title", ""),
            "artist": (row.get("artist") or {}).get("name", ""),
            "cover": row.get("cover_big") or row.get("cover_medium") or "",
            "record_type": (row.get("record_type") or "album").lower(),
            "track_count": int(row.get("nb_tracks") or 0),
            "release_date": row.get("release_date") or "",
        } for row in (payload or {}).get("data", [])
            if isinstance(row, dict) and row.get("id") and row.get("title")]

    def artist_albums(self, artist_id, limit=100):
        """Album links for a Deezer artist favorite."""
        payload = self._fetch("/artist/%s/albums?limit=%d" % (artist_id, limit))
        return [
            {"id": row.get("id"), "title": row.get("title", ""),
             "artist": (row.get("artist") or {}).get("name", ""),
             "cover": row.get("cover_big") or row.get("cover_medium") or "",
             "record_type": (row.get("record_type") or "").lower(),
             "track_count": int(row.get("nb_tracks") or 0),
             "release_date": row.get("release_date") or ""}
            for row in (payload or {}).get("data", [])
            if isinstance(row, dict) and row.get("id")
        ]

    def genres(self):
        """Chart genres. Genre id 0 ('All') is filtered out by the caller."""
        payload = self._fetch("/genre")
        if not payload:
            return []
        return [
            {"id": row.get("id"), "name": row.get("name", ""),
             "picture": row.get("picture_big") or row.get("picture_medium", "")}
            for row in payload.get("data", [])
            if row.get("name")
        ]

    # -- editorial --------------------------------------------------------- #

    def editorial_playlists(self, limit=100):
        """Return Deezer's popular playlists after filtering unsuitable themes."""
        payload = self._fetch("/chart/0/playlists?limit=100")
        playlists = (payload or {}).get("data", [])
        if not playlists:
            payload = self._fetch("/editorial/0/charts")
            playlists = ((payload or {}).get("playlists") or {}).get("data", [])
        if not payload:
            return []
        excluded = re.compile(
            r"\b(?:lo[\s-]?fi|sleep|relaxation|calm|rain|meditation|"
            r"concentration|ambient|synthwave|meditative|focus)\b", re.I)
        return [
            {"id": row.get("id"), "title": row.get("title", ""),
             "cover": row.get("picture_big") or row.get("picture_medium", ""),
             "count": row.get("nb_tracks", 0)}
            for row in playlists
            if row.get("id") and not excluded.search(row.get("title") or "")
        ][:limit]

    def genre_playlists(self, genre_id, limit=30):
        """Deezer's most popular playlists in one chart genre."""
        payload = self._fetch("/chart/%d/playlists?limit=100" % int(genre_id))
        rows = (payload or {}).get("data", [])
        if not rows:
            editorial = self._fetch("/editorial/%d/charts" % int(genre_id))
            rows = ((editorial or {}).get("playlists") or {}).get("data", [])
        excluded = re.compile(
            r"\b(?:lo[\s-]?fi|sleep|relaxation|calm|rain|meditation|"
            r"concentration|ambient|synthwave|meditative|focus)\b", re.I)
        return [
            {"id": row.get("id"), "title": row.get("title", ""),
             "cover": row.get("picture_big") or row.get("picture_medium", ""),
             "count": row.get("nb_tracks", 0)}
            for row in rows
            if isinstance(row, dict) and row.get("id")
            and not excluded.search(row.get("title") or "")
        ][:limit]

    def playlist_tracks(self, playlist_id, limit=100):
        payload = self._fetch("/playlist/%s/tracks?limit=%d"
                              % (playlist_id, int(limit)))
        return self._tracks(payload)

    # -- artist ------------------------------------------------------------ #

    def find_artist(self, name):
        """Resolve an artist name to a Deezer artist id. Returns None on miss."""
        payload = self._fetch("/search/artist?q=%s&limit=1" % quote_plus(name))
        if not payload:
            return None
        rows = payload.get("data", [])
        if not rows:
            return None
        return {
            "id":      rows[0].get("id"),
            "name":    rows[0].get("name", name),
            "picture": rows[0].get("picture_big")
                       or rows[0].get("picture_medium", ""),
        }

    def artist_radio(self, artist_id, limit=50):
        """
        Deezer's own radio for an artist — a mix of that artist and
        stylistically adjacent ones. Capped at 100 server-side.
        """
        payload = self._fetch("/artist/%s/radio?limit=%d"
                              % (artist_id, max(1, min(int(limit), 100))))
        return self._tracks(payload)

    def artist_top(self, artist_id, limit=50):
        payload = self._fetch("/artist/%s/top?limit=%d"
                              % (artist_id, max(1, min(int(limit), 100))))
        return self._tracks(payload)

    def search_tracks(self, artist, title, limit=8):
        """Find track metadata used to enrich album-less playlist rows."""
        count = max(1, min(int(limit), 25))
        artist = artist or ""
        title = title or ""
        # Deezer's fielded query avoids popular cover versions displacing the
        # original recording from a short result set. Keep a broad search as a
        # fallback for punctuation and unusual provider credits.
        queries = [
            ('artist:"%s" track:"%s"' % (artist, title), "fielded-v2"),
            ("%s %s" % (artist, title), "broad-v2"),
        ]
        results = []
        seen = set()
        for query, strategy in queries:
            payload = self._fetch(
                "/search?q=%s&limit=%d" % (quote_plus(query.strip()), count),
                cache_key="deezer:track-search:%s:%s:%s:%d" % (
                    strategy, artist.casefold(), title.casefold(), count))
            for row in self._tracks(payload):
                key = row.get("id") or (
                    row.get("artist"), row.get("title"), row.get("album"))
                if key in seen:
                    continue
                seen.add(key)
                results.append(row)
            # A fielded Deezer hit already has the artist/title precision the
            # broad fallback exists to recover. Avoid doubling network work
            # while enriching a full radio station.
            if results and strategy == "fielded-v2":
                break
        return results


# --------------------------------------------------------------------------- #
# Last.fm
# --------------------------------------------------------------------------- #

class LastFmProvider(object):
    """
    Last.fm charts and similarity. Requires a free API key.

    Chosen over Deezer for radio because artist.getSimilar exposes the
    similarity graph directly, which makes the mix controllable — how many
    neighbours, how deep into each one — instead of being a black box.
    """

    name = "Last.fm"

    def __init__(self, api_key, cache, timeout=15, interactive=True):
        self.api_key = (api_key or "").strip()
        self.cache = cache
        self.timeout = timeout
        self.interactive = interactive
        self.health = ProviderHealth(self.name)
        self._cached_notice_shown = False

    def _stale_or_raise(self, key, error):
        stale = self.cache.get_stale(key)
        if stale is None:
            raise error
        if self.interactive and not self._cached_notice_shown:
            xbmcgui.Dialog().notification(
                "Last.fm unavailable", "Showing cached results",
                xbmcgui.NOTIFICATION_WARNING, 4500)
            self._cached_notice_shown = True
        xbmc.log("[rotation] Last.fm unavailable; using stale cache for %s" % key,
                 xbmc.LOGWARNING)
        return stale

    @property
    def enabled(self):
        return bool(self.api_key)

    def _call(self, method, **params):
        if not self.enabled:
            return None

        # Internal cache discriminator; never send it to Last.fm. Listening
        # history needs a much shorter freshness window than charts.
        cache_suffix = str(params.pop("_cache_suffix", "") or "")

        parts = ["method=" + method, "api_key=" + self.api_key, "format=json"]
        for key, value in sorted(params.items()):
            if value in (None, ""):
                continue
            parts.append("%s=%s" % (key, quote_plus(str(value))))
        url = LASTFM_API + "?" + "&".join(parts)

        # Cache key omits the API key so a key change doesn't orphan the cache.
        cache_key = url.replace(self.api_key, "KEY") + cache_suffix

        cached = self.cache.get(cache_key)
        if cached is not None:
            return cached

        circuit = self.health.circuit_error()
        if circuit:
            return self._stale_or_raise(cache_key, circuit)
        try:
            data = _get_json(url, self.name, self.health, timeout=self.timeout)
        except ProviderError as exc:
            return self._stale_or_raise(cache_key, exc)
        if not data:
            raise self.health.failure("malformed", "Empty JSON response")
        if "error" in data:
            code = str(data.get("error") or "")
            category = ("configuration" if code in ("4", "9", "10", "26")
                        else "rate_limited" if code == "29" else "service_error")
            error = self.health.failure(category, data.get("message", ""))
            return self._stale_or_raise(cache_key, error)

        self.cache.put(cache_key, data)
        return data

    @staticmethod
    def _tracks(rows):
        entries = []
        for position, row in enumerate(rows or [], 1):
            if not isinstance(row, dict):
                continue
            artist = row.get("artist")
            if isinstance(artist, dict):
                artist = artist.get("name") or artist.get("#text", "")
            title = row.get("name", "")
            if not artist or not title:
                continue

            cover = ""
            for image in row.get("image", []) or []:
                if image.get("size") in ("extralarge", "large") and image.get("#text"):
                    cover = image["#text"]

            # Last.fm uses this image hash for its white/grey star "no
            # artwork" tile.  Treating it as a real cover prevents Rotation
            # from applying its own fallback and from enriching the row with
            # actual album artwork later.
            if "2a96cbd8b46e442fc41c2b86b821562f" in cover.lower():
                cover = ""

            entries.append({
                "artist":   artist,
                "title":    title,
                "album":    "",
                "duration": int(row.get("duration") or 0),
                "cover":    cover,
                "position": position,
            })
        return entries

    def chart_tracks(self, limit=100):
        data = self._call("chart.gettoptracks", limit=limit)
        if not data:
            return []
        return self._tracks((data.get("tracks") or {}).get("track", []))

    def track_info(self, artist, title):
        """Return album metadata for one recording when Last.fm knows it."""
        data = self._call("track.getInfo", artist=artist, track=title,
                          autocorrect=1)
        track = (data or {}).get("track") or {}
        album = track.get("album") or {}
        album_title = album.get("title") or ""
        if not album_title:
            return None
        credited = track.get("artist") or {}
        credited = (credited.get("name") if isinstance(credited, dict)
                    else credited)
        cover = ""
        for image in album.get("image", []) or []:
            if image.get("size") in ("extralarge", "large") and image.get("#text"):
                cover = image["#text"]
        return {
            "artist": credited or artist,
            "title": track.get("name") or title,
            "album": album_title,
            "cover": cover,
            "duration": int(track.get("duration") or 0) // 1000,
        }

    @staticmethod
    def _artists(rows):
        artists = []
        for row in rows or []:
            if not isinstance(row, dict) or not row.get("name"):
                continue
            picture = ""
            for image in row.get("image", []) or []:
                if image.get("size") in ("extralarge", "large") and image.get("#text"):
                    picture = image["#text"]
            if "2a96cbd8b46e442fc41c2b86b821562f" in picture.lower():
                picture = ""
            artists.append({"id": "", "name": row["name"], "picture": picture,
                            "playcount": int(row.get("playcount") or 0)})
        return artists

    def chart_artists(self, limit=50):
        data = self._call("chart.gettopartists", limit=limit)
        if not data:
            return []
        return self._artists((data.get("artists") or {}).get("artist", []))

    def user_top_tracks(self, username, period="3month", limit=100):
        data = self._call("user.gettoptracks", user=username,
                          period=period, limit=limit)
        return self._tracks((data or {}).get("toptracks", {}).get("track", []))

    def user_recent_tracks(self, username, limit=100):
        quarter_hour = int(time.time() // (15 * 60))
        data = self._call("user.getrecenttracks", user=username, limit=limit,
                          _cache_suffix=":%d" % quarter_hour)
        rows = (data or {}).get("recenttracks", {}).get("track", [])
        # Last.fm includes the current now-playing row. It is useful in the
        # history list but must still be de-duplicated like completed plays.
        return _dedupe_tracks(self._tracks(rows), limit=limit)

    def user_top_artists(self, username, period="3month", limit=50):
        # Fetch extra rows so collaboration duplicates do not shrink the list.
        data = self._call("user.gettopartists", user=username,
                          period=period, limit=min(1000, max(100, limit * 3)))
        return primary_top_artists(
            self._artists((data or {}).get("topartists", {}).get("artist", [])), limit)

    def similar_tracks(self, artist, title, limit=20):
        data = self._call("track.getsimilar", artist=artist, track=title,
                          autocorrect=1, limit=limit)
        return self._tracks((data or {}).get("similartracks", {}).get("track", []))

    def geo_artists(self, country="united states", limit=50):
        data = self._call("geo.gettopartists", country=country, limit=limit)
        if not data:
            return []
        return self._artists((data.get("topartists") or {}).get("artist", []))

    def tag_artists(self, tag, limit=50):
        data = self._call("tag.gettopartists", tag=tag, limit=limit)
        if not data:
            return []
        return self._artists((data.get("topartists") or {}).get("artist", []))

    def geo_tracks(self, country="united states", limit=100):
        data = self._call("geo.gettoptracks", country=country, limit=limit)
        if not data:
            return []
        return self._tracks((data.get("tracks") or {}).get("track", []))

    def tag_tracks(self, tag, limit=100):
        data = self._call("tag.gettoptracks", tag=tag, limit=limit)
        if not data:
            return []
        return self._tracks((data.get("tracks") or {}).get("track", []))

    def artist_info(self, artist):
        """Biography for the exact requested artist, with cached API results."""
        payload = self._call("artist.getInfo", artist=artist, autocorrect=0)
        info = (payload or {}).get("artist") or {}
        # Do not turn an information request into a different artist's bio.
        if str(info.get("name") or "").strip().casefold() != str(artist).strip().casefold():
            return {}
        return info

    def artist_top(self, artist, limit=50):
        data = self._call("artist.gettoptracks", artist=artist, limit=limit)
        if not data:
            return []
        return self._tracks((data.get("toptracks") or {}).get("track", []))

    def similar_artists(self, artist, limit=20):
        data = self._call("artist.getsimilar", artist=artist, limit=limit)
        if not data:
            return []
        rows = (data.get("similarartists") or {}).get("artist", [])
        return [r.get("name") for r in rows if r.get("name")]

    def artist_radio(self, artist, limit=50, neighbours=12, per_artist=4,
                     seed_share=0.25, on_progress=None):
        """
        Build an artist radio mix.

        Roughly seed_share of the result is the seed artist's own top tracks;
        the rest is drawn round-robin from its nearest neighbours, so the mix
        widens gradually instead of front-loading one adjacent artist. The
        round-robin also means a neighbour with a thin catalogue doesn't
        starve the rest.
        """
        limit = max(1, int(limit))
        seed_count = max(1, int(limit * seed_share))

        mix = _dedupe_tracks(self.artist_top(artist, limit=seed_count))
        if on_progress:
            on_progress(mix[:limit])

        names = self.similar_artists(artist, limit=neighbours)
        if not names:
            return mix[:limit]

        pools = []
        for name in names:
            tracks = self.artist_top(name, limit=per_artist)
            if tracks:
                pools.append(tracks)
                if on_progress:
                    on_progress(_dedupe_tracks(
                        mix + [song for pool in pools for song in pool], limit))
            # Stay clear of the documented 5 req/sec average.
            time.sleep(0.22)
            if sum(len(p) for p in pools) >= limit * 2:
                break

        # Round-robin across neighbour pools.
        seen_tracks = {
            track_identity_key(row.get("artist", ""), row.get("title", ""))
            for row in mix}
        depth = 0
        while len(mix) < limit and pools:
            added = False
            advanced = False
            for pool in pools:
                if depth < len(pool):
                    advanced = True
                    candidate = pool[depth]
                    key = track_identity_key(candidate.get("artist", ""),
                                             candidate.get("title", ""))
                    if key not in seen_tracks:
                        mix.append(candidate)
                        seen_tracks.add(key)
                        added = True
                    if len(mix) >= limit:
                        break
            if not advanced:
                break
            depth += 1

        # Keep the seed artist at the front, shuffle the rest so repeat plays
        # of the same station don't open identically.
        head, tail = mix[:seed_count], mix[seed_count:]
        random.shuffle(tail)

        result = _dedupe_tracks(head + tail, limit)
        for position, entry in enumerate(result, 1):
            entry["position"] = position
        return result[:limit]


# --------------------------------------------------------------------------- #
# Facade
# --------------------------------------------------------------------------- #

class PlaylistSource(object):
    """
    Chooses between providers and exposes one interface to the routes.

    Last.fm is preferred for charts and radio when a key is configured;
    Deezer handles everything else and covers the no-key case.
    """

    def __init__(self, cache_dir, ttl_hours=12, timeout=15, lastfm_key="",
                 prefer_lastfm=True, country="united states", interactive=True):
        self.cache = JsonCache(cache_dir, ttl_hours=ttl_hours)
        self.deezer = DeezerProvider(
            self.cache, timeout=timeout, interactive=interactive)
        self.lastfm = LastFmProvider(
            lastfm_key, self.cache, timeout=timeout, interactive=interactive)
        self.prefer_lastfm = prefer_lastfm and self.lastfm.enabled
        self.country = country
        self.interactive = interactive
        self._fallback_notice_shown = False
        self.last_failure = None

    def _lastfm_or_deezer(self, lastfm_call, deezer_call):
        failure = None
        try:
            rows = lastfm_call()
            if rows:
                return rows
        except ProviderError as exc:
            self.last_failure = exc
            failure = exc
            xbmc.log("[rotation] %s Falling back to Deezer." % exc,
                     xbmc.LOGWARNING)
        try:
            rows = deezer_call()
        except ProviderError as exc:
            if failure:
                raise ProviderError(
                    "Last.fm and Deezer", "unreachable",
                    "%s; %s" % (failure.detail, exc.detail))
            raise
        if failure and self.interactive and not self._fallback_notice_shown:
            xbmcgui.Dialog().notification(
                "Last.fm unavailable", "Using Deezer results",
                xbmcgui.NOTIFICATION_WARNING, 4500)
            self._fallback_notice_shown = True
        return rows

    @property
    def radio_backend(self):
        return "Last.fm" if self.prefer_lastfm else "Deezer"

    def top_tracks(self, limit=100):
        if self.prefer_lastfm:
            return self._lastfm_or_deezer(
                lambda: self.lastfm.chart_tracks(limit=limit),
                lambda: self.deezer.chart_tracks(limit=limit))
        return self.deezer.chart_tracks(limit=limit)

    def top_artists(self, limit=50):
        if self.prefer_lastfm:
            return self._lastfm_or_deezer(
                lambda: self.lastfm.chart_artists(limit=limit),
                lambda: self.deezer.chart_artists(limit=limit))
        return self.deezer.chart_artists(limit=limit)

    def country_artists(self, limit=50):
        if self.lastfm.enabled:
            return self._lastfm_or_deezer(
                lambda: self.lastfm.geo_artists(
                    country=self.country, limit=limit),
                lambda: self.deezer.chart_artists(limit=limit))
        return self.deezer.chart_artists(limit=limit)

    def genre_artists(self, genre_id, genre_name="", limit=50):
        if self.lastfm.enabled and genre_name:
            return self._lastfm_or_deezer(
                lambda: self.lastfm.tag_artists(genre_name, limit=limit),
                lambda: self.deezer.chart_artists(
                    limit=limit, genre_id=genre_id))
        return self.deezer.chart_artists(limit=limit, genre_id=genre_id)

    def country_tracks(self, limit=100):
        if self.lastfm.enabled:
            return self._lastfm_or_deezer(
                lambda: self.lastfm.geo_tracks(
                    country=self.country, limit=limit),
                lambda: self.deezer.chart_tracks(limit=limit))
        return self.deezer.chart_tracks(limit=limit)

    def genre_tracks(self, genre_id, limit=100):
        return self.deezer.chart_tracks(limit=limit, genre_id=genre_id)

    def tag_tracks(self, tag, limit=100):
        if self.lastfm.enabled:
            return self.lastfm.tag_tracks(tag, limit=limit)
        return []

    def genres(self):
        return [g for g in self.deezer.genres() if g.get("id") not in (0, None)]

    def editorial_playlists(self, limit=100):
        return self.deezer.editorial_playlists(limit=limit)

    def fresh_albums(self, limit=100):
        return self.deezer.fresh_albums(limit=limit)

    def genre_playlists(self, genre_id, limit=30):
        return self.deezer.genre_playlists(genre_id, limit=limit)

    def playlist_tracks(self, playlist_id, limit=100):
        return self.deezer.playlist_tracks(playlist_id, limit=limit)

    def artist_radio(self, artist_name, limit=50, on_progress=None):
        """Artist radio by name. Falls back to Deezer if Last.fm is unset."""
        failure = None
        if self.prefer_lastfm:
            try:
                tracks = self.lastfm.artist_radio(
                    artist_name, limit=limit, on_progress=on_progress)
                if tracks:
                    return tracks
            except ProviderError as exc:
                self.last_failure = exc
                failure = exc
                xbmc.log("[rotation] %s Building radio with Deezer." % exc,
                         xbmc.LOGWARNING)

        try:
            found = self.deezer.find_artist(artist_name)
        except ProviderError as exc:
            if failure:
                raise ProviderError(
                    "Last.fm and Deezer", "unreachable",
                    "%s; %s" % (failure.detail, exc.detail))
            raise
        if not found:
            return []
        tracks = self.deezer.artist_radio(found["id"], limit=limit)
        if not tracks:
            tracks = self.deezer.artist_top(found["id"], limit=limit)
        if failure and self.interactive and not self._fallback_notice_shown:
            xbmcgui.Dialog().notification(
                "Last.fm unavailable", "Building radio with Deezer",
                xbmcgui.NOTIFICATION_WARNING, 4500)
            self._fallback_notice_shown = True
        return _dedupe_tracks(tracks, limit)

    def related_artists(self, artist_name, limit=60, max_lookups=8):
        """Return a widening artist neighborhood for local-library radio.

        Last.fm exposes the relationship graph directly, so breadth-first
        expansion can move into second-degree neighbors when the first ring
        is thin in the user's library. Deezer's radio artists provide the
        no-key fallback. This method returns names only; callers decide which
        ones actually exist locally.
        """
        limit = max(1, int(limit))
        seen = {artist_name.casefold()}
        related = []
        failure = None

        if self.prefer_lastfm:
            try:
                queue = [(artist_name, 0)]
                lookups = 0
                while queue and len(related) < limit and lookups < max_lookups:
                    current, depth = queue.pop(0)
                    names = self.lastfm.similar_artists(current, limit=30)
                    lookups += 1
                    for name in names:
                        key = name.casefold()
                        if not key or key in seen:
                            continue
                        seen.add(key)
                        related.append(name)
                        if depth < 1:
                            queue.append((name, depth + 1))
                        if len(related) >= limit:
                            break
                    if queue and lookups < max_lookups:
                        time.sleep(0.22)
                return related
            except ProviderError as exc:
                self.last_failure = exc
                failure = exc
                xbmc.log("[rotation] %s Using Deezer similar artists." % exc,
                         xbmc.LOGWARNING)

        try:
            found = self.deezer.find_artist(artist_name)
        except ProviderError as exc:
            if failure:
                raise ProviderError(
                    "Last.fm and Deezer", "unreachable",
                    "%s; %s" % (failure.detail, exc.detail))
            raise
        if not found:
            return []
        for track in self.deezer.artist_radio(found["id"], limit=100):
            name = track.get("artist", "")
            key = name.casefold()
            if name and key not in seen:
                seen.add(key)
                related.append(name)
                if len(related) >= limit:
                    break
        if failure and self.interactive and not self._fallback_notice_shown:
            xbmcgui.Dialog().notification(
                "Last.fm unavailable", "Using Deezer similar artists",
                xbmcgui.NOTIFICATION_WARNING, 4500)
            self._fallback_notice_shown = True
        return related

    def clear_cache(self):
        return self.cache.clear()
