"""Room allocation rules.

Run with:  python test_rooms.py
Uses a throwaway SQLite file, so the real instance database is untouched.

The invariants under test are the ones that stop a guest being double-booked
or a room being quietly overfilled, and the guarantee that allocating a room
never touches a guest's category or RSVP status.
"""

import os
import tempfile

TMP = os.path.join(tempfile.mkdtemp(), "rooms.db")
os.environ["DATABASE_URL"] = "sqlite:///" + TMP.replace("\\", "/")
os.environ["DASHBOARD_PASSWORD"] = "testpw"
os.environ.pop("RENDER", None)

import app as application  # noqa: E402

SABITHA_REGRET = "a85314c233924a77"

failures = []


def check(label, got, want):
    ok = got == want
    print("%-60s %-24s %s" % (label, got, "OK" if ok else "FAIL want=%s" % (want,)))
    if not ok:
        failures.append(label)


c = application.app.test_client()
c.post("/login", data={"password": "testpw"})


def rooms():
    return c.get("/api/data").get_json()["rooms"]


def party_named(name):
    for p in c.get("/api/data").get_json()["parties"]:
        if p["name"] == name:
            return p
    return None


def alloc_row(name):
    for p in rooms()["parties"]:
        if p["name"] == name:
            return p
    return None


def room_named(name):
    for r in rooms()["rooms"]:
        if r["name"] == name:
            return r
    return None


print("\n--- baseline: no rooms yet ---")
s = rooms()["summary"]
check("attending people", s["attending_people"], 242)
check("required people (nothing excused yet)", s["required_people"], 242)
check("allocated", s["allocated_people"], 0)
check("unallocated", s["unallocated_people"], 242)
check("rooms total", s["rooms_total"], 0)
check("available capacity", s["available_capacity"], 0)
check("parties listed", len(rooms()["parties"]), 105)
check("only attending parties listed",
      all(p["people"] > 0 for p in rooms()["parties"]), True)

print("\n--- manage rooms ---")
r1 = c.post("/api/rooms", json={"name": "Room 101", "capacity": 4,
                                "property": "Marriott", "room_type": "Double",
                                "notes": "ground floor"})
check("create room", r1.status_code, 200)
c.post("/api/rooms", json={"name": "Room 102", "capacity": 2})
c.post("/api/rooms", json={"name": "Suite 201", "capacity": 5})
check("three rooms", rooms()["summary"]["rooms_total"], 3)
check("total capacity", rooms()["summary"]["total_capacity"], 11)
check("available capacity", rooms()["summary"]["available_capacity"], 11)
check("no room numbers hardcoded anywhere",
      sorted(r["name"] for r in rooms()["rooms"]),
      ["Room 101", "Room 102", "Suite 201"])
check("duplicate room name rejected",
      c.post("/api/rooms", json={"name": "Room 101", "capacity": 2}).status_code, 409)
check("capacity must be a number",
      c.post("/api/rooms", json={"name": "Bad", "capacity": "x"}).status_code, 400)
check("capacity must be positive",
      c.post("/api/rooms", json={"name": "Bad", "capacity": 0}).status_code, 400)
check("room needs a name",
      c.post("/api/rooms", json={"name": "  ", "capacity": 2}).status_code, 400)

R101 = room_named("Room 101")["id"]
R102 = room_named("Room 102")["id"]
S201 = room_named("Suite 201")["id"]

print("\n--- allocate by party ---")
arasu = party_named("Arasu Sengodan")          # 4 people, single CSV row
check("Arasu attending", arasu["attending_people"], 4)
r = c.post("/api/allocate", json={"party_key": arasu["party_key"],
                                  "room_id": R101, "people": 4})
check("allocate 4 into Room 101", r.status_code, 200)
room = room_named("Room 101")
check("room occupied", room["occupied"], 4)
check("room free", room["free"], 0)
check("room flagged full", room["full"], True)
check("occupant named", room["occupants"][0]["name"], "Arasu Sengodan")
check("occupant headcount", room["occupants"][0]["people"], 4)
s = rooms()["summary"]
check("allocated people", s["allocated_people"], 4)
check("unallocated people", s["unallocated_people"], 238)
check("rooms used", s["rooms_used"], 1)
check("available capacity", s["available_capacity"], 7)
check("party state", alloc_row("Arasu Sengodan")["state"], "allocated")

print("\n--- capacity is protected, override is explicit ---")
sampath = party_named("Sampath Sengodu")       # 4 people
r = c.post("/api/allocate", json={"party_key": sampath["party_key"],
                                  "room_id": R102, "people": 4})
check("over capacity refused", r.status_code, 409)
check("refusal flags an override is possible",
      r.get_json().get("needs_override"), True)
check("nothing was written", room_named("Room 102")["occupied"], 0)
r = c.post("/api/allocate", json={"party_key": sampath["party_key"],
                                  "room_id": R102, "people": 4,
                                  "allow_overflow": True})
check("override accepted when asked for", r.status_code, 200)
check("room marked over capacity", room_named("Room 102")["over"], True)
check("occupied beyond capacity", room_named("Room 102")["occupied"], 4)
check("over-full room adds no available capacity",
      rooms()["summary"]["available_capacity"], 5)
c.post("/api/allocate", json={"party_key": sampath["party_key"],
                              "room_id": R102, "people": 0})
check("removing the allocation clears the overflow",
      room_named("Room 102")["over"], False)

print("\n--- a party cannot be placed twice ---")
suresh = party_named("Suresh Srinivasan")      # 4 people
c.post("/api/allocate", json={"party_key": suresh["party_key"],
                              "room_id": R102, "people": 2})
r = c.post("/api/allocate", json={"party_key": suresh["party_key"],
                                  "room_id": S201, "people": 3})
check("cannot exceed the party's own headcount", r.status_code, 409)
check("message explains the remaining allowance",
      "at most 2 can go here" in r.get_json()["error"], True)
r = c.post("/api/allocate", json={"party_key": suresh["party_key"],
                                  "room_id": S201, "people": 2})
check("splitting the party across rooms is allowed", r.status_code, 200)
row = alloc_row("Suresh Srinivasan")
check("split recorded in two rooms", len(row["placements"]), 2)
check("split totals the whole party", row["allocated"], 4)
check("nothing left over", row["remaining"], 0)
check("state is allocated", row["state"], "allocated")
check("re-placing in the same room updates rather than duplicates",
      c.post("/api/allocate", json={"party_key": suresh["party_key"],
                                    "room_id": R102, "people": 1}).status_code, 200)
check("still only two placements", len(alloc_row("Suresh Srinivasan")["placements"]), 2)
check("party now partly allocated", alloc_row("Suresh Srinivasan")["state"],
      "partly_allocated")
check("remaining reported", alloc_row("Suresh Srinivasan")["remaining"], 1)

print("\n--- non-attending guests can never be allocated ---")
regret = None
for p in c.get("/api/data").get_json()["parties"]:
    if p["attending_people"] == 0:
        regret = p
        break
check("found a non-attending party", regret is not None, True)
r = c.post("/api/allocate", json={"party_key": regret["party_key"],
                                  "room_id": R101, "people": 1})
check("allocation refused", r.status_code, 409)
check("reason given", "not attending" in r.get_json()["error"], True)
check("not offered in the allocation list",
      any(p["party_key"] == regret["party_key"] for p in rooms()["parties"]), False)
r = c.post("/api/room-status", json={"party_key": regret["party_key"],
                                     "status": "no_room_required"})
check("cannot even mark it as needing no room", r.status_code, 409)

print("\n--- No Room Required ---")
local = party_named("Vaishu")                   # 1 person
r = c.post("/api/room-status", json={"party_key": local["party_key"],
                                     "status": "no_room_required"})
check("flag accepted", r.status_code, 200)
check("state", alloc_row("Vaishu")["state"], "no_room_required")
s = rooms()["summary"]
check("required drops by that party", s["required_people"], 241)
check("attending is unchanged", s["attending_people"], 242)
check("counted in the summary", s["no_room_required_parties"], 1)
r = c.post("/api/room-status", json={"party_key": arasu["party_key"],
                                     "status": "no_room_required"})
check("refused while the party holds rooms", r.status_code, 409)
check("reason mentions the allocation",
      "Remove those allocations first" in r.get_json()["error"], True)
c.post("/api/room-status", json={"party_key": local["party_key"], "status": ""})
check("flag can be cleared", alloc_row("Vaishu")["state"], "not_decided")
check("required back up", rooms()["summary"]["required_people"], 242)

print("\n--- allocating never touches category or RSVP ---")
c.post("/api/category", json={"party_key": arasu["party_key"], "category": "Friend",
                              "friend_of": "Ram",
                              "friend_location": "Close Friend - Outside NC"})
before = c.get("/api/data").get_json()["summary"]
c.post("/api/allocate", json={"party_key": arasu["party_key"],
                              "room_id": R101, "people": 3})
after = c.get("/api/data").get_json()["summary"]
check("attending unchanged", after["attending"], before["attending"])
check("regrets unchanged", after["regrets"], before["regrets"])
check("category survives", party_named("Arasu Sengodan")["category"]["category"],
      "Friend")
check("sub-category survives",
      party_named("Arasu Sengodan")["category"]["friend_of"], "Ram")
check("category counts unchanged",
      after["attending_by_category"], before["attending_by_category"])
check("allocation carries the category for colouring",
      room_named("Room 101")["occupants"][0]["category"]["category"], "Friend")

print("\n--- deleting a room frees its people ---")
before_alloc = rooms()["summary"]["allocated_people"]
r = c.post("/api/rooms/delete", json={"id": R102})
check("delete ok", r.status_code, 200)
check("its allocations were released", r.get_json()["freed_allocations"], 1)
check("allocated total drops", rooms()["summary"]["allocated_people"],
      before_alloc - 1)
check("rooms total", rooms()["summary"]["rooms_total"], 2)
check("delete unknown room", c.post("/api/rooms/delete",
                                    json={"id": 99999}).status_code, 404)

print("\n--- shrinking a room below its occupancy is guarded ---")
r = c.post("/api/rooms", json={"id": R101, "name": "Room 101", "capacity": 1})
check("refused", r.status_code, 409)
check("override offered", r.get_json().get("needs_override"), True)
r = c.post("/api/rooms", json={"id": R101, "name": "Room 101", "capacity": 1,
                               "allow_overflow": True})
check("accepted with override", r.status_code, 200)
check("room now over capacity", room_named("Room 101")["over"], True)
c.post("/api/rooms", json={"id": R101, "name": "Room 101", "capacity": 4})

print("\n--- audit trail ---")
hist = c.get("/api/history").get_json()["history"]
kinds = {h["kind"] for h in hist}
check("room changes logged", "room" in kinds, True)
check("allocation changes logged", "allocation" in kinds, True)
alloc = [h for h in hist if h["kind"] == "allocation"]
check("allocation entry names the party", bool(alloc[0]["subject"]), True)
check("allocation entry has before and after",
      alloc[0]["from"] is not None and alloc[0]["to"] is not None, True)
check("allocation entry has changed_by", alloc[0]["changed_by"],
      "shared-dashboard-user")
check("allocation entry timestamped", bool(alloc[0]["changed_at"]), True)

print("\n--- survives a restart ---")
import importlib  # noqa: E402
importlib.reload(application)
c2 = application.app.test_client()
c2.post("/login", data={"password": "testpw"})
after_reload = c2.get("/api/data").get_json()["rooms"]
check("rooms still there", after_reload["summary"]["rooms_total"], 2)
check("allocations still there", after_reload["summary"]["allocated_people"] > 0, True)
check("occupancy recomputed correctly",
      sum(r["occupied"] for r in after_reload["rooms"]),
      after_reload["summary"]["allocated_people"])

print("\n--- auth ---")
anon = application.app.test_client()
for path in ("/api/rooms", "/api/rooms/delete", "/api/allocate", "/api/room-status"):
    check("anonymous POST %s redirected" % path,
          anon.post(path, json={}).status_code, 302)

print("\n" + ("ALL ROOM CHECKS PASSED" if not failures
              else "%d FAILURE(S): %s" % (len(failures), failures)))
raise SystemExit(1 if failures else 0)
