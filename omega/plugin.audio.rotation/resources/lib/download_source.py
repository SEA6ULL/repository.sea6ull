"""Conservative MusicMP3.ru matching for permanent downloads."""
import re
from .library import norm_artist, norm_title, primary_artist, _similar, recording_title, art_artist_name

VERSIONS = re.compile(r'\b(live|acoustic|remix|instrumental|karaoke|tribute|demo|cover|unplugged|extended|sped|slowed)\b', re.I)


def best_match(entry, rows):
    wanted_versions = set(VERSIONS.findall(recording_title(entry.get('title', '')).lower()))
    best, score = None, 0
    for row in rows:
        if not row.get('track_id') or not row.get('rel') or not row.get('artist'):
            continue
        if set(VERSIONS.findall(recording_title(row.get('title', '')).lower())) != wanted_versions:
            continue
        artist_score = max(_similar(norm_artist(entry.get('artist')), norm_artist(row['artist'])),
                           _similar(norm_artist(art_artist_name(entry.get('artist'))), norm_artist(art_artist_name(row['artist']))))
        title_score = _similar(norm_title(recording_title(entry.get('title')), False), norm_title(recording_title(row.get('title')), False))
        if artist_score < .94 or title_score < .94:
            continue
        candidate = (artist_score + title_score) / 2
        if entry.get('album') and norm_title(entry['album'], False) == norm_title(row.get('album'), False):
            candidate += .02
        if candidate > score:
            best, score = row, candidate
    return best


class MusicMP3Source:
    def __init__(self, api):
        self.api = api
        self.albums = {}

    def resolve(self, entry):
        if not entry.get('artist') or not entry.get('title'):
            raise ValueError('Missing artist or track title')
        # A match the user picked (synced across devices) always wins.
        from .manual_matches import saved_match, NOT_AVAILABLE
        saved = saved_match(entry)
        if saved == NOT_AVAILABLE:
            raise ValueError('Marked as not on MusicMP3.ru')
        if saved:
            return dict(saved, manual=True)
        if entry.get('album'):
            album_artist = entry.get('album_artist') or entry['artist']
            key = (norm_artist(album_artist), norm_title(entry['album'], False))
            if key not in self.albums:
                matches = self.api.search(entry['album'], 'albums', fast=True)
                album = next((row for row in matches
                    if _similar(norm_artist(album_artist), norm_artist(row.get('artist'))) >= .94
                    and _similar(norm_title(entry['album'], False), norm_title(row.get('title'), False)) >= .94), None)
                rows = self.api.album_tracks(album['link'])[0] if album and album.get('link') else []
                self.albums[key] = [dict(row, track_number=number)
                                    for number, row in enumerate(rows, 1)]
            found = best_match(entry, self.albums[key])
            if found:
                return found
        for query in (entry['artist'] + ' ' + entry['title'], entry['title']):
            found = best_match(entry, self.api.search(query, 'songs', limit=150, fast=True))
            if found:
                return found
            if self.api.last_error:
                raise RuntimeError('MusicMP3.ru is unavailable: ' + self.api.last_error)
        raise ValueError('No confident artist/recording match found')
