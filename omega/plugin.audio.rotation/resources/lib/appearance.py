"""Persistent user artwork; all interaction uses Kodi's standard dialogs."""
import json
import os
import uuid

import xbmc
import xbmcgui
import xbmcvfs
from PIL import Image


LABELS = {"fanart": "Fallback Fanart", "artist": "Fallback Artist Image",
          "album": "Fallback Album Image"}


class Appearance:
    def __init__(self, profile):
        self.directory = os.path.join(profile, "custom_artwork")
        self.manifest = os.path.join(self.directory, "images.json")

    def _read(self):
        try:
            with open(self.manifest, encoding="utf-8") as stream:
                data = json.load(stream)
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _path(self, filename):
        if not isinstance(filename, str) or os.path.basename(filename) != filename:
            return ""
        return os.path.join(self.directory, filename) if filename else ""

    def image(self, kind, default):
        path = self._path(self._read().get(kind, ""))
        return path if path and os.path.isfile(path) else default

    def _write(self, data):
        os.makedirs(self.directory, exist_ok=True)
        temporary = self.manifest + "." + uuid.uuid4().hex + ".tmp"
        try:
            with open(temporary, "w", encoding="utf-8") as stream:
                json.dump(data, stream)
            os.replace(temporary, self.manifest)
        finally:
            if os.path.exists(temporary):
                os.remove(temporary)

    def choose(self, kind):
        source = xbmcgui.Dialog().browseSingle(
            1, "Choose " + LABELS[kind], "files",
            ".jpg|.jpeg|.png|.JPG|.JPEG|.PNG", True, False)
        if not source:
            return False
        extension = os.path.splitext(source)[1].lower()
        if extension not in (".jpg", ".jpeg", ".png"):
            raise ValueError("Choose a JPG or PNG image.")
        os.makedirs(self.directory, exist_ok=True)
        filename = kind + "-" + uuid.uuid4().hex + extension
        destination = self._path(filename)
        try:
            if not xbmcvfs.copy(source, destination):
                raise OSError("The selected image could not be copied.")
            with Image.open(destination) as image:
                if image.format not in ("JPEG", "PNG"):
                    raise ValueError("Choose a valid JPG or PNG image.")
                image.verify()
            # Decode as well: verify() alone does not detect truncated JPEGs.
            with Image.open(destination) as image:
                image.load()
            data = self._read()
            old = self._path(data.get(kind, ""))
            data[kind] = filename
            self._write(data)
        except Exception:
            if os.path.exists(destination):
                os.remove(destination)
            raise
        if old and old != destination:
            self._remove(old)
        return True

    @staticmethod
    def _remove(path):
        try:
            os.remove(path)
        except OSError:
            pass

    def restore(self, kind):
        data = self._read()
        old = self._path(data.pop(kind, ""))
        self._write(data)
        if old:
            self._remove(old)


def change_image(addon, kind, action):
    if kind not in LABELS or action not in ("choose", "restore"):
        return False
    store = Appearance(xbmcvfs.translatePath(addon.getAddonInfo("profile")))
    try:
        if action == "restore":
            store.restore(kind)
        elif not store.choose(kind):
            return False
    except Exception as exc:
        xbmc.log("[rotation] Custom artwork: %s" % exc, xbmc.LOGWARNING)
        xbmcgui.Dialog().ok(LABELS[kind], "The image could not be saved. "
                           "Choose a valid, accessible JPG or PNG image.")
        return False
    xbmcgui.Dialog().notification(
        addon.getAddonInfo("name"),
        LABELS[kind] + (" restored to default" if action == "restore" else " updated"),
        xbmcgui.NOTIFICATION_INFO, 2500)
    return True
