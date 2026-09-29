# -*- coding: utf-8 -*-
"""Portable, one-file-per-playlist storage for Rotation user playlists."""

import json
import os
import time
import uuid

import xbmcvfs


TRACK_FIELDS = (
    "artist", "title", "album", "album_artist", "duration", "cover",
    "artist_cover", "id", "album_id", "explicit", "file",
)


def _read(path):
    for candidate in (path, path + ".bak"):
        try:
            if not xbmcvfs.exists(candidate):
                continue
            handle = xbmcvfs.File(candidate)
            raw = handle.read()
            handle.close()
            payload = json.loads(raw) if raw else {}
            if (isinstance(payload, dict) and payload.get("id") and
                    isinstance(payload.get("tracks"), list)):
                return payload
        except (ValueError, TypeError, OSError):
            continue
    return {}


def _write(path, payload):
    parent = path.replace("\\", "/").rsplit("/", 1)[0]
    if not xbmcvfs.mkdirs(parent) and not xbmcvfs.exists(parent):
        return False
    temporary = path + ".tmp-" + uuid.uuid4().hex
    backup = path + ".bak"
    try:
        handle = xbmcvfs.File(temporary, "w")
        handle.write(json.dumps(payload, ensure_ascii=False, indent=2,
                                sort_keys=True))
        handle.close()
        if not _read(temporary):
            xbmcvfs.delete(temporary)
            return False
        if xbmcvfs.exists(backup):
            xbmcvfs.delete(backup)
        if xbmcvfs.exists(path) and not xbmcvfs.rename(path, backup):
            xbmcvfs.delete(temporary)
            return False
        if xbmcvfs.rename(temporary, path):
            return True
        if xbmcvfs.exists(backup):
            xbmcvfs.rename(backup, path)
        xbmcvfs.delete(temporary)
    except Exception:
        try:
            xbmcvfs.delete(temporary)
        except Exception:
            pass
    return False


def _identity(track):
    def clean(value):
        return " ".join(str(value or "").casefold().split())
    return clean(track.get("artist")), clean(track.get("title"))


def _portable_track(track):
    row = {key: track.get(key) for key in TRACK_FIELDS if track.get(key) not in (None, "")}
    row["artist"] = str(row.get("artist") or "").strip()
    row["title"] = str(row.get("title") or "").strip()
    if "duration" in row:
        try:
            row["duration"] = int(float(row["duration"]))
        except (TypeError, ValueError):
            row.pop("duration", None)
    return row


class UserPlaylistStore(object):
    def __init__(self, directory):
        self.directory = directory.rstrip("/\\")
        xbmcvfs.mkdirs(self.directory)

    def _path(self, playlist_id):
        safe = "".join(ch for ch in str(playlist_id) if ch.isalnum() or ch in "-_")
        return self.directory + "/" + safe + ".json"

    def list(self):
        try:
            _, files = xbmcvfs.listdir(self.directory)
        except Exception:
            return []
        rows = []
        for filename in files:
            if filename.endswith(".json"):
                row = _read(self.directory + "/" + filename)
                if row:
                    rows.append(row)
        rows.sort(key=lambda row: (-float(row.get("modified") or 0),
                                   str(row.get("name") or "").casefold()))
        return rows

    def get(self, playlist_id):
        return _read(self._path(playlist_id))

    def create(self, name):
        now = time.time()
        row = {"version": 1, "id": uuid.uuid4().hex,
               "name": str(name).strip(), "created": now,
               "modified": now, "tracks": []}
        if not _write(self._path(row["id"]), row):
            raise IOError("Could not create playlist")
        return row

    def save(self, row):
        row = dict(row)
        row["version"] = 1
        row["modified"] = time.time()
        row["tracks"] = [_portable_track(track) for track in row.get("tracks", [])
                         if track.get("title")]
        if not _write(self._path(row["id"]), row):
            raise IOError("Could not save playlist")
        return row

    def delete(self, playlist_id):
        path = self._path(playlist_id)
        deleted = not xbmcvfs.exists(path) or xbmcvfs.delete(path)
        backup = path + ".bak"
        if xbmcvfs.exists(backup):
            xbmcvfs.delete(backup)
        return deleted

    def add_tracks(self, playlist_id, tracks, allow_duplicates=False):
        row = self.get(playlist_id)
        if not row:
            raise KeyError(playlist_id)
        existing = {_identity(track) for track in row.get("tracks", [])}
        added, skipped = [], []
        for track in tracks:
            portable = _portable_track(track)
            key = _identity(portable)
            if not all(key):
                continue
            if not allow_duplicates and key in existing:
                skipped.append(portable)
                continue
            row["tracks"].append(portable)
            existing.add(key)
            added.append(portable)
        if added:
            self.save(row)
        return added, skipped

    def remove_duplicates(self, playlist_id):
        row = self.get(playlist_id)
        seen, tracks = set(), []
        for track in row.get("tracks", []):
            key = _identity(track)
            if key not in seen:
                seen.add(key)
                tracks.append(track)
        removed = len(row.get("tracks", [])) - len(tracks)
        if removed:
            row["tracks"] = tracks
            self.save(row)
        return removed
