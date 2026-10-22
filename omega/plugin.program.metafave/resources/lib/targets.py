# SPDX-License-Identifier: GPL-3.0-only
# MetaFave is authored by SEA6ULL / Claude / GPT and licensed under GPL-3.0.
"""Launch targets: how a saved item opens, independent of how it looks.

Kinds:
    folder   open a directory in a window          (widgets: native folder item)
    item     play or run a non-folder path         (widgets: native item)
    addon    RunAddon(<id>)                         (widgets: routed through MetaFave)
    script   RunScript(<id or path>)                (widgets: routed through MetaFave)
    command  any Kodi built-in command, verbatim    (widgets: routed through MetaFave)
"""

import re
from urllib.parse import unquote

KINDS = [('folder', 30047), ('item', 30048), ('addon', 30049), ('script', 30050), ('command', 30051)]
WINDOWS = [('videos', 30052), ('music', 30053), ('programs', 30054), ('pictures', 30055)]

WINDOW_IDS = {'videos': 10025, 'video': 10025, 'music': 10502, 'programs': 10001,
              'pictures': 10002, 'addonbrowser': 10040, 'favouritesbrowser': 10060}
_NAME_FOR_ID = {10025: 'videos', 10502: 'music', 10001: 'programs', 10002: 'pictures'}

_EXEC = re.compile(r'^\s*([A-Za-z][\w.]*)\s*\((.*)\)\s*$', re.DOTALL)


def window_id(window):
    window = str(window or '').strip().lower()
    if window.isdigit():
        return int(window)
    return WINDOW_IDS.get(window)


def media_window_name(current_window_id):
    return _NAME_FOR_ID.get(current_window_id)


def guess_window(path):
    low = path.lower()
    if low.startswith(('musicdb://', 'plugin://plugin.audio.', 'library://music')):
        return 'music'
    if low.startswith('plugin://plugin.image.'):
        return 'pictures'
    if low.startswith(('plugin://plugin.program.', 'plugin://script.')):
        return 'programs'
    return 'videos'


def split_params(text):
    """Split a built-in's parameter list on commas outside double quotes."""
    params, current, quoted, index = [], '', False, 0
    while index < len(text):
        char = text[index]
        if char == '\\' and text[index + 1:index + 2] == '"':
            current += '"'
            index += 2
            continue
        if char == '"':
            quoted = not quoted
        elif char == ',' and not quoted:
            params.append(current.strip())
            current = ''
        else:
            current += char
        index += 1
    if current.strip() or params:
        params.append(current.strip())
    return params


def from_command(command):
    """Turn a built-in (e.g. from a Kodi favorite) into the most native target."""
    match = _EXEC.match(command)
    if match and match.group(1).lower() == 'activatewindow':
        params = split_params(match.group(2))
        if len(params) >= 2 and params[1] and params[1].lower() != 'return':
            return {'kind': 'folder', 'path': params[1], 'window': params[0]}
    return {'kind': 'command', 'path': command.strip()}


def derive(path, is_folder, playable, addon_id, window):
    """Build a target from a captured ListItem."""
    low = path.lower()
    if low.startswith('favourites://'):
        return from_command(unquote(path[len('favourites://'):].rstrip('/')))
    if low.startswith('addons://'):
        if not is_folder:
            addon = addon_id or path.rstrip('/').rsplit('/', 1)[-1]
            if addon:
                return {'kind': 'addon', 'path': addon}
        return {'kind': 'folder', 'path': path, 'window': 'addonbrowser'}
    if low.startswith('script://'):
        return {'kind': 'script', 'path': path[len('script://'):].strip('/')}
    if low.startswith('androidapp://'):
        package = path.rstrip('/').rsplit('/', 1)[-1]
        return {'kind': 'command', 'path': 'StartAndroidActivity("%s")' % package}
    if is_folder:
        return {'kind': 'folder', 'path': path, 'window': window or guess_window(path)}
    return {'kind': 'item', 'path': path, 'playable': bool(playable)}


def _quote(value):
    return '"%s"' % value.replace('"', '\\"')


def command(target):
    """The Kodi built-in that launches a target."""
    kind, path = target.get('kind'), (target.get('path') or '').strip()
    if not path:
        return ''
    if kind == 'folder':
        return 'ActivateWindow(%s,%s,return)' % (target.get('window') or guess_window(path), _quote(path))
    if kind == 'item':
        return 'PlayMedia(%s)' % _quote(path)
    if kind == 'addon':
        return 'RunAddon(%s)' % path
    if kind == 'script':
        return 'RunScript(%s)' % path
    return path
