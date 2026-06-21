"""Persistent device-book metadata cache.

Calibre marks a library book as "on device" by matching the device's book list
against the library — primarily by ``uuid`` (which Calibre embeds in every EPUB
it sends), falling back to title+author. The device only stores raw files, so on
reconnect ``books()`` would otherwise have nothing but a filename to offer, and
nothing matches.

This cache remembers, per device, the identity of each file we know about
(``lpath -> {size, uuid, title, authors}``). It is populated when a book is sent
(we have the full ``Metadata`` then) and read back on every connect, so a book
sent from this machine is recognized and marked on-device without re-downloading
it. Entries are keyed by a device id so multiple devices don't collide, and are
pruned when a file is deleted or no longer present on the device.
"""

from calibre.utils.config import JSONConfig

# Stored on disk alongside the other plugin prefs.
_CACHE = JSONConfig('plugins/crosspoint_reader_devices')
_CACHE.defaults['books'] = {}


def _books():
    return _CACHE['books']


def get_entry(device_id, lpath):
    """Return the cached entry dict for ``lpath`` on ``device_id``, or None."""
    return _books().get(device_id, {}).get(lpath)


def put_many(device_id, entries):
    """Store/overwrite many ``(lpath, entry_dict)`` pairs for one device."""
    if not entries:
        return
    books = _books()
    dev = books.setdefault(device_id, {})
    for lpath, entry in entries:
        dev[lpath] = entry
    _CACHE['books'] = books  # reassign so JSONConfig commits to disk


def remove_many(device_id, lpaths):
    """Drop cached entries for the given lpaths on one device."""
    books = _books()
    dev = books.get(device_id)
    if not dev:
        return
    changed = False
    for lpath in lpaths:
        if lpath in dev:
            del dev[lpath]
            changed = True
    if changed:
        _CACHE['books'] = books


def prune(device_id, valid_lpaths):
    """Remove cached entries whose lpath is no longer present on the device."""
    books = _books()
    dev = books.get(device_id)
    if not dev:
        return
    stale = [lpath for lpath in dev if lpath not in valid_lpaths]
    if not stale:
        return
    for lpath in stale:
        del dev[lpath]
    _CACHE['books'] = books


def entry_from_metadata(size, mi):
    """Build a cache entry from a Calibre Metadata object."""
    authors = list(getattr(mi, 'authors', None) or [])
    return {
        'size': size,
        'uuid': getattr(mi, 'uuid', None) or None,
        'title': getattr(mi, 'title', None) or None,
        'authors': authors,
    }
