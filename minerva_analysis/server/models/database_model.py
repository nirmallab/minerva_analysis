from minerva_analysis import app, db, data_path
from sqlalchemy.orm import relationship
from sqlalchemy import func

import io
import numpy as np
import sqlite3
import time


# Via https://stackoverflow.com/questions/2546207/does-sqlalchemy-have-an-equivalent-of-djangos-get-or-create
def create(model, **kwargs):
    instance = model(**kwargs)
    db.session.add(instance)
    db.session.commit()
    return instance


def get(model, **kwargs):
    return db.session.query(model).filter_by(**kwargs).one_or_none()


def edit(model, id, edit_field, edit_value):
    instance = get(model, id=id)
    instance.__setattr__(edit_field, edit_value)
    db.session.commit()


def get_all(model, **kwargs):
    return db.session.query(model).filter_by(is_deleted=False, **kwargs).order_by(model.id).all()


def get_or_create(model, **kwargs):
    if 'cells' in kwargs:
        cells = kwargs['cells']
        del kwargs['cells']
    instance = db.session.query(model).filter_by(**kwargs).one_or_none()
    if instance:
        return instance
    else:
        instance = model(cells=cells, **kwargs)
        db.session.add(instance)
        db.session.commit()
        return instance


def save_list(model, **kwargs):
    if 'cells' in kwargs:
        cells = kwargs['cells']
        del kwargs['cells']

    instance = db.session.query(model).filter_by(**kwargs).one_or_none()
    if instance:
        instance.__setattr__('cells', cells)
        db.session.commit()
        return instance
    else:
        instance = model(cells=cells, **kwargs)
        db.session.add(instance)
        db.session.commit()
        return instance


class ChannelList(db.Model):
    __tablename__ = 'channelList'
    id = db.Column(db.Integer, primary_key=True)
    datasource = db.Column(db.String(80), unique=False, nullable=False)
    cells = db.Column(db.LargeBinary, default={}, nullable=False)
    is_deleted = db.Column(db.Boolean, default=False, nullable=False)


class GatingList(db.Model):
    __tablename__ = 'gatinglist'
    id = db.Column(db.Integer, primary_key=True)
    datasource = db.Column(db.String(80), unique=False, nullable=False)
    cells = db.Column(db.LargeBinary, default={}, nullable=False)
    is_deleted = db.Column(db.Boolean, default=False, nullable=False)


def _ensure_healthy_database():
    """Move a corrupted db.sqlite3 aside so db.create_all() below starts fresh
    instead of the app crashing on every query against an unreadable file."""
    db_file = data_path / "db.sqlite3"
    if not db_file.exists():
        return
    try:
        conn = sqlite3.connect(str(db_file))
        try:
            result = conn.execute("PRAGMA integrity_check").fetchone()
        finally:
            conn.close()
        if not result or result[0] != "ok":
            raise sqlite3.DatabaseError(f"integrity_check reported: {result}")
    except sqlite3.DatabaseError as error:
        backup_path = db_file.with_name(f"db.sqlite3.corrupt-{int(time.time())}")
        print(f"WARNING: {db_file} failed integrity check ({error}); "
              f"moving it to {backup_path} and recreating a fresh database.")
        db_file.rename(backup_path)


_ensure_healthy_database()

with app.app_context():
    db.create_all()
