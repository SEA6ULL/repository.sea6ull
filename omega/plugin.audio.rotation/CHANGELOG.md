## 1.0.24 — 2026-09-30
- Expand the public README with the Rotation introduction, playlist-import formats and steps, and a downloadable TXT prompt for AI-created lists.
- Include five screenshots, a README showcase, and a complete gallery for GitHub visitors.
- Documentation-only release; playback, artwork loading, and navigation are unchanged.

## 1.0.23 — 2026-09-30
- Replace the custom scrollable import preview with Kodi's standard confirmation: song count plus Import and Cancel.
- Remove the preview/editor message and custom dialog files; keep the editable imported-playlist name after confirmation.

## 1.0.22 — 2026-09-30
- Remove the omitted-line count from the import preview header.
- Clarify that changes are made in the source file; the preview remains a read-only list with Import and Cancel buttons.

## 1.0.21 — 2026-09-30
- Prepopulate new playlists created from albums with Artist — Album, preferring album-artist metadata and keeping the existing editable duplicate-name behavior.
- Preserve Kodi path history when navigating to addon/artist homes and returning from the playlist-cover editor.
- Avoid committing empty successful directories during radio/album-search redirects; defer search/random artist transitions until their action dialogs close.
- Cancel delayed redirects if the user has already backed out of their source screen.

## 1.0.20 — 2026-09-30
- Move playlist editing and missing-track reporting from standalone rows into the summary row's context menu; preserve song management menus.
- Move Rotation Plus album download/completion and Most Wanted Albums actions into the summary context menu.
- Keep empty playlists editable through their summary and expose Library Radio playback actions there too.
- Allow explicit missing-track reporting from My Playlists.

## 1.0.19 — 2026-09-30
- Give playlist summary rows their selected playlist cover, including genre/popular playlists, followed playlists, albums, and My Playlists.
- Use library-first artist thumbnails, fanart and clearlogos for Artist Radio, artist Top Tracks and Library Radio by Artist summaries.
- Include the radio seed artist in the existing consolidated artwork pass when it is absent from the track list; no extra refreshes.

## 1.0.18 — 2026-09-30
- Use one shared progress-dialog owner across separate Kodi Python invocations.
- Keep completed song counts visible throughout the artist artwork phase.
- Remove partial availability redraws and combine scan/artwork completion into one final refresh.
- Wait until progress-dialog teardown finishes before applying that refresh.

## 1.0.17 — 2026-09-30
- Identify open playlists with a directory-scoped marker for artwork progress and the single completion refresh.
- Use the same canonical playlist location in browse, availability scan, and playback workers.
- Allow final refreshes with harmless background skin dialogs; keep context-menu, modal, busy-dialog and player-window guards.
- Log progress visibility and refresh hold reasons to diagnose remaining Kodi-specific behavior.

## 1.0.16
- Restore the artwork progress indicator across equivalent Kodi playlist URLs and re-entry during an active pass.
- Apply one final artwork refresh through an independent script, including after an in-place addon update; no per-item refreshes.
- Build imported playlist mosaic covers from learned metadata and cached album art; update the texture path as artwork becomes available.

## 1.0.15 — 2026-09-30

- Combine imported-song preview with explicit Import Playlist and Cancel buttons.
- Move the final playlist artwork redraw to the independent service after the worker exits.

## 1.0.14 — 2026-09-30

- Fix missing xbmcvfs import when reading playlist files.

## 1.0.13 — 2026-09-30

- Import TXT or CSV playlists through Kodi's file browser from any accessible location.
- Show import format help, including an AI prompt requesting a downloadable .txt file.
- Review parsed songs and omitted lines; edit the suggested filename-based name before saving.
- Save imports as independent, editable My Playlists. No import folder or AI account required.
- Retry genuine streaming request failures once before a fixed 60-second cooldown.
- Blocked attempts no longer extend cooldown; show one outage notice and countdowns for manual retries.
- Continue with remaining library tracks during source failures and report accurately when none remain.
- Preserve the completed-artwork single-refresh behavior from 1.0.12.

# Rotation Changelog

## 1.0.11

- Added library-first artist clearlogos throughout artist and playlist views.
- Added fanart.tv clearlogo discovery for artists outside the Kodi library.
- Passed artist clearlogos to local and streamed playback items so compatible
  skins can display them in the player.
- Existing artwork cache entries are upgraded in place and checked for logos
  without discarding their saved thumbnails or fanart.

## 1.0.10

- Carry cached library-first artist fanart from playlist rows into streaming playback.
- Stop replacing available artist fanart with Rotation's fallback when a remote song starts.
- Keep playback artwork lookup cache-only so starting a song remains immediate.

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
