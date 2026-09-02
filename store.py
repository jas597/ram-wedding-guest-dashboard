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

from sqlalchemy import (Column, DateTime, ForeignKey, Integer, MetaData,
                        String, Table, Text, UniqueConstraint, create_engine,
                        delete, insert, select, update)

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

# The dashboard has one shared password, so there is no individual identity to
# record. Naming that explicitly beats inventing a user we cannot know.
CHANGED_BY = "shared-dashboard-user"

change_log = Table(
    "change_log", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("kind", String(16), nullable=False),          # 'status' | 'category'
    Column("target_key", String(64), nullable=False),
    Column("subject", Text),                             # readable name
    Column("original_value", Text),   # JSON: the very first value ever recorded
    Column("from_value", Text),       # JSON: value immediately before this edit
    Column("to_value", Text),         # JSON: value after this edit
    Column("changed_by", String(64), nullable=False),
    Column("changed_at", DateTime(timezone=True), nullable=False),
)

# --------------------------------------------------------------- rooms
# Accommodation lives entirely in its own tables. Nothing here writes to
# party_category or guest_status_override, so allocating a room can never
# change a guest's category or RSVP status.
room = Table(
    "room", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("name", String(120), nullable=False, unique=True),
    Column("property", String(120)),
    Column("room_type", String(80)),
    Column("capacity", Integer, nullable=False),
    Column("notes", Text),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)

# One row per (room, party). A party split across rooms simply has several
# rows -- 64 of the 105 attending parties carry their whole headcount in a
# single CSV row, so their people have no individual identity and a split can
# only ever be expressed as a count, never as a list of members.
room_allocation = Table(
    "room_allocation", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("room_id", Integer, ForeignKey("room.id", ondelete="CASCADE"),
           nullable=False),
    Column("party_key", String(64), nullable=False),
    Column("people", Integer, nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("room_id", "party_key", name="uq_room_party"),
)

# Only ever holds NO_ROOM_REQUIRED. A party with no row here and no
# allocations is "not decided".
NO_ROOM_REQUIRED = "no_room_required"

party_room_status = Table(
    "party_room_status", metadata,
    Column("party_key", String(64), primary_key=True),
    Column("status", String(32), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
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
            "original": json.loads(r["original_value"]) if r["original_value"] else None,
            "from": json.loads(r["from_value"]) if r["from_value"] else None,
            "to": json.loads(r["to_value"]) if r["to_value"] else None,
            "changed_by": r["changed_by"],
            "changed_at": _iso(r["changed_at"]),
        })
    return out


# ------------------------------------------------------------------- writes
def _original_value(conn, kind, target_key, fallback):
    """The first value ever recorded for this target.

    Every log row carries it, so the earliest row is authoritative and later
    edits just copy it forward. That keeps "what did the CSV originally say"
    answerable after any number of changes.
    """
    row = conn.execute(
        select(change_log.c.original_value)
        .where(change_log.c.kind == kind, change_log.c.target_key == target_key)
        .order_by(change_log.c.id.asc()).limit(1)
    ).first()
    if row and row[0] is not None:
        return json.loads(row[0])
    return fallback


def _log(conn, kind, target_key, subject, from_value, to_value, when,
         original_fallback=None):
    original = _original_value(conn, kind, target_key,
                               original_fallback if original_fallback is not None
                               else from_value)
    conn.execute(insert(change_log).values(
        kind=kind,
        target_key=target_key,
        subject=subject,
        original_value=json.dumps(original, sort_keys=True),
        from_value=json.dumps(from_value, sort_keys=True),
        to_value=json.dumps(to_value, sort_keys=True),
        changed_by=CHANGED_BY,
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
                 when,
                 # the CSV's own status, so "original" survives repeated moves
                 original_fallback={"status": rec.get("original_status"),
                                    "people": rec.get("original_people")})
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


# ------------------------------------------------------------ room reads
def load_rooms():
    stmt = select(room).order_by(room.c.name.asc())
    with engine().connect() as conn:
        rows = conn.execute(stmt).mappings().all()
    return [{
        "id": r["id"],
        "name": r["name"],
        "property": r["property"] or "",
        "room_type": r["room_type"] or "",
        "capacity": r["capacity"],
        "notes": r["notes"] or "",
    } for r in rows]


def load_allocations():
    stmt = select(room_allocation)
    with engine().connect() as conn:
        rows = conn.execute(stmt).mappings().all()
    return [{
        "id": r["id"],
        "room_id": r["room_id"],
        "party_key": r["party_key"],
        "people": r["people"],
        "updated_at": _iso(r["updated_at"]),
    } for r in rows]


def load_room_statuses():
    with engine().connect() as conn:
        rows = conn.execute(select(party_room_status)).mappings().all()
    return {r["party_key"]: r["status"] for r in rows}


# ----------------------------------------------------------- room writes
def save_room(values, room_id=None):
    """Create or update one room. Returns the room id."""
    when = _now()
    payload = {
        "name": values["name"].strip(),
        "property": (values.get("property") or "").strip() or None,
        "room_type": (values.get("room_type") or "").strip() or None,
        "capacity": int(values["capacity"]),
        "notes": (values.get("notes") or "").strip() or None,
    }
    with engine().begin() as conn:
        clash = conn.execute(
            select(room.c.id).where(room.c.name == payload["name"])
        ).first()
        if clash and (room_id is None or clash[0] != room_id):
            raise ValueError("A room called %r already exists." % payload["name"])

        if room_id is None:
            result = conn.execute(insert(room).values(
                created_at=when, updated_at=when, **payload))
            new_id = result.inserted_primary_key[0]
            _log(conn, "room", str(new_id), payload["name"], None, payload, when)
            return new_id

        before = conn.execute(
            select(room).where(room.c.id == room_id)).mappings().first()
        if not before:
            raise ValueError("Unknown room.")
        conn.execute(update(room).where(room.c.id == room_id)
                     .values(updated_at=when, **payload))
        _log(conn, "room", str(room_id), payload["name"],
             {k: before[k] for k in payload}, payload, when)
        return room_id


def delete_room(room_id):
    """Remove a room. Its allocations go with it -- callers warn first."""
    when = _now()
    with engine().begin() as conn:
        before = conn.execute(
            select(room).where(room.c.id == room_id)).mappings().first()
        if not before:
            return {"deleted": False}
        freed = conn.execute(
            select(room_allocation)
            .where(room_allocation.c.room_id == room_id)).mappings().all()
        conn.execute(delete(room_allocation)
                     .where(room_allocation.c.room_id == room_id))
        conn.execute(delete(room).where(room.c.id == room_id))
        _log(conn, "room", str(room_id), before["name"],
             {"name": before["name"], "capacity": before["capacity"],
              "allocations": len(freed)},
             {"deleted": True}, when)
    return {"deleted": True, "freed_allocations": len(freed)}


def set_allocation(room_id, party_key, people, subject, room_name):
    """Place `people` of a party in a room, or remove them when people is 0."""
    when = _now()
    with engine().begin() as conn:
        before = conn.execute(
            select(room_allocation).where(
                room_allocation.c.room_id == room_id,
                room_allocation.c.party_key == party_key)).mappings().first()

        if people <= 0:
            if not before:
                return {"changed": False}
            conn.execute(delete(room_allocation).where(
                room_allocation.c.id == before["id"]))
            _log(conn, "allocation", party_key, subject,
                 {"room": room_name, "people": before["people"]},
                 {"room": None, "people": 0}, when)
            return {"changed": True, "removed": True}

        if before:
            conn.execute(update(room_allocation)
                         .where(room_allocation.c.id == before["id"])
                         .values(people=people, updated_at=when))
        else:
            conn.execute(insert(room_allocation).values(
                room_id=room_id, party_key=party_key,
                people=people, updated_at=when))

        # An explicit allocation retires any "no room required" flag.
        conn.execute(delete(party_room_status)
                     .where(party_room_status.c.party_key == party_key))

        _log(conn, "allocation", party_key, subject,
             {"room": room_name if before else None,
              "people": before["people"] if before else 0},
             {"room": room_name, "people": people}, when)
    return {"changed": True}


def set_room_status(party_key, status, subject):
    """Flag a party as needing no room, or clear the flag."""
    when = _now()
    with engine().begin() as conn:
        before = conn.execute(
            select(party_room_status)
            .where(party_room_status.c.party_key == party_key)).mappings().first()
        previous = before["status"] if before else None

        if status == previous:
            return {"changed": False}

        if status is None:
            conn.execute(delete(party_room_status)
                         .where(party_room_status.c.party_key == party_key))
        elif before:
            conn.execute(update(party_room_status)
                         .where(party_room_status.c.party_key == party_key)
                         .values(status=status, updated_at=when))
        else:
            conn.execute(insert(party_room_status).values(
                party_key=party_key, status=status, updated_at=when))

        _log(conn, "allocation", party_key, subject,
             {"room_status": previous}, {"room_status": status}, when)
    return {"changed": True}
