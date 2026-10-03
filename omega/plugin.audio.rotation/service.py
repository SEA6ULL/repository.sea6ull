# -*- coding: utf-8 -*-
"""Rotation background playback and safe directory refresh service."""

import hashlib
import json
import sys
import time
from urllib.parse import unquote, urlparse, parse_qs

import xbmc
import xbmcaddon
import xbmcgui
import xbmcvfs
from resources.lib.cache_maintenance import prune_cache_files
from resources.lib.downloads import start_worker as start_download_worker
from resources.lib import stream_slots


ADDON = xbmcaddon.Addon("plugin.audio.rotation")
PENDING_REFRESH_PROPERTY = "Rotation.PendingDirectoryRefresh"
PENDING_REFRESH_TTL = 30 * 60
GUI_SETTLE_SECONDS = 3
_LAST_UNSAFE_GUI = time.monotonic()
_LOOKAHEAD_STOPPED_AT = 0.0


def _player_shuffled():
    """Kodi's music shuffle flag (the player's shuffle button)."""
    try:
        result = json.loads(xbmc.executeJSONRPC(json.dumps({
            "jsonrpc": "2.0", "id": 1, "method": "Playlist.GetProperties",
            "params": {"playlistid": 0, "properties": ["shuffled"]}}))).get("result") or {}
        if "shuffled" in result:
            return bool(result["shuffled"])
    except Exception:
        pass
    return bool(xbmc.getCondVisibility("Playlist.IsRandom"))


def _maintain_playback_lookahead():
    """Ask the plugin to append one verified song before Kodi needs it."""
    global _LOOKAHEAD_STOPPED_AT
    window = xbmcgui.Window(10000)
    session = window.getProperty("Rotation.LookaheadSession")
    if not session:
        _LOOKAHEAD_STOPPED_AT = 0.0
        return
    player = xbmc.Player()
    if not player.isPlayingAudio():
        if not _LOOKAHEAD_STOPPED_AT:
            _LOOKAHEAD_STOPPED_AT = time.monotonic()
        elif time.monotonic() - _LOOKAHEAD_STOPPED_AT > 12:
            window.clearProperty("Rotation.LookaheadSession")
            window.clearProperty("Rotation.LookaheadBusy")
            window.clearProperty("Rotation.QueueShuffled")
            window.clearProperty("Rotation.QueueHasStreams")
            window.clearProperty("Rotation.LookaheadRetryAt")
        return
    _LOOKAHEAD_STOPPED_AT = 0.0
    # The shuffle button changed: the plugin re-orders the held-back queue
    # straight away, even though a song is already queued ahead.
    queue_shuffled = window.getProperty("Rotation.QueueShuffled")
    reorder = bool(queue_shuffled) and (queue_shuffled == "1") != _player_shuffled()
    if not reorder:
        # Give rapid track changes time to settle before requesting another stream.
        try:
            if player.getTime() < 2:
                return
        except (RuntimeError, AttributeError):
            return
        playlist = xbmc.PlayList(xbmc.PLAYLIST_MUSIC)
        try:
            position = playlist.getposition()
        except AttributeError:
            position = 0
        # At most one future item is exposed to the player. Unverified URLs can
        # therefore never form a rapid skip chain, regardless of the active skin.
        if playlist.size() - max(0, position) > 1:
            return
    try:
        unavailable_until = float(window.getProperty(
            "Rotation.MusicMP3UnavailableUntil") or 0)
    except (TypeError, ValueError):
        unavailable_until = 0
    # Keep the saved queue intact while the provider recovers. The next
    # service pass after the deadline resumes lookahead automatically.
    if time.time() < unavailable_until:
        return
    try:
        busy_since = float(window.getProperty("Rotation.LookaheadBusy") or 0)
    except (TypeError, ValueError):
        busy_since = 0
    # Stream discovery may legitimately take longer than fifteen seconds when
    # several candidates are absent. A short expiry launched overlapping
    # workers against the same queue and provider.
    if busy_since and time.time() - busy_since < 120:
        return
    # A deferred next-song check (a download held the other connection) is
    # retried every 15 s, and immediately - allowed to pause the download -
    # once the current song has 20 s or less to go.
    urgent = False
    try:
        retry_at = float(window.getProperty("Rotation.LookaheadRetryAt") or 0)
    except (TypeError, ValueError):
        retry_at = 0
    if retry_at:
        try:
            left = player.getTotalTime() - player.getTime()
        except (RuntimeError, AttributeError):
            left = 999
        urgent = 0 < left <= 20
        if not urgent and time.time() < retry_at:
            return
    if reorder:
        _log("Player shuffle changed - re-ordering Rotation's queue")
    if urgent:
        _log("Current song nearly over - checking the next song now, pausing a download if needed")
    window.setProperty("Rotation.LookaheadBusy", str(time.time()))
    xbmc.executebuiltin(
        "RunPlugin(plugin://plugin.audio.rotation/playlists/lookahead?session=%s%s)" %
        (session, "&urgent=1" if urgent else ""))


def _plugin_route(value):
    """Keep query-selected playlists distinct when applying a pending redraw."""
    parsed = urlparse(value or "")
    query = tuple(sorted(
        (key.casefold(), tuple(item.casefold() for item in values))
        for key, values in parse_qs(
            parsed.query, keep_blank_values=True).items()))
    return parsed.netloc.casefold(), parsed.path.rstrip("/").casefold(), query


_LAST_REFRESH_HOLD = ""


def _refresh_hold(reason):
    global _LAST_REFRESH_HOLD
    if reason != _LAST_REFRESH_HOLD:
        _LAST_REFRESH_HOLD = reason
        _log("Artwork refresh held: %s" % reason)


def _refresh_pending_directory():
    """Apply one deferred refresh after the playlist returns from playback."""
    global _LAST_UNSAFE_GUI
    window = xbmcgui.Window(10000)
    # Observe unsafe GUI states on every service pass, even before artwork has
    # produced a pending marker. Otherwise a marker could arrive in the narrow
    # gap between a context menu closing and its selected action starting.
    try:
        dialog_active = (xbmc.getCondVisibility("System.HasActiveModalDialog") or
                         xbmcgui.getCurrentWindowDialogId() in (10106, 10138, 10160, 12000))
        player_window = xbmcgui.getCurrentWindowId() in (12005, 12006)
    except (AttributeError, RuntimeError):
        dialog_active = xbmc.getCondVisibility("System.HasActiveModalDialog")
        player_window = False
    try:
        play_action_active = time.time() < float(
            window.getProperty("Rotation.PlayActionUntil") or 0)
    except (TypeError, ValueError):
        play_action_active = False
    if dialog_active or player_window or play_action_active:
        _LAST_UNSAFE_GUI = time.monotonic()
        if window.getProperty(PENDING_REFRESH_PROPERTY):
            _refresh_hold("dialog=%s dialog_id=%s window_id=%s player_window=%s play_action=%s" %
                          (dialog_active, xbmcgui.getCurrentWindowDialogId(),
                           xbmcgui.getCurrentWindowId(), player_window, play_action_active))
        return False

    raw = window.getProperty(PENDING_REFRESH_PROPERTY)
    if not raw:
        return False
    try:
        pending = json.loads(raw)
        path = pending.get("path") or ""
        created = float(pending.get("created") or 0)
    except (TypeError, ValueError, AttributeError):
        window.clearProperty(PENDING_REFRESH_PROPERTY)
        return False
    if not path or time.time() - created > PENDING_REFRESH_TTL:
        window.clearProperty(PENDING_REFRESH_PROPERTY)
        return False
    if time.time() < float(pending.get("not_before") or 0):
        return False
    marker = xbmc.getInfoLabel("Container.Property(Rotation.PlaylistLocation)")
    current = marker or xbmc.getInfoLabel("Container.FolderPath") or ""
    if not current or _plugin_route(current) != _plugin_route(path):
        _refresh_hold("directory differs: expected=%r visible=%r marker=%r" %
                      (path, current, marker))
        return False
    # The progress owner may still be closing its native dialog even after
    # the artwork worker published completion. Wait for its shared lease.
    progress_key = "rotation_playlist_art_" + hashlib.sha1(json.dumps(
        (urlparse(path).netloc.casefold(), urlparse(path).path.rstrip("/").casefold(),
         sorted((key.casefold(), sorted(v.casefold() for v in values))
                for key, values in parse_qs(urlparse(path).query,
                                          keep_blank_values=True).items())),
        ensure_ascii=False).encode("utf-8")).hexdigest() + "_progress_owner"
    try:
        if time.time() - float(window.getProperty(progress_key).rsplit("|", 1)[-1]) < 10:
            _refresh_hold("waiting for progress dialog to close")
            return False
    except (TypeError, ValueError):
        pass
    # Context menus disappear just before their selected action opens Kodi's
    # busy dialog. Waiting briefly after every dialog/player transition closes
    # that race without treating ordinary list navigation as unsafe.
    if time.monotonic() - _LAST_UNSAFE_GUI < GUI_SETTLE_SECONDS:
        _refresh_hold("waiting for GUI to settle")
        return False
    key = "Rotation.SafeRefresh.%s" % hashlib.sha1(
        path.encode("utf-8")).hexdigest()[:16]
    try:
        if time.time() - float(window.getProperty(key) or 0) < 15:
            return False
    except (TypeError, ValueError):
        pass
    # Clear first so the directory rebuild cannot race another service pass.
    # A worker that finishes later can still leave a fresh marker.
    window.clearProperty(PENDING_REFRESH_PROPERTY)
    window.setProperty(key, str(time.time()))
    _log("Applying deferred directory refresh: %s" % (
        pending.get("reason") or "background update"))
    xbmc.executebuiltin("Container.Refresh")
    return True


def _log(message, level=xbmc.LOGINFO):
    xbmc.log("[rotation.service] %s" % message, level)


def _run_service():
    monitor = xbmc.Monitor()
    # Don't resume paused downloads during Kodi's start-up. Starting the clock
    # here means the first check (every 30 s) happens one minute in. Starting
    # or retrying a download, or opening Downloads, still starts it at once.
    DOWNLOAD_RESUME_DELAY = 60
    last_download_check = time.time() - 30 + DOWNLOAD_RESUME_DELAY
    last_cache_cleanup = 0
    refresh_only = "refresh-only" in sys.argv[1:]
    window = xbmcgui.Window(10000)
    worker_key = "Rotation.DirectoryRefreshWorker.1.0.18"
    token = str(time.time())
    if refresh_only:
        try:
            if time.time() - float(window.getProperty(worker_key) or 0) < 10:
                return
        except ValueError:
            pass
        window.setProperty(worker_key, token)
        _log("Independent artwork refresh worker started")
    try:
        while not monitor.abortRequested():
            if refresh_only:
                if not window.getProperty(PENDING_REFRESH_PROPERTY):
                    break
                token = str(time.time())
                window.setProperty(worker_key, token)
            _refresh_pending_directory()
            if not refresh_only:
                if time.time() - last_cache_cleanup >= 86400:
                    try:
                        removed = prune_cache_files(xbmcvfs.translatePath(ADDON.getAddonInfo("profile")))
                        if removed:
                            _log("Removed %d old disposable cache files" % removed)
                    except Exception as exc:
                        _log("Cache housekeeping deferred: %s" % exc, xbmc.LOGWARNING)
                    last_cache_cleanup = time.time()
                if time.time() - last_download_check >= 30:
                    start_download_worker(ADDON)
                    last_download_check = time.time()
                _maintain_playback_lookahead()
                try:
                    stream_slots.watch_playback()
                except Exception as exc:
                    _log("Playback watch skipped: %s" % exc, xbmc.LOGDEBUG)
            if monitor.waitForAbort(1):
                break
    finally:
        if refresh_only and window.getProperty(worker_key) == token:
            window.clearProperty(worker_key)



_run_service()
