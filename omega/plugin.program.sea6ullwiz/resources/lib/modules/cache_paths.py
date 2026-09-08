"""Detection of add-on cache stores that Kodi and its add-ons rebuild on demand.

Shared by the Clear Cache Files tool and the backup builder so both agree on
what counts as a cache. TMDb Helper bumps the suffix on its stores whenever the
format changes (blur_v3 -> blur_v4, database_07 -> database_08 and so on), so
the family name is matched and any trailing version number is accepted.
"""

import os
import re

TMDBH_ID = 'plugin.video.themoviedb.helper'

TMDBH_KNOWN = [
    re.compile(r'^blur(_v?\d+)?$', re.IGNORECASE),
    re.compile(r'^crop(_v?\d+)?$', re.IGNORECASE),
    re.compile(r'^database(_v?\d+)?$', re.IGNORECASE),
]

# Catches any new versioned cache store TMDb Helper adds later that follows the
# same naming convention, e.g. "desaturate_v1". Folders with no version suffix
# are never picked up by this, so user data is left alone.
TMDBH_GENERIC = re.compile(r'^[A-Za-z][A-Za-z0-9]*_v?\d+$')

# Never treated as cache, no matter what the patterns above say.
TMDBH_PROTECTED = ['settings.xml', 'players', 'themes', 'imports']


def is_tmdbh_cache(name, full_path):
    """Is this entry inside TMDb Helper's add-on data folder a cache store?"""
    if name.startswith('.') or name.lower() in TMDBH_PROTECTED:
        return False
    if any(pattern.match(name) for pattern in TMDBH_KNOWN):
        return True
    return bool(os.path.isdir(full_path) and TMDBH_GENERIC.match(name))


def tmdbh_cache_dirs(addon_data_path, logger=None):
    """Full paths of the TMDb Helper cache stores currently on disk."""
    base = os.path.join(addon_data_path, TMDBH_ID)
    if not os.path.isdir(base):
        return []
    try:
        entries = sorted(os.listdir(base))
    except OSError as e:
        if logger:
            logger('Unable to read %s. Reason: %s' % (base, e))
        return []
    return [os.path.join(base, entry) for entry in entries
            if is_tmdbh_cache(entry, os.path.join(base, entry))]
