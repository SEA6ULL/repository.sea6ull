# -*- coding: utf-8 -*-
"""Shared, process-safe health state for Rotation's external providers."""

import json
import time

import xbmc
import xbmcgui


class ProviderError(Exception):
    """A classified provider failure suitable for a user-facing message."""

    def __init__(self, provider, category="unreachable", detail=""):
        self.provider = provider
        self.category = category
        self.detail = str(detail or "")
        super(ProviderError, self).__init__(self.message)

    @property
    def message(self):
        if self.category == "configuration":
            return "%s configuration or authorization failed." % self.provider
        if self.category == "rate_limited":
            return "%s is temporarily rate limited. Try again shortly." % self.provider
        if self.category == "malformed":
            return "%s returned an unexpected response." % self.provider
        if self.category == "service_error":
            return "%s returned an error. Try again later." % self.provider
        return "%s is currently unreachable. Try again later." % self.provider


class ProviderHealth(object):
    """Circuit breaker stored on Kodi's home window across plugin processes."""

    PREFIX = "Rotation.ProviderHealth."

    def __init__(self, provider, cooldown=60):
        self.provider = provider
        self.slug = provider.lower().replace(".", "").replace(" ", "-")
        self.cooldown = max(15, int(cooldown))
        self.window = xbmcgui.Window(10000)

    @property
    def key(self):
        return self.PREFIX + self.slug

    def state(self):
        try:
            return json.loads(self.window.getProperty(self.key) or "{}")
        except (TypeError, ValueError):
            return {}

    def circuit_error(self):
        state = self.state()
        if float(state.get("until") or 0) > time.time():
            return ProviderError(
                self.provider, state.get("category") or "unreachable",
                state.get("detail") or "Temporarily paused after a recent failure")
        return None

    def success(self):
        state = self.state()
        state.update({"state": "connected", "category": "", "detail": "",
                      "until": 0, "last_success": time.time()})
        self.window.setProperty(self.key, json.dumps(state))

    def failure(self, category, detail=""):
        state = self.state()
        state.update({"state": category, "category": category,
                      "detail": str(detail or ""),
                      "until": time.time() + self.cooldown,
                      "last_failure": time.time()})
        self.window.setProperty(self.key, json.dumps(state))
        xbmc.log("[rotation] %s circuit opened for %ds: %s" %
                 (self.provider, self.cooldown, detail), xbmc.LOGWARNING)
        return ProviderError(self.provider, category, detail)

    def status_text(self):
        state = self.state()
        if not state:
            return "Not checked"
        until = float(state.get("until") or 0)
        if until > time.time():
            remaining = max(1, int(round(until - time.time())))
            return "%s — retry in %ds" % (
                (state.get("category") or "unavailable").replace("_", " ").title(),
                remaining)
        if state.get("state") == "connected":
            return "Connected"
        return "Ready to retry"
