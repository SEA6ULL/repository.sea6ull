## 1.0.67
- DOWNLOADS
- New setting, While Streaming from MusicMP3.ru: Keep Downloading (default) or Wait Until Streaming Stops. "Streaming" means a Rotation session with MusicMP3.ru songs, including a mixed playlist while one of its library songs plays. Library-only playback, videos and other add-ons never affect downloads.
- Keep Downloading: one track keeps downloading while you stream. The routine check of the next song no longer pauses it: if the site is busy, the check is retried every 15 s (your current song finishes transferring before it finishes playing, which frees its connection), and only with 20 s of the song left may it pause the download so the next song is ready in time.
- Wait Until Streaming Stops: no new track starts while streaming; a track already downloading is allowed to finish, and the download resumes by itself afterwards ("Waiting · streaming" in the progress line).
- With "Wait Until Streaming Stops", downloads now keep waiting for a while after streaming stops, in case you're only picking something else to play: new setting Resume Downloads After Streaming Stops (Immediately, 1, 2 or 5 minutes; default 2 minutes), shown only with that option. Starting a video or music from your Kodi library ends the wait at once. Pausing a song and the moments between songs never count as stopping. The progress line counts down ("Waiting · resuming in 1:35").
- A paused track restarts from zero (the site can't resume), so a track estimated to finish within 15 s is never paused, and when one of two transfers must give way, the one furthest along keeps going.
- Download page progress heading reads "Updating Information" instead of "Updating Artwork", since the background pass now also fetches album details, release dates and biographies.

- INFORMATION
- Albums show their release date in the fields Kodi's own library widgets use for the info line.
- The description area shows an album facts line. Toggled on by user with user selected information.
- The album page header shows the album's "about" text from Last.fm (when a Last.fm API key is set) or TheAudioDB (when enabled), cached per album; otherwise the facts line.
- Artist biographies, in the description field show for artists (Artist_Description) if the skin supports it. From the Kodi library when it has one, otherwise Last.fm (needs a Last.fm API key) or TheAudioDB.

- MISC
- Downloads: Find a Match… is offered on every track that isn't downloaded, including ones never attempted, which are now labelled "Not attempted yet" (or "Waiting" while the download runs) instead of showing the whole download's status.
- Remove the remaining symbols from menu labels ("↕ Reorder…", the Stereo Upmix toggle) and from the "Saved to…" notice and "Done Reordering" entry.

## 1.0.61
- DOWNLOADS
- Add Downloads under My Music and direct download context actions.
- Add Settings → Downloads: destination, playlist organization, optional library scan, per-download options prompt, and playlist saving/export, with explanatory helper text. Defaults use Various Artists compilation tagging, no automatic scan, and no options prompt.
- Write artist/title/album/album-artist tags and cover artwork; compilation downloads retain each song’s performer while using the playlist name and Various Artists. Album and individual track downloads preserve original album organization.
- Share MusicMP3.ru's two-connection limit between streaming and downloads. Kodi playing a MusicMP3.ru song counts as one connection; downloads use what's left. Streaming always comes first: while a stream plays, downloads drop to one transfer and pause briefly whenever the next song is checked. Playing from the Kodi library doesn't affect downloads.
- Downloads pause instead of failing. A paused track keeps its partial file and resumes from the same byte. "Server busy" (HTTP 503/429) means wait and retry with growing delays; a track is only given up after about 15 minutes of busy answers. The download progress shows "Paused while streaming" or "Waiting for a free connection".
- Add Download Notifications: Continuous Progress, Milestones, and When Finished (default). Downloads screens override the preference with continuous progress; full-screen players hide progress.
- Limit library scans to actual downloaded album/compilation folders, skip automatic scans after cancellation, and add a manual Scan Downloaded Folders context action.
- Add Export Playlist to Downloads, using Kodi’s folder picker and an ordered M3U8 of accessible files. Confirm unavailable omissions and protect existing export files.
- Wait one minute after Kodi starts before resuming a paused download, so it doesn't add to start-up load. Starting or retrying a download, or opening Downloads, still resumes it straight away.
- Find a Match… on any track that isn't in your Kodi library (album and playlist pages, and failed tracks in Downloads). Shows the closest MusicMP3.ru recordings with album, length and the difference from the expected length; each can be previewed before choosing. Also offers Search with Different Words… and Not on MusicMP3.ru.

- ARTWORK
- Optimize art caching and standardize across windows as much as possible.
- Ask fanart.tv's voted album covers first when a key is set; Deezer/iTunes/Cover Art Archive fill gaps. Deezer grid covers are shown immediately and upgraded once in the background.
- Use TheAudioDB clearlogos as an unvoted fallback.
- Ask fanart.tv before TheAudioDB and skip TheAudioDB when fanart.tv already supplied portrait, background and logo.
- Add Refresh Artwork to artist and album context menus. Kodi library art is untouched and still takes priority.
- One artwork order everywhere. Kodi library → resolved online (fanart.tv highest-voted first) → provider tile.
- Fix missing fanart and logos for artists with accented names when another service spells them without the accent.
- Singles & EPs on an artist's albums page use the album fallback image (or your custom one from Appearance) instead of the albums icon, the artist's clearlogo, and "Singles & EPs" as the album title in the info line.

- PLAYER
- Playback order follows the player's shuffle button, as in Kodi's music library.
- While a playlist or album page is open, a streamed track 1 is found and verified in the background, so Play starts almost immediately. The link is re-checked before use and expires after three minutes.
- Remove the symbol prefixes (▶ 🔀 ★ ✕ ↑ ↓ ⇈ ⇊ ↕ ✎ ⌂) from context-menu labels so Rotation's menus match Kodi's own.
- A song Kodi stops early (skip, preview, Stop) is now counted as holding a MusicMP3.ru connection for about 70 seconds, not for the rest of the song. Measured in a real session: still busy 50–59 s after the stop, free again by 67–68 s.

- ARTIST MATCHING
- Use Kodi's MusicBrainz artist IDs when present and cache every resolved MBID.
- Match MusicBrainz artists by alias and without a leading "The", so renamed or differently credited artists (Kanye West, The Goo Goo Dolls) reach fanart.tv instead of being recorded as "no match".
- Rank MusicBrainz artist matches over a bare duplicate.
- If fanart.tv has no record at all for the chosen ID, try the next exact-name match (up to two). Never applies to MBIDs from your Kodi library.
- Treat a MusicBrainz outage during an album lookup as transient rather than caching "no cover" for three days.
- Group explicit featured/collaboration credits under the primary artist in Your Top Artists and For You album recommendations; combine play counts, avoid collaboration portraits/IDs, fetch extra candidates, and preserve genuine band names and full song credits.
- Bundle Mutagen with its GPL license for consistent MP3 validation and ID3 tagging on supported Kodi devices.

- MISC
- Add My Albums and My Songs to My Music. Albums and songs could be saved but the lists had no menu entry.
- Streaming no longer substitutes a live, remix, acoustic or similar version for the track that was asked for, and among good matches prefers the one closest in length.

## 1.0.38
- ARTWORK
- Add Appearance → Icon Style with the existing Rotation pack as default and an optional transparent white pack.
- Add JPG/PNG custom Fallback Art with individual Restore Default actions.

- ARTIST
- Add Hide Artist and a restore dialog.
- Add Artist Biography for every artist. Suppress Information on non-library artist folders rather than error unavailable.
- Standardize artist context menus as much as possible across windows.

- MISC
- Keep My Playlists limited to saved playlists for clean browsing and widgets; move creation, import, and format help to My Music.

## 1.0.24
- Expand the public README with the Rotation introduction, playlist-import formats and steps, and a downloadable TXT prompt for AI-created lists.
- Include five screenshots, a README showcase, and a complete gallery for GitHub visitors.
- Use library-first artist thumbnails, fanart and clearlogos for Artist Radio, artist Top Tracks and Library Radio by Artist summaries.
- Import TXT or CSV playlists through Kodi's file browser from any accessible location.
- Save imports as independent, editable My Playlists. No import folder or AI account required.
- Fully hide and guard library-only actions when Kodi library use is disabled.
- Add a visual My Playlists cover editor with automatic, shuffled, album, and custom-image covers.
- Use clean one-, two-, and three-panel automatic covers without repeated artwork.
- Add an optional streaming-only mode that disables Kodi music-library queries and hides library-only sections.
- Added an offline Changelog viewer under Maintenance.
- Added clear read/write permission guidance for cross-device synchronization.
- Added a clear notification when Rotation cannot write to the shared source.

## 1.0.0

- Initial release of Rotation as an independently installable add-on.
- Added Artist Radio, Library Radio, charts, editorial playlists, album search and artist discovery.
- Added personalized For You recommendations using Last.fm listening history.
- Added My Playlists, Followed Playlists, favorites and optional cross-device synchronization.
- Added library-first artwork, background artwork enrichment and playlist cover generation.
- Added library availability labels, missing-music reports and CSV export.
- Added optional MusicMP3.ru streaming, disabled by default.
- Added guarded playlist playback and service-health handling.