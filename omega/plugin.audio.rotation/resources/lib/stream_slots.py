# -*- coding: utf-8 -*-
"""
One shared budget for connections to MusicMP3.ru's file server.

The server allows two simultaneous connections per address. Kodi playing a
MusicMP3.ru song holds one; every download transfer holds one; every stream
check before playback briefly needs one. A third connection is answered with
HTTP 503, which used to be read as an outage (streaming) or as a missing
track (downloads).

The plugin, the background service and the download worker run as separate
Python invocations, so the budget lives in Kodi's global window properties.

Priority, highest first:
  1. the song Kodi is playing from MusicMP3.ru
  2. a stream check that playback is waiting on ("demand")
  3. downloads - they use whatever is left and pause when it shrinks

Playing music from the Kodi library uses no connection here and never
affects downloads.
"""

import json
import time

import xbmc
import xbmcgui

MAX_CONNECTIONS = 2
FILE_HOST = "listen.musicmp3.ru"

_DOWNLOADS = "Rotation.MusicMP3.DownloadSlots"
_LINGERING = "Rotation.MusicMP3.Lingering"
_WATCH = "Rotation.MusicMP3.PlaybackWatch"
_PREVIEW_LIMIT = "Rotation.MusicMP3.PreviewLimit"


def _base(url):
    return (url or "").split("|", 1)[0]


def set_preview_limit(url, seconds=30):
    """Have the service stop this preview after `seconds`.

    The site ignores requests for only part of a song, so a preview can't
    be limited at the source; it is stopped here instead.
    """
    _window().setProperty(_PREVIEW_LIMIT, json.dumps(
        {"file": _base(url), "at": time.time() + seconds}))


def _enforce_preview_limit(player, current):
    try:
        limit = json.loads(_window().getProperty(_PREVIEW_LIMIT) or "{}")
    except ValueError:
        limit = {}
    if not limit:
        return
    if _base(current) != limit.get("file"):
        _window().clearProperty(_PREVIEW_LIMIT)      # something else is playing now
        return
    if time.time() >= limit.get("at", 0):
        _window().clearProperty(_PREVIEW_LIMIT)
        xbmc.log("[rotation] Preview reached its 30 s limit; stopping it", xbmc.LOGINFO)
        player.stop()
_DEMAND = "Rotation.MusicMP3.StreamDemandUntil"
_HEARTBEAT_STALE = 30      # a transfer that stopped reporting is ignored
_DEMAND_SECONDS = 20       # how long one stream request reserves its slot
_HANDOFF_SECONDS = 6       # time for Kodi to open a stream after it is checked


def _window():
    return xbmcgui.Window(10000)


def kodi_streaming():
    """1 when Kodi is playing a MusicMP3.ru song (it holds a connection)."""
    try:
        player = xbmc.Player()
        if player.isPlayingAudio() and FILE_HOST in (player.getPlayingFile() or ""):
            return 1
    except Exception:
        pass
    return 0


def _download_slots():
    try:
        slots = json.loads(_window().getProperty(_DOWNLOADS) or "{}")
    except ValueError:
        slots = {}
    now = time.time()
    return {token: info for token, info in slots.items()
            if now - info.get("beat", 0) < _HEARTBEAT_STALE}


def _save_download_slots(slots):
    _window().setProperty(_DOWNLOADS, json.dumps(slots))


def downloads_active():
    return len(_download_slots())


# --------------------------------------------------------------------------- #
# Lingering connections
#
# When Kodi stops a stream early (a skip, a preview, Stop) it keeps the
# connection open for a while before closing it. Measured against
# MusicMP3.ru in a real session: still "busy" 50-59 s after the stop, free
# again by 67-68 s. Downloads, which close their connection themselves,
# free it at once. The service records each early Kodi stop here so every
# part of Rotation counts what the site counts.
# --------------------------------------------------------------------------- #

KODI_RELEASE_SECONDS = 70

def _lingering_rows():
    try:
        rows = json.loads(_window().getProperty(_LINGERING) or "[]")
    except ValueError:
        rows = []
    now = time.time()
    return [row for row in rows if row.get("until", 0) > now]


def add_lingering(until, label=""):
    rows = _lingering_rows()
    rows.append({"until": float(until), "label": label})
    _window().setProperty(_LINGERING, json.dumps(rows[-6:]))


def lingering_active():
    return len(_lingering_rows())


def seconds_until_free():
    """How long until the site should have a free connection, or 0."""
    rows = sorted(row["until"] for row in _lingering_rows())
    busy = kodi_streaming() + downloads_active() + len(rows)
    if busy < MAX_CONNECTIONS or not rows:
        return 0
    # The (busy - MAX + 1)-th lingering stream to expire frees a slot.
    index = min(len(rows) - 1, busy - MAX_CONNECTIONS)
    return max(0, rows[index] - time.time())


def watch_playback():
    """Called by the service once a second: notice MusicMP3.ru songs that
    stop early and record how long the site will keep counting them."""
    window = _window()
    try:
        watched = json.loads(window.getProperty(_WATCH) or "{}")
    except ValueError:
        watched = {}
    player = xbmc.Player()
    current = ""
    try:
        if player.isPlayingAudio():
            current = player.getPlayingFile() or ""
    except Exception:
        current = ""
    if FILE_HOST not in current:
        current = ""
    _enforce_preview_limit(player, current)
    note_streaming()
    if watched.get("file") and watched["file"] != current:
        total, elapsed = watched.get("total", 0), watched.get("elapsed", 0)
        if total and total - elapsed > 5:
            # Never longer than the song would have taken to finish anyway.
            until = min(time.time() + KODI_RELEASE_SECONDS,
                        watched.get("started", time.time()) + total + 5)
            add_lingering(until, watched.get("label", ""))
            xbmc.log("[rotation] MusicMP3.ru stream stopped %ds early; Kodi keeps "
                     "its connection for about another %ds" % (
                         int(total - elapsed), int(until - time.time())),
                     xbmc.LOGINFO)
        watched = {}
    if current:
        try:
            elapsed = float(player.getTime() or 0)
            total = float(player.getTotalTime() or 0)
        except Exception:
            elapsed, total = watched.get("elapsed", 0), watched.get("total", 0)
        if watched.get("file") != current:
            watched = {"file": current, "started": time.time() - elapsed}
        watched["elapsed"] = elapsed
        if total:
            watched["total"] = total
        window.setProperty(_WATCH, json.dumps(watched))
    else:
        window.clearProperty(_WATCH)


def demand_active():
    try:
        return time.time() < float(_window().getProperty(_DEMAND) or 0)
    except ValueError:
        return False


def streaming_session_active():
    """True while Rotation is streaming from MusicMP3.ru - including a mixed
    playlist while one of its library songs plays, because its next streamed
    song will need a connection. Library-only playback, videos and other
    add-ons don't count."""
    window = _window()
    return bool(kodi_streaming() or (
        window.getProperty("Rotation.LookaheadSession") and
        window.getProperty("Rotation.QueueHasStreams") == "1"))


def download_allowance(setting):
    """How many download transfers may run right now (0, 1 or 2)."""
    allowance = (MAX_CONNECTIONS - kodi_streaming() - lingering_active()
                 - (1 if demand_active() else 0))
    return max(0, min(int(setting), allowance))


_LAST_STREAMING = "Rotation.MusicMP3.LastStreamingAt"


def note_streaming():
    """Record that Rotation is streaming right now (called every second by
    the service, and by the download worker)."""
    if streaming_session_active():
        _window().setProperty(_LAST_STREAMING, str(time.time()))


def _moved_on():
    """Playing something that needs no MusicMP3.ru connection - a video, or
    music that isn't a Rotation stream - means the user isn't coming back to
    streaming in a moment, so downloads needn't wait."""
    try:
        player = xbmc.Player()
        if player.isPlayingVideo():
            return True
        return player.isPlayingAudio() and not streaming_session_active()
    except Exception:
        return False


def grace_left(resume_delay):
    """Seconds downloads should still wait after streaming stopped, or 0."""
    if not resume_delay or streaming_session_active():
        return 0
    try:
        last = float(_window().getProperty(_LAST_STREAMING) or 0)
    except ValueError:
        last = 0
    if not last or _moved_on():
        return 0
    return max(0, last + resume_delay - time.time())


def download_limits(setting, wait_while_streaming=False, resume_delay=0):
    """(new transfers that may start, running transfers that may continue).

    With "Wait until streaming stops", no new track starts during a
    streaming session, but a track already downloading may finish while it
    fits within the connection limit - stopping it would only throw away
    what it has received.
    """
    allowance = download_allowance(setting)
    if wait_while_streaming:
        note_streaming()
        # Also hold for a short while after streaming stops: stopping is
        # often just a pause to pick something else, and a track started in
        # that gap would be thrown away when playback resumes.
        if streaming_session_active() or grace_left(resume_delay) > 0:
            return 0, allowance
    return allowance, allowance


# --------------------------------------------------------------------------- #
# Download side
# --------------------------------------------------------------------------- #

def claim_download(token):
    """Register a running transfer. Its rank decides who pauses first."""
    slots = _download_slots()
    slots[token] = {"start": time.time(), "beat": time.time()}
    _save_download_slots(slots)


def heartbeat_download(token, received=0, expected=0):
    slots = _download_slots()
    if token in slots:
        slots[token]["beat"] = time.time()
        if expected:
            slots[token]["received"] = received
            slots[token]["expected"] = expected
        _save_download_slots(slots)


PROTECT_SECONDS = 15


def _seconds_left(info):
    """Estimated seconds until a transfer finishes, or None if unknown."""
    received, expected = info.get("received", 0), info.get("expected", 0)
    elapsed = time.time() - info.get("start", time.time())
    if not expected or not received or elapsed <= 0:
        return None
    rate = received / elapsed
    return (expected - received) / rate if rate > 0 else None


def _progress(info):
    expected = info.get("expected", 0)
    return info.get("received", 0) / float(expected) if expected else 0.0


def release_download(token):
    slots = _download_slots()
    if slots.pop(token, None) is not None:
        _save_download_slots(slots)


def download_must_yield(token, setting, wait_while_streaming=False, resume_delay=0):
    """True when this transfer has to pause to free a connection.

    A paused track restarts from zero (the site can't resume), so:
    a transfer about to finish (under PROTECT_SECONDS left) never pauses,
    and otherwise the one furthest along keeps going.
    """
    slots = _download_slots()
    if token not in slots:
        return False
    left = _seconds_left(slots[token])
    if left is not None and left <= PROTECT_SECONDS:
        return False
    order = sorted(slots, key=lambda key: _progress(slots[key]), reverse=True)
    keep = download_limits(setting, wait_while_streaming, resume_delay)[1]
    return order.index(token) >= keep


# --------------------------------------------------------------------------- #
# Streaming side
# --------------------------------------------------------------------------- #

def request_stream_slot(wait_seconds=8.0):
    """Reserve a connection for a stream check, pausing downloads if needed.

    Returns once a connection is free or after wait_seconds; the caller
    proceeds either way, because a free slot cannot be guaranteed (another
    device on the network may be using the site).
    """
    window = _window()
    window.setProperty(_DEMAND, str(time.time() + _DEMAND_SECONDS))
    deadline = time.time() + wait_seconds
    monitor = xbmc.Monitor()
    while time.time() < deadline:
        if kodi_streaming() + downloads_active() + lingering_active() < MAX_CONNECTIONS:
            return True
        if monitor.waitForAbort(0.2):
            break
    return False


def release_stream_slot(handoff=True):
    """End a reservation. With handoff, keep it briefly so a download does
    not take the connection in the moment before Kodi opens the stream."""
    window = _window()
    if handoff:
        window.setProperty(_DEMAND, str(time.time() + _HANDOFF_SECONDS))
    else:
        window.clearProperty(_DEMAND)


def connection_free():
    """True when a stream check could run without pausing anything."""
    return kodi_streaming() + downloads_active() + lingering_active() < MAX_CONNECTIONS


def rotation_using_connections():
    """True when Rotation itself holds connections - so a 503 is most likely
    our own doing rather than an outage."""
    return bool(kodi_streaming() or downloads_active() or lingering_active())
