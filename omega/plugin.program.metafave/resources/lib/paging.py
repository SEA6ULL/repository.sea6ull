# SPDX-License-Identifier: GPL-3.0-only
"""Pagination is a view of a collection, never a storage or import limit."""


def positive(value, default=20):
    try:
        value = int(value)
        return value if 0 < value <= 2147483647 else default
    except (ValueError, TypeError, OverflowError):
        return default


def size(settings, folder=None, show_all=False):
    if show_all:
        return 0
    override = (folder or {}).get('page_size')
    if override == 0:
        return 0
    if override is not None:
        return positive(override)
    return positive(settings.items_per_page) if settings.paginate_folders else 0


def slice_entries(entries, page, page_size):
    """Return visible rows, their global offset, current page and total pages."""
    if not page_size:
        return entries, 0, 1, 1
    count = max(1, (len(entries) + page_size - 1) // page_size)
    current = min(positive(page, 1), count)
    start = (current - 1) * page_size
    return entries[start:start + page_size], start, current, count
