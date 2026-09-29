# -*- coding: utf-8 -*-
"""Rotation's local persistent data store.

This module deliberately contains no streaming-provider implementation.  It
owns only user data (favorites) and the shared SQLite connection used by the
artwork cache.
"""

import os
import time

from .peewee import SqliteDatabase, Model, CharField, TextField, FloatField


db = SqliteDatabase(None)


class BaseModel(Model):
    class Meta:
        database = db


class Favorite(BaseModel):
    kind = CharField()
    url = CharField(unique=True)
    label = TextField()
    thumb = TextField(default="")
    artist = TextField(default="")
    album = TextField(default="")
    added_at = FloatField(default=0.0)


class FavoritesStore(object):
    """Small compatibility API for Rotation's saved provider entries."""

    def __init__(self, data_dir):
        os.makedirs(data_dir, exist_ok=True)
        if getattr(db, "database", None) is None:
            db.init(os.path.join(data_dir, "rotation.db"))
        db.connect(reuse_if_open=True)
        db.create_tables([Favorite], safe=True)

    def add_favorite(self, kind, url, label, thumb="", artist="", album=""):
        Favorite.replace(
            kind=kind, url=url, label=label, thumb=thumb,
            artist=artist, album=album, added_at=time.time()
        ).execute()

    def remove_favorite(self, url):
        Favorite.delete().where(Favorite.url == url).execute()

    def is_favorite(self, url):
        return Favorite.select().where(Favorite.url == url).exists()

    def get_favorites(self, kind=None):
        query = Favorite.select().order_by(Favorite.added_at.desc())
        if kind:
            query = query.where(Favorite.kind == kind)
        return [{
            "kind": row.kind, "url": row.url, "label": row.label,
            "thumb": row.thumb, "artist": row.artist, "album": row.album,
        } for row in query]

    def export_favorites(self):
        """Return every favorite with ordering metadata for shared migration."""
        return [{
            "kind": row.kind, "url": row.url, "label": row.label,
            "thumb": row.thumb, "artist": row.artist, "album": row.album,
            "added_at": float(row.added_at or 0.0),
        } for row in Favorite.select().order_by(Favorite.added_at.desc())]

    def set_order(self, kind, urls):
        """Persist a complete user-defined order for one favorites section."""
        anchor = time.time()
        with db.atomic():
            for position, url in enumerate(urls):
                (Favorite.update(added_at=anchor - position)
                 .where((Favorite.kind == kind) & (Favorite.url == url))
                 .execute())
