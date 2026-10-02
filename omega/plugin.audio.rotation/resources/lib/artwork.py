# -*- coding: utf-8 -*-
#
# Rotation — external artwork lookup
#
# Playlist providers sometimes supply only a modest cover thumbnail. This
# module cross-references free music metadata services to find a high-resolution
# cover and, more importantly, a real 16:9 artist background.
#
# Design notes:
#
#   * Every lookup is cached in SQLite, including misses. A miss is cheap to
#     re-check occasionally but expensive to repeat on every directory load.
#   * Album grid lookups remain budgeted. Playlist artist backgrounds are
#     resolved by a separate worker so browsing never waits on HTTP calls.
#   * Every provider fails soft. No artwork is a cosmetic problem; a raised
#     exception inside a directory build is a broken addon.
#   * MusicBrainz requires a descriptive User-Agent and no more than one
#     request per second. Both are enforced below. Do not remove the
#     throttle — sustained abuse gets the client IP blocked.

import concurrent.futures
import hashlib
import os
import logging
import re
import threading
import time
import unicodedata
from urllib.parse import quote_plus

import requests
import xbmc
from .library import art_artist_name, library_artist_mbid, library_album_art

from .storage import db, BaseModel
from .peewee import CharField, TextField, FloatField
from .provider_health import ProviderHealth

log = logging.getLogger(__name__)


def _log(message, level=xbmc.LOGDEBUG):
    """
    Write to kodi.log.

    Python's logging module produces no output inside a Kodi addon, so
    every diagnostic that needs to be visible has to go through xbmc.log.
    """
    try:
        xbmc.log("[rotation][artwork] %s" % message, level)
    except Exception:
        pass

# Marker stored as the source for an album Deezer could not find during a
# bulk grid pass. It stops the grid re-querying the same failures on every
# visit, while still telling album_art() that the full provider chain has
# not been tried yet.
BULK_MISS = "bulk-miss"

# Sources recorded on an artist cache row. These are not cosmetic: artist_art
# reads them to decide whether a row with no portrait represents "every
# provider was asked and came up empty" or "this predates the provider that
# would have found it".
#
# They are prefixed because cover rows and artist rows share one table and
# cover rows already store a bare "deezer". stats() tells the two apart by
# source alone, so an artist row must never be stored under a name a cover
# row could also use.
# Portrait providers, in the order artist_art tries them. These names are
# recorded verbatim in the cache row's "tried" column.
#
# Earlier versions inferred "has this row been fully looked up?" from the
# source string, and got it wrong three times running: a row could be
# written by one code path and read by another that disagreed about what
# the string meant. Recording what was actually asked removes the
# inference. A row is finished when every provider currently available has
# its name in "tried" - which also means enabling a new provider, or adding
# a key, automatically reopens the rows that predate it.
P_DEEZER   = "deezer"
P_AUDIODB  = "audiodb"
P_FANARTTV = "fanarttv"
P_FANARTTV_LOGO = "fanarttv-logo"

# Sources recorded on an artist row, for stats() and for reading the log.
SRC_DEEZER   = "artist-deezer"
SRC_AUDIODB  = "artist-audiodb"
SRC_FANARTTV = "fanarttv"
SRC_NONE     = "artist-none"

# Sources that identify a row as an artist row rather than a cover. The bare
# legacy names are kept so rows written by earlier versions still count.
ARTIST_SOURCES = (SRC_DEEZER, SRC_AUDIODB, SRC_FANARTTV, SRC_NONE,
                  "artist-deezer-full", "artist-checked", "nomatch", "none")

AUDIODB_BASE = "https://www.theaudiodb.com/api/v1/json"

# TheAudioDB answers 429 past 30 requests/minute on the free key and 100 on
# a premium one. Serialise our own calls just under those ceilings.
AUDIODB_FREE_KEY      = "123"
_AUDIODB_FREE_INTERVAL    = 2.1
_AUDIODB_PREMIUM_INTERVAL = 0.7
_adb_lock = threading.Lock()
_adb_last = [0.0]


class _Failed(object):
    """Sentinel: the request did not complete. Not the same as 'no result'."""
    def __repr__(self):
        return "FAILED"


FAILED = _Failed()

# MusicBrainz is frequently slower than the consumer APIs and a 6s timeout
# produces spurious "artist not found" results. Give it its own budget and
# one retry.
MB_TIMEOUT  = 15
MB_ATTEMPTS = 2

# Cache lifetimes
HIT_TTL  = 30 * 24 * 3600   # 30 days — artwork URLs are stable
MISS_TTL = 3 * 24 * 3600    # Album misses
# How long a *confirmed* absence (the service answered "nothing") stands
# before an artist row's missing assets are asked for again. Transient
# failures are never cached, so this is not a retry interval: a short value
# here only re-runs the throttled MusicBrainz -> fanart.tv chain for artists
# that genuinely have no background or logo. 1.0.44 used 15 minutes, which
# put most of a large playlist back through the 1 req/s queue on every
# visit. Three days still lets newly uploaded fanart.tv art appear promptly.
ARTIST_MISS_TTL = 3 * 24 * 3600
# Artist rows written before 1.0.45 carry no MusicBrainz ID. Some recorded
# absences that were really failures (e.g. fanart.tv error bodies before
# 1.0.44), so their missing assets are re-checked on the old 15-minute rule
# once; the re-check stores an ID (or NO_MBID) and the row becomes normal.
LEGACY_ARTIST_MISS_TTL = 15 * 60
# Stored in the mbid column when MusicBrainz was asked and had no match, so
# a row from this version is never mistaken for a legacy one.
NO_MBID = "none"
_MBID_MIGRATED = False

# MusicBrainz asks for one request per second, maximum.
_MB_MIN_INTERVAL = 1.1
_mb_lock = threading.Lock()
_mb_last = [0.0]

USER_AGENT = "Rotation/2026.1.0 (Kodi audio addon)"


class ArtworkCache(BaseModel):
    """Resolved artwork per artist/album. Empty strings mean a cached miss."""
    key        = CharField(unique=True)
    thumb      = TextField(default="")
    fanart     = TextField(default="")
    logo       = TextField(default="")
    source     = TextField(default="")
    # Comma-separated provider names already asked for this row. See the
    # P_* constants; the empty string means "nothing, or written by a
    # version that predates this column".
    tried      = TextField(default="")
    # MusicBrainz ID this row was resolved against (artist ID for artist
    # rows, release-group ID for cover rows). Caching it means a reopened
    # row goes straight to fanart.tv/TheAudioDB instead of repeating the
    # throttled name search.
    mbid       = TextField(default="")
    fetched_at = FloatField(default=0.0)


_EDITION_SUFFIX = re.compile(
    r"\s*[\(\[][^)\]]*"
    r"(deluxe|remaster|remastered|edition|expanded|anniversary|bonus|"
    r"explicit|clean|reissue|version|mono|stereo)"
    r"[^)\]]*[\)\]]")


def _norm(value):
    """Loose normalisation for comparing artist/album names across services.

    Accents are folded to their base letters ("Beyoncé" -> "beyonce",
    "Björk" -> "bjork") and letters from any alphabet are kept. The previous
    version dropped every character outside a-z, which turned "Beyoncé"
    into "beyonc" - so it never matched "Beyonce" from another service - and
    reduced Cyrillic or Japanese names to an empty string that all such
    artists then shared.
    """
    if not value:
        return ""
    value = unicodedata.normalize("NFKD", str(value))
    value = "".join(c for c in value if not unicodedata.combining(c)).lower()
    # Drop edition suffixes that stop otherwise-identical albums matching
    value = _EDITION_SUFFIX.sub("", value)
    # "&" and "and" are used interchangeably across services and in URL
    # slugs, so fold them together before stripping punctuation. Without
    # this, "Doo-Wops & Hooligans" and "doo-wops-and-hooligans" normalise
    # to different strings and never match.
    value = value.replace("&", " and ").replace("+", " and ")
    return re.sub(r"[\W_]+", "", value)


def _norm_legacy(value):
    """The pre-1.0.61 normalisation; only used to find older cache rows."""
    if not value:
        return ""
    value = _EDITION_SUFFIX.sub("", str(value).lower())
    value = value.replace("&", " and ").replace("+", " and ")
    return re.sub(r"[^a-z0-9]+", "", value)


def _mb_completeness(entry):
    """
    How fully a MusicBrainz artist entry is filled in.

    Several entries can carry exactly the same name and the same search
    score. Miley Cyrus is one: a second "Miley Cyrus" entry with no type,
    area, dates or aliases scored 100 and was picked over the real one, so
    fanart.tv (which only knows the real MBID) returned nothing. A real,
    established artist is a typed Person or Group with dates, an area,
    aliases, tags or ISNI codes; a stub or duplicate rarely has any.
    """
    score = 0
    if entry.get("type"):
        score += 3
    if entry.get("isnis"):
        score += 2
    for field in ("gender", "aliases", "tags", "disambiguation"):
        if entry.get(field):
            score += 1
    if entry.get("country") or entry.get("area"):
        score += 1
    if (entry.get("life-span") or {}).get("begin"):
        score += 1
    return score


def _same_artist(a, b):
    """
    True only when two strings name the same artist.

    Deliberately stricter than _looks_like. That function accepts one name
    containing the other, which is right for album titles - "21" against
    "21 (Deluxe Edition)" - and badly wrong for artists: normalised,
    "adele" is a substring of "venitaadeleandrodney", so a search for Adele
    matched a gospel duo and used their album cover as her portrait. The
    same rule would match Queen to Queensryche and Bush to Kate Bush.

    A leading "the" is ignored, since services disagree about it constantly.
    Everything else has to match exactly. A blank tile is a better outcome
    than a confidently wrong face.
    """
    na, nb = _norm(a), _norm(b)
    if not na or not nb:
        return False
    if na.startswith("the"):
        na = na[3:]
    if nb.startswith("the"):
        nb = nb[3:]
    return bool(na) and na == nb


def _looks_like(a, b):
    """True when two names are the same, or one clearly contains the other."""
    na, nb = _norm(a), _norm(b)
    if not na or not nb:
        return False
    if na == nb:
        return True
    shorter, longer = (na, nb) if len(na) <= len(nb) else (nb, na)
    return len(shorter) >= 4 and shorter in longer


class ArtworkProvider:
    """
    Resolves high-resolution artwork for an artist/album pair.

    Providers are tried in order and the first usable result wins:

      Cover   1. Deezer          — cover_xl, 1000x1000, no key required
              2. iTunes Search   — artworkUrl100 rewritten to 1200x1200
              3. Cover Art Archive via MusicBrainz — 1200px front cover

      Fanart  1. fanart.tv artistbackground — genuine 1920x1080 landscape

    Only fanart.tv needs an API key, and only for backgrounds. Covers work
    with no configuration at all.
    """

    def __init__(self, cache_dir=None, fanarttv_key="", timeout=6,
                 want_backgrounds=True, audiodb_key=AUDIODB_FREE_KEY):
        self.key         = (fanarttv_key or "").strip()
        self.audiodb_key = (audiodb_key or "").strip()
        self.timeout = timeout
        self.want_backgrounds = want_backgrounds
        self._deezer_health = ProviderHealth("Deezer")

        self._local = threading.local()

        self._db_ready = self._ensure_db(cache_dir)

    # ----------------------------------------------------------------- #
    # Cache
    # ----------------------------------------------------------------- #

    def _ensure_db(self, cache_dir):
        """
        Make sure the shared peewee database is bound to a file.

        db is a SqliteDatabase(None) that Rotation storage normally binds.
        Routes that build an ArtworkProvider without first building the
        scraper - Clear Artwork Cache, for one - would otherwise hit
        "database must be initialized before opening a connection". Binding
        here makes the provider independent of construction order.
        """
        try:
            if getattr(db, "database", None) is None:
                if not cache_dir:
                    _log("no cache_dir given and database is unbound",
                         xbmc.LOGWARNING)
                    return False
                db.init(os.path.join(cache_dir, "rotation.db"))
            db.connect(reuse_if_open=True)
            db.create_tables([ArtworkCache], safe=True)
            self._add_cache_columns()
            return True
        except Exception as exc:
            _log("could not initialise cache database: %s" % exc,
                 xbmc.LOGWARNING)
            return False

    def _add_cache_columns(self):
        """
        Add the "tried" column to a cache written by an earlier version.

        create_tables(safe=True) creates a missing table but will not alter
        an existing one, so an upgrade over a populated tracks.db needs
        this. Existing rows get an empty "tried", which reads as "no
        provider has been asked" and lets them be resolved properly.
        """
        try:
            columns = {c.name for c in db.get_columns(
                ArtworkCache._meta.table_name)}
            if "tried" not in columns:
                db.execute_sql(
                    "ALTER TABLE %s ADD COLUMN tried TEXT NOT NULL DEFAULT ''"
                    % ArtworkCache._meta.table_name)
                _log("cache: added 'tried' column to existing table", xbmc.LOGINFO)
                self._drop_guessed_portraits()
            if "mbid" not in columns:
                db.execute_sql(
                    "ALTER TABLE %s ADD COLUMN mbid TEXT NOT NULL DEFAULT ''"
                    % ArtworkCache._meta.table_name)
                _log("cache: added MusicBrainz ID column", xbmc.LOGINFO)
            self._migrate_unconfirmed_mbids()
            if "logo" not in columns:
                db.execute_sql(
                    "ALTER TABLE %s ADD COLUMN logo TEXT NOT NULL DEFAULT ''"
                    % ArtworkCache._meta.table_name)
                _log("cache: added clearlogo support; existing artists will be checked",
                     xbmc.LOGINFO)
        except Exception as exc:
            _log("could not add 'tried' column: %s" % exc, xbmc.LOGWARNING)

    def _migrate_unconfirmed_mbids(self):
        """
        One-time: forget MBIDs chosen by the 1.0.45-1.0.47 matcher that
        produced neither fanart nor a logo.

        That matcher took the first exact-name result, which could be a
        duplicate entry with no art (Miley Cyrus). Clearing the ID makes the
        row look legacy, so it is re-checked once with the ranked matcher.
        Rows with art, and rows with no MusicBrainz match, are untouched.
        """
        global _MBID_MIGRATED
        if _MBID_MIGRATED:
            return
        _MBID_MIGRATED = True
        marker = "__migration:mbid_rank_v2"
        try:
            if ArtworkCache.get_or_none(ArtworkCache.key == marker):
                return
            changed = (ArtworkCache.update(mbid="")
                       .where((ArtworkCache.source.in_(list(ARTIST_SOURCES))) &
                              (ArtworkCache.mbid != "") &
                              (ArtworkCache.mbid != NO_MBID) &
                              (ArtworkCache.fanart == "") &
                              (ArtworkCache.logo == ""))
                       .execute())
            ArtworkCache.replace(key=marker, source="meta",
                                 fetched_at=time.time()).execute()
            _log("cache: %d artist(s) queued for a re-check with ranked "
                 "MusicBrainz matching" % changed, xbmc.LOGINFO)
        except Exception as exc:
            _log("MBID migration skipped: %s" % exc, xbmc.LOGWARNING)

    def _drop_guessed_portraits(self):
        """
        Clear artist portraits that came from the old fuzzy name matcher.

        Those rows were filled by a Deezer name search that accepted one
        name containing another, so "Adele" could match "Venita Adele &
        Rodney" and take their album cover as her portrait. A row holding a
        wrong picture still looks resolved, so nothing would ever revisit
        it - this is the one-time correction.

        Only the portrait is cleared, and only for rows a name search
        wrote. Backgrounds are keyed on a MusicBrainz ID rather than a
        name, and are left alone.
        """
        try:
            cursor = db.execute_sql(
                "UPDATE %s SET thumb = '' WHERE source IN (?, ?)"
                % ArtworkCache._meta.table_name,
                ("artist-deezer", "artist-deezer-full"),
            )
            count = getattr(cursor, "rowcount", 0) or 0
            if count:
                _log("cache: cleared %d artist portrait(s) resolved by the "
                     "old name matcher; they will be looked up again"
                     % count, xbmc.LOGINFO)
        except Exception as exc:
            _log("could not clear guessed portraits: %s" % exc,
                 xbmc.LOGWARNING)

    def _session(self):
        """
        One requests.Session per thread.

        Bulk listing lookups run concurrently, and a Session is not safe to
        share across threads. Each worker gets its own.
        """
        session = getattr(self._local, "session", None)
        if session is None:
            session = requests.Session()
            session.headers.update({"User-Agent": USER_AGENT})
            self._local.session = session
        return session

    def _remember_legacy(self, key, legacy_raw, legacy_parts):
        """Note the pre-1.0.61 key for `key` so its cached row can be reused."""
        if all(legacy_parts):
            legacy = hashlib.md5(legacy_raw.encode("utf-8")).hexdigest()
            if legacy != key:
                if not hasattr(self, "_legacy_keys"):
                    self._legacy_keys = {}
                self._legacy_keys[key] = legacy

    def _key(self, artist, album):
        raw = "{0}|{1}".format(_norm(artist), _norm(album))
        key = hashlib.md5(raw.encode("utf-8")).hexdigest()
        old = (_norm_legacy(artist), _norm_legacy(album))
        self._remember_legacy(key, "{0}|{1}".format(*old), old)
        return key

    def _artist_key(self, artist):
        """
        Cache key for an artist background.

        Backgrounds are an artist-level asset on fanart.tv, not an album
        one. Caching them per artist means one lookup serves every album
        by that artist, and - more importantly - a cached album cover can
        never suppress the background lookup the way it used to.
        """
        raw = "artistbg|{0}".format(_norm(artist))
        old = _norm_legacy(artist)
        self._remember_legacy(hashlib.md5(raw.encode("utf-8")).hexdigest(),
                              "artistbg|{0}".format(old), (old,))
        return hashlib.md5(raw.encode("utf-8")).hexdigest()

    def _cached(self, key):
        try:
            row = ArtworkCache.get_or_none(ArtworkCache.key == key)
            legacy = getattr(self, "_legacy_keys", {}).get(key)
            if not row and legacy:
                # Saved under the pre-1.0.61 key: carry it over once.
                row = ArtworkCache.get_or_none(ArtworkCache.key == legacy)
                if row:
                    ArtworkCache.replace(
                        key=key, thumb=row.thumb, fanart=row.fanart,
                        logo=getattr(row, "logo", "") or "", source=row.source,
                        tried=getattr(row, "tried", "") or "",
                        mbid=getattr(row, "mbid", "") or "",
                        fetched_at=row.fetched_at).execute()
        except Exception:
            return None
        if not row:
            return None

        age = time.time() - (row.fetched_at or 0)
        ttl = HIT_TTL if (row.thumb or row.fanart or row.logo) else MISS_TTL
        if age > ttl:
            return None

        tried = set(filter(None, (getattr(row, "tried", "") or "").split(",")))
        # A successful portrait must not lock absent backgrounds/logos out
        # for the portrait's 30-day lifetime. Keep hits and reopen only the
        # providers that could supply a missing asset after 15 minutes.
        legacy = not (getattr(row, "mbid", "") or "")
        reopen_after = LEGACY_ARTIST_MISS_TTL if legacy else ARTIST_MISS_TTL
        if row.source in ARTIST_SOURCES and age >= reopen_after:
            if not row.thumb:
                tried.difference_update((P_DEEZER, P_AUDIODB, P_FANARTTV))
            if self.want_backgrounds and not row.fanart:
                tried.difference_update((P_AUDIODB, P_FANARTTV))
            if self.key and not getattr(row, "logo", ""):
                tried.discard(P_FANARTTV_LOGO)
        return {"thumb": row.thumb, "fanart": row.fanart,
                "logo": getattr(row, "logo", "") or "",
                "mbid": getattr(row, "mbid", "") or "",
                "source": row.source,
                "tried": ",".join(sorted(tried))}

    def _store(self, key, thumb, fanart, source, tried=None, logo="", mbid=""):
        try:
            ArtworkCache.replace(
                key=key, thumb=thumb or "", fanart=fanart or "", logo=logo or "",
                source=source or "", tried=",".join(sorted(tried or ())),
                mbid=mbid or "", fetched_at=time.time()
            ).execute()
        except Exception as exc:
            _log("could not write cache: %s" % exc, xbmc.LOGWARNING)

    def _get_json(self, url, params=None, headers=None, timeout=None,
                  attempts=1):
        """
        GET and decode JSON.

        Returns the decoded body on success, or FAILED when the request did
        not complete. FAILED is deliberately distinct from an empty result:
        a service that did not answer tells us nothing about whether the
        thing we asked for exists, and callers must not cache it as absent.
        """
        timeout = timeout or self.timeout
        is_deezer = "api.deezer.com" in url.lower()
        if is_deezer and self._deezer_health.circuit_error():
            _log("Deezer artwork lookup skipped during provider cooldown",
                 xbmc.LOGWARNING)
            return FAILED

        for attempt in range(1, attempts + 1):
            try:
                r = self._session().get(
                    url, params=params, headers=headers or {}, timeout=timeout
                )
            except Exception as exc:
                _log("request failed (%s) attempt %d/%d: %s"
                     % (url, attempt, attempts, exc), xbmc.LOGWARNING)
                if is_deezer:
                    self._deezer_health.failure("unreachable", exc)
                continue

            if r.status_code == 200:
                try:
                    data = r.json()
                    if is_deezer:
                        self._deezer_health.success()
                    return data
                except Exception as exc:
                    _log("bad JSON from %s: %s" % (url, exc), xbmc.LOGWARNING)
                    if is_deezer:
                        self._deezer_health.failure("malformed", exc)
                    return FAILED
            if r.status_code == 404:
                # Definitive: the service answered and has nothing.
                return None
            _log("HTTP %d from %s (attempt %d/%d)"
                 % (r.status_code, url, attempt, attempts), xbmc.LOGWARNING)
            if is_deezer:
                category = "rate_limited" if r.status_code == 429 else "service_error"
                self._deezer_health.failure(category, "HTTP %d" % r.status_code)

        return FAILED

    # ----------------------------------------------------------------- #
    # Cover providers
    # ----------------------------------------------------------------- #

    def _deezer_cover(self, artist, album, timeout=None):
        query = 'artist:"{0}" album:"{1}"'.format(artist, album)
        data = self._get_json(
            "https://api.deezer.com/search/album",
            {"q": query, "limit": 5}, timeout=timeout,
        )
        if not data or not data.get("data"):
            return ""

        for entry in data["data"]:
            entry_artist = (entry.get("artist") or {}).get("name", "")
            if not _same_artist(entry_artist, artist):
                continue
            if not _looks_like(entry.get("title", ""), album):
                continue
            cover = entry.get("cover_xl") or entry.get("cover_big") or ""
            if cover:
                return cover
        return ""

    def _deezer_artist_image(self, artist):
        """
        Artist portrait from Deezer — square, up to 1000x1000, no API key.

        This is what makes artist thumbs work out of the box. fanart.tv has
        nicer, hand-picked portraits, but it needs a key and a MusicBrainz
        round trip first, so it is the upgrade rather than the requirement.

        Returns FAILED when Deezer did not answer, so the caller can avoid
        recording a network problem as "this artist has no picture" for the
        next three days.
        """
        data = self._get_json(
            "https://api.deezer.com/search/artist",
            {"q": artist, "limit": 5},
        )
        if data is FAILED:
            return FAILED
        if not data or not data.get("data"):
            return ""

        for entry in data["data"]:
            if not _same_artist(entry.get("name", ""), artist):
                continue
            picture = (entry.get("picture_xl")
                       or entry.get("picture_big")
                       or entry.get("picture_medium")
                       or "")
            # When Deezer has no photo for an artist it still returns a URL,
            # with the image hash left empty - ".../images/artist//500x500...".
            # That renders as a grey silhouette, which is worse than letting
            # the skin draw its own default icon.
            if picture and "/artist//" not in picture:
                return picture
        return ""

    def _itunes_cover(self, artist, album):
        data = self._get_json(
            "https://itunes.apple.com/search",
            {"term": "{0} {1}".format(artist, album),
             "entity": "album", "limit": 5},
        )
        if not data or not data.get("results"):
            return ""

        for entry in data["results"]:
            if not _same_artist(entry.get("artistName", ""), artist):
                continue
            if not _looks_like(entry.get("collectionName", ""), album):
                continue
            art = entry.get("artworkUrl100", "")
            if art:
                # Apple serves any size from the same path template
                return art.replace("100x100bb", "1200x1200bb")
        return ""

    # ----------------------------------------------------------------- #
    # MusicBrainz (needed for Cover Art Archive and fanart.tv)
    # ----------------------------------------------------------------- #

    def _mb_throttle(self):
        with _mb_lock:
            elapsed = time.time() - _mb_last[0]
            if elapsed < _MB_MIN_INTERVAL:
                time.sleep(_MB_MIN_INTERVAL - elapsed)
            _mb_last[0] = time.time()

    def _mb_release_group(self, artist, album):
        """Return a release-group MBID, or "" if there's no confident match."""
        self._mb_throttle()
        query = 'artist:"{0}" AND releasegroup:"{1}"'.format(artist, album)
        data = self._get_json(
            "https://musicbrainz.org/ws/2/release-group",
            {"query": query, "fmt": "json", "limit": 5},
            timeout=MB_TIMEOUT, attempts=MB_ATTEMPTS,
        )
        if data is FAILED:
            # Not "no match": callers must not cache this as absent.
            return FAILED
        if not data:
            return ""

        for rg in data.get("release-groups", []):
            credits = rg.get("artist-credit") or []
            names = [(c.get("artist") or {}).get("name", "") for c in credits]
            if not any(_same_artist(n, artist) for n in names):
                continue
            if not _looks_like(rg.get("title", ""), album):
                continue
            return rg.get("id", "")
        return ""

    def _mb_artist(self, artist):
        """Best artist MBID, "" when there's no confident match, or FAILED."""
        found = self._mb_artist_candidates(artist)
        if found is FAILED:
            return FAILED
        return found[0] if found else ""

    def _mb_artist_candidates(self, artist):
        """
        Exact-name MusicBrainz matches for an artist, best first.

        Returns a list of MBIDs ([] when nothing matches) or FAILED.

        Matches the artist's MusicBrainz name first, then its registered
        aliases. Aliases matter for renamed artists and for credits that
        services spell differently: MusicBrainz lists "Ye" with the alias
        "Kanye West", and "Goo Goo Dolls" without the leading "The" that
        Deezer uses - both previously came back as "no match", which left
        them without fanart.tv art. Alias matches are still exact, so this
        does not loosen the rule that keeps Adele from matching a namesake.
        """
        name = (artist or "").replace('"', " ").strip()
        if not name:
            return []
        variants = [name]
        if name.lower().startswith("the "):
            variants.append(name[4:].strip())
        clauses = []
        for variant in variants:
            clauses += ['artist:"%s"' % variant, 'alias:"%s"' % variant]
        self._mb_throttle()
        data = self._get_json(
            "https://musicbrainz.org/ws/2/artist",
            {"query": " OR ".join(clauses), "fmt": "json", "limit": 10},
            timeout=MB_TIMEOUT, attempts=MB_ATTEMPTS,
        )
        if data is FAILED:
            return FAILED
        if not data:
            return []

        def _rank(entry):
            return (bool(entry.get("type")), int(entry.get("score", 0) or 0),
                    _mb_completeness(entry))

        entries = data.get("artists", [])
        named = sorted((e for e in entries
                        if _same_artist(e.get("name", ""), artist)),
                       key=_rank, reverse=True)
        aliased = sorted((e for e in entries if e not in named and any(
                              _same_artist(a.get("name", ""), artist)
                              for a in e.get("aliases") or [] if a.get("name"))),
                         key=_rank, reverse=True)
        candidates = named + aliased
        if not candidates:
            return []
        best = candidates[0]
        _log("MusicBrainz artist %r -> %s%s (type=%s score=%s detail=%d, "
             "%d exact-name candidate(s))" %
             (artist, best.get("id", ""),
              "" if best in named else " via alias of %r" % best.get("name", ""),
              best.get("type", ""), best.get("score", ""),
              _mb_completeness(best), len(candidates)), xbmc.LOGINFO)
        return [e.get("id", "") for e in candidates if e.get("id")]

    def _caa_cover(self, release_group_mbid):
        if not release_group_mbid:
            return ""
        # The redirect target is the real image; a 404 just means no art.
        url = "https://coverartarchive.org/release-group/{0}/front-1200".format(
            release_group_mbid
        )
        try:
            r = self._session().head(url, timeout=self.timeout, allow_redirects=True)
            if r.status_code == 200:
                return r.url
        except Exception as exc:
            _log("cover art archive lookup failed: %s" % exc, xbmc.LOGWARNING)
        return ""

    # ----------------------------------------------------------------- #
    # TheAudioDB — curated artist portraits and fanart
    # ----------------------------------------------------------------- #

    def _adb_throttle(self):
        interval = (_AUDIODB_FREE_INTERVAL
                    if self.audiodb_key == AUDIODB_FREE_KEY
                    else _AUDIODB_PREMIUM_INTERVAL)
        with _adb_lock:
            elapsed = time.time() - _adb_last[0]
            if elapsed < interval:
                time.sleep(interval - elapsed)
            _adb_last[0] = time.time()

    def _audiodb_artist(self, mbid="", name=""):
        """
        Artist assets from TheAudioDB, by MusicBrainz ID or by name.

        The MBID form is the one worth having: artist-mb.php is an exact
        lookup, so it cannot pick the wrong artist the way a name search
        can. The name form is a fallback and still verifies the returned
        strArtist before trusting it.

        Returns FAILED when the service did not answer, otherwise a dict
        with "thumb" and "background", either of which may be empty.
        """
        empty = {"thumb": "", "background": "", "logo": ""}
        if not self.audiodb_key or not (mbid or name):
            return empty

        if mbid:
            url = "%s/%s/artist-mb.php" % (AUDIODB_BASE, self.audiodb_key)
            params = {"i": mbid}
        else:
            url = "%s/%s/search.php" % (AUDIODB_BASE, self.audiodb_key)
            params = {"s": name}

        self._adb_throttle()
        data = self._get_json(url, params, attempts=2)
        if data is FAILED:
            return FAILED
        if not data:
            return empty

        entries = data.get("artists") or []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            # An MBID lookup is exact; only a name search needs checking.
            if not mbid and not _same_artist(entry.get("strArtist", ""), name):
                continue
            return {
                "thumb": (entry.get("strArtistThumb")
                          or entry.get("strArtistWideThumb") or ""),
                "background": entry.get("strArtistFanart") or "",
                # TheAudioDB has no votes, so its logo is only a fallback
                # for artists fanart.tv has none for (or when no key is set).
                "logo": entry.get("strArtistLogo") or "",
            }
        return empty

    # ----------------------------------------------------------------- #
    # fanart.tv — artist backgrounds
    # ----------------------------------------------------------------- #

    def _fanarttv(self, artist_mbid):
        """
        Fetch an artist's assets from fanart.tv.

        Returns FAILED when the request did not complete, otherwise a dict
        with "thumb" (square artist portrait) and "background" (1920x1080
        landscape). Both may be empty strings when fanart.tv has nothing.

        fanart.tv keys everything on MusicBrainz IDs, which is why the MBID
        lookup is a prerequisite rather than an optimisation.
        """
        empty = {"thumb": "", "background": "", "logo": ""}

        if not self.key or not artist_mbid:
            return empty

        data = self._get_json(
            "https://webservice.fanart.tv/v3/music/{0}".format(artist_mbid),
            {"api_key": self.key}, attempts=2,
        )
        if data is FAILED:
            _log("fanart.tv artist %s request failed" % artist_mbid, xbmc.LOGWARNING)
            return FAILED
        if not data:
            _log("fanart.tv artist %s returned no artwork data" % artist_mbid, xbmc.LOGINFO)
            return dict(empty, found=False)
        if data.get("error"):
            _log("fanart.tv artist %s returned an API error" % artist_mbid, xbmc.LOGWARNING)
            return FAILED
        _log("fanart.tv artist %s (%s): thumbs=%d backgrounds=%d HD logos=%d logos=%d" %
             (artist_mbid, data.get("name", ""), len(data.get("artistthumb") or []),
              len(data.get("artistbackground") or []), len(data.get("hdmusiclogo") or []),
              len(data.get("musiclogo") or [])), xbmc.LOGINFO)

        def _best(entries):
            """Highest-rated image URL from a fanart.tv asset list."""
            entries = entries or []
            if not entries:
                return ""
            entries = sorted(
                entries,
                key=lambda e: int(e.get("likes", 0) or 0),
                reverse=True,
            )
            return entries[0].get("url", "")

        # artistthumb is the square portrait. Fall back to a music banner
        # when no thumb exists - wrong shape for a grid, but far better
        # than an empty tile.
        thumb = _best(data.get("artistthumb")) or _best(data.get("musicbanner"))

        return {
            "thumb":      thumb,
            "background": _best(data.get("artistbackground")),
            "logo":       _best(data.get("hdmusiclogo")) or
                          _best(data.get("musiclogo")),
        }

    def _fanarttv_album_cover(self, release_group_mbid):
        """
        Highest-voted album cover from fanart.tv, by release-group MBID.

        This is the only cover source with community votes, so it is asked
        first whenever a key is configured. Returns FAILED on a transient
        error, otherwise a URL or "".
        """
        if not self.key or not release_group_mbid:
            return ""
        data = self._get_json(
            "https://webservice.fanart.tv/v3/music/albums/{0}".format(
                release_group_mbid),
            {"api_key": self.key}, attempts=2,
        )
        if data is FAILED:
            return FAILED
        if not data or data.get("error") or data.get("status") == "error":
            return ""
        album = (data.get("albums") or {}).get(release_group_mbid) or {}
        covers = sorted(album.get("albumcover") or [],
                        key=lambda e: int(e.get("likes", 0) or 0),
                        reverse=True)
        return covers[0].get("url", "") if covers else ""

    # ----------------------------------------------------------------- #
    # Public API
    # ----------------------------------------------------------------- #

    def album_art_bulk(self, pairs, budget=5.0, workers=8):
        """
        Resolve covers for a whole directory at once, concurrently.

        Grid listings call this. Design constraints that shaped it:

          * Deezer only. It is fast, needs no key, and has no throttle. The
            MusicBrainz path is deliberately excluded here because its
            one-request-per-second rule makes it unusable for 40 albums.
          * Hard wall-clock budget. Whatever has not resolved when the
            budget expires is abandoned and the caller falls back to the
            site thumbnail. A slow directory is worse than an ugly one.
          * Misses are NOT cached. A Deezer miss says nothing about whether
            the full provider chain would find something, so caching it
            would poison the album page lookup for three days.
          * Cache writes happen on this thread after the workers finish.
            The SQLite connection is shared and is not safe to write to
            from several threads at once.

        pairs: iterable of (artist, album). Returns {(artist, album): dict}.
        """
        results = {}
        pending = []

        for artist, album in pairs:
            if not artist or not album:
                continue
            key = self._key(artist, album)
            cached = self._cached(key)
            if cached is not None:
                results[(artist, album)] = cached
            else:
                pending.append((artist, album, key))

        if not pending:
            _log("bulk: all %d albums already cached" % len(results))
            return results

        deadline = time.time() + budget
        # Per-request timeout is capped so one hung connection cannot eat
        # the whole budget on its own.
        req_timeout = max(2, min(self.timeout, int(budget)))
        resolved = []

        def _work(item):
            artist, album, key = item
            if time.time() >= deadline:
                return None
            try:
                # Passed per call: workers used to swap self.timeout, which
                # raced between threads and could leave it at the short value.
                cover = self._deezer_cover(artist, album, timeout=req_timeout)
            except Exception as exc:
                _log("bulk worker failed for %r / %r: %s" % (artist, album, exc))
                return None
            # cover == "" means Deezer genuinely had no match, which is
            # worth recording. Returning None means the attempt did not
            # complete, which is not.
            return (artist, album, key, cover)

        executor = concurrent.futures.ThreadPoolExecutor(max_workers=workers)
        try:
            futures = [executor.submit(_work, item) for item in pending]
            try:
                for future in concurrent.futures.as_completed(
                    futures, timeout=max(0.1, deadline - time.time())
                ):
                    outcome = future.result()
                    if outcome:
                        resolved.append(outcome)
            except concurrent.futures.TimeoutError:
                _log("bulk: budget of %.1fs expired with %d/%d resolved"
                     % (budget, len(resolved), len(pending)), xbmc.LOGINFO)
        finally:
            try:
                executor.shutdown(wait=False, cancel_futures=True)
            except TypeError:
                # cancel_futures is Python 3.9+
                executor.shutdown(wait=False)

        # Cache writes on the calling thread only
        hits = 0
        for artist, album, key, cover in resolved:
            if cover:
                hits += 1
                # Recorded as Deezer-only: album_art() will still ask the
                # voted source (fanart.tv) once when a key is configured.
                self._store(key, cover, "", "deezer", tried=(P_DEEZER,))
                results[(artist, album)] = {
                    "thumb": cover, "fanart": "", "source": "deezer",
                    "settled": self._cover_settled({"thumb": cover,
                                                    "tried": P_DEEZER}),
                }
            else:
                # Recorded so the next visit to this grid does not repeat
                # the same failed lookup. album_art() ignores this marker.
                self._store(key, "", "", BULK_MISS)

        _log("bulk: %d cached, %d looked up, %d resolved, %d recorded as misses"
             % (len(results) - hits, len(pending), hits, len(resolved) - hits),
             xbmc.LOGINFO)
        return results

    def _cover_settled(self, cached):
        """
        True when a cover row needs no further lookup.

        A cover counts as final once the voted source has been asked, or
        when there is no voted source to ask (no fanart.tv key). Rows written
        before 1.0.45 have an empty "tried" column; with a key configured
        they are upgraded once, in the background.
        """
        if not cached or cached.get("source") == BULK_MISS:
            return False
        if not self.key:
            return True
        tried = set((cached.get("tried") or "").split(","))
        return P_FANARTTV in tried

    def album_art_cached(self, artist, album):
        """
        Return already-resolved artwork, or None. Never hits the network.

        The returned dict carries "settled": False when the cover is usable
        but a voted cover has not been checked yet, so callers can show it
        now and queue the upgrade.
        """
        if not artist or not album:
            return None
        cached = self._cached(self._key(artist, album))
        if cached is None or cached.get("source") == BULK_MISS:
            return None
        cached["settled"] = self._cover_settled(cached)
        return cached

    def album_art(self, artist, album, release_group_mbid=""):
        """
        Resolve artwork for one album.

        Cover order: fanart.tv albumcover (highest-voted) when a key is set,
        then Deezer, iTunes and the Cover Art Archive. Returns {"thumb",
        "fanart", "source"}; any field may be empty and callers must fall
        back to library or site artwork.
        """
        if not artist or not album:
            _log("skipped: missing artist (%r) or album (%r)" % (artist, album))
            return {"thumb": "", "fanart": "", "source": ""}

        _log("looking up %r / %r" % (artist, album), xbmc.LOGINFO)

        # Background first - it is cached separately per artist, so it is
        # resolved regardless of whether the cover came from cache.
        fanart = self.artist_background(artist)

        key = self._key(artist, album)
        cached = self._cached(key)

        if cached is not None and self._cover_settled(cached):
            _log("cover cache hit for %r / %r (thumb=%s)"
                 % (artist, album, cached.get("thumb") or "NONE"))
            return {
                "thumb":  cached.get("thumb", ""),
                "fanart": fanart,
                "source": cached.get("source", "") + "+cached",
            }

        usable = cached if (cached and cached.get("source") != BULK_MISS) else {}
        tried = set(filter(None, (usable.get("tried") or "").split(",")))
        thumb, source = usable.get("thumb", ""), usable.get("source", "")
        rg_mbid = (release_group_mbid or usable.get("mbid") or
                   library_album_art(artist, album).get("mbid", ""))

        # 1. The voted source. Needs a release-group MBID.
        if self.key and P_FANARTTV not in tried:
            if not rg_mbid:
                rg_mbid = self._mb_release_group(artist, album)
            if rg_mbid is FAILED:
                _log("MusicBrainz unavailable for %r / %r - keeping current "
                     "cover, not caching a miss" % (artist, album),
                     xbmc.LOGWARNING)
                return {"thumb": thumb or "", "fanart": fanart, "source": source}
            voted = self._fanarttv_album_cover(rg_mbid) if rg_mbid else ""
            if voted is FAILED:
                _log("fanart.tv unavailable for %r / %r - keeping current cover"
                     % (artist, album), xbmc.LOGWARNING)
                return {"thumb": thumb, "fanart": fanart, "source": source}
            tried.add(P_FANARTTV)
            if voted:
                thumb, source = voted, "fanarttv"

        # 2. Unvoted sources, only when nothing better exists.
        if not thumb and P_DEEZER not in tried:
            thumb = self._deezer_cover(artist, album)
            tried.add(P_DEEZER)
            if thumb:
                source = "deezer"
        if not thumb:
            thumb = self._itunes_cover(artist, album)
            if thumb:
                source = "itunes"
        if not thumb:
            if not rg_mbid:
                rg_mbid = self._mb_release_group(artist, album)
            if rg_mbid is FAILED:
                rg_mbid = ""
            thumb = self._caa_cover(rg_mbid)
            if thumb:
                source = "coverartarchive"

        self._store(key, thumb, "", source, tried=tried, mbid=rg_mbid)
        _log("resolved %r / %r -> thumb=%s fanart=%s via %s"
             % (artist, album, thumb or "NONE", fanart or "NONE",
                source or "NONE"),
             xbmc.LOGINFO)
        return {"thumb": thumb, "fanart": fanart, "source": source}

    def _portrait_providers(self):
        """Portrait sources available right now, in the order they are tried."""
        names = [P_DEEZER]
        if self.audiodb_key:
            names.append(P_AUDIODB)
        if self.key:
            names.append(P_FANARTTV)
        return names

    def _background_providers(self):
        if not self.want_backgrounds:
            return []
        names = []
        if self.key:
            names.append(P_FANARTTV)
        if self.audiodb_key:
            names.append(P_AUDIODB)
        return names

    def _portrait_settled(self, cached):
        """
        True when a cached artist row's portrait needs no further lookup.

        One definition, used by every caller. The listing path reads rows
        through artist_thumb_cached() and the resolve path reads them in
        artist_art(); when those two disagreed about what a row meant, rows
        that needed resolving were reported as finished and the code meant
        to fix them never ran.
        """
        if cached.get("thumb"):
            return True
        tried = set((cached.get("tried") or "").split(","))
        return all(name in tried for name in self._portrait_providers())

    def _background_settled(self, cached):
        if cached.get("fanart"):
            return True
        tried = set((cached.get("tried") or "").split(","))
        return all(name in tried for name in self._background_providers())

    def _logo_settled(self, cached):
        if cached.get("logo") or not self.key:
            return True
        tried = set((cached.get("tried") or "").split(","))
        return P_FANARTTV_LOGO in tried

    def artist_art(self, artist, mbid=""):
        """
        Resolve an artist's portrait and background, cached per artist.

        Providers, in order, stopping once a portrait is found:

          1. Deezer          — no key, no throttle, covers most artists
          2. TheAudioDB      — curated; exact when a MusicBrainz ID is known
          3. fanart.tv       — curated, and the only source of a background

        Deezer goes first because it is free and fast. The two curated
        sources are consulted anyway whenever a background is wanted, and
        their hand-picked portraits override Deezer's search result when
        they have one.

        Every provider asked is recorded in the row's "tried" column, so
        adding a key or enabling a source later reopens exactly the rows
        that could now be improved, and nothing else.

        Transient failures are never cached - a service that did not answer
        tells us nothing about whether the artist has a picture.
        """
        artist = art_artist_name(artist)
        blank = {"thumb": "", "fanart": "", "clearlogo": ""}
        if not artist:
            return blank

        key    = self._artist_key(artist)
        cached = self._cached(key)
        # Kodi's own tags are exact; a name search can pick a namesake.
        known_mbid = mbid or library_artist_mbid(artist)

        cached_mbid = (cached or {}).get("mbid", "")
        if cached_mbid == NO_MBID:
            cached_mbid = ""
        if (cached is not None and known_mbid and cached_mbid
                and cached_mbid != known_mbid):
            # Resolved against a different artist than the one in the user's
            # library. Discard it rather than show a confident wrong face.
            _log("artist %r cached under MBID %s but library says %s - "
                 "re-resolving" % (artist, cached_mbid, known_mbid),
                 xbmc.LOGINFO)
            cached = None

        if cached is not None:
            thumb      = cached.get("thumb", "")
            background = cached.get("fanart", "")
            logo       = cached.get("logo", "")
            source     = cached.get("source", "")
            tried      = set(t for t in (cached.get("tried") or "").split(",") if t)
            if (self._portrait_settled(cached)
                    and self._background_settled(cached)
                    and self._logo_settled(cached)):
                return {"thumb": thumb, "fanart": background, "clearlogo": logo}
            known_mbid = known_mbid or cached_mbid
        else:
            thumb, background, logo, source, tried = "", "", "", "", set()
        resolved_mbid = known_mbid

        def _result():
            return {"thumb": thumb, "fanart": background, "clearlogo": logo}

        # 1. Deezer - fast, unvoted. Skipped when fanart.tv is about to be
        #    asked anyway with a known MBID: its voted portrait would replace
        #    Deezer's, so the Deezer request would only be thrown away. It is
        #    still asked afterwards if fanart.tv has no portrait.
        voted_next = bool(self.key and known_mbid and P_FANARTTV not in tried)
        if not thumb and P_DEEZER not in tried and not voted_next:
            found = self._deezer_artist_image(artist)
            if found is FAILED:
                _log("Deezer unavailable for %r - not caching a miss" % artist,
                     xbmc.LOGWARNING)
                return _result()
            tried.add(P_DEEZER)
            if found:
                thumb, source = found, SRC_DEEZER

        # 2/3. MusicBrainz-keyed sources. Worth the throttled lookup when a
        # background is still wanted, or when nothing has a portrait yet.
        audiodb_logo    = ""
        need_portrait   = not thumb
        need_background = bool(self._background_providers()) and not background
        need_logo       = bool(self.key) and not self._logo_settled(
            {"logo": logo, "tried": ",".join(tried)})
        mbid_useful     = ((self.audiodb_key and P_AUDIODB not in tried)
                           or (self.key and (P_FANARTTV not in tried or
                                             P_FANARTTV_LOGO not in tried)))

        if (need_portrait or need_background or need_logo) and mbid_useful:
            if known_mbid:
                candidates = [known_mbid]
            else:
                candidates = self._mb_artist_candidates(artist)
            if candidates is FAILED:
                mbid = FAILED
            else:
                mbid = candidates[0] if candidates else ""
            if mbid and mbid is not FAILED:
                resolved_mbid = mbid

            if mbid is FAILED:
                _log("MusicBrainz unavailable for %r - not caching a miss"
                     % artist, xbmc.LOGWARNING)
                return _result()

            if not mbid:
                _log("no MusicBrainz match for artist %r" % artist,
                     xbmc.LOGINFO)
                # fanart.tv cannot be queried without an MBID. Record that
                # this route was exhausted so every directory refresh does
                # not repeat the same throttled MusicBrainz lookup.
                if self.key:
                    tried.add(P_FANARTTV)
                    tried.add(P_FANARTTV_LOGO)

            # Voted source first. TheAudioDB is then asked only for what is
            # still missing: on the free key it is throttled to one request
            # every 2.1 s, which set the pace of a whole playlist pass.
            if self.key and mbid and (P_FANARTTV not in tried or
                                      P_FANARTTV_LOGO not in tried):
                assets = self._fanarttv(mbid)
                # fanart.tv has no record at all for this MBID. When the name
                # search found other entries with exactly the same name, the
                # chosen one may be a duplicate; fanart.tv only indexes the
                # established entry. Never applies to Kodi's own MBIDs.
                for alternative in candidates[1:3]:
                    if assets is FAILED or assets.get("found", True):
                        break
                    _log("fanart.tv has no record for %s - trying exact-name "
                         "match %s for %r" % (mbid, alternative, artist),
                         xbmc.LOGINFO)
                    other = self._fanarttv(alternative)
                    if other is FAILED:
                        assets = FAILED
                    elif other.get("found", True):
                        mbid = resolved_mbid = alternative
                        assets = other
                if assets is FAILED:
                    _log("fanart.tv unavailable for %r - not caching a miss"
                         % artist, xbmc.LOGWARNING)
                    return _result()
                tried.add(P_FANARTTV)
                tried.add(P_FANARTTV_LOGO)
                if assets.get("thumb"):
                    thumb, source = assets["thumb"], SRC_FANARTTV
                background = assets.get("background", "") or background
                logo = assets.get("logo", "") or logo

            still_missing = (source != SRC_FANARTTV or
                             (self.want_backgrounds and not background) or
                             not logo)
            if (self.audiodb_key and P_AUDIODB not in tried and still_missing):
                assets = self._audiodb_artist(mbid=mbid) if mbid else None
                if assets is FAILED:
                    _log("TheAudioDB unavailable for %r - not caching a miss"
                         % artist, xbmc.LOGWARNING)
                    return _result()
                if assets is None:
                    # No MBID, so fall back to the name search.
                    assets = self._audiodb_artist(name=artist)
                    if assets is FAILED:
                        _log("TheAudioDB unavailable for %r - not caching a "
                             "miss" % artist, xbmc.LOGWARNING)
                        return _result()
                tried.add(P_AUDIODB)
                if assets.get("thumb") and source != SRC_FANARTTV:
                    # Hand-picked portrait beats a search result.
                    thumb, source = assets["thumb"], SRC_AUDIODB
                background = background or assets.get("background", "")
                audiodb_logo = assets.get("logo", "")

            # Unvoted logo only when the voted source had none.
            logo = logo or audiodb_logo

        # Deezer as the last resort when it was deferred for fanart.tv.
        if not thumb and P_DEEZER not in tried:
            found = self._deezer_artist_image(artist)
            if found is not FAILED:
                tried.add(P_DEEZER)
                if found:
                    thumb, source = found, SRC_DEEZER

        # A name-only TheAudioDB pass, for artists MusicBrainz could not
        # place at all.
        if not thumb and self.audiodb_key and P_AUDIODB not in tried:
            assets = self._audiodb_artist(name=artist)
            if assets is FAILED:
                _log("TheAudioDB unavailable for %r - not caching a miss"
                     % artist, xbmc.LOGWARNING)
                return _result()
            tried.add(P_AUDIODB)
            if assets.get("thumb"):
                thumb, source = assets["thumb"], SRC_AUDIODB
            background = background or assets.get("background", "")
            logo = logo or assets.get("logo", "")

        self._store(key, thumb, background, source or SRC_NONE, tried, logo=logo,
                    mbid=(resolved_mbid if resolved_mbid and
                          resolved_mbid is not FAILED else NO_MBID))
        _log("artist art for %r -> thumb=%s background=%s logo=%s via %s (asked: %s)"
             % (artist, thumb or "NONE", background or "NONE", logo or "NONE",
                source or "NONE", ",".join(sorted(tried)) or "nothing"),
             xbmc.LOGINFO)
        return _result()

    def artist_thumb_cached(self, artist):
        """
        Cached artist portrait, or None when a lookup is still worth doing.

        None means "ask a provider", which is how a listing decides whether
        to hand the artist to a background worker. An empty string means
        every available provider has been asked and none had a portrait.
        """
        artist = art_artist_name(artist)
        if not artist:
            return ""
        cached = self._cached(self._artist_key(artist))
        if cached is None:
            return None
        if not self._portrait_settled(cached):
            return None
        return cached.get("thumb", "")

    def artist_background_cached(self, artist):
        """
        Cached artist background, or None when nothing is cached.

        None means "not looked up", which the caller uses to decide whether
        to kick off a background resolve. An empty string means the lookup
        has run and there genuinely is no background.
        """
        artist = art_artist_name(artist)
        if not artist or not self._background_providers():
            return ""
        cached = self._cached(self._artist_key(artist))
        if cached is None or not self._background_settled(cached):
            return None
        return cached.get("fanart", "")

    def artist_logo_cached(self, artist):
        """Cached clearlogo, or None when fanart.tv has not been checked."""
        artist = art_artist_name(artist)
        if not artist:
            return ""
        cached = self._cached(self._artist_key(artist))
        if cached and cached.get("logo"):
            return cached["logo"]
        if not self.key:
            # No voted source to wait for. A TheAudioDB logo, if any, arrives
            # with the portrait lookup.
            return ""
        if cached is None or not self._logo_settled(cached):
            return None
        return ""

    def artist_background(self, artist):
        """
        Resolve a 1920x1080 artist background, cached per artist.

        Returns "" when backgrounds are disabled, no API key is set, or
        fanart.tv has nothing for this artist. Misses are cached so a
        missing background does not trigger a MusicBrainz lookup on every
        single album by that artist.
        """
        artist = art_artist_name(artist)
        if not self.want_backgrounds:
            return ""
        return self.artist_art(artist).get("fanart", "")

    def close(self):
        """
        Release this thread's HTTP session.

        Housekeeping for the background worker, which would otherwise leave
        a session and its sockets open after the thread finishes. Safe to
        call more than once.
        """
        session = getattr(self._local, "session", None)
        if session is None:
            return
        try:
            session.close()
        except Exception:
            pass
        self._local.session = None

    def stats(self):
        """
        Summarise what is currently cached.

        Returns a dict with total rows, album covers, artist entries, and
        how many artist entries were expensive to obtain. Artist rows are
        the costly ones: each required a MusicBrainz lookup, and that API
        permits only one request per second.
        """
        empty = {"total": 0, "covers": 0, "artists": 0, "artist_hits": 0}
        try:
            rows = list(ArtworkCache.select())
        except Exception as exc:
            _log("could not read cache stats: %s" % exc, xbmc.LOGWARNING)
            return empty

        covers = artists = artist_hits = 0
        for row in rows:
            # Artist rows are the only ones that carry a background.
            # ARTIST_SOURCES lists the sources they are stored under,
            # including the legacy bare names. Cover rows use provider
            # names or the bulk marker.
            if row.source in ARTIST_SOURCES:
                artists += 1
                if row.thumb or row.fanart or getattr(row, "logo", ""):
                    artist_hits += 1
            elif row.thumb:
                covers += 1

        return {
            "total":       len(rows),
            "covers":      covers,
            "artists":     artists,
            "artist_hits": artist_hits,
        }

    def forget(self, artist="", album=""):
        """Drop one artist's row (and one album's cover row) so the next
        lookup starts from scratch. Returns the number of rows removed."""
        keys = []
        name = art_artist_name(artist)
        if name:
            keys.append(self._artist_key(name))
        if artist and album:
            keys.append(self._key(artist, album))
        if not keys:
            return 0
        try:
            return ArtworkCache.delete().where(ArtworkCache.key.in_(keys)).execute()
        except Exception as exc:
            _log("could not forget artwork: %s" % exc, xbmc.LOGWARNING)
            return 0

    def clear(self):
        try:
            return ArtworkCache.delete().execute()
        except Exception as exc:
            _log("could not clear cache: %s" % exc, xbmc.LOGWARNING)
            return 0
