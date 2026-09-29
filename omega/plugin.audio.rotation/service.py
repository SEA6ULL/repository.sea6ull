# -*- coding: utf-8 -*-
"""Rotation background playback and safe directory refresh service."""

import hashlib
import json
import time
from urllib.parse import unquote

import xbmc
import xbmcaddon
import xbmcgui


ADDON = xbmcaddon.Addon("plugin.audio.rotation")
PENDING_REFRESH_PROPERTY = "Rotation.PendingDirectoryRefresh"
PENDING_REFRESH_TTL = 30 * 60
GUI_SETTLE_SECONDS = 3
_LAST_UNSAFE_GUI = time.monotonic()
_LOOKAHEAD_STOPPED_AT = 0.0


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
        return
    _LOOKAHEAD_STOPPED_AT = 0.0
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
    window.setProperty("Rotation.LookaheadBusy", str(time.time()))
    xbmc.executebuiltin(
        "RunPlugin(plugin://plugin.audio.rotation/playlists/lookahead?session=%s)" %
        session)


def _plugin_route(value):
    return unquote(value or "").split("?", 1)[0].rstrip("/").casefold()


def _refresh_pending_directory():
    """Apply one deferred refresh after the playlist returns from playback."""
    global _LAST_UNSAFE_GUI
    window = xbmcgui.Window(10000)
    # Observe unsafe GUI states on every service pass, even before artwork has
    # produced a pending marker. Otherwise a marker could arrive in the narrow
    # gap between a context menu closing and its selected action starting.
    try:
        dialog_active = xbmcgui.getCurrentWindowDialogId() != 0
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
    current = xbmc.getInfoLabel("Container.FolderPath") or ""
    if not current or _plugin_route(current) != _plugin_route(path):
        return False
    # Context menus disappear just before their selected action opens Kodi's
    # busy dialog. Waiting briefly after every dialog/player transition closes
    # that race without treating ordinary list navigation as unsafe.
    if time.monotonic() - _LAST_UNSAFE_GUI < GUI_SETTLE_SECONDS:
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


MONITOR = xbmc.Monitor()
while not MONITOR.abortRequested():
    _refresh_pending_directory()
    _maintain_playback_lookahead()
    if MONITOR.waitForAbort(1):
        break
