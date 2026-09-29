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
from urllib.parse import quote_plus

import requests
import xbmc

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
MISS_TTL = 3 * 24 * 3600    # 3 days  — coverage improves over time

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
    source     = TextField(default="")
    # Comma-separated provider names already asked for this row. See the
    # P_* constants; the empty string means "nothing, or written by a
    # version that predates this column".
    tried      = TextField(default="")
    fetched_at = FloatField(default=0.0)


def _norm(value):
    """Loose normalisation for comparing artist/album names across services."""
    if not value:
        return ""
    value = value.lower()
    # Drop edition suffixes that stop otherwise-identical albums matching
    value = re.sub(
        r"\s*[\(\[][^)\]]*"
        r"(deluxe|remaster|remastered|edition|expanded|anniversary|bonus|"
        r"explicit|clean|reissue|version|mono|stereo)"
        r"[^)\]]*[\)\]]",
        "", value
    )
    # "&" and "and" are used interchangeably across services and in URL
    # slugs, so fold them together before stripping punctuation. Without
    # this, "Doo-Wops & Hooligans" and "doo-wops-and-hooligans" normalise
    # to different strings and never match.
    value = value.replace("&", " and ").replace("+", " and ")
    value = re.sub(r"[^a-z0-9]+", "", value)
    return value


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
            self._add_tried_column()
            return True
        except Exception as exc:
            _log("could not initialise cache database: %s" % exc,
                 xbmc.LOGWARNING)
            return False

    def _add_tried_column(self):
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
            if "tried" in columns:
                return
            db.execute_sql(
                "ALTER TABLE %s ADD COLUMN tried TEXT NOT NULL DEFAULT ''"
                % ArtworkCache._meta.table_name
            )
            _log("cache: added 'tried' column to existing table",
                 xbmc.LOGINFO)
            self._drop_guessed_portraits()
        except Exception as exc:
            _log("could not add 'tried' column: %s" % exc, xbmc.LOGWARNING)

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

    def _key(self, artist, album):
        raw = "{0}|{1}".format(_norm(artist), _norm(album))
        return hashlib.md5(raw.encode("utf-8")).hexdigest()

    def _artist_key(self, artist):
        """
        Cache key for an artist background.

        Backgrounds are an artist-level asset on fanart.tv, not an album
        one. Caching them per artist means one lookup serves every album
        by that artist, and - more importantly - a cached album cover can
        never suppress the background lookup the way it used to.
        """
        raw = "artistbg|{0}".format(_norm(artist))
        return hashlib.md5(raw.encode("utf-8")).hexdigest()

    def _cached(self, key):
        try:
            row = ArtworkCache.get_or_none(ArtworkCache.key == key)
        except Exception:
            return None
        if not row:
            return None

        age = time.time() - (row.fetched_at or 0)
        ttl = HIT_TTL if (row.thumb or row.fanart) else MISS_TTL
        if age > ttl:
            return None

        return {"thumb": row.thumb, "fanart": row.fanart,
                "source": row.source,
                "tried": getattr(row, "tried", "") or ""}

    def _store(self, key, thumb, fanart, source, tried=None):
        try:
            ArtworkCache.replace(
                key=key, thumb=thumb or "", fanart=fanart or "",
                source=source or "", tried=",".join(sorted(tried or ())),
                fetched_at=time.time()
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

    def _deezer_cover(self, artist, album):
        query = 'artist:"{0}" album:"{1}"'.format(artist, album)
        data = self._get_json(
            "https://api.deezer.com/search/album",
            {"q": query, "limit": 5},
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
        if not data or data is FAILED:
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
        """Return an artist MBID, or "" if there's no confident match."""
        self._mb_throttle()
        data = self._get_json(
            "https://musicbrainz.org/ws/2/artist",
            {"query": 'artist:"{0}"'.format(artist), "fmt": "json", "limit": 5},
            timeout=MB_TIMEOUT, attempts=MB_ATTEMPTS,
        )
        if data is FAILED:
            return FAILED
        if not data:
            return ""

        for entry in data.get("artists", []):
            if _same_artist(entry.get("name", ""), artist):
                return entry.get("id", "")
        return ""

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
        empty = {"thumb": "", "background": ""}
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
        empty = {"thumb": "", "background": ""}

        if not self.key or not artist_mbid:
            return empty

        data = self._get_json(
            "https://webservice.fanart.tv/v3/music/{0}".format(artist_mbid),
            {"api_key": self.key}, attempts=2,
        )
        if data is FAILED:
            return FAILED
        if not data:
            return empty

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
        }

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
                previous, self.timeout = self.timeout, req_timeout
                try:
                    cover = self._deezer_cover(artist, album)
                finally:
                    self.timeout = previous
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
                self._store(key, cover, "", "deezer")
                results[(artist, album)] = {
                    "thumb": cover, "fanart": "", "source": "deezer"
                }
            else:
                # Recorded so the next visit to this grid does not repeat
                # the same failed lookup. album_art() ignores this marker.
                self._store(key, "", "", BULK_MISS)

        _log("bulk: %d cached, %d looked up, %d resolved, %d recorded as misses"
             % (len(results) - hits, len(pending), hits, len(resolved) - hits),
             xbmc.LOGINFO)
        return results

    def album_art_cached(self, artist, album):
        """
        Return already-resolved artwork, or None. Never hits the network.

        Grid listings use this. A genre page can hold 40 albums, and doing
        live lookups there would mean 40 sequential HTTP requests while the
        user stares at an empty screen. Instead the listing shows whatever
        previous album visits have already resolved, so coverage fills in
        as you browse.
        """
        if not artist or not album:
            return None
        cached = self._cached(self._key(artist, album))
        if cached is not None and cached.get("source") == BULK_MISS:
            return None
        return cached

    def album_art(self, artist, album):
        """
        Resolve artwork for one album.

        Returns {"thumb": url, "fanart": url, "source": str}. Any field may
        be an empty string; callers must fall back to site artwork.
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

        if cached is not None and cached.get("source") != BULK_MISS:
            _log("cover cache hit for %r / %r (thumb=%s)"
                 % (artist, album, cached.get("thumb") or "NONE"))
            return {
                "thumb":  cached.get("thumb", ""),
                "fanart": fanart,
                "source": cached.get("source", "") + "+cached",
            }

        # A bulk miss only means Deezer had nothing. The rest of the chain
        # has not been tried yet, so fall through rather than trusting it.
        thumb, source = "", ""

        thumb = self._deezer_cover(artist, album)
        if thumb:
            source = "deezer"

        if not thumb:
            thumb = self._itunes_cover(artist, album)
            if thumb:
                source = "itunes"

        if not thumb:
            rg_mbid = self._mb_release_group(artist, album)
            thumb = self._caa_cover(rg_mbid)
            if thumb:
                source = "coverartarchive"

        self._store(key, thumb, "", source)
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

    def artist_art(self, artist):
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
        blank = {"thumb": "", "fanart": ""}
        if not artist:
            return blank

        key    = self._artist_key(artist)
        cached = self._cached(key)

        if cached is not None:
            thumb      = cached.get("thumb", "")
            background = cached.get("fanart", "")
            source     = cached.get("source", "")
            tried      = set(t for t in (cached.get("tried") or "").split(",") if t)
            if (self._portrait_settled(cached)
                    and self._background_settled(cached)):
                return {"thumb": thumb, "fanart": background}
        else:
            thumb, background, source, tried = "", "", "", set()

        def _result():
            return {"thumb": thumb, "fanart": background}

        # 1. Deezer.
        if not thumb and P_DEEZER not in tried:
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
        need_portrait   = not thumb
        need_background = bool(self._background_providers()) and not background
        mbid_useful     = ((self.audiodb_key and P_AUDIODB not in tried)
                           or (self.key and P_FANARTTV not in tried))

        if (need_portrait or need_background) and mbid_useful:
            mbid = self._mb_artist(artist)

            if mbid is FAILED:
                _log("MusicBrainz unavailable for %r - not caching a miss"
                     % artist, xbmc.LOGWARNING)
                return _result()

            if not mbid:
                _log("no MusicBrainz match for artist %r" % artist,
                     xbmc.LOGINFO)

            if self.audiodb_key and P_AUDIODB not in tried:
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
                if assets.get("thumb"):
                    # Hand-picked portrait beats a search result.
                    thumb, source = assets["thumb"], SRC_AUDIODB
                background = background or assets.get("background", "")

            if self.key and mbid and P_FANARTTV not in tried:
                assets = self._fanarttv(mbid)
                if assets is FAILED:
                    _log("fanart.tv unavailable for %r - not caching a miss"
                         % artist, xbmc.LOGWARNING)
                    return _result()
                tried.add(P_FANARTTV)
                if assets.get("thumb"):
                    thumb, source = assets["thumb"], SRC_FANARTTV
                background = assets.get("background", "") or background

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

        self._store(key, thumb, background, source or SRC_NONE, tried)
        _log("artist art for %r -> thumb=%s background=%s via %s (asked: %s)"
             % (artist, thumb or "NONE", background or "NONE",
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
        if not artist or not self._background_providers():
            return ""
        cached = self._cached(self._artist_key(artist))
        if cached is None or not self._background_settled(cached):
            return None
        return cached.get("fanart", "")

    def artist_background(self, artist):
        """
        Resolve a 1920x1080 artist background, cached per artist.

        Returns "" when backgrounds are disabled, no API key is set, or
        fanart.tv has nothing for this artist. Misses are cached so a
        missing background does not trigger a MusicBrainz lookup on every
        single album by that artist.
        """
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
                if row.thumb or row.fanart:
                    artist_hits += 1
            elif row.thumb:
                covers += 1

        return {
            "total":       len(rows),
            "covers":      covers,
            "artists":     artists,
            "artist_hits": artist_hits,
        }

    def clear(self):
        try:
            return ArtworkCache.delete().execute()
        except Exception as exc:
            _log("could not clear cache: %s" % exc, xbmc.LOGWARNING)
            return 0
