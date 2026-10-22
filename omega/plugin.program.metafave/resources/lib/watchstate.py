# SPDX-License-Identifier: GPL-3.0-only
"""Current library status, then an optional cached read-only MDBList fallback."""

import json
import math
import re
from urllib.parse import urlsplit

import xbmc

from . import targets, mdblist
from .common import log

_VIDEO = {
    'tvshow': ('VideoLibrary.GetTVShowDetails', 'tvshowid', 'tvshowdetails'),
    'movie': ('VideoLibrary.GetMovieDetails', 'movieid', 'moviedetails'),
    'episode': ('VideoLibrary.GetEpisodeDetails', 'episodeid', 'episodedetails'),
    'musicvideo': ('VideoLibrary.GetMusicVideoDetails', 'musicvideoid', 'musicvideodetails'),
}
_FIELDS = ['file', 'playcount', 'resume', 'lastplayed', 'runtime']
_STATUS_PROPERTIES = {
    'resumetime', 'totaltime', 'percentplayed', 'playcount', 'watched', 'watchedprogress',
    'watchedepisodes', 'unwatchedepisodes', 'totalepisodes', 'watchedepisodepercent', 'numepisodes', 'inprogress',
}


def _number(value):
    try:
        value = float(value)
        return max(0.0, value) if math.isfinite(value) else 0.0
    except (ValueError, TypeError, OverflowError):
        return 0.0


def _path(item):
    target = item.get('target') or {}
    path = target.get('path') or ''
    if target.get('kind') == 'command':
        match = re.match(r'^\s*PlayMedia\((.*)\)\s*$', path, re.I | re.S)
        if not match:
            return ''
        args = targets.split_params(match.group(1))
        path = args[0] if args else ''
    elif target.get('kind') != 'item' and not (target.get('kind') == 'folder'
            and (item.get('info') or {}).get('mediatype') == 'tvshow'):
        return ''
    # A source add-on's database ID must never be mistaken for a Kodi ID.
    # SMB/NFS shares are library media too, but plugin/HTTP targets aren't.
    try:
        scheme = urlsplit(path).scheme.lower()
    except ValueError:
        return ''
    if scheme and scheme not in ('videodb', 'file', 'smb', 'nfs', 'special', 'stack', 'rar', 'zip'):
        if not re.match(r'^[A-Za-z]:[\\/]', path):
            return ''
    return path


def _same_file(left, right):
    # Preserve case on case-sensitive shares; normalize Windows separators only.
    return bool(left and right) and left.replace('\\', '/') == right.replace('\\', '/')


def _request(item):
    path = _path(item)
    if not path:
        return None
    info = item.get('info') or {}
    media = info.get('mediatype', '')
    if media and media not in _VIDEO and media != 'video':
        return None
    try:
        dbid = int(info.get('dbid', 0))
    except (ValueError, TypeError):
        dbid = 0
    if media in _VIDEO and dbid > 0:
        method, key, result_key = _VIDEO[media]
        if path.lower().startswith('videodb://'):
            # Prefer the ID encoded in the actual target if it was edited.
            parts = urlsplit(path)
            hosts = {'tvshow': ('tvshows',), 'movie': ('movies', 'recentlyaddedmovies'),
                     'episode': ('tvshows', 'recentlyaddedepisodes', 'inprogressepisodes'),
                     'musicvideo': ('musicvideos', 'recentlyaddedmusicvideos')}
            if parts.netloc.lower() not in hosts[media]:
                return None
            match = re.search(r'/(\d+)(?:\.[^/]*)?/?$', parts.path)
            if not match or int(match.group(1)) != dbid:
                return None
        fields = ['file', 'playcount', 'episode', 'watchedepisodes'] if media == 'tvshow' else _FIELDS
        return method, {key: dbid, 'properties': fields}, result_key, path, media
    if media == 'tvshow':
        return None  # A folder without a verified show ID is not a file lookup.
    # Older shortcuts / Kodi Favorites can have a file path but no saved ID.
    return ('Files.GetFileDetails', {'file': path, 'media': 'video', 'properties': _FIELDS},
            'filedetails', path, '')


def _batch(requests):
    results = {}
    # Keep very large folders from creating one unbounded JSON-RPC message.
    for start in range(0, len(requests), 50):
        chunk = requests[start:start + 50]
        payload = [{'jsonrpc': '2.0', 'id': start + i, 'method': req[0], 'params': req[1]}
                   for i, req in enumerate(chunk)]
        try:
            response = json.loads(xbmc.executeJSONRPC(json.dumps(payload)))
            if not isinstance(response, list):
                continue
            for row in response:
                if isinstance(row, dict) and isinstance(row.get('id'), int) and 'result' in row:
                    results[row['id']] = row['result']
        except Exception as exc:
            log('Library indicators unavailable: %s' % exc, xbmc.LOGDEBUG)
    return results


class ListingStatus:
    def __init__(self, items, enabled):
        self.enabled = enabled
        self._states = {}
        self._remote = None
        requests, indices = [], {}
        for item in items:
            request = _request(item) if enabled else None
            if request is None:
                continue
            key = json.dumps(request, sort_keys=True)
            if key not in indices:
                indices[key] = len(requests)
                requests.append(request)
        results = _batch(requests)
        for index, request in enumerate(requests):
            result = results.get(index) or {}
            details = result.get(request[2]) or {}
            if not isinstance(details, dict) or not details:
                continue
            path, media = request[3:]
            if request[2] == 'filedetails':
                media = details.get('type')
                if media not in _VIDEO or _number(details.get('id')) <= 0:
                    continue  # a file on disk is not necessarily a library item
            if not path.lower().startswith('videodb://') and not _same_file(path, details.get('file')):
                continue  # removed/reused IDs and edited file targets must not cross-match
            self._states[json.dumps(request, sort_keys=True)] = details
        # Library hits, including playcount=0, are authoritative. No cloud
        # requests are needed if every displayed item has a local result.
        missing = [item for item in items if self._local(item) is None]
        self._remote = mdblist.Snapshot(missing)

    def _local(self, item):
        request = _request(item) if self.enabled else None
        return self._states.get(json.dumps(request, sort_keys=True)) if request else None

    def apply(self, listitem, item):
        state = self._local(item)
        if state is None and self._remote is not None:
            state = self._remote.get(item)
        # Saved progress is deliberately not reused: it can become stale or refer
        # to an unsupported source. The setting also suppresses manually saved status.
        props = item.get('properties') or {}
        for key in props:
            if key.lower() in _STATUS_PROPERTIES:
                listitem.setProperty(key, '')
        media = (item.get('info') or {}).get('mediatype', '')
        if media and media not in _VIDEO and media != 'video':
            return
        if media == 'tvshow':
            self._apply_show(listitem, state)
            return
        tag = listitem.getVideoInfoTag()
        if state and state.get('type') in _VIDEO:
            tag.setMediaType(state['type'])
            tag.setDbId(int(state['id']))
        playcount = int(_number(state.get('playcount'))) if state else 0
        tag.setPlaycount(playcount)
        # Overlay is still a ListItem field rather than an InfoTagVideo setter.
        # Keep this narrow compatibility call for skins using ListItem.Overlay.
        listitem.setInfo('video', {'overlay': (7 if playcount else 6) if state else 0})
        resume = (state.get('resume') or {}) if state else {}
        total = _number(resume.get('total')) or (_number(state.get('runtime')) if state else 0.0)
        position = min(_number(resume.get('position')), total) if total else 0.0
        tag.setResumePoint(position, total)
        if state and state.get('lastplayed'):
            tag.setLastPlayed(state['lastplayed'])
        # Native InfoTag values drive ListItem.IsResumable / PercentPlayed.
        # These properties cover skins using the older property-based convention.
        listitem.setProperty('ResumeTime', str(position) if position else '')
        listitem.setProperty('TotalTime', str(total) if total else '')
        percent = 100.0 * position / total if total else (_number(state.get('percent')) if state else 0)
        listitem.setProperty('PercentPlayed', '%g' % percent if total or percent else '')

    @staticmethod
    def _apply_show(listitem, state):
        tag = listitem.getVideoInfoTag()
        playcount = int(_number(state.get('playcount'))) if state else 0
        tag.setPlaycount(playcount)
        tag.setResumePoint(0.0, 0.0)
        listitem.setInfo('video', {'overlay': (7 if playcount else 6) if state else 0})
        for name in ('WatchedEpisodes', 'UnWatchedEpisodes', 'TotalEpisodes', 'NumEpisodes',
                     'WatchedEpisodePercent', 'WatchedProgress', 'PercentPlayed', 'InProgress',
                     'ResumeTime', 'TotalTime'):
            listitem.setProperty(name, '')
        if not state:
            return
        total = int(_number(state.get('total_episodes', state.get('episode'))))
        watched = int(_number(state.get('watched_episodes', state.get('watchedepisodes'))))
        if total:
            watched = min(total, watched)
            # Skins can use this string as a numbered dial texture filename.
            # Match Kodi/source conventions: whole percentages, truncated.
            percent = 100 * watched // total
            for name, value in (('WatchedEpisodes', watched), ('UnWatchedEpisodes', total-watched),
                                ('TotalEpisodes', total), ('NumEpisodes', total),
                                ('WatchedEpisodePercent', str(percent)),
                                ('WatchedProgress', str(percent)), ('PercentPlayed', str(percent))):
                listitem.setProperty(name, str(value))
        elif playcount:
            # Completion is explicit even when MDBList supplies no episode total.
            listitem.setProperty('UnWatchedEpisodes', '0')
            for name in ('WatchedEpisodePercent', 'WatchedProgress', 'PercentPlayed'):
                listitem.setProperty(name, '100')
        listitem.setProperty('InProgress', 'true' if state.get('inprogress') or (0 < watched < total) else '')
