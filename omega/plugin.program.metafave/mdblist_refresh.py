# SPDX-License-Identifier: GPL-3.0-only
"""One-shot reader launched by listings; never runs as a service."""
from resources.lib.mdblist import refresh

refresh()
