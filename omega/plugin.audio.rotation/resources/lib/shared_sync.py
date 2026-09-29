# -*- coding: utf-8 -*-
"""Favorites synchronization through a shared Kodi music source.

Only portable user data belongs here. Artwork/cache databases and account
credentials deliberately remain local to each Kodi installation.
"""

import json
import ntpath
import os
import time
import uuid

import xbmc
import xbmcgui
import xbmcvfs


def _json_rpc(method, params=None):
    request = {"jsonrpc": "2.0", "id": 1, "method": method}
    if params is not None:
        request["params"] = params
    return json.loads(xbmc.executeJSONRPC(json.dumps(request)))


def _music_sources():
    attempts = [
        ("AudioLibrary.GetSources", {"properties": ["file", "paths"]}),
        ("Files.GetSources", {"media": "music"}),
    ]
    for method, params in attempts:
        try:
            sources = ((_json_rpc(method, params).get("result") or {})
                       .get("sources") or [])
            rows = []
            for source in sources:
                for path in source.get("paths") or [source.get("file")]:
                    if path:
                        rows.append({"label": source.get("label") or path,
                                     "path": str(path).strip()})
            if rows:
                unique = {}
                for row in rows:
                    unique.setdefault(row["path"], row)
                return list(unique.values())
        except (TypeError, ValueError):
            continue
    return []


def _read_local_json(path):
    try:
        with open(path, "r", encoding="utf-8") as stream:
            return json.load(stream)
    except (OSError, ValueError, TypeError):
        return {}


def _write_local_json(path, payload):
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        temporary = path + ".tmp"
        with open(temporary, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
        os.replace(temporary, path)
        return True
    except OSError:
        return False


def shared_favorites_path(profile, music_root_hint=""):
    """Resolve or ask for the Kodi music source used for shared data."""
    sources = _music_sources()
    if not sources:
        return ""
    source_file = os.path.join(profile, "kodi-music-source.json")
    saved = _read_local_json(source_file).get("path")
    if saved and any(row["path"] == saved for row in sources):
        root = saved
    elif len(sources) == 1:
        root = sources[0]["path"]
    else:
        name = ntpath.basename(ntpath.normpath(music_root_hint or "")).lower()
        matches = [row for row in sources if name and (
            name == row["label"].strip().lower() or
            name == row["path"].rstrip("/\\").replace("\\", "/").rsplit("/", 1)[-1].lower())]
        if len(matches) == 1:
            root = matches[0]["path"]
        else:
            selected = xbmcgui.Dialog().select(
                "Shared Rotation Music Source",
                ["%s\n%s" % (row["label"], row["path"]) for row in sources])
            if selected < 0:
                return ""
            root = sources[selected]["path"]
    _write_local_json(source_file, {"path": root})
    return root.rstrip("/\\") + "/.rotation/favorites.json"


def _read_vfs(path):
    for candidate in (path, path + ".bak"):
        try:
            if not xbmcvfs.exists(candidate):
                continue
            handle = xbmcvfs.File(candidate)
            raw = handle.read()
            handle.close()
            payload = json.loads(raw) if raw else {}
            if isinstance(payload, dict) and isinstance(payload.get("favorites"), list):
                return payload
        except (ValueError, TypeError):
            continue
    return {}


def _write_vfs(path, payload):
    parent = path.replace("\\", "/").rsplit("/", 1)[0]
    if not xbmcvfs.mkdirs(parent) and not xbmcvfs.exists(parent):
        return False
    temporary = path + ".tmp-" + uuid.uuid4().hex
    try:
        handle = xbmcvfs.File(temporary, "w")
        handle.write(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        handle.close()
        if not _read_vfs(temporary):
            xbmcvfs.delete(temporary)
            return False
        backup = path + ".bak"
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


class SharedFavoritesStore(object):
    """FavoritesStore-compatible JSON store shared by multiple Kodi devices."""

    def __init__(self, path, local_store):
        self.path = path
        self.local = local_store
        if not _read_vfs(path):
            rows = local_store.export_favorites()
            if not _write_vfs(path, {"version": 1, "updated": time.time(),
                                     "favorites": rows}):
                raise IOError(
                    "Rotation could not write to the selected Kodi music source. "
                    "Check the folder's sharing permissions and Kodi's SMB credentials.")

    def _rows(self):
        payload = _read_vfs(self.path)
        return payload.get("favorites", []) if payload else []

    def _save(self, rows):
        if not _write_vfs(self.path, {"version": 1, "updated": time.time(),
                                     "favorites": rows}):
            raise IOError("Could not update the shared Rotation favorites file")

    def add_favorite(self, kind, url, label, thumb="", artist="", album=""):
        rows = [row for row in self._rows() if row.get("url") != url]
        rows.append({"kind": kind, "url": url, "label": label,
                     "thumb": thumb, "artist": artist, "album": album,
                     "added_at": time.time()})
        self._save(rows)

    def remove_favorite(self, url):
        self._save([row for row in self._rows() if row.get("url") != url])

    def is_favorite(self, url):
        return any(row.get("url") == url for row in self._rows())

    def get_favorites(self, kind=None):
        rows = self._rows()
        if kind:
            rows = [row for row in rows if row.get("kind") == kind]
        rows.sort(key=lambda row: float(row.get("added_at") or 0), reverse=True)
        return [{key: row.get(key, "") for key in
                 ("kind", "url", "label", "thumb", "artist", "album")}
                for row in rows]

    def set_order(self, kind, urls):
        rows = self._rows()
        anchor = time.time()
        order = {url: anchor - position for position, url in enumerate(urls)}
        for row in rows:
            if row.get("kind") == kind and row.get("url") in order:
                row["added_at"] = order[row["url"]]
        self._save(rows)
