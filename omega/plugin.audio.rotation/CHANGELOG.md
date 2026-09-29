# Rotation Changelog

## 1.0.9

- Prepopulate snapshot names from the displayed source-playlist title.
- Add dates to personalized snapshots and duplicate named-playlist snapshots.
- Preserve the actual Deezer playlist title after opening a Popular Playlist.
- Keep every suggested snapshot name editable before creation.

## 1.0.8

- Version every custom playlist-cover filename so Kodi immediately displays edits.
- Make Shuffle Mosaic create a genuinely new mosaic on every selection.
- Fix album-cover choices appearing to do nothing after another custom cover was cached.
- Remove superseded managed custom-cover files after successful replacement.

## 1.0.7

- Delete managed local and shared playlist covers when their playlist is deleted.
- Remove orphaned playlist covers while clearing the artwork cache.
- Preserve Kodi's global thumbnail cache and unrelated artwork.

## 1.0.6

- Show cancellable native progress while locating playable playlist sources.
- Fully hide and guard library-only actions when Kodi library use is disabled.
- Add a visual My Playlists cover editor with automatic, shuffled, album, and custom-image covers.
- Keep custom covers managed and rename-safe, including shared-library sync storage.
- Use clean one-, two-, and three-panel automatic covers without repeated artwork.

## 1.0.5

- Retry MusicMP3.ru searches with accent-folded artist and track names.
- Add a strictly validated title-only fallback when combined searches return no match.
- Resolve known albums through MusicMP3.ru once and reuse their canonical track listing.
- Keep the existing strict artist/title thresholds to prevent incorrect playback matches.

## 1.0.4

- Recognize MusicMP3.ru HTTP 429 and 5xx stream responses as temporary provider outages.
- Pause playlist lookahead during a provider outage instead of repeatedly testing every remaining track.
- Preserve the interrupted track and untested playlist queue, then resume automatically after cooldown.

## 1.0.3

- Treat an empty Kodi music library as a valid cached state instead of repeatedly rebuilding it.
- Add an optional streaming-only mode that disables Kodi music-library queries and hides library-only sections.
- Pre-buffer two verified tracks so Kodi exposes Next immediately when another playable song exists.
- Prevent overlapping playback lookahead workers and pause streaming checks when the provider is unavailable.
- Clarify that a personal fanart.tv API key is optional and improves artwork coverage.

## 1.0.2

- Added an offline Changelog viewer under Maintenance.

## 1.0.1

- Renamed Favorite Playlists to Followed Playlists.
- Added clear read/write permission guidance for cross-device synchronization.
- Added Kodi music-source selection when more than one source is configured.
- Added a clear notification when Rotation cannot write to the shared source.
- Improved automatic artwork refreshes on Your Top Artists and other artist listings.
- Fixed completed artist-artwork batches remaining permanently marked as active.

## 1.0.0

- Initial release of Rotation as an independently installable add-on.
- Added Artist Radio, Library Radio, charts, editorial playlists, album search and artist discovery.
- Added personalized For You recommendations using Last.fm listening history.
- Added My Playlists, Followed Playlists, favorites and optional cross-device synchronization.
- Added library-first artwork, background artwork enrichment and playlist cover generation.
- Added library availability labels, missing-music reports and CSV export.
- Added optional MusicMP3.ru streaming, disabled by default.
- Added guarded playlist playback and service-health handling.
