# -*- coding: utf-8 -*-
"""Read portable playlist lists without Kodi or network dependencies."""
import csv
import io
import re

HELP = """Import a TXT or CSV playlist from any location Kodi can access.

TXT: One song per line, formatted as Artist - Song Title.
Example: Chris Stapleton - Tennessee Whiskey

CSV: Include columns named artist and title. An album column is optional.
Use CSV when an artist or song name itself contains ' - '.

Asking an AI? Use this instruction:
Provide this playlist as a downloadable .txt file, one song per line in Artist - Song Title format, without numbering, explanations, or Markdown.

You can review songs and edit the playlist name before saving. The saved playlist does not depend on the original file."""


def parse_playlist(raw, extension):
    if isinstance(raw, (bytes, bytearray)):
        try:
            text = bytes(raw).decode("utf-8-sig")
        except UnicodeDecodeError:
            text = bytes(raw).decode("cp1252")
    else:
        text = str(raw).lstrip("\ufeff")
    tracks, problems, seen = [], [], set()

    def add(artist, title, album, line):
        artist, title = (artist or "").strip(), (title or "").strip()
        if not artist or not title:
            problems.append("Line %d: missing artist or title" % line)
            return
        key = (artist.casefold(), title.casefold())
        if key in seen:
            problems.append("Line %d: duplicate omitted — %s - %s" % (line, artist, title))
            return
        seen.add(key)
        row = {"artist": artist, "title": title}
        if album and album.strip(): row["album"] = album.strip()
        tracks.append(row)

    if extension.lower() == ".csv":
        reader = csv.DictReader(io.StringIO(text, newline=""))
        fields = {str(k).strip().casefold(): k for k in (reader.fieldnames or [])}
        if not {"artist", "title"}.issubset(fields):
            raise ValueError("CSV must include columns named artist and title.")
        try:
            for row in reader:
                if None in row:
                    problems.append("Line %d: extra columns; quote names containing commas" % reader.line_num)
                    continue
                add(row.get(fields["artist"]), row.get(fields["title"]),
                    row.get(fields.get("album"), ""), reader.line_num)
        except csv.Error as exc:
            raise ValueError("Could not read CSV: %s" % exc)
    elif extension.lower() == ".txt":
        for number, line in enumerate(text.splitlines(), 1):
            line = line.strip()
            if not line or line.startswith("```"): continue
            line = re.sub(r"^(?:\d+[.)]\s+|[-*•]\s+)", "", line)
            parts = re.split(r"\s+[-–—]\s+", line, maxsplit=1)
            if len(parts) != 2:
                problems.append("Line %d: expected Artist - Song Title" % number)
                continue
            add(parts[0], parts[1], "", number)
    else:
        raise ValueError("Choose a TXT or CSV file.")
    return tracks, problems
