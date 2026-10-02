# -*- coding: utf-8 -*-
"""
Matches the user picked by hand for tracks Rotation could not find itself.

A saved match overrides automatic MusicMP3.ru matching everywhere: the
availability check, streaming and downloads. A track can also be marked
"not on MusicMP3.ru" so Rotation stops searching for it.

With shared sync on, the file lives next to the shared favorites
(<music source>/.rotation/matches.json) so every Kodi device uses the same
choices. The shared folder is the one the user already picked for favorites;
this module only reads that saved choice and never prompts, because the
download worker uses it too and runs without a UI.
"""

import json
import os
import time

import xbmcaddon

from .library import norm_artist, norm_title
from .shared_sync import _read_vfs, _write_vfs

NOT_AVAILABLE = "none"
_CACHE = {}
_CACHE_TTL = 5.0


def match_key(entry):
    """Same identity the availability cache uses: artist + exact title."""
    return "%s\n%s" % (norm_artist((entry or {}).get("artist", "")),
                       norm_title((entry or {}).get("title", ""), False))


def _profile():
    try:
        from xbmcvfs import translatePath
    except ImportError:          # Kodi 18
        from xbmc import translatePath
    return translatePath(xbmcaddon.Addon("plugin.audio.rotation")
                         .getAddonInfo("profile"))


def _shared_path(profile):
    addon = xbmcaddon.Addon("plugin.audio.rotation")
    if addon.getSetting("shared_favorites_sync") != "true":
        return ""
    try:
        with open(os.path.join(profile, "kodi-music-source.json"), "r",
                  encoding="utf-8") as stream:
            root = (json.load(stream) or {}).get("path", "")
    except (OSError, ValueError, TypeError):
        return ""
    return root.rstrip("/\\") + "/.rotation/matches.json" if root else ""


class MatchStore(object):
    def __init__(self, profile=None):
        profile = profile or _profile()
        self.local_path = os.path.join(profile, "matches.json")
        self.path = _shared_path(profile) or self.local_path
        if self.path != self.local_path and not _read_vfs(self.path, "matches"):
            # First use of the shared file: carry over this device's choices.
            local = _read_vfs(self.local_path, "matches")
            if local.get("matches"):
                _write_vfs(self.path, local, "matches")

    def _all(self):
        cached = _CACHE.get(self.path)
        if cached and time.time() - cached[0] < _CACHE_TTL:
            return dict(cached[1])
        payload = _read_vfs(self.path, "matches")
        rows = payload.get("matches") if payload else None
        rows = rows if isinstance(rows, dict) else {}
        _CACHE[self.path] = (time.time(), dict(rows))
        return rows

    def _save(self, rows):
        payload = {"version": 1, "updated": time.time(), "matches": rows}
        if not _write_vfs(self.path, payload, "matches"):
            _CACHE.pop(self.path, None)
            raise IOError("Could not save the match to %s" % self.path)
        _CACHE[self.path] = (time.time(), dict(rows))

    def get(self, entry):
        """The saved choice for entry: a match dict, NOT_AVAILABLE, or None."""
        row = self._all().get(match_key(entry))
        if not isinstance(row, dict):
            return None
        if row.get("status") == NOT_AVAILABLE:
            return NOT_AVAILABLE
        return row.get("match") if isinstance(row.get("match"), dict) else None

    def save(self, entry, match):
        rows = self._all()
        keep = ("track_id", "rel", "album_url", "title", "artist", "album",
                "duration", "image")
        rows[match_key(entry)] = {
            "status": "match", "chosen_at": time.time(),
            "entry": {k: entry.get(k, "") for k in ("artist", "title", "album")},
            "match": {k: match.get(k, "") for k in keep}}
        self._save(rows)

    def mark_unavailable(self, entry):
        rows = self._all()
        rows[match_key(entry)] = {
            "status": NOT_AVAILABLE, "chosen_at": time.time(),
            "entry": {k: entry.get(k, "") for k in ("artist", "title", "album")}}
        self._save(rows)

    def clear(self, entry):
        rows = self._all()
        if rows.pop(match_key(entry), None) is not None:
            self._save(rows)
            return True
        return False


def saved_match(entry):
    """Convenience lookup that never raises."""
    try:
        return MatchStore().get(entry)
    except Exception:
        return None
