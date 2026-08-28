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

Open <http://127.0.0.1:5000> and sign in. Without `DASHBOARD_PASSWORD` set, the
local default is `wedding2026`. To choose your own on Windows PowerShell:

```bash
$env:DASHBOARD_PASSWORD = "something-better"; python app.py
```

### Environment variables

| Variable | Required | What it does |
|---|---|---|
| `DASHBOARD_PASSWORD` | in production | The shared sign-in password. Defaults to `wedding2026` locally only. |
| `SECRET_KEY` | in production | Signs the session cookie. A random one is generated per process if unset, which logs everyone out on restart. |
| `PORT` | no | Port to bind. Defaults to 5000. |
| `RENDER` | no | Set automatically by Render; makes the session cookie `Secure`. |

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
