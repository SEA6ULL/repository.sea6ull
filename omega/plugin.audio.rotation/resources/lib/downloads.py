"""Shared direct-download queue, settings flow, tagging and storage."""
import csv
import hashlib
import json
import os
import re
import sys
import time
import uuid
from urllib.parse import urlsplit

import requests
import xbmc
import xbmcgui
import xbmcvfs

sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'vendor'))
from mutagen.mp3 import MP3
from mutagen.id3 import ID3, TIT2, TPE1, TPE2, TALB, TRCK, TPOS, TDRC, TCON, TXXX, APIC

ORGANIZATIONS = ['Playlist Compilation — Various Artists', 'Original Albums']


def safe_name(value):
    value = re.sub(r'\[[^]]*\]', '', str(value or '')).strip()
    value = re.sub(r'[\x00-\x1f<>:"/\\|?*]', '_', value).strip(' .')
    if re.fullmatch(r'(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])', value, re.I):
        value = '_' + value
    extension = next((ext for ext in ('.mp3', '.m3u8') if value.lower().endswith(ext)), '')
    if extension and len(value) > 140:
        value = value[:-len(extension)][:140 - len(extension)].rstrip(' .') + extension
    return (value[:140] if not extension else value).rstrip(' .') or 'Unknown'


def join_path(base, *parts):
    # Kodi VFS paths use /; native Windows paths are accepted with / as well.
    return base.rstrip('/\\') + '/' + '/'.join(safe_name(p) for p in parts)


def read_json(path, default=None):
    try:
        with open(path, encoding='utf-8') as stream:
            return json.load(stream)
    except (OSError, ValueError):
        return default


def write_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    temporary = path + '.' + uuid.uuid4().hex + '.tmp'
    try:
        with open(temporary, 'w', encoding='utf-8') as stream:
            json.dump(data, stream, ensure_ascii=False)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.remove(temporary)


class DownloadStore:
    def __init__(self, profile):
        self.directory = os.path.join(profile, 'downloads')
        self.jobs_dir = os.path.join(self.directory, 'jobs')
        self.snapshots_dir = os.path.join(self.directory, 'snapshots')
        self.temp_dir = os.path.join(self.directory, 'temporary')
        self.lock = os.path.join(self.directory, 'worker.lock')
        for folder in (self.jobs_dir, self.snapshots_dir, self.temp_dir):
            os.makedirs(folder, exist_ok=True)

    def snapshot(self, entries, label, album=False, single=False, cover=""):
        data = {'entries': entries, 'label': label, 'album': bool(album), 'single': bool(single), 'cover': cover}
        key = hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()
        write_json(os.path.join(self.snapshots_dir, key + '.json'), data)
        return key

    def snapshot_data(self, key):
        if not re.fullmatch(r'[a-f0-9]{64}', key or ''):
            return None
        return read_json(os.path.join(self.snapshots_dir, key + '.json'))

    def job_path(self, key):
        if not re.fullmatch(r'[a-f0-9]{32}', key or ''):
            raise ValueError('Invalid download ID')
        return os.path.join(self.jobs_dir, key + '.json')

    def get(self, key):
        return read_json(self.job_path(key))

    def save(self, job):
        write_json(self.job_path(job['id']), job)

    def jobs(self):
        rows = [read_json(os.path.join(self.jobs_dir, name))
                for name in os.listdir(self.jobs_dir) if name.endswith('.json')]
        return sorted((row for row in rows if isinstance(row, dict) and row.get('id') and not row.get('superseded_by')),
                      key=lambda row: row.get('created', 0))

    def enqueue(self, snapshot, options, missing_only=True):
        candidate = dict(snapshot, options=options)
        key = album_identity(candidate)
        if key:
            merge_album_jobs(self)
            old = next((job for job in self.jobs() if album_identity(job) == key), None)
            if old:
                if old['state'] in ('queued', 'running'):
                    return old
                from .library import track_identity_key
                previous = {track_identity_key(old['entries'][row['position'] - 1].get('artist'),
                    old['entries'][row['position'] - 1].get('title')): row for row in playable_results(old)}
                history = list(old.get('history', [])) + [{'state': old['state'],
                    'finished': old.get('finished'), 'results': old.get('results', [])}]
                entries = list(snapshot['entries'])
                identities = {track_identity_key(e.get('artist'), e.get('title')) for e in entries}
                entries += [e for e in old['entries'] if track_identity_key(e.get('artist'), e.get('title')) not in identities]
                results = []
                for number, entry in enumerate(entries, 1):
                    result = previous.get(track_identity_key(entry.get('artist'), entry.get('title')))
                    if result:
                        results.append(dict(result, position=number))
                old.update(snapshot, entries=entries, options=options, missing_only=missing_only,
                    state='queued', results=results, completed=len(results), history=history, milestones=[], milestones_notified=[])
                old['label'] = album_label(old)
                cancel = self.job_path(old['id']) + '.cancel'
                if os.path.exists(cancel):
                    os.remove(cancel)
                self.save(old)
                return old
        job = dict(snapshot, id=uuid.uuid4().hex, created=time.time(), options=options,
                   missing_only=missing_only, state='queued', results=[], completed=0)
        if key:
            job['label'] = album_label(job)
        self.save(job)
        return job

    def remove_history(self, key):
        """Remove terminal bookkeeping only; never touch music or saved playlists."""
        job = self.get(key)
        if not job or job.get('state') in ('queued', 'running'):
            return False
        all_rows = [read_json(os.path.join(self.jobs_dir, name))
                    for name in os.listdir(self.jobs_dir) if name.endswith('.json')]
        members = [row for row in all_rows if isinstance(row, dict) and
                   (row.get('id') == key or row.get('superseded_by') == key)]
        if any(row.get('state') in ('queued', 'running') for row in members):
            return False
        for row in members:
            identifier = row['id']
            # Remove the record first, so a failure cannot leave a visible
            # history row whose report has already gone.
            os.remove(self.job_path(identifier))
            for path in (self.job_path(identifier) + '.cancel',
                         os.path.join(self.directory, identifier + '-report.csv')):
                try:
                    os.remove(path)
                except FileNotFoundError:
                    pass
        return True

    def cancel(self, key):
        path = self.job_path(key) + '.cancel'
        with open(path, 'w') as stream:
            stream.write('cancel')

    def canceled(self, key):
        return os.path.exists(self.job_path(key) + '.cancel')

    def pending(self):
        return [row for row in self.jobs() if row.get('state') in ('queued', 'running')]

    def active_worker(self):
        try:
            return time.time() - os.path.getmtime(self.lock) < 120
        except OSError:
            return False


def start_worker(addon):
    store = DownloadStore(xbmcvfs.translatePath(addon.getAddonInfo('profile')))
    if store.pending() and not store.active_worker():
        script = os.path.join(xbmcvfs.translatePath(addon.getAddonInfo('path')),
                              'download_worker.py')
        xbmc.executebuiltin('RunScript("%s",%s)' % (script, addon.getAddonInfo('id')))


def _folder(current=''):
    return xbmcgui.Dialog().browseSingle(3, 'Download Folder', '', '', False, False, current)


def defaults(addon):
    return {'folder': addon.getSetting('download_folder'),
            'organization': addon.getSetting('download_organization') or 'compilation',
            'scan': addon.getSetting('download_scan') == 'true',
            'add_playlist': addon.getSetting('download_add_playlist') != 'false'}


def confirm_options(addon, label, entries, album, missing_only):
    options = defaults(addon)
    if not options['folder']:
        selected = _folder()
        if not selected:
            return None
        options['folder'] = selected
        addon.setSetting('download_folder', selected)
    needed = sum(not (missing_only and row.get('_local_file')) for row in entries)
    if not needed:
        xbmcgui.Dialog().ok('Downloads', 'All tracks are already in your library.')
        return None
    while True:
        existing = len(entries) - needed
        organization = ('Original Albums' if album or options['organization'] == 'original'
                        else ORGANIZATIONS[0])
        prompt = 'Download %d track%s from MusicMP3.ru?' % (needed, '' if needed == 1 else 's')
        if existing:
            prompt += '\n%d tracks are already in your library.' % existing
        if addon.getSetting('download_ask') != 'true':
            return options if xbmcgui.Dialog().yesno(label, prompt,
                                                     nolabel='Cancel', yeslabel='Download') else None
        text = prompt + '\n' + options['folder'] + '\n' + organization
        choice = xbmcgui.Dialog().yesnocustom(label, text, nolabel='Cancel',
                                               yeslabel='Download', customlabel='Change Options')
        if choice == 1:
            return options
        if choice != 2:
            return None
        while True:
            labels = ['Download Folder: ' + options['folder']]
            actions = ['folder']
            if not album:
                labels.append('Playlist Organization: ' + organization)
                actions.append('organization')
            labels += ['Scan Library After Download: ' + ('On' if options['scan'] else 'Off'),
                       'Done']
            actions += ['scan', 'done']
            if not album:
                labels.insert(-1, 'Add Downloaded Playlists to My Playlists: ' + ('On' if options['add_playlist'] else 'Off'))
                actions.insert(-1, 'add_playlist')
            selection = xbmcgui.Dialog().select('Options for This Download', labels)
            if selection < 0 or actions[selection] == 'done':
                break
            action = actions[selection]
            if action == 'folder':
                chosen = _folder(options['folder'])
                if chosen:
                    options['folder'] = chosen
            elif action == 'organization':
                selected = xbmcgui.Dialog().select('Playlist Organization', ORGANIZATIONS,
                    preselect=1 if options['organization'] == 'original' else 0)
                if selected >= 0:
                    options['organization'] = 'original' if selected else 'compilation'
                    organization = ORGANIZATIONS[selected]
            else:
                options[action] = not options[action]


def write_tags(path, entry, match, job, number, total, artwork=None):
    audio = MP3(path)
    if audio.tags is None:
        audio.add_tags()
    tags = audio.tags
    compilation = not (job['album'] or job.get('single')) and job['options']['organization'] == 'compilation'
    artist = entry.get('artist') or match.get('artist') or 'Unknown Artist'
    album = job['label'] if compilation else (entry.get('album') or match.get('album') or 'Singles')
    album_artist = 'Various Artists' if compilation else (entry.get('album_artist') or artist)
    for frame in ('TIT2', 'TPE1', 'TPE2', 'TALB', 'TRCK', 'TPOS', 'TDRC', 'TCON', 'TCMP'):
        tags.delall(frame)
    for cls, text in [(TIT2, entry.get('title') or match.get('title')), (TPE1, artist),
                      (TPE2, album_artist), (TALB, album)]:
        tags.add(cls(encoding=3, text=[str(text or '')]))
    track = number if compilation else (entry.get('track_number') or match.get('track_number') or (number if job['album'] else None))
    if track:
        tags.add(TRCK(encoding=3, text=['%s/%s' % (track, total) if compilation or job['album'] else str(track)]))
    if not compilation and entry.get('disc_number'):
        tags.add(TPOS(encoding=3, text=[str(entry['disc_number'])]))
    if not compilation and entry.get('year'):
        tags.add(TDRC(encoding=3, text=[str(entry['year'])]))
    if entry.get('genre'):
        tags.add(TCON(encoding=3, text=[str(entry['genre'])]))
    tags.delall('TXXX:COMPILATION')
    if compilation:
        tags.add(TXXX(encoding=3, desc='COMPILATION', text=['1']))
    if artwork:
        mime, image = artwork
        tags.delall('APIC')
        tags.add(APIC(encoding=3, mime=mime, type=3, desc='Cover', data=image))
    audio.save(v2_version=3)
    return artist, album, album_artist


class TransferPaused(Exception):
    """A transfer stopped to free a connection for streaming; the partial
    file is kept and the track resumes from where it stopped."""


class SourceBusy(Exception):
    """The file server is at its connection limit (HTTP 503/429). Not a
    missing track: retry later."""


def transfer_audio(api, match, entry, temporary, canceled, progress,
                   should_pause=None, heartbeat=None):
    """Download one track to temporary, resuming a partial file if present.

    should_pause() is polled about once a second; when it returns True the
    connection is closed and TransferPaused is raised, keeping the partial
    file. The server honours Range requests, so the next attempt continues
    from the same byte.
    """
    url = api.play_url(match['track_id'], match['rel'], referer_url=match.get('album_url')).split('|', 1)[0]
    resume_from = os.path.getsize(temporary) if os.path.exists(temporary) else 0
    headers = {'User-Agent': api.user_agent, 'Referer': api.base_url}
    if resume_from:
        headers['Range'] = 'bytes=%d-' % resume_from
    with requests.get(url, headers=headers, timeout=(10, 20), stream=True) as response:
        if response.status_code in (429, 503):
            raise SourceBusy('HTTP %d' % response.status_code)
        complete_on_disk = bool(resume_from) and response.status_code == 416
        if not complete_on_disk:
            response.raise_for_status()
            content_type = response.headers.get('Content-Type', '').lower()
            if 'html' in content_type or content_type.startswith('text/'):
                raise ValueError('The source returned a page instead of audio')
            appending = bool(resume_from) and response.status_code == 206
            if resume_from:
                xbmc.log('[rotation.downloads] Resume from %d KB: server answered HTTP %d (%s)' % (
                    resume_from // 1024, response.status_code,
                    'continuing' if appending else 'range ignored, starting the track over'),
                    xbmc.LOGINFO)
            if resume_from and not appending:
                resume_from = 0      # the server ignored the range: start over
            expected_bytes = int(response.headers.get('Content-Length') or 0)
            if appending:
                expected_bytes += resume_from
            received = resume_from
            last_check = time.monotonic()
            with open(temporary, 'ab' if appending else 'wb') as stream:
                for chunk in response.iter_content(65536):
                    if canceled():
                        raise InterruptedError('Canceled')
                    if not chunk:
                        continue
                    received += len(chunk)
                    if received > 100 * 1024 * 1024:
                        raise ValueError('Audio file exceeds 100 MB')
                    stream.write(chunk)
                    progress(received, expected_bytes)
                    if time.monotonic() - last_check >= 1:
                        last_check = time.monotonic()
                        if heartbeat:
                            heartbeat()
                        if should_pause and should_pause():
                            raise TransferPaused(received)
            if expected_bytes and not response.headers.get('Content-Encoding') and received != expected_bytes:
                raise ValueError('Incomplete audio transfer')
    audio = MP3(temporary)
    if audio.info.length < 10:
        raise ValueError('The source returned an empty or short audio file')
    # A hand-picked match may be a different edit than the playlist listed;
    # check the file against the recording that was chosen instead.
    expected_duration = float((match.get('duration') if match.get('manual') else
                               entry.get('duration')) or match.get('duration') or 0)
    if expected_duration and abs(audio.info.length - expected_duration) > max(8, expected_duration * .08):
        raise ValueError('Recording duration does not match the requested track')
    return {'duration': round(audio.info.length, 2), 'bitrate': audio.info.bitrate}


def commit_file(temporary, destination):
    folder = destination.rsplit('/', 1)[0]
    if not xbmcvfs.mkdirs(folder) and not xbmcvfs.exists(folder + '/'):
        raise OSError('The download folder could not be created')
    if xbmcvfs.exists(destination):
        raise FileExistsError('A file with this name already exists; it was left untouched')
    partial = destination + '.' + uuid.uuid4().hex + '.part'
    try:
        if not xbmcvfs.copy(temporary, partial):
            raise OSError('Could not write to the download folder')
        if xbmcvfs.exists(destination):
            raise FileExistsError('A file with this name already exists; it was left untouched')
        if not xbmcvfs.rename(partial, destination):
            raise OSError('Could not finalize the downloaded file')
    finally:
        if xbmcvfs.exists(partial):
            xbmcvfs.delete(partial)


def destination_path(job, entry, match, number):
    compilation = not (job['album'] or job.get('single')) and job['options']['organization'] == 'compilation'
    title = entry.get('title') or match.get('title') or 'Unknown Track'
    artist = entry.get('artist') or match.get('artist') or 'Unknown Artist'
    if compilation:
        return join_path(job['options']['folder'], job['label'],
                         '%03d - %s - %s.mp3' % (number, artist, title))
    album = entry.get('album') or match.get('album') or 'Singles'
    album_artist = entry.get('album_artist') or artist
    track = entry.get('track_number') or match.get('track_number') or (number if job['album'] else None)
    filename = ('%02d - ' % int(track) if str(track or '').isdigit() else '') + title + '.mp3'
    if entry.get('disc_number') and int(entry['disc_number']) > 1:
        filename = 'Disc %s - ' % entry['disc_number'] + filename
    return join_path(job['options']['folder'], album_artist, album, filename)


def export_report(store, job):
    path = os.path.join(store.directory, job['id'] + '-report.csv')
    with open(path, 'w', newline='', encoding='utf-8-sig') as stream:
        writer = csv.writer(stream)
        writer.writerow(['Position', 'Artist', 'Title', 'Status', 'File', 'Details', 'Bitrate', 'Match seconds', 'Transfer seconds', 'Tag seconds', 'Write seconds'])
        for row in job['results']:
            writer.writerow([row['position'], row['artist'], row['title'], row['status'],
                             row.get('file', ''), row.get('error', ''), row.get('bitrate', ''),
                             *[row.get('timings', {}).get(stage, '') for stage in ('match', 'transfer', 'tag', 'write')]])
    job['report'] = path


def export_m3u(store, job):
    if not job['options']['m3u'] or job.get('single'):
        return
    paths = playable_results(job)
    if not paths:
        return
    path = os.path.join(store.temp_dir, job['id'] + '.m3u8')
    with open(path, 'w', encoding='utf-8') as stream:
        stream.write('#EXTM3U\n')
        for row in paths:
            stream.write('#EXTINF:-1,%s - %s\n%s\n' %
                         (row['artist'].replace('\n', ' '), row['title'].replace('\n', ' '), row['file']))
    destination = join_path(job['options']['folder'], job['label'] + ' - ' + job['id'][:8] + '.m3u8')
    try:
        commit_file(path, destination)
        job['m3u'] = destination
    finally:
        if os.path.exists(path):
            os.remove(path)


def check_folder(folder):
    probe = folder.rstrip('/\\') + '/.rotation-write-test-' + uuid.uuid4().hex
    try:
        if not xbmcvfs.mkdirs(folder) and not xbmcvfs.exists(folder.rstrip('/\\') + '/'):
            return False
        stream = xbmcvfs.File(probe, 'w')
        try:
            return bool(stream.write('Rotation download destination test'))
        finally:
            stream.close()
    except Exception:
        return False
    finally:
        if xbmcvfs.exists(probe):
            xbmcvfs.delete(probe)


def playable_results(job):
    return sorted((row for row in job.get('results', [])
        if row.get('file') and row.get('status') in ('downloaded', 'already downloaded', 'in library')
        and xbmcvfs.exists(row['file'])), key=lambda row: row['position'])


def downloaded_directories(job):
    return sorted({row['file'].rsplit('/', 1)[0].rstrip('/\\') + '/'
        for row in playable_results(job) if row['status'] in ('downloaded', 'already downloaded')})


def scan_downloads(job, heartbeat=None):
    """Scan exact destination subfolders; never fall back to a music root."""
    directories = downloaded_directories(job)
    monitor = xbmc.Monitor()
    def pause(seconds):
        if heartbeat:
            heartbeat()
        return monitor.waitForAbort(seconds)
    for directory in directories:
        if heartbeat:
            heartbeat()
        while xbmc.getCondVisibility('Library.IsScanningMusic'):
            if pause(.25):
                return False
        response = json.loads(xbmc.executeJSONRPC(json.dumps({'jsonrpc': '2.0', 'id': 1,
            'method': 'AudioLibrary.Scan', 'params': {'directory': directory, 'showdialogs': False}})))
        if response.get('error'):
            raise RuntimeError(str(response['error']))
        if pause(1):
            return False
        while xbmc.getCondVisibility('Library.IsScanningMusic'):
            if pause(.25):
                return False
    return bool(directories)


def download_cover(store, job):
    for image in [job.get('artwork'), job.get('cover')] + [
            entry.get('cover') or entry.get('image') for entry in job.get('entries', [])]:
        if image and (image.startswith(('http://', 'https://', 'image://')) or xbmcvfs.exists(image)):
            return image
    if job.get('state') in ('queued', 'running'):
        return ''
    # Older jobs can recover artwork already embedded in their downloaded MP3.
    for row in playable_results(job):
        temporary = os.path.join(store.temp_dir, job['id'] + '-art.mp3')
        try:
            if not xbmcvfs.copy(row['file'], temporary):
                continue
            frames = MP3(temporary).tags
            images = frames.getall('APIC') if frames else []
            if images:
                frame = images[0]
                directory = os.path.join(store.directory, 'artwork')
                os.makedirs(directory, exist_ok=True)
                target = os.path.join(directory, job['id'] + ('.png' if frame.mime == 'image/png' else '.jpg'))
                with open(target, 'wb') as stream:
                    stream.write(frame.data)
                job['artwork'] = target
                store.save(job)
                return target
        except Exception:
            pass
        finally:
            if os.path.exists(temporary):
                os.remove(temporary)
        break
    return ''

def album_identity(job):
    from .library import norm_artist, norm_title
    if not job.get('album') or not job.get('entries'):
        return None
    entry = job['entries'][0]
    artist = entry.get('album_artist') or entry.get('artist', '')
    album = entry.get('album') or job.get('label', '')
    if not artist or not album:
        return None
    return norm_artist(artist), norm_title(album, False), job['options']['folder'].rstrip('/\\')


def album_label(job):
    entry = job['entries'][0]
    return '%s — %s' % (entry.get('album_artist') or entry.get('artist', ''),
                        entry.get('album') or job['label'])


def merge_album_jobs(store):
    """Consolidate terminal legacy jobs without changing their audio files."""
    groups = {}
    for job in store.jobs():
        key = album_identity(job)
        if key:
            group = groups.setdefault(key, [])
            if not any(old['id'] == job['id'] for old in group):
                group.append(job)
    for group in groups.values():
        if any(job['state'] in ('queued', 'running') for job in group):
            continue
        target = group[0]
        if len(group) > 1:
            entries, results, identities = [], {}, {}
            from .library import track_identity_key
            rank = {'downloaded': 4, 'already downloaded': 3, 'in library': 2, 'failed': 1}
            history = list(target.get('history', []))
            for job in group:
                if job['id'] != target['id']:
                    history.append({'id': job['id'], 'state': job['state'], 'finished': job.get('finished'),
                                    'results': job.get('results', [])})
                for position, entry in enumerate(job['entries'], 1):
                    identity = track_identity_key(entry.get('artist'), entry.get('title'))
                    if identity not in identities:
                        identities[identity] = len(entries) + 1
                        entries.append(entry)
                    old = next((row for row in job.get('results', []) if row['position'] == position), None)
                    if old:
                        number = identities[identity]
                        if rank.get(old['status'], 0) >= rank.get(results.get(number, {}).get('status'), 0):
                            results[number] = dict(old, position=number)
            target.update(entries=entries, results=sorted(results.values(), key=lambda row: row['position']), history=history)
            target['completed'] = len(target['results'])
            target['state'] = 'completed' if target['completed'] == len(entries) else 'canceled'
            store.save(target)
            for old in group[1:]:
                old['superseded_by'] = target['id']
                store.save(old)
        target['label'] = album_label(target)
        if len(group) > 1:
            export_report(store, target)
        store.save(target)


def save_downloaded_playlist(store, job):
    """Keep one My Playlists entry across retries, including unscanned files."""
    from .user_playlists import UserPlaylistStore
    if job.get('album') or job.get('single'):
        return None
    rows = playable_results(job)
    if not rows:
        return None
    directory = job.get('playlist_directory') or os.path.join(os.path.dirname(store.directory), 'user_playlists')
    playlists = UserPlaylistStore(directory)
    playlist_id = job.get('playlist_id') or hashlib.md5(('rotation-download:' + job['id']).encode()).hexdigest()
    existing = playlists.get(playlist_id)
    row = existing or {'id': playlist_id, 'name': job['label'], 'created': time.time(), 'tracks': []}
    row['tracks'] = []
    compilation = job['options']['organization'] == 'compilation'
    for result in rows:
        entry = dict(job['entries'][result['position'] - 1])
        entry.update(file=result['file'], title=result['title'], artist=result['artist'])
        if compilation and result['status'] != 'in library':
            entry.update(album=job['label'], album_artist='Various Artists')
        if not entry.get('cover'):
            entry['cover'] = job.get('artwork') or job.get('cover', '')
        row['tracks'].append(entry)
    playlists.save(row)
    job['playlist_id'] = playlist_id
    store.save(job)
    return row


def export_playlist_file(store, label, rows, folder):
    """Write an explicit, ordered, portable M3U8; never overwrite a file."""
    key = uuid.uuid4().hex
    temporary = os.path.join(store.temp_dir, key + '.m3u8')
    try:
        with open(temporary, 'w', encoding='utf-8') as stream:
            stream.write('#EXTM3U\n')
            for row in rows:
                file = row['file']
                if '\n' in file or '\r' in file:
                    continue
                stream.write('#EXTINF:-1,%s - %s\n%s\n' % (
                    row.get('artist', '').replace('\n', ' ').replace('\r', ' '),
                    row.get('title', '').replace('\n', ' ').replace('\r', ' '), file))
        destination = join_path(folder, label + '.m3u8')
        if xbmcvfs.exists(destination):
            destination = join_path(folder, label + ' - ' + key[:8] + '.m3u8')
        commit_file(temporary, destination)
        return destination
    finally:
        if os.path.exists(temporary):
            os.remove(temporary)


class DownloadProgress:
    """Stock BG progress only when requested or browsing this add-on's Downloads."""
    def __init__(self, addon):
        self.addon, self.dialog = addon, None
        self.job, self.store, self.percent, self.last_mode = None, None, 0, None
        self.heading, self.message = addon.getAddonInfo('name') + ' — Downloads', ''

    def bind(self, job, store):
        self.job, self.store = job, store
        self.percent, self.last_mode = 0, None

    def create(self, heading, message):
        self.heading, self.message = heading, message

    def fullscreen(self):
        return xbmc.getCondVisibility('Window.IsActive(12006)') or xbmc.getCondVisibility('Window.IsActive(12005)')

    def visible(self):
        from urllib.parse import urlsplit
        path = urlsplit(xbmc.getInfoLabel('Container.FolderPath'))
        downloads = path.netloc == self.addon.getAddonInfo('id') and (
            path.path == '/downloads' or path.path.startswith('/downloads/'))
        return not self.fullscreen() and (downloads or self.addon.getSetting('download_notifications') == 'continuous')

    def update(self, percent, message=''):
        # Matching/committing can temporarily reduce the raw estimate. Keep
        # both the displayed percentage and milestone thresholds monotonic.
        self.percent = max(self.percent, min(99, int(percent)))
        percent = self.percent
        self.message = message or self.message
        self._milestones(percent)
        if self.visible():
            if self.dialog is None:
                self.dialog = xbmcgui.DialogProgressBG()
                self.dialog.create(self.heading, self.message)
            self.dialog.update(percent, message=self.message)
        else:
            self.close()

    def _milestones(self, percent):
        if not self.job:
            return
        job = self.job
        mode = self.addon.getSetting('download_notifications')
        if mode != self.last_mode:
            xbmc.log('[rotation.downloads] Notification mode: %s' % mode, xbmc.LOGINFO)
            self.last_mode = mode
        crossed = [n for n in (25, 50, 75) if n <= percent and n not in job.get('milestones', [])]
        changed = bool(crossed)
        if crossed:
            job['milestones'] = sorted(set(job.get('milestones', []) + crossed))
            xbmc.log('[rotation.downloads] Display progress %d%% crossed milestone %d%%' %
                     (percent, crossed[-1]), xbmc.LOGINFO)
        pending = sorted(set(job.get('milestones', [])) - set(job.get('milestones_notified', [])))
        if pending and mode == 'milestones' and not self.fullscreen() and not self.visible():
            xbmcgui.Dialog().notification(self.addon.getAddonInfo('name'),
                '%d%% complete · %s' % (pending[-1], job['label']), xbmcgui.NOTIFICATION_INFO, 3500)
            # One current notice; older hidden checkpoints do not stack up.
            job['milestones_notified'] = sorted(set(job.get('milestones_notified', []) + pending))
            changed = True
            xbmc.log('[rotation.downloads] Milestone notification shown: %d%%' % pending[-1], xbmc.LOGINFO)
        if changed and self.store:
            self.store.save(job)

    def processed(self, job):
        if self.job is None:
            self.job = job
        percent = int(len(job['results']) * 100 / max(1, len(job['entries'])))
        self.update(min(99, percent), message='%s · %d/%d tracks checked' %
                    (job['label'], len(job['results']), len(job['entries'])))

    def close(self):
        if self.dialog:
            self.dialog.close()
            self.dialog = None


def stable_file(path):
    return bool(path and not path.lower().startswith(('http://', 'https://', 'plugin://'))
                and xbmcvfs.exists(path))
