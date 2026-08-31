"""Load the guest-list CSV and roll it up into parties.

Counting rules (verified against the source file, see README):

* A *party* is one ``Group Name`` where the source provides one, otherwise a
  single standalone client row.
* ``Guest 2`` / ``Guest 3`` / ``Guest 4`` rows are extra people inside their
  ``Guest 1`` primary's party -- never separate clients.
* Inside every grouped party each row carries ``Total Attending = 1``,
  including the primary. The primary does *not* already include its extra
  rows, so they are added, never discarded.
* Ungrouped clients carry their whole party in ``Total Attending``.
* Therefore attending people == plain sum of ``Total Attending`` (242).
* Non-attending rows always carry ``Total Attending = 0``, so their headcount
  falls back to ``Invited``, and to 1 person where ``Invited`` is 0 too.
"""

from __future__ import annotations

import copy
import csv
import hashlib
import os
from collections import OrderedDict

DATA_FILE = os.path.join(os.path.dirname(__file__), "data", "guestlist.csv")

ATTENDING = "Attending"
REGRETS = "Regrets"
PENDING_STATUSES = ["Page viewed", "Text opened", "Sent", "Bounced"]
ALL_STATUSES = [ATTENDING, REGRETS] + PENDING_STATUSES


def _int(value) -> int:
    text = str(value or "").strip()
    if not text:
        return 0
    try:
        return int(float(text))
    except ValueError:
        return 0


def _split_contact(raw: str):
    """The source packs phone and email into one column."""
    contact = (raw or "").strip()
    if not contact:
        return "", ""
    if "@" in contact:
        return "", contact
    return contact, ""


def _people_for(status: str, total_attending: int, invited: int) -> int:
    """Headcount for a single row."""
    if status == ATTENDING:
        return total_attending
    return max(invited, 1)


def record_key(row: dict) -> str:
    """Stable id for one CSV row.

    The export has no id column, so the key is a hash of the fields that
    identify a row: its group, name, contact and guest label. Verified unique
    across all 179 rows. Content-derived rather than positional, so re-exporting
    the CSV in a different order does not orphan the saved overrides.
    """
    raw = "|".join([
        (row.get("Group Name") or "").strip(),
        (row.get("Full Name") or "").strip(),
        (row.get("Email/Phone Number") or "").strip(),
        (row.get("Guest") or "").strip(),
    ])
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def _build_member(row: dict) -> dict:
    status = (row.get("Status") or "").strip()
    total_attending = _int(row.get("Total Attending"))
    invited = _int(row.get("Invited"))
    phone, email = _split_contact(row.get("Email/Phone Number"))
    name = (row.get("Full Name") or "").strip()
    return {
        "record_key": record_key(row),
        "guest_label": (row.get("Guest") or "").strip() or "Guest 1",
        "full_name": name or "(name not recorded)",
        "name_missing": not name,
        # `status` is the effective value and may be overridden later;
        # `source_status` is what the CSV said and is never mutated.
        "status": status,
        "source_status": status,
        "moved": False,
        "moved_at": None,
        "total_attending": total_attending,
        "adults": _int(row.get("Adults")),
        "kids": _int(row.get("Kids")),
        "invited": invited,
        "checked_in": _int(row.get("Total Checked In")),
        "people_count": _people_for(status, total_attending, invited),
        "phone": phone,
        "email": email,
        "message": (row.get("Message") or "").strip(),
        "channel": (row.get("Type") or "").strip(),
        "guest_tags": (row.get("Guest Tags") or "").strip(),
        "group_name": (row.get("Group Name") or "").strip(),
        "imported_first_name": (row.get("Imported First Name") or "").strip(),
        "imported_last_name": (row.get("Imported Last Name") or "").strip(),
    }


def load_parties(path: str = DATA_FILE):
    """Read the CSV and return the ordered list of parties."""
    with open(path, newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))

    buckets: "OrderedDict[str, list]" = OrderedDict()
    for index, row in enumerate(rows):
        group = (row.get("Group Name") or "").strip()
        key = group or "__solo_%d" % index
        buckets.setdefault(key, []).append(row)

    parties = []
    for number, (key, raw_members) in enumerate(buckets.items(), start=1):
        members = [_build_member(r) for r in raw_members]
        primary = members[0]
        is_group = not key.startswith("__solo_")
        name = key if is_group else primary["full_name"]

        parties.append({
            "party_no": number,
            # group name when there is one, else the primary row's own key --
            # stable across re-exports, unlike party_no which is positional
            "party_key": key if is_group else primary["record_key"],
            "name": name,
            "group_name": primary["group_name"],
            "is_group": is_group,
            "members": members,
            "member_count": len(members),
            "category": None,
            # every searchable string for this party, lowercased once up front
            "search_blob": " ".join(filter(None, [
                name,
                primary["group_name"],
                *[m["full_name"] for m in members],
                *[m["phone"] for m in members],
                *[m["email"] for m in members],
            ])).lower(),
        })
        recompute_party(parties[-1])

    return parties


def recompute_party(party):
    """Refresh the derived totals from the party's current member statuses.

    Called once when the CSV is parsed, and again after an override changes a
    member's status, so headcounts always follow from the members themselves
    rather than being stored twice.
    """
    members = party["members"]
    people_by_status = {}
    for member in members:
        people_by_status[member["status"]] = (
            people_by_status.get(member["status"], 0) + member["people_count"]
        )

    party["primary_status"] = members[0]["status"]
    party["statuses"] = sorted(
        {m["status"] for m in members},
        key=lambda s: ALL_STATUSES.index(s) if s in ALL_STATUSES else 99)
    party["people_by_status"] = people_by_status
    party["total_people"] = sum(m["people_count"] for m in members)
    party["attending_people"] = people_by_status.get(ATTENDING, 0)
    party["adults"] = sum(m["adults"] for m in members)
    party["kids"] = sum(m["kids"] for m in members)
    return party


def apply_overlay(base_parties, overrides, categories):
    """Lay saved status overrides and categories over the parsed CSV.

    Returns a fresh copy -- the parsed base is shared between requests and both
    gunicorn workers, so it must never be mutated in place.
    """
    parties = copy.deepcopy(base_parties)
    for party in parties:
        touched = False
        for member in party["members"]:
            override = overrides.get(member["record_key"])
            if not override:
                continue
            member["status"] = override["status"]
            member["moved"] = override["status"] != member["source_status"]
            member["moved_at"] = override["updated_at"]
            if override["status"] == ATTENDING:
                member["total_attending"] = override["total_attending"]
                member["adults"] = override["adults"]
                member["kids"] = override["kids"]
                member["people_count"] = override["total_attending"]
            else:
                member["people_count"] = _people_for(
                    override["status"], override["total_attending"], member["invited"])
            touched = True
        party["category"] = categories.get(party["party_key"])
        if touched:
            recompute_party(party)
    return parties


def build_summary(parties):
    """Per-status totals, plus the headline figures."""
    per_status = OrderedDict()
    for status in ALL_STATUSES:
        per_status[status] = {
            "status": status,
            "parties": 0,
            "entries": 0,
            "people": 0,
            "adults": 0,
            "kids": 0,
            "invited": 0,
        }

    total_rows = 0
    for party in parties:
        for member in party["members"]:
            bucket = per_status.setdefault(member["status"], {
                "status": member["status"], "parties": 0, "entries": 0,
                "people": 0, "adults": 0, "kids": 0, "invited": 0,
            })
            bucket["entries"] += 1
            bucket["people"] += member["people_count"]
            bucket["adults"] += member["adults"]
            bucket["kids"] += member["kids"]
            bucket["invited"] += member["invited"]
            # a party is attributed to the status on its Guest 1 primary row
            if member["guest_label"] == "Guest 1":
                bucket["parties"] += 1
            total_rows += 1

    pending = sum(per_status[s]["people"] for s in PENDING_STATUSES if s in per_status)

    # Category breakdowns count attending PEOPLE, not cards. A party of four
    # contributes four to its category, matching the 242 headline.
    by_category = {"Musician": 0, "Family": 0, "Friend": 0, "Other": 0,
                   "Uncategorised": 0}
    by_friend_of = {}
    by_friend_location = {}
    by_family_location = {}

    for party in parties:
        heads = party.get("attending_people", 0)
        if not heads:
            continue
        cat = (party.get("category") or {}).get("category")
        if cat in by_category:
            by_category[cat] += heads
        else:
            by_category["Uncategorised"] += heads
            continue
        if cat == "Friend":
            who = party["category"].get("friend_of") or "Unspecified"
            by_friend_of[who] = by_friend_of.get(who, 0) + heads
            loc = party["category"].get("friend_location") or "Unspecified"
            by_friend_location[loc] = by_friend_location.get(loc, 0) + heads
        elif cat == "Family":
            loc = party["category"].get("family_location") or "Unspecified"
            by_family_location[loc] = by_family_location.get(loc, 0) + heads

    return {
        "attending_by_category": by_category,
        "attending_by_friend_of": by_friend_of,
        "attending_by_friend_location": by_friend_location,
        "attending_by_family_location": by_family_location,
        "categorised_parties": sum(
            1 for p in parties if (p.get("category") or {}).get("category")),
        "moved_records": sum(
            1 for p in parties for m in p["members"] if m.get("moved")),
        "total_rows": total_rows,
        "total_parties": len(parties),
        "grouped_parties": sum(1 for p in parties if p["is_group"]),
        "extra_guest_rows": sum(p["member_count"] - 1 for p in parties),
        "total_people": sum(b["people"] for b in per_status.values()),
        "attending": per_status[ATTENDING]["people"],
        "attending_adults": per_status[ATTENDING]["adults"],
        "attending_kids": per_status[ATTENDING]["kids"],
        "regrets": per_status[REGRETS]["people"],
        "pending": pending,
        "page_viewed": per_status["Page viewed"]["people"],
        "text_opened": per_status["Text opened"]["people"],
        "sent": per_status["Sent"]["people"],
        "bounced": per_status["Bounced"]["people"],
        "by_status": list(per_status.values()),
    }
