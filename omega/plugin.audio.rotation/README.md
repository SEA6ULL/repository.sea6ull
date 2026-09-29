# Rotation

Rotation is a Kodi music add-on that turns current charts, editorial playlists,
artist radio, and intelligent library mixes into playlists made entirely from
music already scanned into Kodi.

## Features

- Global, country, and genre charts from Deezer and Last.fm metadata
- Deezer featured playlists and playlists by genre
- Artist Radio seeded by search or a Kodi library artist
- Universal album search for albums, compilations, scores, and soundtracks
- Local-only Library Radio by artist, genre, decade, rediscovery, or full mix
- Availability-first playlist matching with one final availability notification
- Artwork enrichment for local and browsable streaming tracks
- Followed playlists and favorite radio stations
- Optional cross-device favorites sync through a `.rotation` folder in the
  shared Kodi music source; credentials and caches remain device-local
- High-resolution cover, portrait, and fanart enrichment with local Kodi art
  preferred first and fanart.tv results ranked by votes
- Automatic matching-index validation when the Kodi music library changes
- Optional in-Kodi unavailable-track catalog and synchronized CSV export
- Phosphor Duotone navigation artwork optimized for large Kodi list tiles

Rotation never downloads or acquires music. Local-library playback is the
default; the optional MusicMP3.ru switch can provide streaming fallbacks.

## Optional services

Deezer endpoints used by Rotation require no user key. A personal Last.fm API
key improves charts and radio. A fanart.tv personal API key enables additional
artist backgrounds. Keys are intentionally blank in distributed packages.

## Credits and licenses

Rotation is authored by SEA6ULL / GPT and licensed under GPL-3.0.

Navigation symbols are based on Phosphor Icons and retain their bundled MIT
license attribution in `resources/media/icons/LICENSE.phosphor.txt`.
