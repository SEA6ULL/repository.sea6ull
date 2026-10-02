"""Bounded housekeeping for disposable Rotation caches only."""
import os
import re
import time


def prune_cache_files(profile, now=None):
    """Prune old provider JSON/snapshots and abandoned download temporaries.

    Saved playlists, custom art, library indexes, availability/ignore reports,
    job records, download artwork and actual music are outside this scope.
    """
    now = time.time() if now is None else now
    rules = [('playlists', r'[a-f0-9]{40}\.json', 30 * 86400),
             ('downloads/snapshots', r'[a-f0-9]{64}\.json', 30 * 86400)]
    removed = 0
    for relative, pattern, age in rules:
        directory = os.path.join(profile, *relative.split('/'))
        try:
            names = os.listdir(directory)
        except OSError:
            continue
        for name in names:
            if not re.fullmatch(pattern, name):
                continue
            path = os.path.join(directory, name)
            try:
                if now - os.path.getmtime(path) > age:
                    os.remove(path)
                    removed += 1
            except OSError:
                pass
    # Receipts for an existing job must survive, including canceled jobs:
    # retries use them to recover a committed file after a process crash.
    directory = os.path.join(profile, 'downloads', 'temporary')
    jobs_dir = os.path.join(profile, 'downloads', 'jobs')
    try:
        names = os.listdir(directory)
    except OSError:
        return removed
    for name in names:
        match = re.match(r'^([a-f0-9]{32})[-.]', name)
        if match and os.path.exists(os.path.join(jobs_dir, match[1] + '.json')):
            continue
        path = os.path.join(directory, name)
        try:
            if os.path.isfile(path) and now - os.path.getmtime(path) > 86400:
                os.remove(path)
                removed += 1
        except OSError:
            pass
    return removed
