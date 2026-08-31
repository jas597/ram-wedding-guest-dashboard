"""Persistent overlay for guest categories and manual RSVP status changes.

`data/guestlist.csv` stays the immutable record of what the invitation system
exported -- nothing here ever rewrites it. Every change a user makes in the
dashboard is stored in this database instead, keyed to a stable content hash
of the CSV row, and applied as an overlay when the guest list is read.

Two consequences worth keeping in mind:

* The app runs under `gunicorn --workers 2`. Each worker holds its own copy of
  the parsed CSV, so overrides must be read from here per request rather than
  cached in a module global, otherwise the two workers disagree.
* `change_log` is append-only. Rows are never updated or deleted, so the
  original RSVP status stays recoverable no matter how often a record is moved.

Storage is chosen by `DATABASE_URL`: PostgreSQL in production, and a local
SQLite file under `instance/` (which is gitignored) when it is unset.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone

from sqlalchemy import (Column, DateTime, Integer, MetaData, String, Table,
                        Text, create_engine, delete, insert, select, update)

# --------------------------------------------------------------- vocabularies
CATEGORIES = ["Musician", "Family", "Friend", "Other"]
FRIEND_OF = ["Jawa", "Shanthi", "Ram"]
FRIEND_LOCATIONS = ["Local / NC", "Close Friend - Outside NC",
                    "Close Friend - India", "Other"]
FAMILY_LOCATIONS = ["Local / NC", "Outside NC", "India", "Other"]

# Statuses a record may be moved to. Only Attending for now, but the override
# column is a free string so widening this later needs no migration.
MOVE_TARGETS = ["Attending"]

INSTANCE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "instance")


def _database_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if not url:
        os.makedirs(INSTANCE_DIR, exist_ok=True)
        return "sqlite:///" + os.path.join(INSTANCE_DIR, "dashboard.db")
    # Render (and Heroku) hand out postgres://, which SQLAlchemy 2 rejects.
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql://", 1)
    return url


metadata = MetaData()

guest_status_override = Table(
    "guest_status_override", metadata,
    Column("record_key", String(32), primary_key=True),
    Column("status", String(64), nullable=False),
    Column("total_attending", Integer, nullable=False, default=0),
    Column("adults", Integer, nullable=False, default=0),
    Column("kids", Integer, nullable=False, default=0),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)

party_category = Table(
    "party_category", metadata,
    Column("party_key", String(64), primary_key=True),
    Column("category", String(32)),
    Column("friend_of", String(32)),
    Column("friend_location", String(64)),
    Column("family_location", String(64)),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)

change_log = Table(
    "change_log", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("kind", String(16), nullable=False),          # 'status' | 'category'
    Column("target_key", String(64), nullable=False),
    Column("subject", Text),                             # readable name
    Column("from_value", Text),                          # JSON snapshot
    Column("to_value", Text),                            # JSON snapshot
    Column("changed_at", DateTime(timezone=True), nullable=False),
)

_engine = None


def engine():
    global _engine
    if _engine is None:
        url = _database_url()
        kwargs = {"future": True, "pool_pre_ping": True}
        if url.startswith("sqlite"):
            # Flask serves concurrent requests; allow cross-thread reuse.
            kwargs["connect_args"] = {"check_same_thread": False}
        _engine = create_engine(url, **kwargs)
    return _engine


def init_db():
    metadata.create_all(engine())


def _now():
    return datetime.now(timezone.utc)


def _iso(value):
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat()


# -------------------------------------------------------------------- reads
def load_overrides():
    """record_key -> override dict, for applying over the parsed CSV."""
    with engine().connect() as conn:
        rows = conn.execute(select(guest_status_override)).mappings().all()
    return {
        r["record_key"]: {
            "status": r["status"],
            "total_attending": r["total_attending"],
            "adults": r["adults"],
            "kids": r["kids"],
            "updated_at": _iso(r["updated_at"]),
        }
        for r in rows
    }


def load_categories():
    """party_key -> category dict."""
    with engine().connect() as conn:
        rows = conn.execute(select(party_category)).mappings().all()
    return {
        r["party_key"]: {
            "category": r["category"],
            "friend_of": r["friend_of"],
            "friend_location": r["friend_location"],
            "family_location": r["family_location"],
            "updated_at": _iso(r["updated_at"]),
        }
        for r in rows
    }


def load_history(limit=200):
    stmt = select(change_log).order_by(change_log.c.id.desc()).limit(limit)
    with engine().connect() as conn:
        rows = conn.execute(stmt).mappings().all()
    out = []
    for r in rows:
        out.append({
            "id": r["id"],
            "kind": r["kind"],
            "target_key": r["target_key"],
            "subject": r["subject"],
            "from": json.loads(r["from_value"]) if r["from_value"] else None,
            "to": json.loads(r["to_value"]) if r["to_value"] else None,
            "changed_at": _iso(r["changed_at"]),
        })
    return out


# ------------------------------------------------------------------- writes
def _log(conn, kind, target_key, subject, from_value, to_value, when):
    conn.execute(insert(change_log).values(
        kind=kind,
        target_key=target_key,
        subject=subject,
        from_value=json.dumps(from_value, sort_keys=True),
        to_value=json.dumps(to_value, sort_keys=True),
        changed_at=when,
    ))


def save_category(party_key, subject, values):
    """Upsert one party's categorisation and record what changed.

    `values` carries category / friend_of / friend_location / family_location;
    fields irrelevant to the chosen category are cleared so a party switched
    from Friend to Family cannot keep a stale "Whose friend?" answer.
    """
    category = values.get("category") or None
    payload = {
        "category": category,
        "friend_of": values.get("friend_of") or None if category == "Friend" else None,
        "friend_location": values.get("friend_location") or None if category == "Friend" else None,
        "family_location": values.get("family_location") or None if category == "Family" else None,
    }
    when = _now()

    with engine().begin() as conn:
        existing = conn.execute(
            select(party_category).where(party_category.c.party_key == party_key)
        ).mappings().first()

        before = None
        if existing:
            before = {k: existing[k] for k in
                      ("category", "friend_of", "friend_location", "family_location")}

        if before == payload:
            return {"changed": False, "category": payload}

        if existing:
            if not category:
                # Clearing the category removes the row entirely.
                conn.execute(delete(party_category)
                             .where(party_category.c.party_key == party_key))
            else:
                conn.execute(update(party_category)
                             .where(party_category.c.party_key == party_key)
                             .values(updated_at=when, **payload))
        elif category:
            conn.execute(insert(party_category).values(
                party_key=party_key, updated_at=when, **payload))

        _log(conn, "category", party_key, subject, before, payload, when)

    return {"changed": True, "category": payload}


def move_records(records, to_status, subject):
    """Move specific CSV rows to `to_status`.

    `records` is a list of dicts, each carrying record_key, from_status and
    the headcount to record. Only the keys passed in are touched -- a party
    that is part attending and part not keeps its attending members untouched,
    because those keys are never in this list.
    """
    when = _now()
    written = []

    with engine().begin() as conn:
        for rec in records:
            key = rec["record_key"]
            payload = {
                "status": to_status,
                "total_attending": int(rec.get("total_attending") or 0),
                "adults": int(rec.get("adults") or 0),
                "kids": int(rec.get("kids") or 0),
            }
            existing = conn.execute(
                select(guest_status_override)
                .where(guest_status_override.c.record_key == key)
            ).mappings().first()

            if existing:
                conn.execute(update(guest_status_override)
                             .where(guest_status_override.c.record_key == key)
                             .values(updated_at=when, **payload))
            else:
                conn.execute(insert(guest_status_override).values(
                    record_key=key, updated_at=when, **payload))

            _log(conn, "status", key, rec.get("name") or subject,
                 {"status": rec.get("from_status"),
                  "people": rec.get("from_people")},
                 {"status": to_status,
                  "people": payload["total_attending"],
                  "adults": payload["adults"],
                  "kids": payload["kids"]},
                 when)
            written.append(key)

    return {"moved": written, "changed_at": _iso(when)}


def revert_record(record_key, subject):
    """Drop an override so the record falls back to its original CSV status."""
    when = _now()
    with engine().begin() as conn:
        existing = conn.execute(
            select(guest_status_override)
            .where(guest_status_override.c.record_key == record_key)
        ).mappings().first()
        if not existing:
            return {"reverted": False}
        conn.execute(delete(guest_status_override)
                     .where(guest_status_override.c.record_key == record_key))
        _log(conn, "status", record_key, subject,
             {"status": existing["status"], "people": existing["total_attending"]},
             {"status": "(reverted to source CSV)"}, when)
    return {"reverted": True, "changed_at": _iso(when)}
