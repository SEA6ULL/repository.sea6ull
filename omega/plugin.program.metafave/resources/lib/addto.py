# SPDX-License-Identifier: GPL-3.0-only
# MetaFave is authored by SEA6ULL / Claude / GPT and licensed under GPL-3.0.
"""The global context menu entry: Add to MetaFave."""

import sys

import xbmcgui

from . import capture, storage
from .common import ICON_REPAIR, L, confirm, notify, plain
from .dialogs import choose_folder, folder_label, pick_item, resolve_destination, text


def run():
    item, hints = capture.capture(sys.listitem)  # capture first, while the item has focus
    if not item:
        notify(L(30033))
        return

    if item['target'].get('kind') == 'folder':
        mode = xbmcgui.Dialog().select(L(30000), [L(30301), L(30300)])
        if mode < 0:
            return
        if mode == 1:
            from . import listcopy
            listcopy.run(item)
            return

    data = storage.load_for_edit()
    dest, extra = resolve_destination(
        data, L(30000), capture.suggest_folder_name(item, hints),
        extra=[('repair', L(30016), ICON_REPAIR)] if data['items'] else None)
    if extra == 'repair':
        _replace_target(item)
        return
    if not dest:
        return

    label = text(L(30015), item['label'])
    if not label:
        return
    item['label'] = label
    storage.add_item(data, dest, item)
    storage.save(data)
    confirm(L(30018) % folder_label(data, dest))


def _replace_target(captured):
    """Repair a broken shortcut using the target of the item just captured."""
    data = storage.load_for_edit()
    choice = choose_folder(data, L(30082))
    if not choice:
        return
    folder_id = choice[1]
    if not data['folders'][folder_id]['items']:
        notify(L(30022))
        return
    item_id = pick_item(data, folder_id, L(30019))
    if not item_id:
        return
    label = plain(data['items'][item_id]['label'])
    if not xbmcgui.Dialog().yesno(L(30016), L(30020) % label):
        return
    data['items'][item_id]['target'] = captured['target']
    storage.save(data)
    confirm(L(30021))
