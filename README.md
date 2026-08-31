# Ram's Wedding — Guest List Dashboard

An internal Flask dashboard over the wedding RSVP export. It shows the headline
headcounts, lets you filter and search the guest list by RSVP status, keeps
grouped parties together under their main client, and flags possible duplicate
invitations for review.

> **This is not a public site.** The data contains guests' phone numbers, email
> addresses and personal messages. Every page except `/healthz` sits behind a
> shared password, and the repository is private for the same reason.

---

## Headline figures

| RSVP status | Parties | Row entries | People |
|---|---:|---:|---:|
| Attending | 105 | 131 | **242** (215 adults + 27 kids) |
| Regrets | 6 | 7 | **9** |
| Page viewed | 19 | 19 | 41 |
| Text opened | 4 | 4 | 7 |
| Sent | 17 | 17 | 46 |
| Bounced | 1 | 1 | 2 |
| **Pending subtotal** | 41 | 41 | **96** |
| **Total** | **152** | **179** | **347** |

## How the counting works

These rules were verified against the source file before any of this was built,
and `data_loader.py` implements exactly them.

1. A **party** is one `Group Name` where the source provides one, otherwise a
   single standalone client row. 152 parties across 179 rows.
2. `Guest 2` / `Guest 3` / `Guest 4` rows are additional people inside their
   `Guest 1` primary's party — never separate clients. All 27 of them carry a
   `Group Name` matching a primary row.
3. **No double-counting.** Inside all 20 grouped parties, every row *including
   the Guest 1 primary* carries `Total Attending = 1`. The primary row does not
   already contain its extra rows, so those rows are added, not discarded.
4. For ungrouped clients the single row holds the whole party in
   `Total Attending` (e.g. Arasu Sengodan = 4), and there are no extra rows.
5. Both patterns give the same rule: **attending people = the plain sum of the
   `Total Attending` column = 242**, cross-checked against
   `Adults + Kids = 215 + 27 = 242` on all 179 rows.
6. Non-attending rows always carry `Total Attending = 0`, so their headcount
   falls back to `Invited`, and to 1 person where `Invited` is 0 too.
7. One party spans two statuses — Sabitha Theetharappan, 3 attending and 1
   regret. It appears under both, counted once in each, never twice overall.

Possible duplicate invitations live in `duplicates.py`. **They are warnings
only.** No record is removed and no count is reduced; the dashboard reports the
verified 242. The Review tab shows what the figures would become if each
flagged pair were confirmed (attending 237, pending 72).

---

## Running it locally

Requires Python 3.11 or newer.

```bash
git clone <your-repo-url>
cd ram-wedding-dashboard
python -m venv .venv
```

Activate the virtual environment — on Windows PowerShell:

```bash
.venv\Scripts\Activate.ps1
```

or on macOS and Linux:

```bash
source .venv/bin/activate
```

Then install and run:

```bash
pip install -r requirements.txt
```

```bash
python app.py
```

Open <http://127.0.0.1:5000> and sign in. No password is baked into the source,
so if `DASHBOARD_PASSWORD` is unset the app prints a random one for that run:

```
  DASHBOARD_PASSWORD is not set.
  This run's local password is: kJ2n-Qx7Vd
```

To choose your own on Windows PowerShell:

```bash
$env:DASHBOARD_PASSWORD = "something-better"; python app.py
```

In production the app **refuses to start** without `DASHBOARD_PASSWORD`, rather
than falling back to anything guessable.

### Environment variables

| Variable | Required | What it does |
|---|---|---|
| `DASHBOARD_PASSWORD` | **yes in production** | The shared sign-in password. Never committed. Unset locally, a random one is generated and printed per run; unset on Render, the app refuses to boot. |
| `SECRET_KEY` | in production | Signs the session cookie. A random one is generated per process if unset, which logs everyone out on restart. |
| `DATABASE_URL` | recommended in production | Where categories and status moves are stored. Unset, it falls back to a SQLite file in `instance/`, **which Render's free plan wipes on every restart and redeploy.** |
| `PORT` | no | Port to bind. Defaults to 5000. |
| `RENDER` | no | Set automatically by Render; makes the session cookie `Secure` and enforces the password check above. |

---

## Categories and RSVP moves

### Where the data lives

`data/guestlist.csv` is **immutable**. Nothing in the app ever writes to it, so
the original export stays intact and no two users can race to rewrite a
Git-tracked file. Everything a user changes goes to a database instead and is
applied as an overlay when the guest list is read:

```
data/guestlist.csv  ->  parsed once at import  ->  base parties (never mutated)
                                                        +
database overlay  ->  status overrides + categories  ->  what you see
```

Each CSV row gets a stable `record_key`: `sha1(group|name|contact|guest_label)`,
truncated to 16 characters. It is **content-derived, not positional**, so
re-exporting the CSV in a different order does not orphan saved data. Verified
unique across all 179 rows.

The overlay is read **per request**, not cached in a module global, because
`--workers 2` means two processes that would otherwise disagree after a write.

### Tables

| Table | Holds |
|---|---|
| `guest_status_override` | One row per CSV row whose status was changed, with the confirmed headcount |
| `party_category` | One row per categorised party |
| `change_log` | Append-only audit: what changed, from what, to what, when |

`change_log` is never updated or deleted, so the original RSVP status stays
recoverable however many times a record is moved. `POST /api/revert` drops an
override and returns a row to whatever the CSV says.

### Choosing a database

`DATABASE_URL` unset gives you SQLite in `instance/` — fine locally, **wrong on
Render's free plan**, whose filesystem is ephemeral. For a deploy that must keep
its data, point it at PostgreSQL. Render's own free Postgres expires after 30
days; a paid instance or an external free tier (Neon, Supabase) does not. The
code is identical either way.

### Headcounts when moving a record

Attending is the only status with a real headcount — a non-attending row carries
`Total Attending = 0`, so its people count is an *estimate* from `Invited`
(falling back to 1). Promoting a record therefore asks you to confirm the actual
number, pre-filled with that estimate.

A consequence worth knowing: **the list total can move.** Confirming 2 people for
a row that was estimated at 5 drops the total by 3. That is the estimate being
replaced by a real figure, not a counting error.

### Mixed-status parties

A move targets **specific record keys, never a whole party.** Sabitha
Theetharappan has three attending members and one regret; on the Regrets tab her
card's button carries only the Guest 4 key, and the API rejects any key that is
already Attending. Her attending members cannot be touched by that action.

### Two ways to view Attending

An `Overview | Full List` toggle sits above the Attending list; the choice is
remembered in `localStorage`.

**Overview** is a three-level drill-down, built so the distribution of 242
people reads in about five seconds without scrolling:

* **Level 1** -- five colour-coded category cards (Family gold, Friends blue,
  Musicians purple, Other teal, Uncategorised grey) showing people and parties,
  with the important sub-group counts printed inside each card. **No guest cards
  are rendered at this level.**
* **Level 2** -- clicking a category reveals its sub-groups as tiles with their
  own counts: whose friend (Jawa / Shanthi / Ram) and location for Friends,
  location for Family.
* **Level 3** -- the guest cards appear only once a category is chosen, and
  narrow further as sub-groups are picked.

A breadcrumb (`Attending > Friends > Ram > Outside NC`) walks back up a level at
a time, and **Show all attending** jumps to the full list. Saving a category
re-counts every level and relocates the party immediately, with no reload.

**Full List** is the detailed view with contacts, messages and status filters.

### One colour per category

Each category owns exactly one colour, declared once in `style.css` as three CSS
variables on an `.is-*` class:

| Category | Colour |
|---|---|
| Family | warm gold |
| Friends | blue |
| Musicians | purple |
| Other | teal |
| Uncategorised | neutral grey |

Every element that represents a category -- the level 1 card, its sub-group
tiles, a guest card's left edge and its saved label -- inherits those variables,
so Family is the same gold everywhere and no individual guest ever gets a shade
of their own.

### Collapsed category cards

A categorised party shows its categorisation as one label in the category
colour, and nothing else:

```
Prof. Balu Gokaraju                    4 people
Family · India                            [Edit]
```

The dropdowns only exist after pressing **Edit**, and saving collapses the card
back to the label. Uncategorised parties show an italic *Uncategorised* label and
a **Categorise** button instead. This keeps a screen of guests scannable by
colour and label rather than a wall of select boxes.

### Undoing a manual move

An Attending record that was moved there manually carries a
**Change status / undo move** action. It opens a confirmation showing
*Originally*, *Current status*, *Confirmed attending* and *Reverting to* -- there
is deliberately no one-click path.

Undo is record level, like the move it reverses. Because categories are stored
per party, the "also remove the category" question only appears when the
reversal leaves nobody in that party attending; otherwise removing it would
strip the category from members who never moved. The reversal is written to
`change_log` like any other change.

### Endpoints

| Route | Method | Purpose |
|---|---|---|
| `/api/category` | POST | Save one party's categorisation |
| `/api/move` | POST | Move specific records to Attending |
| `/api/revert` | POST | Drop an override, restoring the CSV status |
| `/api/history` | GET | Audit trail, newest first |

### Confirmed vs estimated

Only **Attending** is a confirmed headcount -- those guests told us how many are
coming. Every other status has `Total Attending = 0` in the source, so its
people figure is **estimated from `Invited`** (falling back to 1 where `Invited`
is 0 too). The UI says so in three places: a legend under the summary cards, a
per-card `Confirmed headcount` / `Estimated from Invited` flag, and an `(est.)`
marker on individual people counts.

The overall 347 therefore mixes 242 confirmed with 105 estimated and should be
read as an upper bound, not a confirmed figure. The Total Guests card spells the
split out rather than presenting one number as if it were solid.

## Tests

```bash
python test_overlay.py
```

Covers the audited baseline (347 / 242 / 9 / 96 / 152 / 179), category counting
by headcount, the mixed-status guard, headcount arithmetic in both directions,
the audit trail (original vs previous vs new, `changed_by`, timestamp),
persistence across a restart, revert, and auth on every new endpoint. Uses a
throwaway SQLite file; your real database is untouched.

```bash
python test_workers.py
```

Starts **two real Flask processes against one shared database** and writes
through each while reading from the other, which is what `gunicorn --workers 2`
does in production. This is the check that would fail if the merged overlay were
ever cached in a module global.

It uses a shared SQLite file rather than PostgreSQL, because neither Docker nor
a Postgres server is available on the development machine. Same code path -- one
engine per process, overlay read per request -- so it proves the cross-process
design, but it does not exercise Postgres-specific behaviour under load.

---

## Deploying to Render

The repo ships a `render.yaml` blueprint, so Render can configure the service
itself.

1. Push this repository to GitHub (private).
2. In the Render dashboard choose **New → Blueprint** and pick this repository.
   Render reads `render.yaml` and proposes a free web service called
   `ram-wedding-dashboard`.
3. Render will prompt for the one value it cannot generate:
   **`DASHBOARD_PASSWORD`**. Set it to whatever the team should use.
   `SECRET_KEY` is generated for you.
4. Click **Apply**. First build takes a couple of minutes.
5. Confirm `https://<your-service>.onrender.com/healthz` returns
   `{"status":"ok","parties":152,"people":347}`, then open the root URL and
   sign in.

On Render's free plan the service sleeps after inactivity, so the first request
after an idle period takes roughly 30 seconds to wake.

### Changing the password later

Service → **Environment** → edit `DASHBOARD_PASSWORD` → **Save**, which
redeploys. Existing sessions stay signed in until their cookie is cleared; to
force everyone out, change `SECRET_KEY` as well.

---

## Project layout

```
app.py               Flask routes, the password gate, the JSON API
data_loader.py       CSV parsing, party roll-up, the counting rules
duplicates.py        Flagged duplicate pairs and data-quality notes
data/guestlist.csv   Source export, unmodified
templates/           login.html, index.html
static/              style.css, app.js
render.yaml          Render blueprint
requirements.txt     Flask, gunicorn
```

### Routes

| Route | Auth | Purpose |
|---|---|---|
| `/` | required | The dashboard |
| `/login`, `/logout` | public | Sign in and out |
| `/api/data` | required | Summary, parties and duplicates as JSON |
| `/healthz` | public | Health check. Returns counts only, no guest data. |

## Updating the guest list

Replace `data/guestlist.csv` with a fresh export keeping the same column
headers, then restart the app — the CSV is parsed once at import. Re-check
`duplicates.py`, since the flagged pairs are tied to specific records.

The frontend inserts every guest value with `textContent`, never `innerHTML`,
so names and messages from the CSV cannot be interpreted as markup.
