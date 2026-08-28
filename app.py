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
from data_loader import build_summary, load_parties

app = Flask(__name__)

# In production Render supplies both of these. Locally we fall back to a
# throwaway key and the documented default password.
app.secret_key = os.environ.get("SECRET_KEY") or secrets.token_hex(32)
DASHBOARD_PASSWORD = os.environ.get("DASHBOARD_PASSWORD", "wedding2026")

app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    # Render terminates TLS, so cookies can be marked secure there
    SESSION_COOKIE_SECURE=bool(os.environ.get("RENDER")),
)

# The CSV never changes at runtime, so parse it once at import.
PARTIES = load_parties()
SUMMARY = build_summary(PARTIES)
REVIEW = duplicates.review_totals()


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("authenticated"):
            return redirect(url_for("login", next=request.path))
        return view(*args, **kwargs)

    return wrapped


@app.route("/healthz")
def healthz():
    """Unauthenticated so Render's health check can reach it."""
    return jsonify(status="ok", parties=len(PARTIES), people=SUMMARY["total_people"])


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
    return render_template("index.html", summary=SUMMARY)


@app.route("/api/data")
@login_required
def api_data():
    return jsonify({
        "summary": SUMMARY,
        "parties": PARTIES,
        "duplicates": duplicates.DUPLICATES,
        "review": REVIEW,
        "data_quality_notes": [
            {"subject": subject, "note": note}
            for subject, note in duplicates.DATA_QUALITY_NOTES
        ],
    })


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=int(os.environ.get("PORT", 5000)), debug=True)
