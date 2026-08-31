"""Ram's Wedding guest-list dashboard.

Internal tool: every page except the health check sits behind a shared
password, because the data holds guests' phone numbers, email addresses and
personal messages.
"""

from __future__ import annotations

import hmac
import os
import secrets
from functools import wraps

from flask import (Flask, jsonify, redirect, render_template, request,
                   session, url_for)

import duplicates
import store
from data_loader import apply_overlay, build_summary, load_parties

app = Flask(__name__)

# In production Render supplies both of these.
app.secret_key = os.environ.get("SECRET_KEY") or secrets.token_hex(32)

# No password is ever hardcoded here: a default committed to the repository
# would be a published password protecting real guests' contact details.
DASHBOARD_PASSWORD = os.environ.get("DASHBOARD_PASSWORD")
if not DASHBOARD_PASSWORD:
    if os.environ.get("RENDER"):
        raise RuntimeError(
            "DASHBOARD_PASSWORD is not set. Add it under Service -> Environment "
            "in Render; refusing to start rather than leave the guest data "
            "behind a guessable or absent password."
        )
    # Local development: mint a throwaway password and say what it is.
    DASHBOARD_PASSWORD = secrets.token_urlsafe(9)
    print("\n  DASHBOARD_PASSWORD is not set."
          "\n  This run's local password is: %s\n" % DASHBOARD_PASSWORD, flush=True)

# Same reasoning for storage. Without DATABASE_URL the app falls back to SQLite
# under instance/, which on Render sits on an ephemeral disk that is wiped on
# every restart, redeploy and idle-sleep wake. Every saved category and status
# move would disappear with no error anywhere. Refuse to start instead.
if os.environ.get("RENDER") and not os.environ.get("DATABASE_URL"):
    raise RuntimeError(
        "DATABASE_URL is not set. Point it at the Neon PostgreSQL connection "
        "string under Service -> Environment in Render; refusing to start "
        "rather than write changes to a disk Render wipes on every restart."
    )

app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    # Render terminates TLS, so cookies can be marked secure there
    SESSION_COOKIE_SECURE=bool(os.environ.get("RENDER")),
)

# The CSV never changes at runtime, so parse it once at import. This is the
# immutable base -- overrides from the database are laid over a copy of it per
# request, because each gunicorn worker has its own process and caching the
# merged result here would let the two workers drift apart.
BASE_PARTIES = load_parties()
REVIEW = duplicates.review_totals()

store.init_db()


def current_data():
    """Guest list with saved categories and status moves applied."""
    parties = apply_overlay(BASE_PARTIES, store.load_overrides(),
                            store.load_categories())
    return parties, build_summary(parties)


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("authenticated"):
            return redirect(url_for("login", next=request.path))
        return view(*args, **kwargs)

    return wrapped


@app.route("/healthz")
def healthz():
    """Unauthenticated so Render's health check can reach it.

    Deliberately reports counts from the untouched CSV only: no guest data and
    no database round-trip, so a database problem cannot take the service down.
    """
    return jsonify(status="ok", parties=len(BASE_PARTIES),
                   people=build_summary(BASE_PARTIES)["total_people"])


@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        supplied = request.form.get("password", "")
        if hmac.compare_digest(supplied, DASHBOARD_PASSWORD):
            session["authenticated"] = True
            session.permanent = False
            target = request.args.get("next") or url_for("index")
            # only ever redirect within this app
            if not target.startswith("/") or target.startswith("//"):
                target = url_for("index")
            return redirect(target)
        error = "That password is not correct."
    return render_template("login.html", error=error), (401 if error else 200)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/")
@login_required
def index():
    _, summary = current_data()
    return render_template("index.html", summary=summary)


@app.route("/api/data")
@login_required
def api_data():
    parties, summary = current_data()
    return jsonify({
        "summary": summary,
        "parties": parties,
        "duplicates": duplicates.DUPLICATES,
        "review": REVIEW,
        "vocab": {
            "categories": store.CATEGORIES,
            "friend_of": store.FRIEND_OF,
            "friend_locations": store.FRIEND_LOCATIONS,
            "family_locations": store.FAMILY_LOCATIONS,
        },
        "data_quality_notes": [
            {"subject": subject, "note": note}
            for subject, note in duplicates.DATA_QUALITY_NOTES
        ],
    })


def _find_party(parties, party_key):
    for party in parties:
        if party["party_key"] == party_key:
            return party
    return None


@app.route("/api/category", methods=["POST"])
@login_required
def api_category():
    """Save one party's categorisation."""
    body = request.get_json(silent=True) or {}
    party_key = (body.get("party_key") or "").strip()
    parties, _ = current_data()
    party = _find_party(parties, party_key)
    if not party:
        return jsonify(error="Unknown party."), 404

    category = body.get("category") or None
    if category and category not in store.CATEGORIES:
        return jsonify(error="Unknown category %r." % category), 400
    if category == "Friend":
        if body.get("friend_of") and body["friend_of"] not in store.FRIEND_OF:
            return jsonify(error="Unknown 'whose friend' value."), 400
        if (body.get("friend_location")
                and body["friend_location"] not in store.FRIEND_LOCATIONS):
            return jsonify(error="Unknown friend location."), 400
    if category == "Family":
        if (body.get("family_location")
                and body["family_location"] not in store.FAMILY_LOCATIONS):
            return jsonify(error="Unknown family location."), 400

    result = store.save_category(party_key, party["name"], body)
    parties, summary = current_data()
    return jsonify({
        "ok": True,
        "changed": result["changed"],
        "party": _find_party(parties, party_key),
        "summary": summary,
    })


@app.route("/api/move", methods=["POST"])
@login_required
def api_move():
    """Move specific rows to Attending.

    Only the record keys sent are touched. A part-attending party keeps its
    attending members untouched because their keys are never in the payload,
    and any key that is already Attending is rejected outright.
    """
    body = request.get_json(silent=True) or {}
    to_status = body.get("to_status") or "Attending"
    if to_status not in store.MOVE_TARGETS:
        return jsonify(error="Unsupported target status %r." % to_status), 400

    wanted = body.get("records") or []
    if not wanted:
        return jsonify(error="No records supplied."), 400

    parties, _ = current_data()
    by_key = {m["record_key"]: (p, m) for p in parties for m in p["members"]}

    records, subject = [], None
    for item in wanted:
        key = (item.get("record_key") or "").strip()
        if key not in by_key:
            return jsonify(error="Unknown record %r." % key), 404
        party, member = by_key[key]
        if member["status"] == to_status:
            return jsonify(
                error="%s is already %s." % (member["full_name"], to_status)), 409
        subject = subject or party["name"]
        total = item.get("total_attending")
        total = member["people_count"] if total is None else int(total)
        if total < 0:
            return jsonify(error="Headcount cannot be negative."), 400
        adults = item.get("adults")
        kids = int(item.get("kids") or 0)
        adults = (total - kids) if adults is None else int(adults)
        if adults < 0 or kids < 0:
            return jsonify(error="Adults and kids cannot be negative."), 400
        if adults + kids != total:
            return jsonify(
                error="Adults (%d) plus kids (%d) must equal the headcount (%d)."
                      % (adults, kids, total)), 400
        records.append({
            "record_key": key,
            "name": member["full_name"],
            "from_status": member["status"],
            "from_people": member["people_count"],
            # the CSV's own values, so the audit keeps "original" distinct from
            # "previous" however many times this record is moved
            "original_status": member["source_status"],
            "original_people": member["source_people"],
            "total_attending": total,
            "adults": adults,
            "kids": kids,
        })

    result = store.move_records(records, to_status, subject)
    parties, summary = current_data()
    party_key = by_key[records[0]["record_key"]][0]["party_key"]
    return jsonify({
        "ok": True,
        "moved": result["moved"],
        "changed_at": result["changed_at"],
        "party": _find_party(parties, party_key),
        "summary": summary,
    })


@app.route("/api/revert", methods=["POST"])
@login_required
def api_revert():
    """Undo a manual move, returning the row to its original CSV status.

    Record-level, like the move it undoes: a party that was part attending
    before the move stays that way afterwards. The categorisation is party
    level, so it is only offered for removal when the reversal leaves the
    party with nobody attending -- otherwise removing it would strip the
    category from members who never moved.
    """
    body = request.get_json(silent=True) or {}
    key = (body.get("record_key") or "").strip()
    parties, _ = current_data()
    by_key = {m["record_key"]: (p, m) for p in parties for m in p["members"]}
    if key not in by_key:
        return jsonify(error="Unknown record."), 404

    party, member = by_key[key]
    if not member["moved"]:
        return jsonify(
            error="%s was not moved manually, so there is nothing to undo."
                  % member["full_name"]), 409

    result = store.revert_record(key, member["full_name"])
    if not result.get("reverted"):
        return jsonify(error="No saved override for that record."), 409

    # Recompute before deciding anything about the category.
    parties, summary = current_data()
    updated = _find_party(parties, party["party_key"])
    still_attending = updated["attending_people"] if updated else 0

    category_removed = False
    if body.get("remove_category") and not still_attending:
        store.save_category(party["party_key"], party["name"], {})
        category_removed = True
        parties, summary = current_data()
        updated = _find_party(parties, party["party_key"])

    return jsonify({
        "ok": True,
        "reverted_to": member["source_status"],
        "party": updated,
        "summary": summary,
        "still_attending": still_attending,
        "category_removed": category_removed,
    })


@app.route("/api/history")
@login_required
def api_history():
    return jsonify({"history": store.load_history(limit=300)})


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=int(os.environ.get("PORT", 5000)), debug=True)
