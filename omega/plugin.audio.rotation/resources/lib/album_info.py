# -*- coding: utf-8 -*-
"""
"About this album" text for album pages, and artist biographies.

Album sources, in order: Last.fm (album.getInfo wiki, needs the Last.fm API
key from Settings) and TheAudioDB (album description, when TheAudioDB
artwork is enabled). Artist biographies come from the Kodi library first,
then Last.fm (artist.getInfo) and TheAudioDB. Results are cached: found
text for 30 days, a confirmed "nothing" for 7 days. Failures are not cached.
"""

import hashlib
import html
import json
import os
import re
import threading
import time
import unicodedata

import requests
import xbmc

HIT_TTL = 30 * 24 * 3600
MISS_TTL = 7 * 24 * 3600
AUDIODB_INTERVAL = 2.1
_audiodb_lock = threading.Lock()
_audiodb_last = [0.0]


def _fold(value):
    value = unicodedata.normalize("NFKD", str(value or ""))
    value = "".join(c for c in value if not unicodedata.combining(c)).lower()
    return re.sub(r"[\W_]+", "", value)


def _clean(text):
    """Plain text from Last.fm/TheAudioDB markup."""
    text = html.unescape(re.sub(r"<[^>]+>", "", text or ""))
    # Last.fm appends a link and a licence line.
    text = re.sub(r"\s*Read more on Last\.fm\.?.*$", "", text, flags=re.S)
    text = re.sub(r"\s*User-contributed text is available under.*$", "", text, flags=re.S)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


class AlbumInfo(object):
    def __init__(self, cache_dir, lastfm_key="", audiodb_key="", timeout=6):
        self.cache_dir = os.path.join(cache_dir, "album_info")
        self.artist_dir = os.path.join(cache_dir, "artist_info")
        self.lastfm_key = (lastfm_key or "").strip()
        self.audiodb_key = (audiodb_key or "").strip()
        self.timeout = timeout
        for folder in (self.cache_dir, self.artist_dir):
            try:
                os.makedirs(folder, exist_ok=True)
            except OSError:
                pass

    def _path(self, artist, album):
        key = hashlib.md5(("%s|%s" % (_fold(artist), _fold(album))).encode("utf-8")).hexdigest()
        return os.path.join(self.cache_dir, key + ".json")

    def cached(self, artist, album):
        """Cached text, "" for a confirmed absence, or None if unknown."""
        try:
            with open(self._path(artist, album), "r", encoding="utf-8") as stream:
                row = json.load(stream)
        except (OSError, ValueError):
            return None
        age = time.time() - row.get("checked", 0)
        text = row.get("text", "")
        if age > (HIT_TTL if text else MISS_TTL):
            return None
        return text

    def _store(self, artist, album, text, source):
        try:
            path = self._path(artist, album)
            with open(path + ".tmp", "w", encoding="utf-8") as stream:
                json.dump({"text": text, "source": source, "checked": time.time()}, stream)
            os.replace(path + ".tmp", path)
        except OSError:
            pass

    def _lastfm(self, artist, album):
        if not self.lastfm_key:
            return ""
        response = requests.get("https://ws.audioscrobbler.com/2.0/", params={
            "method": "album.getinfo", "api_key": self.lastfm_key, "artist": artist,
            "album": album, "autocorrect": 1, "format": "json"}, timeout=self.timeout)
        if response.status_code != 200:
            raise IOError("Last.fm HTTP %d" % response.status_code)
        wiki = ((response.json() or {}).get("album") or {}).get("wiki") or {}
        return _clean(wiki.get("content") or wiki.get("summary") or "")

    def _audiodb(self, artist, album):
        if not self.audiodb_key:
            return ""
        with _audiodb_lock:
            wait = AUDIODB_INTERVAL - (time.time() - _audiodb_last[0])
            if wait > 0:
                time.sleep(wait)
            _audiodb_last[0] = time.time()
        response = requests.get(
            "https://www.theaudiodb.com/api/v1/json/%s/searchalbum.php" % self.audiodb_key,
            params={"s": artist, "a": album}, timeout=self.timeout)
        if response.status_code != 200:
            raise IOError("TheAudioDB HTTP %d" % response.status_code)
        rows = (response.json() or {}).get("album") or []
        for row in rows:
            if _fold(row.get("strAlbum")) == _fold(album):
                return _clean(row.get("strDescriptionEN") or "")
        return ""

    def description(self, artist, album):
        """Look up (or return cached) text. Network errors leave it uncached."""
        cached = self.cached(artist, album)
        if cached is not None:
            return cached
        failed = False
        for source, lookup in (("lastfm", self._lastfm), ("audiodb", self._audiodb)):
            try:
                text = lookup(artist, album)
            except Exception as exc:
                xbmc.log("[rotation] Album description from %s failed for %r / %r: %s"
                         % (source, artist, album, exc), xbmc.LOGINFO)
                failed = True
                continue
            if text:
                self._store(artist, album, text, source)
                xbmc.log("[rotation] Album description for %r / %r from %s (%d chars)"
                         % (artist, album, source, len(text)), xbmc.LOGINFO)
                return text
        if not failed and (self.lastfm_key or self.audiodb_key):
            self._store(artist, album, "", "")
        return ""

    # ------------------------------------------------------------------ #
    # Artist biographies
    # ------------------------------------------------------------------ #

    def _artist_path(self, artist):
        key = hashlib.md5(_fold(artist).encode("utf-8")).hexdigest()
        return os.path.join(self.artist_dir, key + ".json")

    def artist_cached(self, artist):
        """Cached biography, "" for a confirmed absence, or None if unknown."""
        if not artist:
            return ""
        try:
            with open(self._artist_path(artist), "r", encoding="utf-8") as stream:
                row = json.load(stream)
        except (OSError, ValueError):
            return None
        text = row.get("text", "")
        if time.time() - row.get("checked", 0) > (HIT_TTL if text else MISS_TTL):
            return None
        return text

    def _store_artist(self, artist, text, source):
        try:
            path = self._artist_path(artist)
            with open(path + ".tmp", "w", encoding="utf-8") as stream:
                json.dump({"text": text, "source": source, "checked": time.time()}, stream)
            os.replace(path + ".tmp", path)
        except OSError:
            pass

    @staticmethod
    def _library_bio(artist):
        """The biography Kodi's own library has for this artist, if any."""
        from .library import rpc
        result = rpc("AudioLibrary.GetArtists", {
            "properties": ["description"],
            "filter": {"field": "artist", "operator": "is", "value": artist},
            "limits": {"start": 0, "end": 1}}) or {}
        for row in result.get("artists") or []:
            if row.get("description"):
                return _clean(row["description"])
        return ""

    def _lastfm_bio(self, artist):
        if not self.lastfm_key:
            return ""
        response = requests.get("https://ws.audioscrobbler.com/2.0/", params={
            "method": "artist.getinfo", "api_key": self.lastfm_key, "artist": artist,
            "autocorrect": 1, "format": "json"}, timeout=self.timeout)
        if response.status_code != 200:
            raise IOError("Last.fm HTTP %d" % response.status_code)
        bio = ((response.json() or {}).get("artist") or {}).get("bio") or {}
        return _clean(bio.get("content") or bio.get("summary") or "")

    def _audiodb_bio(self, artist):
        if not self.audiodb_key:
            return ""
        with _audiodb_lock:
            wait = AUDIODB_INTERVAL - (time.time() - _audiodb_last[0])
            if wait > 0:
                time.sleep(wait)
            _audiodb_last[0] = time.time()
        response = requests.get(
            "https://www.theaudiodb.com/api/v1/json/%s/search.php" % self.audiodb_key,
            params={"s": artist}, timeout=self.timeout)
        if response.status_code != 200:
            raise IOError("TheAudioDB HTTP %d" % response.status_code)
        for row in (response.json() or {}).get("artists") or []:
            if _fold(row.get("strArtist")) == _fold(artist):
                return _clean(row.get("strBiographyEN") or "")
        return ""

    def artist_bio(self, artist):
        """Look up (or return cached) biography. Network errors aren't cached."""
        cached = self.artist_cached(artist)
        if cached is not None:
            return cached
        failed = False
        for source, lookup in (("library", self._library_bio), ("lastfm", self._lastfm_bio),
                               ("audiodb", self._audiodb_bio)):
            try:
                text = lookup(artist)
            except Exception as exc:
                xbmc.log("[rotation] Artist biography from %s failed for %r: %s"
                         % (source, artist, exc), xbmc.LOGINFO)
                failed = True
                continue
            if text:
                self._store_artist(artist, text, source)
                xbmc.log("[rotation] Artist biography for %r from %s (%d chars)"
                         % (artist, source, len(text)), xbmc.LOGINFO)
                return text
        if not failed:
            self._store_artist(artist, "", "")
        return ""
