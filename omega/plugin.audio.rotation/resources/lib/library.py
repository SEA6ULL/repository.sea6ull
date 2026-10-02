# -*- coding: utf-8 -*-
#
# Rotation — resources/lib/library.py
#
# Kodi music library index and track resolver.
#
# Playlist providers hand back (artist, title) pairs. This module turns those
# into playable entries from the local Kodi music database. Everything here
# goes through JSON-RPC, which Kodi executes in-process — there is no socket,
# no network, and no dependency on the remote JSON-RPC server being enabled.
# The library it sees is whatever the Kodi instance running this addon has
# scanned.
#
# Matching is the hard part. Chart APIs return commercial metadata:
#   "Elton John"  /  "Rocket Man (I Think It's Going To Be A Long, Long Time)"
# while a ripped library often has:
#   "Elton John"  /  "Rocket Man"
# and a remaster reissue has:
#   "Elton John"  /  "Rocket Man - 2017 Remaster"
#
# So the resolver normalises aggressively, indexes several key forms per song,
# and falls back to difflib scoring inside an artist bucket. Every match
# records how it was made so a bad threshold can be diagnosed from the log
# rather than guessed at.
#
# Logging note: xbmc.log() only — Python's standard logging module produces
# zero output in Kodi addon context.

import difflib
import json
import xbmcaddon
import os
import re
import time
import unicodedata

import xbmc

# --------------------------------------------------------------------------- #
# Normalisation
# --------------------------------------------------------------------------- #

# Bracketed groups: "(Remastered)", "[Explicit]", "{Live}". Removed wholesale
# from titles. This is safe far more often than not, but it does damage a
# small number of legitimate titles where the bracket is part of the name
# ("Sgt. Pepper's Lonely Hearts Club Band (Reprise)"). The resolver keeps an
# unstripped key as well so those still match exactly.
_RE_BRACKETS = re.compile(r"[\(\[\{][^\)\]\}]*[\)\]\}]")

# Trailing " - Something Version" suffixes. Anchored to a known vocabulary so
# that "Hall - Part 2" or "Us - And Them" survive intact.
_RE_DASH_SUFFIX = re.compile(
    r"\s+-\s+.*\b("
    r"remaster(ed)?|re-?master(ed)?|"
    r"radio\s+edit|single\s+(version|edit|mix)|album\s+version|"
    r"mono|stereo|live|acoustic|instrumental|demo|"
    r"deluxe|explicit|clean|edited|"
    r"bonus\s+track|re-?record(ed)?|"
    r"anniversary|edition|version|remix|mix"
    r")\b.*$",
    re.IGNORECASE,
)

# "feat. X", "ft X", "featuring X", "with X" — everything from the marker on.
_RE_FEAT = re.compile(
    r"\s*[\(\[]?\s*(feat\.?|ft\.?|featuring|w/|with)(?:\s+|$).*$",
    re.IGNORECASE,
)

_RE_PUNCT = re.compile(r"[^\w\s]", re.UNICODE)
_RE_SPACE = re.compile(r"\s+")

# Artist separators. Applied only to derive an *additional* alias key — the
# full string is always indexed too, so "Earth, Wind & Fire" and
# "Simon & Garfunkel" still resolve on their full names.
_RE_ARTIST_SPLIT = re.compile(
    r"\s*(?:,|&|/|\bfeat\.?\b|\bft\.?\b|\bfeaturing\b|\bvs\.?\b|\bwith\b|\bx\b)\s*",
    re.IGNORECASE,
)

# Albums we never want a chart track resolving to. A karaoke backing track
# technically matches "artist + title" perfectly and will win an exact-key
# lookup, which makes for a memorable first test of the feature.
_RE_JUNK_ALBUM = re.compile(
    r"\b(karaoke|tribute|made\s+popular\s+by|in\s+the\s+style\s+of|"
    r"as\s+made\s+famous\s+by|backing\s+track|cover\s+version)\b",
    re.IGNORECASE,
)


def _strip_accents(text):
    """Fold accented characters to ASCII. 'Beyoncé' -> 'beyonce'."""
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def _basic(text):
    """Lowercase, de-accent, strip punctuation, collapse whitespace."""
    if not text:
        return ""
    text = _strip_accents(str(text)).lower()
    # Normalise the various dash characters to a plain hyphen before the
    # suffix regex runs — chart APIs use en-dashes freely.
    text = text.replace("\u2013", "-").replace("\u2014", "-").replace("\u2212", "-")
    text = _RE_PUNCT.sub(" ", text)
    return _RE_SPACE.sub(" ", text).strip()


def norm_title(title, aggressive=True):
    """
    Normalise a track title for matching.

    aggressive=True strips bracketed groups, version suffixes and featured
    artists. aggressive=False only does case/accent/punctuation folding, which
    preserves titles where the brackets are load-bearing.
    """
    if not title:
        return ""
    text = str(title).replace("\u2013", "-").replace("\u2014", "-")
    if aggressive:
        text = _RE_DASH_SUFFIX.sub("", text)
        text = _RE_BRACKETS.sub(" ", text)
        text = _RE_FEAT.sub("", text)
    return _basic(text)


def norm_artist(artist):
    """Normalise a full artist string."""
    if not artist:
        return ""
    return _basic(_RE_FEAT.sub("", str(artist)))


_DISTINCT_TRACK_VERSION = re.compile(
    r"\b(live|acoustic|remix|mix|cover|demo|instrumental|unplugged|session|"
    r"solo|reprise|extended)\b", re.I)


def track_identity_key(artist, title):
    """Return a practical identity for playlist/radio deduplication.

    Provider charts frequently list the same recording with punctuation,
    featured-credit, remaster, clean, or explicit-label differences.  Those
    collapse to one key.  Performance-changing labels such as live, acoustic,
    remix, cover, solo, and reprise remain part of the key so legitimate
    alternatives are retained.
    """
    raw = str(title or "")
    if _DISTINCT_TRACK_VERSION.search(raw):
        # Featured credits do not make a different recording, but preserve the
        # rest of a meaningful version label.
        title_key = norm_title(_RE_FEAT.sub("", raw), False)
    else:
        title_key = norm_title(raw, True)
    # Last.fm commonly splits one title into "Wise Man" and "Wiseman".  Once
    # punctuation has been folded, whitespace is not identity-bearing here.
    title_key = title_key.replace(" ", "")
    return norm_artist(artist), title_key


def primary_artist(artist):
    """
    Normalised form of the first artist in a multi-artist credit.

    'Mark Ronson feat. Bruno Mars' -> 'mark ronson'
    Used as a secondary index key only; the full credit is indexed separately.
    """
    if not artist:
        return ""
    parts = _RE_ARTIST_SPLIT.split(str(artist), maxsplit=1)
    return _basic(parts[0]) if parts else ""


def _similar(a, b):
    """0.0-1.0 similarity ratio between two normalised strings."""
    if not a or not b:
        return 0.0
    return difflib.SequenceMatcher(None, a, b).ratio()


# --------------------------------------------------------------------------- #
# JSON-RPC
# --------------------------------------------------------------------------- #

def rpc(method, params=None):
    """
    Execute a JSON-RPC call against the running Kodi instance.

    Returns the 'result' object, or None on error. Errors are logged rather
    than raised — a failed library query should degrade to an empty playlist
    section, not a Python traceback in the user's face.
    """
    if method.startswith("AudioLibrary.") and not library_access_enabled():
        return {"limits": {"start": 0, "end": 0, "total": 0}}
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": method,
        "params": params or {},
    }
    try:
        raw = xbmc.executeJSONRPC(json.dumps(payload))
        data = json.loads(raw)
    except Exception as exc:                                  # noqa: BLE001
        xbmc.log("[rotation] rpc %s failed: %s" % (method, exc), xbmc.LOGWARNING)
        return None

    if "error" in data:
        xbmc.log(
            "[rotation] rpc %s error: %s" % (method, data["error"]),
            xbmc.LOGWARNING,
        )
        return None
    return data.get("result")


def library_song_count():
    """Total songs in the library. Cheap — used as a cache invalidation key."""
    result = rpc("AudioLibrary.GetSongs", {"limits": {"start": 0, "end": 1}})
    if not result:
        return 0
    return int(result.get("limits", {}).get("total", 0))


def library_access_enabled():
    """Whether Rotation may query Kodi's local music database."""
    try:
        return xbmcaddon.Addon().getSetting("use_kodi_library") != "false"
    except Exception:  # noqa: BLE001 - settings must never break browsing
        return True


def library_signature():
    """Cheap fingerprint used to notice Kodi library changes automatically."""
    if not library_access_enabled():
        return {"count": 0, "latest": "", "disabled": True}
    result = rpc("AudioLibrary.GetSongs", {
        "properties": ["dateadded"],
        "limits": {"start": 0, "end": 1},
        "sort": {"method": "dateadded", "order": "descending"},
    })
    if not result:
        return None
    songs = result.get("songs", []) or []
    return {
        "count": int(result.get("limits", {}).get("total", 0)),
        "latest": songs[0].get("dateadded", "") if songs else "",
    }


def library_artists():
    """
    All album artists in the library, sorted by name.

    Returns a list of {"artistid": int, "artist": str}.
    """
    result = rpc("AudioLibrary.GetArtists", {
        "albumartistsonly": True,
        "sort": {"method": "artist", "order": "ascending"},
    })
    if not result:
        return []
    return result.get("artists", []) or []


# Artist/album artwork deliberately remains separate from LibraryIndex's song
# cache. Kodi already owns this metadata and returns it in one JSON-RPC call
# per table; copying it into Rotation's persistent song index would create
# another cache that could become stale when artwork changes.
#
# Both tables are read in full and held for LIBRARY_ART_TTL seconds. That is
# one in-process call per table every few minutes, against one call per row
# for a filtered lookup - which on a 40-tile grid is the slower option.
LIBRARY_ART_TTL = 300
_EMPTY_ARTIST = {"thumb": "", "fanart": "", "clearlogo": "", "mbid": ""}
_EMPTY_ALBUM = {"thumb": "", "fanart": "", "mbid": "", "artist_mbid": ""}
_ARTIST_ART = {"checked": 0.0, "items": {}}
_ALBUM_ART = {"checked": 0.0, "exact": {}, "by_title": {}}


def _first_mbid(value):
    """Kodi returns MusicBrainz IDs as a list on v12+ and a string before."""
    if isinstance(value, (list, tuple)):
        values = [v for v in value if v]
        # A multi-artist credit has several IDs; none of them is "the" artist.
        return values[0] if len(values) == 1 else ""
    return value or ""


def _rpc_with_fallbacks(method, base, property_sets):
    """Try progressively smaller property sets for older JSON-RPC schemas."""
    for properties in property_sets:
        params = dict(base, properties=properties)
        result = rpc(method, params)
        if result is not None:
            return result
    return None


def invalidate_library_art():
    """Force the next artwork read to re-query Kodi (e.g. after a scan)."""
    _ARTIST_ART["checked"] = 0.0
    _ALBUM_ART["checked"] = 0.0


def library_artist_art(artist):
    """Return Kodi's thumbnail, fanart, clearlogo and MBID for an artist.

    Every library artist is considered, not only album artists: a featured
    or compilation-only artist that Kodi has scraped art for must not fall
    through to an online lookup just because they never headed an album.
    Matching is on the full normalised credit - a credit such as "Lana Del
    Rey, Father John Misty" must not silently inherit Lana Del Rey's solo
    fanart. Kodi paths are returned unchanged.
    """
    key = norm_artist(artist)
    if not key:
        return dict(_EMPTY_ARTIST)

    now = time.time()
    if now - _ARTIST_ART["checked"] >= LIBRARY_ART_TTL:
        base = {"albumartistsonly": False,
                "limits": {"start": 0, "end": 100000}}
        result = _rpc_with_fallbacks("AudioLibrary.GetArtists", base, (
            ["thumbnail", "fanart", "art", "musicbrainzartistid"],
            ["thumbnail", "fanart", "art"],
            ["thumbnail", "fanart"],
        ))
        if result is not None:
            items = {}
            for row in result.get("artists", []) or []:
                normalized = norm_artist(row.get("artist", ""))
                if not normalized:
                    continue
                art = row.get("art") or {}
                entry = {
                    "thumb": row.get("thumbnail", "") or art.get("thumb", "") or "",
                    "fanart": row.get("fanart", "") or art.get("fanart", "") or "",
                    "clearlogo": art.get("clearlogo") or art.get("logo") or "",
                    "mbid": _first_mbid(row.get("musicbrainzartistid")),
                }
                # Two library rows can normalise alike ("AC/DC", "AC DC");
                # keep whichever carries more artwork.
                current = items.get(normalized)
                if current is None or (sum(map(bool, entry.values())) >
                                       sum(map(bool, current.values()))):
                    items[normalized] = entry
            _ARTIST_ART["items"] = items
            xbmc.log("[rotation] Kodi library art: %d artist(s)"
                     % len(items), xbmc.LOGINFO)
        _ARTIST_ART["checked"] = now

    return dict(_ARTIST_ART["items"].get(key, _EMPTY_ARTIST))


def library_artist_mbid(artist):
    """MusicBrainz artist ID from Kodi's own tags, or ""."""
    return library_artist_art(artist).get("mbid", "")


def _load_library_albums():
    now = time.time()
    if now - _ALBUM_ART["checked"] < LIBRARY_ART_TTL:
        return
    base = {"limits": {"start": 0, "end": 100000}}
    result = _rpc_with_fallbacks("AudioLibrary.GetAlbums", base, (
        ["title", "artist", "thumbnail", "fanart", "art",
         "musicbrainzreleasegroupid", "musicbrainzalbumartistid"],
        ["title", "artist", "thumbnail", "fanart", "art"],
        ["title", "artist", "thumbnail", "fanart"],
    ))
    if result is not None:
        exact, by_title = {}, {}
        for row in result.get("albums", []) or []:
            art = row.get("art") or {}
            entry = {
                "thumb": row.get("thumbnail", "") or art.get("thumb", "") or "",
                "fanart": row.get("fanart", "") or "",
                "mbid": row.get("musicbrainzreleasegroupid", "") or "",
                "artist_mbid": _first_mbid(row.get("musicbrainzalbumartistid")),
            }
            artists = row.get("artist") or []
            if isinstance(artists, str):
                artists = [artists]
            artist_keys = {norm_artist(a) for a in artists if a}
            if len(artists) > 1:
                artist_keys.add(norm_artist(", ".join(artists)))
            for title_key in {norm_title(row.get("title", ""), False),
                              norm_title(row.get("title", ""), True)}:
                if not title_key:
                    continue
                for artist_key in artist_keys or {""}:
                    current = exact.get((title_key, artist_key))
                    if current is None or (entry["thumb"] and not current["thumb"]):
                        exact[(title_key, artist_key)] = entry
                bucket = by_title.setdefault(title_key, {"artists": set(),
                                                         "entry": entry})
                bucket["artists"].update(artist_keys)
        _ALBUM_ART["exact"] = exact
        _ALBUM_ART["by_title"] = by_title
        xbmc.log("[rotation] Kodi library art: %d album key(s)" % len(exact),
                 xbmc.LOGINFO)
    _ALBUM_ART["checked"] = now


def library_album_art(artist, album):
    """Return Kodi's own album thumb/fanart/release-group MBID, or blanks.

    The album artist must match. A title-only match is accepted when Kodi
    associates that title with exactly one album artist, which covers
    soundtrack and "Various Artists" credit variations without claiming
    every self-titled album is locally owned.
    """
    if not album:
        return dict(_EMPTY_ALBUM)
    _load_library_albums()
    artist_key = norm_artist(art_artist_name(artist) or artist)
    full_key = norm_artist(artist)
    for title_key in (norm_title(album, False), norm_title(album, True)):
        if not title_key:
            continue
        for key in (artist_key, full_key):
            hit = _ALBUM_ART["exact"].get((title_key, key))
            if hit:
                return dict(hit)
        bucket = _ALBUM_ART["by_title"].get(title_key)
        if bucket and len(bucket["artists"]) <= 1:
            return dict(bucket["entry"])
    return dict(_EMPTY_ALBUM)


def library_artist_fanart(artist):
    """Return Kodi's designated fanart for an exact library album artist.

    The full normalized credit must match.  We intentionally do not use
    primary-artist or fuzzy matching here: a credit such as "Lana Del Rey,
    Father John Misty" must not silently inherit Lana Del Rey's solo fanart.
    Kodi paths (including image://, special://, SMB and NFS URLs) are returned
    unchanged so Kodi remains responsible for resolving its own artwork.
    """
    return library_artist_art(artist).get("fanart", "")


def library_artist_logo(artist):
    """Return Kodi's clearlogo for an exact library album artist."""
    return library_artist_art(artist).get("clearlogo", "")


# --------------------------------------------------------------------------- #
# Index
# --------------------------------------------------------------------------- #

# Songs are pulled in batches. A single GetSongs call over a 60k-track library
# builds one very large JSON string in memory on both sides of the bridge;
# batching keeps the peak allocation bounded and lets a slow call be seen in
# the log as it progresses.
_BATCH = 4000

_PROPERTIES = [
    "title", "artist", "albumartist", "album", "duration",
    "file", "year", "genre", "track", "thumbnail",
    "dateadded", "lastplayed", "playcount",
]

_CACHE_SCHEMA = 4


class LibraryIndex(object):
    """
    In-memory index of the Kodi music library, persisted to disk.

    Build cost is proportional to library size (a few seconds for tens of
    thousands of tracks), so the built index is cached as JSON and reused
    until either the TTL expires or the library's song count changes.
    """

    def __init__(self, cache_dir, ttl_hours=24):
        self.cache_dir = cache_dir
        self.ttl = max(0, int(ttl_hours)) * 3600
        self.cache_file = os.path.join(cache_dir, "library_index.json")

        self.songs = []          # list of song dicts
        self.by_exact = {}       # (n_artist, n_title)      -> [idx, ...]
        self.by_primary = {}     # (primary_artist, n_title) -> [idx, ...]
        self.by_title = {}       # n_title                   -> [idx, ...]
        self.by_artist = {}      # n_artist / primary        -> [idx, ...]
        self._ready = False
        self._signature = None
        self._last_validation = 0

    # -- construction ------------------------------------------------------ #

    def _fetch_all_songs(self):
        """Pull every song from the library in batches."""
        songs = []
        start = 0
        total = None

        while True:
            result = rpc("AudioLibrary.GetSongs", {
                "properties": _PROPERTIES,
                "limits": {"start": start, "end": start + _BATCH},
                "sort": {"method": "artist", "order": "ascending"},
            })
            if not result:
                break

            batch = result.get("songs", []) or []
            songs.extend(batch)

            limits = result.get("limits", {})
            total = int(limits.get("total", len(songs)))
            start = int(limits.get("end", start + len(batch)))

            if not batch or start >= total:
                break

        xbmc.log("[rotation] library index: fetched %d songs" % len(songs),
                 xbmc.LOGWARNING)
        return songs

    def _compact(self, song):
        """Reduce a JSON-RPC song record to the fields the resolver needs."""
        artists = song.get("artist") or []
        album_artists = song.get("albumartist") or []
        artist = ", ".join(artists) if artists else (
            ", ".join(album_artists) if album_artists else ""
        )
        genres = song.get("genre") or []

        return {
            "songid":   song.get("songid", 0),
            "title":    song.get("title", "") or "",
            "artist":   artist,
            "artists":  artists,
            "albumartist": ", ".join(album_artists) if album_artists else "",
            "albumartists": album_artists,
            "album":    song.get("album", "") or "",
            "file":     song.get("file", "") or "",
            "duration": song.get("duration", 0) or 0,
            "year":     song.get("year", 0) or 0,
            "track":    song.get("track", 0) or 0,
            "genre":    ", ".join(genres) if genres else "",
            "genres":   genres,
            "thumb":    song.get("thumbnail", "") or "",
            "dateadded": song.get("dateadded", "") or "",
            "lastplayed": song.get("lastplayed", "") or "",
            "playcount": song.get("playcount", 0) or 0,
        }

    def _index(self):
        """Build the lookup dictionaries from self.songs."""
        self.by_exact = {}
        self.by_primary = {}
        self.by_title = {}
        self.by_artist = {}

        for idx, song in enumerate(self.songs):
            n_artist = norm_artist(song["artist"])
            p_artist = primary_artist(song["artist"])

            # Two title forms per song: aggressively stripped and lightly
            # folded. A chart entry for the plain title finds the remaster,
            # and a chart entry that includes the bracketed subtitle finds the
            # track whose library tag also includes it.
            titles = {norm_title(song["title"], True),
                      norm_title(song["title"], False)}
            titles.discard("")

            for n_title in titles:
                self.by_exact.setdefault((n_artist, n_title), []).append(idx)
                if p_artist and p_artist != n_artist:
                    self.by_primary.setdefault((p_artist, n_title), []).append(idx)
                self.by_title.setdefault(n_title, []).append(idx)

            if n_artist:
                self.by_artist.setdefault(n_artist, []).append(idx)
            if p_artist and p_artist != n_artist:
                self.by_artist.setdefault(p_artist, []).append(idx)

        self._ready = True

    # -- persistence ------------------------------------------------------- #

    def _load_cache(self):
        if not os.path.exists(self.cache_file):
            return False
        try:
            with open(self.cache_file, "r", encoding="utf-8") as handle:
                blob = json.load(handle)
        except Exception as exc:                              # noqa: BLE001
            xbmc.log("[rotation] library index cache unreadable: %s" % exc,
                     xbmc.LOGWARNING)
            return False

        if self.ttl and (time.time() - blob.get("built", 0)) > self.ttl:
            return False

        if blob.get("schema") != _CACHE_SCHEMA:
            return False

        current = library_signature()
        cached = blob.get("signature", {"count": blob.get("count", -1), "latest": ""})
        if current is not None and cached != current:
            return False

        self.songs = blob.get("songs", [])

        self._index()
        self._signature = current or cached
        self._last_validation = time.time()
        return True

    def _save_cache(self):
        try:
            os.makedirs(self.cache_dir, exist_ok=True)
            tmp = self.cache_file + ".tmp"
            with open(tmp, "w", encoding="utf-8") as handle:
                json.dump({
                    "built": time.time(),
                    "schema": _CACHE_SCHEMA,
                    "count": len(self.songs),
                    "signature": self._signature or library_signature()
                                 or {"count": len(self.songs), "latest": ""},
                    "songs": self.songs,
                }, handle)
            os.replace(tmp, self.cache_file)
        except Exception as exc:                              # noqa: BLE001
            xbmc.log("[rotation] library index cache write failed: %s" % exc,
                     xbmc.LOGWARNING)

    def build(self, force=False):
        """Load the index from cache, or rebuild it from the library."""
        if not library_access_enabled():
            self.songs = []
            self._signature = {"count": 0, "latest": "", "disabled": True}
            self._last_validation = time.time()
            self._index()
            return True
        if self._signature and self._signature.get("disabled"):
            force = True
        if self._ready and not force:
            if time.time() - self._last_validation < 60:
                return True
            self._last_validation = time.time()
            current = library_signature()
            if current is None:
                return True
            if current == self._signature:
                return True
            xbmc.log("[rotation] Kodi music library changed; rebuilding match index",
                     xbmc.LOGINFO)
            force = True

        if not force and self._load_cache():
            xbmc.log("[rotation] library index: %d songs from cache"
                     % len(self.songs), xbmc.LOGWARNING)
            return True

        signature = library_signature()
        if signature is not None and signature.get("count", 0) == 0:
            self.songs = []
            self._signature = signature
            self._last_validation = time.time()
            self._index()
            self._save_cache()
            return True

        raw = self._fetch_all_songs()
        if not raw:
            self.songs = []
            self._index()
            return False

        self.songs = [self._compact(s) for s in raw]
        # Playable entries only. A song row with no file path resolves to
        # nothing and would silently skip during playback.
        self.songs = [s for s in self.songs if s["file"]]
        self._signature = library_signature() or {"count": len(self.songs), "latest": ""}
        self._last_validation = time.time()
        self._index()
        self._save_cache()
        return True

    def invalidate(self):
        """Drop the on-disk cache so the next build() re-reads the library."""
        self._ready = False
        try:
            if os.path.exists(self.cache_file):
                os.remove(self.cache_file)
        except OSError:
            pass

    @property
    def size(self):
        return len(self.songs)

    # -- resolution -------------------------------------------------------- #

    def _score_candidates(self, indices, want_album=""):
        """
        Pick the best song from a candidate list.

        Preference order: not a karaoke/tribute release, then album match if
        one was requested, then the earliest songid so repeated runs over the
        same chart produce a stable playlist rather than shuffling between
        duplicate rips.
        """
        if not indices:
            return None

        n_album = _basic(want_album) if want_album else ""

        def rank(idx):
            song = self.songs[idx]
            junk = 1 if _RE_JUNK_ALBUM.search(song["album"] or "") else 0
            album_miss = 0
            if n_album:
                album_miss = 0 if _basic(song["album"]) == n_album else 1
            return (junk, album_miss, song["songid"])

        return self.songs[min(indices, key=rank)]

    def resolve(self, artist, title, album="", threshold=0.86,
                artist_threshold=0.72, allow_any_artist=False):
        """
        Find the library song matching a chart entry.

        Returns a dict — a copy of the indexed song plus 'match' (how it was
        found) and 'score' — or None.
        """
        if not self._ready:
            self.build()
        if not self.songs:
            return None

        n_artist = norm_artist(artist)
        p_artist = primary_artist(artist)
        t_strict = norm_title(title, False)
        t_loose = norm_title(title, True)

        title_forms = [t for t in (t_loose, t_strict) if t]
        if not title_forms:
            return None

        # 1. Exact artist + title.
        for n_title in title_forms:
            hit = self._score_candidates(
                self.by_exact.get((n_artist, n_title), []), album)
            if hit:
                return dict(hit, match="exact", score=1.0)

        # 2. Primary artist + title. Catches chart credits that name featured
        #    artists the library tags don't ("Mark Ronson feat. Bruno Mars").
        for n_title in title_forms:
            hit = self._score_candidates(
                self.by_primary.get((p_artist, n_title), []), album)
            if hit:
                return dict(hit, match="primary-artist", score=0.97)

        # 3. Title matches exactly somewhere; score the artist. Handles the
        #    reverse case, where the library tag carries the featured credit.
        for n_title in title_forms:
            best, best_score = None, 0.0
            for idx in self.by_title.get(n_title, []):
                song = self.songs[idx]
                score = max(
                    _similar(n_artist, norm_artist(song["artist"])),
                    _similar(p_artist, primary_artist(song["artist"])),
                )
                if score > best_score:
                    best, best_score = idx, score
            if best is not None and best_score >= artist_threshold:
                return dict(self.songs[best], match="title+artist-fuzzy",
                            score=round(best_score, 3))

        # 4. Fuzzy title inside the artist's own catalogue. This is where
        #    "Rocket Man (I Think It's Going...)" finally lands on
        #    "Rocket Man" if the bracket-stripping didn't already do it.
        bucket = self.by_artist.get(n_artist) or self.by_artist.get(p_artist) or []
        best, best_score = None, 0.0
        for idx in bucket:
            song = self.songs[idx]
            score = max(
                _similar(t_loose, norm_title(song["title"], True)),
                _similar(t_strict, norm_title(song["title"], False)),
            )
            if score > best_score:
                best, best_score = idx, score
        if best is not None and best_score >= threshold:
            return dict(self.songs[best], match="title-fuzzy",
                        score=round(best_score, 3))

        # 5. Last resort, off by default: exact title by anyone. Useful for
        #    compilation-heavy libraries where the artist tag is "Various".
        if allow_any_artist:
            for n_title in title_forms:
                hit = self._score_candidates(self.by_title.get(n_title, []), album)
                if hit:
                    return dict(hit, match="title-only", score=0.5)

        return None

    def resolve_many(self, entries, threshold=0.86, allow_any_artist=False,
                     dedupe=True):
        """
        Resolve a provider playlist against the library.

        entries: list of {"artist": str, "title": str, "album": str, ...}

        Returns (resolved, missing). Resolved entries carry the library song
        fields plus 'source' — the original provider entry — so the listing
        can show chart artwork over a local file.
        """
        if not self._ready:
            self.build()

        resolved, missing, seen = [], [], set()

        for entry in entries:
            artist = entry.get("artist", "")
            title = entry.get("title", "")
            if not artist or not title:
                continue

            hit = self.resolve(
                artist, title,
                album=entry.get("album", ""),
                threshold=threshold,
                allow_any_artist=allow_any_artist,
            )

            if not hit:
                missing.append(entry)
                continue

            if dedupe:
                if hit["songid"] in seen:
                    continue
                seen.add(hit["songid"])

            hit = dict(hit)
            hit["source"] = entry
            resolved.append(hit)

        xbmc.log(
            "[rotation] resolved %d/%d playlist entries (%d missing)"
            % (len(resolved), len(entries), len(missing)),
            xbmc.LOGWARNING,
        )
        return resolved, missing


def recording_title(title):
    text = re.sub(r"[\(\[]\s*(?:feat\.?|ft\.?|featuring)\s+[^)\]]+[\)\]]", "", str(title or ""), flags=re.I)
    return re.sub(r"\s+(?:feat\.?|ft\.?|featuring)\s+.*$", "", text, flags=re.I).strip()


def art_artist_name(artist):
    # Explicit featured markers and spaced separators, not band-name ampersands.
    return re.split(r"\s+(?:feat\.?|ft\.?|featuring)\s+|\s+/\s+|\s*;\s*", str(artist or ""), maxsplit=1, flags=re.I)[0].strip(" ([")
