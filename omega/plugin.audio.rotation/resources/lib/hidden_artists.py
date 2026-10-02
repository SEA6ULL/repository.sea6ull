"""Persistent, exact-name artist visibility preferences."""
import json
import os
import tempfile
import unicodedata


def artist_key(name):
    return unicodedata.normalize("NFKC", str(name or "")).strip().casefold()


class HiddenArtists:
    def __init__(self, directory):
        self.directory = directory
        self.path = os.path.join(directory, "hidden-artists.json")

    def all(self):
        try:
            with open(self.path, encoding="utf-8") as stream:
                names = json.load(stream)
        except FileNotFoundError:
            return {}
        if not isinstance(names, list) or not all(isinstance(n, str) for n in names):
            raise ValueError("Invalid hidden artists file")
        return {artist_key(n): n for n in names if artist_key(n)}

    def set_hidden(self, name, hidden):
        names = self.all()
        key = artist_key(name)
        if not key:
            return
        if hidden:
            names[key] = str(name).strip()
        else:
            names.pop(key, None)
        os.makedirs(self.directory, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix="hidden-artists-", suffix=".tmp", dir=self.directory)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump(sorted(names.values(), key=artist_key), stream, ensure_ascii=False)
            os.replace(temporary, self.path)
        finally:
            if os.path.exists(temporary):
                os.remove(temporary)
