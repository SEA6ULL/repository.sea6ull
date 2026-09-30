# Rotation for Kodi

Rotation turns your existing Kodi music library into a modern music-discovery experience. Instead of browsing only by artist, album, or folder, Rotation uses current charts, editorial playlists, listening history, and artist relationships to create dynamic playlists from music you already own.

## Screenshots

Browse curated playlists, explore artists, and enjoy artwork throughout playback.

### Charts and curated playlists

![Curated playlists with individual cover artwork](screenshots/playlists.jpg)

### Artist pages

![Rage Against the Machine artist page with albums, top tracks, and radio](screenshots/artist.jpg)

### Music playback

![Music player showing album artwork, artist fanart, and clearlogo](screenshots/player.jpg)

[View all five screenshots](screenshots/README.md). These examples use the Arctic Vibe skin; layout and artwork presentation depend on your Kodi skin.

## Highlights

- **Artist Radio** — Choose any artist and build a varied station featuring their music and related artists. Rotation matches the results against your Kodi library and can optionally fill remaining gaps with streaming tracks.
- **Personalized recommendations** — Connect a Last.fm username to unlock Recommended Radio, Your Top Artists, recent favorites, rediscovery mixes, forgotten favorites, and album suggestions based on your listening history.
- **Charts and curated playlists** — Browse worldwide and country charts, Top Tracks by genre, Popular Playlists, Albums of the Week, trending artists, and genre-based discovery pages.
- **Powerful artist pages** — Explore an artist’s albums, top tracks, similar artists, and every matching song in your Kodi library—including tracks appearing on compilations where the album artist is listed as Various Artists.
- **Library Radio** — Create completely local mixes by artist, genre, decade, recently added music, rediscovery, or your full library.
- **Album search** — Search for albums, soundtracks, scores, compilations, and other releases without needing to know the artist first.
- **My Playlists** — Build editable playlists inside Rotation, add songs or complete groups of library tracks, reorder entries, and automatically generate Spotify-style covers from the included album artwork. Import playlists from TXT or CSV files, including lists created by an AI assistant.
- **Followed Playlists and favorites** — Follow provider-managed playlists or save favorite artists, albums, songs, and radio stations for quick access.
- **Cross-device synchronization** — Favorites, their order, and My Playlists can be synchronized through a small hidden folder stored in a shared Kodi music source. This works well in homes with several independent Kodi installations using the same network music library.
- **Library-aware browsing** — Rotation clearly distinguishes music already in your Kodi library from music you do not own. Optional `[x]` labels make missing albums and tracks easy to recognize.
- **Missing-music reports** — Track the songs and albums most frequently missing from generated playlists. Browse them by artist or source playlist, ignore unwanted entries, and export the results as CSV.
- **Artwork designed for Kodi** — Rotation prefers artwork already present in your Kodi library, then enriches missing album covers, artist portraits, fanart, and clearlogos in the background. Playlist artwork updates finish with one refresh after the complete pass, keeping the page usable while artwork loads.
- **Resilient playlist playback** — Rotation checks upcoming streaming tracks before Kodi reaches them, avoids rapid skip loops when a provider is unavailable, and prioritizes local music for dependable playback.
- **Optional MusicMP3.ru streaming** — Streaming support can be enabled when desired and remains disabled by default. The normal library-focused experience works without it.

## Bring your own playlists

Import a song list from a text file or CSV and turn it into a normal, editable playlist in **My Playlists**. You can write the list yourself, receive one from a friend, or ask an AI assistant to create it.

### Text files

Use a `.txt` file with one song per line in `Artist - Song Title` format:

```text
Chris Stapleton - Tennessee Whiskey
Brandi Carlile - The Story
Noah Kahan - Stick Season
```

When asking an AI assistant for a playlist, include this instruction:

> Provide this playlist as a downloadable .txt file, one song per line in Artist - Song Title format, without numbering, explanations, or Markdown.

Rotation imports the resulting file; it does not require an AI account, subscription, or connection inside the add-on.

### CSV files

Use columns named `artist` and `title`. An `album` column is optional:

```csv
artist,title,album
Brandi Carlile,The Story,The Story
Noah Kahan,Stick Season,Stick Season
```

CSV is useful when an artist or song name contains ` - `. Quote values that contain commas.

### Importing in Kodi

1. Open **My Music → My Playlists → Import Playlist**.
2. Select **Choose File** and use Kodi’s file browser to select your `.txt` or `.csv` file.
3. Confirm the song count with **Import**, or choose **Cancel**.
4. Accept or edit the suggested playlist name.

Store the file wherever Kodi can access it, including an accessible network share. No dedicated import folder is needed. **Format Help** is also available from the import menu.

Once imported, the playlist is stored in Rotation and no longer depends on the original file. You can edit it and use the same cover features as other My Playlists. Rotation matches its songs against your Kodi library; optional streaming can supply available missing tracks. Importing a playlist imports the song list, not music files.

## Rotation gets better with Last.fm scrobbling

A Last.fm account is not required, but it makes Rotation substantially more personal.

After entering your Last.fm username in Rotation, the add-on can use your listening history to identify your favorite artists and tracks, recognize music you have not played recently, and generate recommendations based on what you actually listen to.

For the best results, use a Last.fm scrobbling service in Kodi. As songs played in Kodi are submitted to your Last.fm profile, Rotation’s **For You** section becomes increasingly useful:

- **Recommended Radio** blends familiar favorites with related artists and tracks.
- **Your Top Artists** reflects the artists you play most often.
- **Your Top Tracks** provides quick access to listening favorites.
- **Recently Played** brings your recent listening into Rotation.
- **Rediscover Favorites** brings back music you enjoy but have not heard recently.
- **Forgotten Favorites** finds previously popular music that has fallen out of your regular rotation.
- **New Albums from Artists You Play** looks for releases connected to the artists you listen to most.

Last.fm can also combine listening history from other connected services, including Spotify. This gives Rotation a broader understanding of your musical taste even before you have accumulated extensive Kodi playback history.

Rotation reads this history for personalization; it does not perform the scrobbling itself. A Kodi scrobbling service, or another service connected directly to [Last.fm](https://www.last.fm), records the plays.

## Built for Kodi libraries

Rotation does not replace Kodi’s music library; it makes that library far more enjoyable to explore. Your existing files, metadata, artwork, playlists, and Kodi playback features remain at the center of the experience.

Rotation never downloads or acquires music. Local-library playback is the default; the optional MusicMP3.ru switch can provide streaming fallbacks.

## Optional services

Deezer endpoints used by Rotation require no user key. A personal Last.fm API key improves charts and radio. A fanart.tv personal API key enables additional artist backgrounds. Keys are intentionally blank in distributed packages.

Optional cross-device synchronization uses a `.rotation` folder in the shared Kodi music source. Credentials and caches remain device-local.

## Credits and licenses

Rotation is authored by SEA6ULL / GPT and licensed under GPL-3.0.

Navigation symbols are based on Phosphor Icons and retain their bundled MIT license attribution in `resources/media/icons/LICENSE.phosphor.txt`.
