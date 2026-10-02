"""Independent serial worker: never changes the player or refreshes song lists."""
import json
import os
import sys
import threading
import time
import uuid
import xbmc
import xbmcaddon
import xbmcgui
import xbmcvfs
from resources.lib.downloads import (DownloadStore, transfer_audio, write_tags,
    destination_path, commit_file, export_m3u, export_report, start_worker, DownloadProgress, save_downloaded_playlist,
    TransferPaused, SourceBusy)
from resources.lib import stream_slots
from resources.lib.download_source import MusicMP3Source
from resources.lib.musicmp3 import musicMp3
import requests


def artwork_bytes(path):
    if not path:
        return None
    try:
        if path.startswith(('http://', 'https://')):
            with requests.get(path, timeout=(5, 8), stream=True) as response:
                response.raise_for_status()
                data = bytearray()
                for chunk in response.iter_content(65536):
                    data.extend(chunk)
                    if len(data) > 5 * 1024 * 1024:
                        return None
                data = bytes(data)
        else:
            stream = xbmcvfs.File(path)
            try:
                data = bytes(stream.readBytes(5 * 1024 * 1024))
            finally:
                stream.close()
        if data.startswith(b'\x89PNG\r\n\x1a\n'):
            return 'image/png', data
        if data.startswith(b'\xff\xd8'):
            return 'image/jpeg', data
    except Exception:
        pass
    return None


def known_downloads(store, job):
    known = {}
    compilation = not (job['album'] or job.get('single')) and job['options']['organization'] == 'compilation'
    for old in store.jobs():
        if old['options']['folder'] != job['options']['folder']:
            continue
        old_compilation = not (old['album'] or old.get('single')) and old['options']['organization'] == 'compilation'
        if old_compilation != compilation or (compilation and old['label'] != job['label']):
            continue
        for result in old.get('results', []):
            if result.get('status') not in ('downloaded', 'already downloaded') or not result.get('file'):
                continue
            if not xbmcvfs.exists(result['file']):
                continue
            entry = old['entries'][result['position'] - 1]
            key = (entry.get('artist', '').casefold(), entry.get('title', '').casefold(),
                   entry.get('album', '').casefold())
            known[key] = result['file']
    return known


class StreamURL:
    """Resolve cookies/catalog on the main thread, transfer independent URLs."""
    def __init__(self, api, url):
        self.user_agent, self.base_url, self.url = api.user_agent, api.base_url, url

    def play_url(self, *args, **kwargs):
        return self.url


_LAST_SETTING = [2]


def _download_setting():
    """Read live, so a change in Settings applies to the next transfer.

    While Kodi installs an update the add-on is briefly unregistered and
    Addon() raises "Unknown addon id". That used to stop the whole worker;
    the last known value is used instead.
    """
    try:
        selected = xbmcaddon.Addon('plugin.audio.rotation').getSetting('download_parallel')
        _LAST_SETTING[0] = int(selected) if selected in ('1', '2') else 2
    except Exception:
        pass
    return _LAST_SETTING[0]


class LostLock(Exception):
    """Another worker owns the lock now; this one must stop."""


class WorkerLock(object):
    """The single-worker lock, owned by a token and kept fresh by a heartbeat.

    The service treats a lock untouched for 120 s as abandoned. The main
    loop used to be the only thing touching it, so a worker blocked for a
    while (finishing transfers after an error, a slow match) looked dead and
    a second worker started on the same download. A background heartbeat
    now refreshes it every 15 s, and only the owner removes it.
    """

    def __init__(self, path):
        self.path = path
        self.token = uuid.uuid4().hex
        self._stop = threading.Event()
        self._thread = None

    def acquire(self):
        if os.path.exists(self.path):
            if time.time() - os.path.getmtime(self.path) < 120:
                return False
            os.remove(self.path)
        fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, self.token.encode('ascii'))
        os.close(fd)
        self._thread = threading.Thread(target=self._beat, name='RotationDownloadLock', daemon=True)
        self._thread.start()
        return True

    def _owner(self):
        try:
            with open(self.path, 'r') as stream:
                return stream.read().strip()
        except OSError:
            return None

    def touch(self):
        owner = self._owner()
        if owner is None:
            # Removed underneath us: take it back rather than crash.
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(fd, self.token.encode('ascii'))
                os.close(fd)
                return
            except OSError:
                owner = self._owner()
        if owner and owner != self.token:
            raise LostLock('Another download worker took over')
        try:
            os.utime(self.path, None)
        except OSError:
            pass

    def _beat(self):
        while not self._stop.wait(15):
            try:
                self.touch()
            except LostLock:
                return

    def release(self):
        self._stop.set()
        if self._owner() == self.token:
            try:
                os.remove(self.path)
            except OSError:
                pass


def download_one(api, match, entry, job, number, store, cover, match_seconds, activity, monitor):
    from resources.lib.downloads import write_json, read_json
    status = {'position': number, 'artist': entry.get('artist', ''), 'title': entry.get('title', '')}
    temporary = os.path.join(store.temp_dir, job['id'] + '-%d.mp3' % number)
    destination = destination_path(job, entry, match, entry.get('_download_number', number))
    timings = {'match': round(match_seconds, 2)}
    canceled = lambda: monitor.abortRequested() or store.canceled(job['id'])
    token = '%s-%d-%s' % (job['id'], number, uuid.uuid4().hex[:6])
    keep_partial = False
    try:
        receipt = read_json(temporary + '.commit.json')
        if receipt and receipt.get('file') == destination and xbmcvfs.exists(destination):
            return receipt
        if canceled():
            raise InterruptedError('Canceled')
        def update(received, expected):
            activity[number] = (entry.get('title', ''),
                                min(.99, received / expected) if expected else 0)
        started = time.monotonic()
        stream_slots.claim_download(token)
        setting = _download_setting()
        try:
            info = transfer_audio(
                api, match, entry, temporary, canceled, update,
                should_pause=lambda: stream_slots.download_must_yield(token, setting),
                heartbeat=lambda: stream_slots.heartbeat_download(token))
        finally:
            stream_slots.release_download(token)
        timings['transfer'] = round(time.monotonic() - started, 2)
        started = time.monotonic()
        activity[number] = (entry.get('title', ''), .99)
        write_tags(temporary, entry, match, job, entry.get('_download_number', number),
                   job.get('total_tracks', len(job['entries'])), cover)
        timings['tag'] = round(time.monotonic() - started, 2)
        if canceled():
            raise InterruptedError('Canceled')
        if xbmcvfs.exists(destination):
            raise FileExistsError('A file with this name already exists; it was left untouched')
        receipt = dict(status, status='downloaded', file=destination, timings=timings, **info)
        write_json(temporary + '.commit.json', receipt)
        activity[number] = (entry.get('title', ''), .99)
        started = time.monotonic()
        commit_file(temporary, destination)
        timings['write'] = round(time.monotonic() - started, 2)
        write_json(temporary + '.commit.json', receipt)
        status.update(receipt)
    except InterruptedError:
        return None
    except TransferPaused:
        # Streaming needs the connection. Keep what was received; the track
        # goes back in the queue and resumes from this byte.
        keep_partial = True
        activity[number] = ('Paused · ' + entry.get('title', ''), 0)
        xbmc.log('[rotation.downloads] Track %d: paused for streaming at %d KB' %
                 (number, os.path.getsize(temporary) // 1024 if os.path.exists(temporary) else 0),
                 xbmc.LOGINFO)
        return {'requeue': True, 'position': number, 'paused': True}
    except SourceBusy as exc:
        # Connection limit reached (our own streaming, or another device).
        # Not a missing track: wait and try again.
        keep_partial = True
        xbmc.log('[rotation.downloads] Track %d: source busy (%s); will retry' % (number, exc),
                 xbmc.LOGINFO)
        return {'requeue': True, 'position': number, 'busy': True}
    except Exception as exc:
        status.update(status='failed', error=str(exc), timings=timings)
        status['_source_error'] = isinstance(exc, (requests.RequestException, RuntimeError))
    finally:
        if not keep_partial and os.path.exists(temporary):
            os.remove(temporary)
    # Do not log destination URLs: network-share paths can contain credentials.
    xbmc.log('[rotation.downloads] Track %d: %s; match=%ss transfer=%ss tag=%ss write=%ss' %
        (number, status.get('status'), *[timings.get(stage, '-') for stage in ('match', 'transfer', 'tag', 'write')]), xbmc.LOGINFO)
    return status


def run(addon):
    from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
    from resources.lib.downloads import scan_downloads
    store = DownloadStore(xbmcvfs.translatePath(addon.getAddonInfo('profile')))
    if store.active_worker():
        return
    lock = WorkerLock(store.lock)
    try:
        if not lock.acquire():
            return
    except OSError:
        return
    monitor, progress = xbmc.Monitor(), None
    try:
        source_dir = os.path.join(store.directory, 'source')
        os.makedirs(source_dir, exist_ok=True)
        api = musicMp3(source_dir, timeout=15, cache_hours=6)
        source, covers = MusicMP3Source(api), {}
        while not monitor.abortRequested():
            pending = store.pending()
            if not pending:
                break
            job = pending[0]
            if store.canceled(job['id']):
                job['state'] = 'canceled'
                store.save(job)
                continue
            job['state'] = 'running'
            store.save(job)
            if progress is None:
                progress = DownloadProgress(addon)
            progress.bind(job, store)
            progress.create(addon.getAddonInfo('name') + ' — Downloads', job['label'])
            known = known_downloads(store, job)
            done_positions = {row['position'] for row in job['results']}
            todo = [(n, entry) for n, entry in enumerate(job['entries'], 1) if n not in done_positions]
            total, cursor, failures = len(job['entries']), 0, 0
            activity, active, reserved = {}, {}, set()
            def record(status):
                nonlocal failures
                if status is None:
                    return
                if status.pop('_source_error', False):
                    failures += 1
                elif status['status'] == 'downloaded':
                    failures = 0
                job['results'].append(status)
                job['results'].sort(key=lambda row: row['position'])
                job['completed'] = len(job['results'])
                progress.processed(job)
                store.save(job)
                if status['status'] in ('downloaded', 'already downloaded'):
                    entry = job['entries'][status['position'] - 1]
                    known[(entry.get('artist', '').casefold(), entry.get('title', '').casefold(),
                           entry.get('album', '').casefold())] = status['file']
            parallel = _download_setting()
            job['parallel'] = parallel
            store.save(job)
            xbmc.log('[rotation.downloads] Starting job with up to %d simultaneous transfers' % parallel, xbmc.LOGINFO)
            # Matching/session access stays serial. Only audio transfer/tag/write
            # overlaps. How many transfers run is decided live by the shared
            # MusicMP3.ru connection budget: streaming always comes first.
            busy_tries, not_before, limited_logged = {}, {}, None
            with ThreadPoolExecutor(max_workers=stream_slots.MAX_CONNECTIONS) as pool:
                while cursor < len(todo) or active:
                    lock.touch()
                    canceled = monitor.abortRequested() or store.canceled(job['id'])
                    if canceled:
                        cursor = len(todo)
                    allowance = stream_slots.download_allowance(_download_setting())
                    if allowance != limited_logged:
                        limited_logged = allowance
                        xbmc.log('[rotation.downloads] Transfers allowed now: %d%s' % (
                            allowance, ' (streaming has priority)' if allowance < _download_setting() else ''),
                            xbmc.LOGINFO)
                    while (not canceled and cursor < len(todo) and len(active) < allowance
                           and time.time() >= not_before.get(todo[cursor][0], 0)):
                        number, entry = todo[cursor]
                        status = {'position': number, 'artist': entry.get('artist', ''), 'title': entry.get('title', '')}
                        identity = (entry.get('artist', '').casefold(), entry.get('title', '').casefold(), entry.get('album', '').casefold())
                        if identity in known:
                            cursor += 1
                            record(dict(status, status='already downloaded', file=known[identity]))
                            continue
                        if job['missing_only'] and entry.get('_local_file') and xbmcvfs.exists(entry['_local_file']):
                            cursor += 1
                            record(dict(status, status='already downloaded' if entry.get('_download_file') else 'in library', file=entry['_local_file']))
                            continue
                        if failures >= 3:
                            cursor += 1
                            record(dict(status, status='failed', error='Source unavailable; remaining tracks were not requested'))
                            continue
                        started = time.monotonic()
                        try:
                            progress.update(int(len(job['results']) * 100 / total), message='Finding ' + entry.get('title', ''))
                            match = source.resolve(entry)
                            destination = destination_path(job, entry, match, entry.get('_download_number', number))
                            if destination in reserved:
                                # Wait for this target before resolving a duplicate.
                                break
                            url = api.play_url(match['track_id'], match['rel'], referer_url=match.get('album_url'))
                            cover_path = (job.get('cover') if not (job['album'] or job.get('single')) and
                                job['options']['organization'] == 'compilation' else
                                entry.get('cover') or entry.get('image') or job.get('cover') or match.get('image'))
                            if cover_path not in covers:
                                covers[cover_path] = artwork_bytes(cover_path)
                            cover = covers[cover_path]
                            if cover and not job.get('artwork'):
                                directory = os.path.join(store.directory, 'artwork')
                                os.makedirs(directory, exist_ok=True)
                                image = os.path.join(directory, job['id'] + ('.png' if cover[0] == 'image/png' else '.jpg'))
                                with open(image, 'wb') as stream:
                                    stream.write(cover[1])
                                job['artwork'] = image
                                store.save(job)
                            activity[number] = (entry.get('title', ''), 0)
                            elapsed = time.monotonic() - started
                            future = pool.submit(download_one, StreamURL(api, url), match, entry, job,
                                number, store, cover, elapsed, activity, monitor)
                            active[future] = (number, destination)
                            reserved.add(destination)
                        except Exception as exc:
                            record(dict(status, status='failed', error=str(exc),
                                timings={'match': round(time.monotonic() - started, 2)},
                                _source_error=isinstance(exc, (requests.RequestException, RuntimeError))))
                        cursor += 1
                        canceled = monitor.abortRequested() or store.canceled(job['id'])
                    if active:
                        finished, _ = wait(active, timeout=.25, return_when=FIRST_COMPLETED)
                        for future in finished:
                            number, destination = active.pop(future)
                            reserved.discard(destination)
                            result = future.result()
                            if result and result.get('requeue'):
                                # Back to the front of the queue, keeping the
                                # partial file. Busy waits grow to a minute;
                                # a track is only given up after ~15 minutes.
                                todo.insert(cursor, (number, job['entries'][number - 1]))
                                if result.get('busy'):
                                    busy_tries[number] = busy_tries.get(number, 0) + 1
                                    if busy_tries[number] > 20:
                                        todo.pop(cursor)
                                        activity.pop(number, None)
                                        record({'position': number, 'status': 'failed',
                                                'artist': job['entries'][number - 1].get('artist', ''),
                                                'title': job['entries'][number - 1].get('title', ''),
                                                'error': 'Source stayed busy; use Retry Unavailable Tracks'})
                                        continue
                                    not_before[number] = time.time() + min(60, 5 * 2 ** min(busy_tries[number] - 1, 4))
                                    activity[number] = ('Waiting · ' +
                                                        job['entries'][number - 1].get('title', ''), 0)
                                continue
                            activity.pop(number, None)
                            record(result)
                        fraction = sum(value[1] for value in list(activity.values()))
                        message = ' · '.join(value[0] for value in list(activity.values()))
                        progress.update(min(99, int((len(job['results']) + fraction) * 100 / total)),
                            message=message or '%s · %d/%d tracks' % (job['label'], len(job['results']), total))
                    elif cursor < len(todo) and not canceled:
                        # Nothing running: streaming holds every connection,
                        # or a busy track is waiting out its delay.
                        progress.update(min(99, int(len(job['results']) * 100 / total)),
                            message=' · '.join(v[0] for v in list(activity.values())) or
                            'Paused · %s' % job['label'])
                        if monitor.waitForAbort(.5):
                            break
            if monitor.abortRequested():
                job['state'] = 'queued'
                store.save(job)
                break
            job['state'] = 'canceled' if store.canceled(job['id']) else 'completed'
            job['finished'] = time.time()
            job.pop('crashes', None)
            if str(job.get('error', '')).startswith('Interrupted:'):
                job.pop('error', None)
            for name in os.listdir(store.temp_dir):
                if name.startswith(job['id'] + '-') and name.endswith('.mp3'):
                    try:
                        os.remove(os.path.join(store.temp_dir, name))
                    except OSError:
                        pass
            export_report(store, job)
            if job['state'] == 'completed' and job['options'].get('add_playlist', addon.getSetting('download_add_playlist') != 'false'):
                try:
                    save_downloaded_playlist(store, job)
                except Exception as exc:
                    job['playlist_error'] = str(exc)
            downloaded = sum(row['status'] == 'downloaded' for row in job['results'])
            failed = sum(row['status'] == 'failed' for row in job['results'])
            if downloaded and job['options']['scan'] and job['state'] == 'completed':
                try:
                    if scan_downloads(job, heartbeat=lock.touch):
                        job['scanned'] = time.time()
                except Exception as exc:
                    job['scan_error'] = str(exc)
            store.save(job)
            progress.close()
            xbmcgui.Dialog().notification(addon.getAddonInfo('name'),
                '%d downloaded · %d unavailable%s · %s' % (downloaded, failed,
                    ' · canceled' if job['state'] == 'canceled' else '', job['label']), xbmcgui.NOTIFICATION_INFO, 6000)
    except LostLock:
        # Another worker is running this download; leave it alone.
        xbmc.log('[rotation.downloads] Another worker took over; this one stops', xbmc.LOGWARNING)
        return
    except Exception as exc:
        # Usually transient (e.g. Kodi updating the add-on). Put the download
        # back in the queue so it resumes where it stopped; only a download
        # that keeps crashing is marked failed.
        xbmc.log('[rotation.downloads] Worker stopped: %s' % exc, xbmc.LOGERROR)
        gave_up = False
        for job in store.pending():
            job['crashes'] = job.get('crashes', 0) + 1
            if job['crashes'] >= 3:
                job.update(state='failed', error=str(exc))
                gave_up = True
            else:
                job.update(state='queued', error='Interrupted: %s (will resume)' % exc)
            store.save(job)
        xbmcgui.Dialog().notification(
            addon.getAddonInfo('name'),
            ('Downloads stopped: ' if gave_up else 'Downloads interrupted; resuming shortly: ') + str(exc),
            xbmcgui.NOTIFICATION_ERROR if gave_up else xbmcgui.NOTIFICATION_WARNING, 6000)
    finally:
        if progress:
            progress.close()
        lock.release()
    if not monitor.abortRequested():
        start_worker(addon)


if __name__ == '__main__':
    run(xbmcaddon.Addon(sys.argv[1]))
