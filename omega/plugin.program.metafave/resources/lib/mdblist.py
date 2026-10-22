# SPDX-License-Identifier: GPL-3.0-only
"""Read-only MDBList status. Listings read disk; a short-lived script refreshes it.

No tracking writes, source-addon imports, playback hooks or persistent service.
"""
import hashlib
import json
import math
import os
import re
import time
from email.utils import parsedate_to_datetime
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import xbmc

from .common import ADDON_PATH, PROFILE_PATH, Settings, log

TTL = 15 * 60
MAX_AGE = 24 * 60 * 60
LOCK_AGE = 180
CACHE = os.path.join(PROFILE_PATH, 'mdblist-status.json')
LOCK = os.path.join(PROFILE_PATH, 'mdblist-refresh.lock')
PROVIDERS = ('imdb', 'tmdb', 'tvdb', 'trakt', 'mdblist')


def number(value):
    try:
        value = float(value)
        return max(0.0, value) if math.isfinite(value) else 0.0
    except (ValueError, TypeError, OverflowError):
        return 0.0


def _ids(values):
    if not isinstance(values, dict):
        return {}
    result = {}
    for provider in PROVIDERS:
        value = str(values.get(provider) or '').strip()
        if provider == 'imdb':
            valid = re.fullmatch(r'tt\d+', value)
        elif provider == 'mdblist':
            valid = re.fullmatch(r'[A-Za-z0-9]+', value)
        else:
            valid = value.isdecimal() and int(value) > 0
            if valid:
                value = str(int(value))
        if valid:
            result[provider] = value
    return result


def _aliases(media, ids, suffix=''):
    return ['%s:%s:%s%s' % (media, provider, value, suffix)
            for provider, value in _ids(ids).items()]


def item_keys(item):
    """Only typed metadata IDs. Never guess a title, Kodi ID or opaque plugin URL."""
    info = item.get('info') or {}
    media = info.get('mediatype')
    if media == 'tvshow':
        return _aliases('tvshow', {'imdb': info.get('imdbnumber') or info.get('tvshow_imdb_id'),
                                   'tmdb': info.get('tmdb_id') or info.get('tvshow_tmdb_id'),
                                   'tvdb': info.get('tvdb_id')})
    if media == 'movie':
        return _aliases('movie', {'imdb': info.get('imdbnumber'), 'tmdb': info.get('tmdb_id')})
    if media != 'episode':
        return []
    # Native episode IDs identify the episode, never the show.
    keys = _aliases('episode', {'tmdb': info.get('episode_tmdb_id'),
                                'tvdb': info.get('episode_tvdb_id')})
    try:
        season, episode = int(info['season']), int(info['episode'])
    except (KeyError, ValueError, TypeError, OverflowError):
        return keys
    if season >= 0 and episode > 0:
        keys += _aliases('show', {'imdb': info.get('tvshow_imdb_id'),
                                 'tmdb': info.get('tvshow_tmdb_id')},
                         ':s%de%d' % (season, episode))
    return keys


def _account(key):
    return hashlib.sha256(key.encode('utf-8')).hexdigest()


def _read(key):
    try:
        with open(CACHE, encoding='utf-8') as stream:
            data = json.load(stream)
        if (isinstance(data, dict) and data.get('version') == 2
                and data.get('account') == _account(key)
                and isinstance(data.get('states'), dict)):
            return data
    except (OSError, ValueError, TypeError):
        pass
    return {'version': 2, 'account': _account(key), 'updated': 0, 'states': {}}


def _write(data):
    os.makedirs(PROFILE_PATH, exist_ok=True)
    temporary = CACHE + '.tmp'
    try:
        with open(temporary, 'w', encoding='utf-8') as stream:
            json.dump(data, stream, separators=(',', ':'), allow_nan=False)
        os.replace(temporary, CACHE)
    finally:
        try:
            os.remove(temporary)
        except OSError:
            pass


def _claim():
    os.makedirs(PROFILE_PATH, exist_ok=True)
    try:
        if time.time() - os.path.getmtime(LOCK) > LOCK_AGE:
            os.remove(LOCK)
    except OSError:
        pass
    try:
        fd = os.open(LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(fd)
        return True
    except FileExistsError:
        return False


def _release():
    try:
        os.remove(LOCK)
    except OSError:
        pass


class Snapshot:
    def __init__(self, items, settings=None):
        settings = settings or Settings()
        self.states = {}
        key = settings.mdblist_api_key
        if not settings.mdblist_indicators or not key or not any(item_keys(item) for item in items):
            return
        now = time.time()
        data = _read(key)
        updated = number(data.get('updated'))
        if updated and 0 <= now - updated < MAX_AGE:
            self.states = data['states']
        if now < number(data.get('retry_after')) or (updated and 0 <= now - updated < TTL):
            return
        try:
            if _claim():
                try:
                    # No credential in the command line; worker reads current settings.
                    script = os.path.join(ADDON_PATH, 'mdblist_refresh.py')
                    xbmc.executebuiltin('RunScript("%s")' % script.replace('"', ''))
                except Exception:
                    _release()
                    raise
        except OSError:
            log('MDBList cache is unavailable.', xbmc.LOGWARNING)

    def get(self, item):
        matches = [self.states[key] for key in item_keys(item) if key in self.states]
        # Conflicting IDs should never mark an unrelated item as watched.
        if not matches or any(state != matches[0] for state in matches):
            return None
        state = matches[0]
        if not isinstance(state, dict):
            return None
        state = dict(state)
        progress = min(number(state.pop('progress', 0)), 100.0)
        total = number(state.get('runtime')) or number((item.get('info') or {}).get('duration'))
        state['resume'] = {'total': total, 'position': total * progress / 100.0}
        state['percent'] = progress  # can display a percentage even if duration is unknown
        return state


class FetchError(Exception):
    def __init__(self, retry=300, status=None):
        self.retry = retry
        self.status = status


def _retry_seconds(value):
    try:
        seconds = float(value)
        return max(900, seconds) if math.isfinite(seconds) else 900
    except (ValueError, TypeError, OverflowError):
        try:
            return max(900, parsedate_to_datetime(value).timestamp() - time.time())
        except (TypeError, ValueError, OverflowError):
            return 900


def _get(endpoint, key, params):
    query = dict(params, apikey=key)
    request = Request('https://api.mdblist.com' + endpoint + '?' + urlencode(query),
                      headers={'Accept': 'application/json', 'User-Agent': 'MetaFave/1.0.2'})
    try:
        with urlopen(request, timeout=8) as response:
            raw = response.read(20 * 1024 * 1024 + 1)
            if len(raw) > 20 * 1024 * 1024:
                raise FetchError()
            return json.loads(raw), response.headers.get('X-Next-Cursor')
    except HTTPError as exc:
        retry = _retry_seconds(exc.headers.get('Retry-After')) if exc.code == 429 else (3600 if exc.code in (401, 403) else 300)
        raise FetchError(retry, exc.code) from None
    except FetchError:
        raise
    except Exception:
        # Never log URLs or exception text: these can contain the API key.
        raise FetchError() from None


def _watched(key, deadline):
    merged = {'movies': [], 'episodes': []}
    cursor, seen = None, set()
    for _ in range(100):
        if time.monotonic() > deadline:
            raise FetchError()
        params = {'limit': 1000}
        if cursor:
            params['cursor'] = cursor
        data, header_cursor = _get('/sync/watched', key, params)
        if not isinstance(data, dict) or not all(isinstance(data.get(k), list) for k in ('movies', 'episodes')):
            raise FetchError()
        for kind in ('movies', 'episodes'):
            if not all(isinstance(row, dict) for row in data[kind]):
                raise FetchError()
            merged[kind].extend(data[kind])
        if 'shows' in data:
            if not isinstance(data['shows'], list) or not all(isinstance(row, dict) for row in data['shows']):
                raise FetchError()
            merged.setdefault('shows', []).extend(data['shows'])
        pagination = data.get('pagination') or {}
        if not isinstance(pagination, dict):
            raise FetchError()
        cursor = pagination.get('next_cursor') or header_cursor
        if not cursor:
            if pagination.get('has_more'):
                raise FetchError()
            return merged
        if not isinstance(cursor, str) or cursor in seen:
            raise FetchError()
        seen.add(cursor)
    raise FetchError()  # Never publish a truncated snapshot as complete.


def _playback(key, deadline):
    """The API documents an array; also accept its cursor-paged items envelope."""
    merged, cursor, seen = [], None, set()
    for _ in range(100):
        if time.monotonic() > deadline:
            raise FetchError()
        params = {'limit': 1000}
        if cursor:
            params['cursor'] = cursor
        data, header_cursor = _get('/sync/playback', key, params)
        if isinstance(data, list):
            rows, pagination = data, {}
        elif isinstance(data, dict) and isinstance(data.get('items'), list):
            rows, pagination = data['items'], data.get('pagination') or {}
        else:
            raise FetchError()
        if not isinstance(pagination, dict) or not all(isinstance(row, dict) for row in rows):
            raise FetchError()
        merged.extend(rows)
        cursor = pagination.get('next_cursor') or header_cursor
        if not cursor:
            if pagination.get('has_more'):
                raise FetchError()
            return merged
        if not isinstance(cursor, str) or cursor in seen:
            raise FetchError()
        seen.add(cursor)
    raise FetchError()


def _shows(key, deadline):
    """Only MDBList's explicit completion flag can mark a whole show watched."""
    rows, cursor, seen = [], None, set()
    for _ in range(100):
        if time.monotonic() > deadline:
            raise FetchError()
        params = {'limit': 1000, 'mediatype': 'show', 'extended': 'ids_only'}
        if cursor:
            params['cursor'] = cursor
        data, header_cursor = _get('/sync/watched', key, params)
        if not isinstance(data, dict) or not isinstance(data.get('shows'), list):
            raise FetchError()
        if not all(isinstance(row, dict) for row in data['shows']):
            raise FetchError()
        rows.extend(data['shows'])
        pagination = data.get('pagination') or {}
        if not isinstance(pagination, dict):
            raise FetchError()
        cursor = pagination.get('next_cursor') or header_cursor
        if not cursor:
            if pagination.get('has_more'):
                raise FetchError()
            return rows
        if not isinstance(cursor, str) or cursor in seen:
            raise FetchError()
        seen.add(cursor)
    raise FetchError()


def _upnext(key, deadline):
    """Bulk episode counts, using the API's offset pagination (not per show)."""
    rows, offset, seen = [], 0, set()
    for _ in range(100):
        if time.monotonic() > deadline:
            raise FetchError()
        data, _ = _get('/upnext', key, {'limit': 100, 'offset': offset})
        if (not isinstance(data, dict) or not isinstance(data.get('items'), list)
                or not isinstance(data.get('has_more'), bool)
                or not all(isinstance(row, dict) for row in data['items'])):
            raise FetchError()
        page = data['items']
        signature = json.dumps(page, sort_keys=True)
        if signature in seen:
            raise FetchError()
        seen.add(signature)
        rows.extend(page)
        if not data['has_more']:
            return rows
        if not page:
            raise FetchError()
        offset += len(page)
    raise FetchError()


def _show_index(watched, playback, shows, upnext):
    # Join compact completion rows to full metadata by provider-qualified aliases.
    groups = {}
    def remember(ids):
        keys = set(_aliases('tvshow', ids))
        for alias in list(keys):
            keys.update(groups.get(alias, ()))
        for alias in keys:
            groups[alias] = keys
        return keys
    for row in watched.get('shows', []):
        remember((row.get('show') or {}).get('ids'))
    for row in watched.get('episodes', []):
        remember(((row.get('episode') or {}).get('show') or {}).get('ids'))
    for row in playback:
        if row.get('type') == 'episode':
            remember(((row.get('episode') or {}).get('show') or row.get('show') or {}).get('ids'))
    for row in upnext:
        remember((row.get('show') or {}).get('ids'))
    for row in shows:
        remember({'tmdb': row.get('tmdb')})
    states = {}
    for row in shows:
        keys = remember({'tmdb': row.get('tmdb')})
        completed, inprogress = row.get('completed'), row.get('inprogress')
        if completed is not True and inprogress is not True:
            continue  # Pending server completion job, not proof of either state.
        state = {'series': True, 'playcount': 1 if completed is True else 0,
                 'inprogress': inprogress is True and completed is not True}
        for alias in keys:
            states[alias] = state
    for row in upnext:
        progress = row.get('progress') or {}
        total = int(number(progress.get('total_episode_count')))
        count = int(number(progress.get('watched_episode_count')))
        if not total or count > total:
            continue
        keys = remember((row.get('show') or {}).get('ids'))
        current = next((states[k] for k in keys if k in states), {})
        state = dict(current or {'series': True, 'playcount': 0, 'inprogress': count > 0})
        # The completion flag wins if a cross-endpoint snapshot catches a change.
        if not state['playcount']:
            state.update(watched_episodes=count, total_episodes=total)
        for alias in keys:
            states[alias] = state
    return states


def _row_keys(row, media):
    detail = row.get(media) or {}
    if not isinstance(detail, dict):
        raise FetchError()
    keys = _aliases(media, detail.get('ids'))
    if media == 'episode':
        show = detail.get('show') or row.get('show') or {}
        if not isinstance(show, dict):
            raise FetchError()
        try:
            season, episode = int(detail['season']), int(detail['number'])
        except (KeyError, ValueError, TypeError, OverflowError):
            return keys
        if season >= 0 and episode > 0:
            keys += _aliases('show', show.get('ids'), ':s%de%d' % (season, episode))
    return keys


def _index(watched, playback, shows=None, upnext=None):
    if not isinstance(playback, list) or not all(isinstance(row, dict) for row in playback):
        raise FetchError()
    states, groups = {}, {}
    for bucket, media in (('movies', 'movie'), ('episodes', 'episode')):
        for row in watched[bucket]:
            aliases = set(_row_keys(row, media))
            for key in aliases:
                states[key] = {'playcount': 1}
                groups[key] = aliases
    for row in playback:
        media = row.get('type')
        if media not in ('movie', 'episode'):
            continue
        progress = min(number(row.get('progress')), 100.0)
        if not progress:
            continue
        keys = set(_row_keys(row, media))
        for key in list(keys):
            keys.update(groups.get(key, ()))
        # A rewatch can be both watched and partially played.
        watched_before = any(states.get(key, {}).get('playcount') for key in keys)
        state = {'playcount': 1 if watched_before else 0, 'progress': progress,
                 'runtime': number(row.get('runtime')) * 60.0}
        for key in keys:
            states[key] = state
    states.update(_show_index(watched, playback, shows or [], upnext or []))
    return states


def refresh():
    """Invoked only by mdblist_refresh.py after a listing has claimed the lock."""
    try:
        settings = Settings()
        key = settings.mdblist_api_key
        if not settings.mdblist_indicators or not key:
            return
        data = _read(key)
        try:
            deadline = time.monotonic() + 90
            watched = _watched(key, deadline)
            playback = _playback(key, deadline)
            shows = _shows(key, deadline)
            upnext = _upnext(key, deadline)
            states = _index(watched, playback, shows, upnext)
            data = {'version': 2, 'account': _account(key), 'updated': time.time(), 'states': states}
            log('MDBList status updated: %d movies, %d episodes, %d show-status rows, %d show-count rows.' %
                (len(watched.get('movies', [])), len(watched.get('episodes', [])), len(shows), len(upnext)), xbmc.LOGINFO)
        except FetchError as exc:
            data['retry_after'] = time.time() + exc.retry
            log('MDBList status refresh unavailable%s; retry delayed.' %
                (' (HTTP %s)' % exc.status if exc.status else ''), xbmc.LOGWARNING)
            # Invalid/revoked credentials must not keep displaying an old account's status.
            if exc.status in (401, 403):
                data['states'], data['updated'] = {}, 0
        current = Settings()
        if current.mdblist_indicators and current.mdblist_api_key == key:
            _write(data)
    except Exception:
        log('MDBList status refresh could not complete.', xbmc.LOGWARNING)
    finally:
        _release()
