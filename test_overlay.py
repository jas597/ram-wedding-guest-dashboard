"""End-to-end checks for the category / status-move overlay.

Run with:  python test_overlay.py
Uses a throwaway SQLite file so the real instance database is untouched.
"""

import os
import tempfile

TMP = os.path.join(tempfile.mkdtemp(), "test.db")
os.environ["DATABASE_URL"] = "sqlite:///" + TMP.replace("\\", "/")
os.environ["DASHBOARD_PASSWORD"] = "testpw"
os.environ.pop("RENDER", None)

import app as application  # noqa: E402  (env must be set first)

SABITHA_REGRET = "a85314c233924a77"   # Additional guest 3, the only non-attending row
SABITHA_G1 = "c93c7bc872867405"       # already Attending -- must never move

failures = []


def check(label, got, want):
    ok = got == want
    print("%-58s %-22s %s" % (label, got, "OK" if ok else "FAIL want=%s" % (want,)))
    if not ok:
        failures.append(label)


def client():
    c = application.app.test_client()
    c.post("/login", data={"password": "testpw"})
    return c


def summary(c):
    return c.get("/api/data").get_json()["summary"]


def party(c, key):
    """Find a party by party_key, or by name for readability in tests."""
    for p in c.get("/api/data").get_json()["parties"]:
        if p["party_key"] == key or p["name"] == key:
            return p
    return None


c = client()

print("\n--- baseline (must match the audited figures) ---")
s = summary(c)
check("total people", s["total_people"], 347)
check("attending", s["attending"], 242)
check("regrets", s["regrets"], 9)
check("pending", s["pending"], 96)
check("total parties", s["total_parties"], 152)
check("total rows", s["total_rows"], 179)
check("adults + kids == attending",
      s["attending_adults"] + s["attending_kids"], 242)
check("uncategorised people == attending",
      s["attending_by_category"]["Uncategorised"], 242)

print("\n--- categorise a party (Arasu Sengodan, 4 attending) ---")
arasu = None
for p in c.get("/api/data").get_json()["parties"]:
    if p["name"] == "Arasu Sengodan":
        arasu = p
check("arasu attending people", arasu["attending_people"], 4)
r = c.post("/api/category", json={"party_key": arasu["party_key"],
                                  "category": "Friend", "friend_of": "Ram",
                                  "friend_location": "Close Friend - Outside NC"})
check("category POST ok", r.status_code, 200)
s = summary(c)
check("friends headcount (people not cards)", s["attending_by_category"]["Friend"], 4)
check("Ram's friends headcount", s["attending_by_friend_of"]["Ram"], 4)
check("outside-NC close friends",
      s["attending_by_friend_location"]["Close Friend - Outside NC"], 4)
check("uncategorised drops by 4", s["attending_by_category"]["Uncategorised"], 238)
check("attending total unchanged", s["attending"], 242)

print("\n--- switching Friend -> Family clears the stale friend answers ---")
c.post("/api/category", json={"party_key": arasu["party_key"],
                              "category": "Family", "family_location": "India"})
p = party(c, arasu["party_key"])
check("category", p["category"]["category"], "Family")
check("friend_of cleared", p["category"]["friend_of"], None)
check("friend_location cleared", p["category"]["friend_location"], None)
s = summary(c)
check("india family headcount", s["attending_by_family_location"]["India"], 4)
check("friend bucket empty again", s["attending_by_category"]["Friend"], 0)

print("\n--- reject bad input ---")
check("unknown category rejected",
      c.post("/api/category", json={"party_key": arasu["party_key"],
                                    "category": "Nope"}).status_code, 400)
check("unknown party rejected",
      c.post("/api/category", json={"party_key": "zzz",
                                    "category": "Family"}).status_code, 404)
check("adults+kids mismatch rejected",
      c.post("/api/move", json={"records": [
          {"record_key": SABITHA_REGRET, "total_attending": 3,
           "adults": 1, "kids": 1}]}).status_code, 400)
check("moving an already-attending row rejected",
      c.post("/api/move", json={"records": [
          {"record_key": SABITHA_G1, "total_attending": 1}]}).status_code, 409)

print("\n--- mixed-status party: move ONLY Sabitha's declined Guest 4 ---")
sab = None
for p in c.get("/api/data").get_json()["parties"]:
    if p["name"] == "Sabitha Theetharappan":
        sab = p
check("before: attending in party", sab["people_by_status"].get("Attending"), 3)
check("before: regrets in party", sab["people_by_status"].get("Regrets"), 1)

r = c.post("/api/move", json={"records": [
    {"record_key": SABITHA_REGRET, "total_attending": 2, "adults": 1, "kids": 1}]})
check("move POST ok", r.status_code, 200)

sab = party(c, sab["party_key"])
check("after: party has 4 members still", sab["member_count"], 4)
check("after: attending in party", sab["people_by_status"].get("Attending"), 5)
check("after: regrets gone from party", sab["people_by_status"].get("Regrets"), None)
moved = [m for m in sab["members"] if m["record_key"] == SABITHA_REGRET][0]
untouched = [m for m in sab["members"] if m["record_key"] == SABITHA_G1][0]
check("moved row status", moved["status"], "Attending")
check("moved row remembers source", moved["source_status"], "Regrets")
check("moved row flagged", moved["moved"], True)
check("moved row headcount", moved["people_count"], 2)
check("moved row adults/kids", (moved["adults"], moved["kids"]), (1, 1))
check("untouched row still Attending", untouched["status"], "Attending")
check("untouched row NOT flagged as moved", untouched["moved"], False)
check("untouched row headcount intact", untouched["people_count"], 1)

print("\n--- grouping, contact details and notes survive the move ---")
check("group name kept", moved["group_name"], "Sabitha Theetharappan")
check("guest label kept", moved["guest_label"], "Guest 4")
check("party still grouped", sab["is_group"], True)
check("primary phone kept",
      [m for m in sab["members"] if m["guest_label"] == "Guest 1"][0]["phone"],
      "19193497484")

print("\n--- summary reflects the move immediately ---")
s = summary(c)
check("attending 242 -> 244", s["attending"], 244)
check("regrets 9 -> 8", s["regrets"], 8)
# That row had Invited = 0, so it was ESTIMATED at 1 person while declined.
# Confirming a real headcount of 2 replaces the estimate, so the list total
# legitimately rises by 1. Only Attending is ever a hard number.
check("total 347 -> 348 (estimate replaced by real count)", s["total_people"], 348)
check("parties unchanged", s["total_parties"], 152)
check("rows unchanged", s["total_rows"], 179)
check("moved counter", s["moved_records"], 1)

print("\n--- the same arithmetic in reverse: a generous estimate shrinks ---")
selvaraj = None
for p in c.get("/api/data").get_json()["parties"]:
    if p["name"] == "Selvaraj":
        selvaraj = p
sel_key = selvaraj["members"][0]["record_key"]
check("Selvaraj counted as 5 pending (Invited=5)",
      selvaraj["people_by_status"].get("Sent"), 5)
c.post("/api/move", json={"records": [
    {"record_key": sel_key, "total_attending": 2, "adults": 2, "kids": 0}]})
s = summary(c)
check("attending 244 -> 246", s["attending"], 246)
check("sent bucket 46 -> 41", s["sent"], 41)
check("total 348 -> 345 (5 estimated, 2 confirmed)", s["total_people"], 345)
c.post("/api/revert", json={"record_key": sel_key})
check("revert restores sent to 46", summary(c)["sent"], 46)

print("\n--- audit trail ---")
hist = c.get("/api/history").get_json()["history"]
status_entries = [h for h in hist if h["kind"] == "status"]
cat_entries = [h for h in hist if h["kind"] == "category"]
# Sabitha's move, then Selvaraj's move and its revert.
check("status changes logged", len(status_entries), 3)
check("category changes logged", len(cat_entries), 2)
sab_entry = [h for h in status_entries if h["target_key"] == SABITHA_REGRET][0]
check("logged previous status", sab_entry["from"]["status"], "Regrets")
check("logged new status", sab_entry["to"]["status"], "Attending")
check("logged timestamp present", bool(sab_entry["changed_at"]), True)
check("logged subject", sab_entry["subject"], "Additional guest 3")
check("history is newest first", hist[0]["id"] > hist[-1]["id"], True)
check("changed_by is the shared account", sab_entry["changed_by"],
      "shared-dashboard-user")
check("logged ORIGINAL csv status", sab_entry["original"]["status"], "Regrets")
check("logged ORIGINAL csv people", sab_entry["original"]["people"], 1)

# Re-moving an already-Attending record is refused by design, so exercise the
# original-vs-previous tracking via revert -> move again.
c.post("/api/revert", json={"record_key": SABITHA_REGRET})
c.post("/api/move", json={"records": [
    {"record_key": SABITHA_REGRET, "total_attending": 3, "adults": 3, "kids": 0}]})
again = [h for h in c.get("/api/history").get_json()["history"]
         if h["target_key"] == SABITHA_REGRET][0]
check("2nd move: ORIGINAL still the CSV value", again["original"]["status"], "Regrets")
check("2nd move: PREVIOUS is the reverted value", again["from"]["status"], "Regrets")
check("2nd move: NEW value recorded", again["to"]["people"], 3)
check("2nd move: attending reflects 3", summary(c)["attending"], 245)
# put it back where the later assertions expect it
c.post("/api/revert", json={"record_key": SABITHA_REGRET})
c.post("/api/move", json={"records": [
    {"record_key": SABITHA_REGRET, "total_attending": 2, "adults": 1, "kids": 1}]})
check("restored to 244", summary(c)["attending"], 244)

cat_entry = [h for h in hist if h["kind"] == "category"][-1]
check("category log has changed_by", cat_entry["changed_by"], "shared-dashboard-user")

print("\n--- persistence across a process restart ---")
import importlib  # noqa: E402
import data_loader  # noqa: E402
importlib.reload(data_loader)
importlib.reload(application)
c2 = application.app.test_client()
c2.post("/login", data={"password": "testpw"})
s = summary(c2)
check("attending still 244 after reload", s["attending"], 244)
check("category still applied after reload",
      s["attending_by_family_location"].get("India"), 4)

print("\n--- revert restores the original CSV status ---")
c2.post("/api/revert", json={"record_key": SABITHA_REGRET})
s = summary(c2)
check("attending back to 242", s["attending"], 242)
check("regrets back to 9", s["regrets"], 9)
hist = c2.get("/api/history").get_json()["history"]
check("revert also logged", len([h for h in hist if h["kind"] == "status"]) >= 4, True)

print("\n--- undo move: guards and category handling ---")
# The Guest 4 record was reverted above, so re-move it to set the cases up.
c.post("/api/move", json={"records": [
    {"record_key": SABITHA_REGRET, "total_attending": 2, "adults": 1, "kids": 1}]})
sab = party(c, "Sabitha Theetharappan")
check("set up: party attending", sab["people_by_status"].get("Attending"), 5)

# a record that was never moved has nothing to undo
check("undo refused for a non-moved record",
      c.post("/api/revert", json={"record_key": SABITHA_G1}).status_code, 409)

# undoing while other members still attend must NOT drop the party category
c.post("/api/category", json={"party_key": sab["party_key"], "category": "Family",
                              "family_location": "Local / NC"})
r = c.post("/api/revert", json={"record_key": SABITHA_REGRET,
                                "remove_category": True})
body = r.get_json()
check("undo ok", r.status_code, 200)
check("reports the status it returned to", body["reverted_to"], "Regrets")
check("3 members still attending", body["still_attending"], 3)
check("category KEPT because others still attend", body["category_removed"], False)
check("category still on the party", body["party"]["category"]["category"], "Family")
check("attending back down", summary(c)["attending"], 242)
check("regrets back up", summary(c)["regrets"], 9)

# a solo party, where undoing does empty it
selv = party(c, "Selvaraj")
skey = selv["members"][0]["record_key"]
c.post("/api/move", json={"records": [
    {"record_key": skey, "total_attending": 2, "adults": 2, "kids": 0}]})
c.post("/api/category", json={"party_key": selv["party_key"], "category": "Musician"})
check("solo party categorised",
      party(c, selv["party_key"])["category"]["category"], "Musician")
body = c.post("/api/revert", json={"record_key": skey,
                                   "remove_category": True}).get_json()
check("nobody left attending", body["still_attending"], 0)
check("category removed on request", body["category_removed"], True)
check("category gone", party(c, selv["party_key"])["category"], None)
check("sent bucket restored", summary(c)["sent"], 46)

# same again, choosing to KEEP the category
c.post("/api/move", json={"records": [
    {"record_key": skey, "total_attending": 2, "adults": 2, "kids": 0}]})
c.post("/api/category", json={"party_key": selv["party_key"], "category": "Musician"})
body = c.post("/api/revert", json={"record_key": skey}).get_json()
check("category kept when not requested", body["category_removed"], False)
check("category survives the undo",
      party(c, selv["party_key"])["category"]["category"], "Musician")
c.post("/api/category", json={"party_key": selv["party_key"], "category": ""})

hist = c.get("/api/history").get_json()["history"]
reversals = [h for h in hist if h["kind"] == "status"
             and (h["to"] or {}).get("status") == "(reverted to source CSV)"]
check("reversals recorded in the audit log", len(reversals) >= 3, True)
check("reversal names the record", bool(reversals[0]["subject"]), True)
check("reversal has changed_by", reversals[0]["changed_by"], "shared-dashboard-user")
check("baseline restored", summary(c)["attending"], 242)

print("\n--- auth still enforced on the new endpoints ---")
anon = application.app.test_client()
for path in ("/api/category", "/api/move", "/api/revert"):
    check("anonymous POST %s redirected" % path,
          anon.post(path, json={}).status_code, 302)
check("anonymous GET /api/history redirected",
      anon.get("/api/history").status_code, 302)

print("\n" + ("ALL CHECKS PASSED" if not failures
              else "%d FAILURE(S): %s" % (len(failures), failures)))
raise SystemExit(1 if failures else 0)
