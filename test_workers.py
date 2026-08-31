"""Two independent worker processes must agree after a write.

gunicorn runs the app with `--workers 2`: two separate OS processes, each with
its own copy of the parsed CSV. The risk this guards against is caching the
merged overlay in a module global, which would let worker A serve stale data
after worker B wrote a change.

This starts two real Flask processes against one shared database, writes
through the first, and reads back from the second.

Run with:  python test_workers.py

Note: this uses a shared SQLite file, not PostgreSQL, because neither Docker
nor a Postgres server is available on this machine. It exercises the same code
path -- one SQLAlchemy engine per process, overlay read per request -- so it
proves the cross-process design. It does not exercise Postgres-specific
behaviour such as connection pooling under load.
"""

import http.cookiejar
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(tempfile.mkdtemp(), "workers.db").replace("\\", "/")
PASSWORD = "workerpw"


def free_port():
    """Ask the OS for an unused port.

    Fixed ports made this flaky: a previous run's sockets can still be held
    briefly after the processes exit, and the next run then gets 'connection
    refused' from a worker that never managed to bind.
    """
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


PORT_A, PORT_B = free_port(), free_port()

SABITHA_REGRET = "a85314c233924a77"

failures = []


def check(label, got, want):
    ok = got == want
    print("%-56s %-20s %s" % (label, got, "OK" if ok else "FAIL want=%s" % (want,)))
    if not ok:
        failures.append(label)


def spawn(port):
    env = dict(os.environ)
    env["DATABASE_URL"] = "sqlite:///" + DB
    env["DASHBOARD_PASSWORD"] = PASSWORD
    env["PORT"] = str(port)
    env.pop("RENDER", None)
    return subprocess.Popen(
        [sys.executable, "-c",
         "import app; app.app.run(host='127.0.0.1', port=%d, debug=False)" % port],
        cwd=HERE, env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)


class Worker:
    """Minimal cookie-holding client for one worker process."""

    def __init__(self, port):
        self.base = "http://127.0.0.1:%d" % port
        # a cookie jar survives the 302 that POST /login issues, which a
        # manually copied Set-Cookie header does not
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.jar))

    def _open(self, req):
        return self.opener.open(req, timeout=20)

    def wait_ready(self, timeout=45):
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                urllib.request.urlopen(self.base + "/healthz", timeout=3).read()
                return True
            except Exception:
                time.sleep(0.4)
        return False

    def login(self):
        data = urllib.parse.urlencode({"password": PASSWORD}).encode()
        req = urllib.request.Request(self.base + "/login", data=data, method="POST")
        req.add_header("Content-Type", "application/x-www-form-urlencoded")
        self._open(req)
        if not len(self.jar):
            raise SystemExit("login failed: no session cookie from %s" % self.base)

    def get(self, path):
        return json.loads(self._open(
            urllib.request.Request(self.base + path)).read().decode())

    def post(self, path, payload):
        req = urllib.request.Request(
            self.base + path, data=json.dumps(payload).encode(), method="POST")
        req.add_header("Content-Type", "application/json")
        try:
            return json.loads(self._open(req).read().decode())
        except urllib.error.HTTPError as e:
            return json.loads(e.read().decode())


procs = [spawn(PORT_A), spawn(PORT_B)]
try:
    a, b = Worker(PORT_A), Worker(PORT_B)
    print("\n--- starting two worker processes on one shared database ---")
    check("worker A up", a.wait_ready(), True)
    check("worker B up", b.wait_ready(), True)
    check("processes are distinct", procs[0].pid != procs[1].pid, True)
    a.login()
    b.login()

    print("\n--- both start from the same baseline ---")
    check("A attending", a.get("/api/data")["summary"]["attending"], 242)
    check("B attending", b.get("/api/data")["summary"]["attending"], 242)

    print("\n--- write through A, read from B ---")
    parties = a.get("/api/data")["parties"]
    arasu = [p for p in parties if p["name"] == "Arasu Sengodan"][0]
    a.post("/api/category", {"party_key": arasu["party_key"], "category": "Friend",
                             "friend_of": "Ram",
                             "friend_location": "Close Friend - Outside NC"})
    check("B sees the category A wrote",
          b.get("/api/data")["summary"]["attending_by_category"]["Friend"], 4)
    check("B sees Ram's friends count",
          b.get("/api/data")["summary"]["attending_by_friend_of"]["Ram"], 4)

    a.post("/api/move", {"records": [{"record_key": SABITHA_REGRET,
                                      "total_attending": 2,
                                      "adults": 1, "kids": 1}]})
    check("B sees attending 244", b.get("/api/data")["summary"]["attending"], 244)
    check("B sees regrets 8", b.get("/api/data")["summary"]["regrets"], 8)

    print("\n--- write through B, read from A (other direction) ---")
    b.post("/api/category", {"party_key": arasu["party_key"], "category": "Musician"})
    sa = a.get("/api/data")["summary"]
    check("A sees musicians 4", sa["attending_by_category"]["Musician"], 4)
    check("A sees friends back to 0", sa["attending_by_category"]["Friend"], 0)

    print("\n--- audit visible from both ---")
    check("A history count", len(a.get("/api/history")["history"]), 3)
    check("B history count", len(b.get("/api/history")["history"]), 3)
    check("B sees changed_by",
          b.get("/api/history")["history"][0]["changed_by"], "shared-dashboard-user")

    print("\n--- revert through B, A agrees ---")
    b.post("/api/revert", {"record_key": SABITHA_REGRET})
    check("A back to 242", a.get("/api/data")["summary"]["attending"], 242)
    check("B back to 242", b.get("/api/data")["summary"]["attending"], 242)
finally:
    for i, p in enumerate(procs):
        if p.poll() is not None:
            print("worker %d exited early with code %s; output:" % (i, p.returncode))
            try:
                print(p.stdout.read()[-2000:])
            except Exception:
                pass
    for p in procs:
        p.terminate()
    for p in procs:
        try:
            p.wait(timeout=10)
        except Exception:
            p.kill()

print("\n" + ("ALL WORKER CHECKS PASSED" if not failures
              else "%d FAILURE(S): %s" % (len(failures), failures)))
raise SystemExit(1 if failures else 0)
