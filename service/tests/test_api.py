"""API tests without docker: python3 -m unittest discover -s tests (from service/, with the
dependencies of requirements.txt on PYTHONPATH). Docker-facing functions are replaced by fakes;
scenario loading and validation are real."""
import os
import sys
import shutil
import tempfile
import time
import unittest
from unittest import mock

API = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
sys.path.insert(0, API)
os.environ["VNAP_API_NO_APP"] = "1"

from fastapi.testclient import TestClient  # noqa: E402

import vnapapi.runs as runs_module  # noqa: E402
from vnapapi.app import create_app  # noqa: E402
from vnapapi.config import load_config  # noqa: E402
from vnapapi import backup, totp  # noqa: E402
from vnapsim.scenario import load_scenario  # noqa: E402

PASSWORD = "correct-horse-battery"


class ApiTest(unittest.TestCase):
    def setUp(self):
        cfg = load_config()
        self.tmp = tempfile.mkdtemp()
        cfg["server"]["data_dir"] = os.path.join(self.tmp, "data")
        cfg["auth"]["totp_key_file"] = os.path.join(self.tmp, "secrets", "totp.key")
        cfg["backup"]["dir"] = os.path.join(self.tmp, "backups")
        cfg["server"]["cookie_secure"] = False
        cfg["auth"]["login_attempts_per_minute"] = 100
        cfg["limits"]["user"]["concurrent_runs"] = 1
        self.app = create_app(cfg, start_workers=False)
        self.accounts, self.db, self.runs = self.app.state.accounts, self.app.state.db, self.app.state.runs
        for name, role in (("root", "admin"), ("alice", "user"), ("bob", "user"), ("vera", "viewer")):
            self.accounts.create_user(name, PASSWORD, role)
        self.up_calls = []

    def tearDown(self):
        self.app.state.db.conn.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def client(self, user):
        c = TestClient(self.app)
        r = c.post("/api/auth/login", json={"username": user, "password": PASSWORD})
        self.assertEqual(r.status_code, 200, r.text)
        c.headers["X-CSRF-Token"] = r.json()["csrf_token"]
        return c

    # docker stand-ins: real scenario loading, fake containers
    def fake_allocate(self, path, sets, role, policy=None, service_sets=()):
        self.up_calls.append({"service_sets": list(service_sets),
                              "env": {k: v for k, v in os.environ.items() if k.startswith("VNAP_RUN_")}})
        return load_scenario(path, sets, 7, role, policy, service_sets), None

    def fake_up(self, sc, wait=30.0, claimed=None, limits=None):
        self.up_calls[-1]["limits"] = limits
        return True, {"run_id": f"{sc['name']}-x", "pending": {}, "status": {"control": {"mobility": {"seed": 5}}}}

    def start(self, run_id):
        with mock.patch.object(runs_module, "allocate_instance", self.fake_allocate), \
                mock.patch.object(runs_module, "scenario_up", self.fake_up):
            self.runs._start(run_id)

    # ------------------------------------------------------------ authentication
    def test_login_logout_and_session(self):
        c = self.client("alice")
        self.assertEqual(c.get("/api/auth/me").json()["username"], "alice")
        self.assertEqual(c.post("/api/auth/logout").status_code, 200)
        self.assertEqual(c.get("/api/auth/me").status_code, 401)

    def test_wrong_password_and_unknown_user_look_the_same(self):
        c = TestClient(self.app)
        a = c.post("/api/auth/login", json={"username": "alice", "password": "nope-nope-nope"})
        b = c.post("/api/auth/login", json={"username": "nobody", "password": "nope-nope-nope"})
        self.assertEqual((a.status_code, a.json()), (b.status_code, b.json()))

    def test_lockout_after_repeated_failures(self):
        c = TestClient(self.app)
        for _ in range(5):
            c.post("/api/auth/login", json={"username": "bob", "password": "wrong-password!"})
        r = c.post("/api/auth/login", json={"username": "bob", "password": PASSWORD})
        self.assertEqual(r.status_code, 401)
        self.assertIn("locked", r.json()["detail"])
        self.client("root").patch("/api/users/bob", json={"unlock": True})
        self.assertEqual(TestClient(self.app).post("/api/auth/login", json={"username": "bob", "password": PASSWORD}).status_code, 200)

    def test_login_rate_limit(self):
        self.accounts.limiter.per_minute = 3
        c = TestClient(self.app)
        codes = [c.post("/api/auth/login", json={"username": "alice", "password": "x" * 12}).status_code for _ in range(4)]
        self.assertEqual(codes[-1], 429)

    def test_csrf_required_for_cookie_sessions(self):
        c = self.client("alice")
        del c.headers["X-CSRF-Token"]
        self.assertEqual(c.post("/api/tokens", json={"name": "t"}).status_code, 403)

    def test_api_token_and_revocation(self):
        c = self.client("alice")
        made = c.post("/api/tokens", json={"name": "ci"}).json()
        bearer = TestClient(self.app)
        bearer.headers["Authorization"] = f"Bearer {made['token']}"
        self.assertEqual(bearer.get("/api/auth/me").json()["username"], "alice")
        self.assertEqual(bearer.post("/api/scenarios/validate", json={"template": "pki-refill"}).status_code, 200)  # no CSRF
        c.delete(f"/api/tokens/{made['id']}")
        self.assertEqual(bearer.get("/api/auth/me").status_code, 401)
        stored = self.db.one("SELECT token_hash FROM tokens WHERE id = ?", (made["id"],))["token_hash"]
        self.assertNotIn(made["token"], stored)   # only the digest is stored

    def test_password_policy_and_change(self):
        root = self.client("root")
        self.assertEqual(root.post("/api/users", json={"username": "x", "password": "short", "role": "user"}).status_code, 400)
        c = self.client("alice")
        r = c.post("/api/auth/password", json={"current_password": PASSWORD, "new_password": "a-new-long-password"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(c.get("/api/auth/me").status_code, 401)   # sessions ended

    # ------------------------------------------------------------ roles
    def test_only_admins_manage_users_and_read_the_audit_log(self):
        self.assertEqual(self.client("alice").get("/api/users").status_code, 403)
        self.assertEqual(self.client("alice").get("/api/audit").status_code, 403)
        root = self.client("root")
        self.assertEqual(root.post("/api/users", json={"username": "carol", "password": PASSWORD, "role": "viewer"}).status_code, 201)
        self.assertEqual(root.patch("/api/users/carol", json={"disabled": True}).json()["disabled"], 1)
        actions = [a["action"] for a in root.get("/api/audit").json()]
        self.assertIn("user.created", actions)
        self.assertIn("user.changed", actions)

    def test_viewers_cannot_start_runs(self):
        r = self.client("vera").post("/api/runs", json={"template": "pki-refill"})
        self.assertEqual(r.status_code, 403)

    # ------------------------------------------------------------ scenarios and policy
    def test_users_get_templates_admins_the_catalogue(self):
        self.assertNotIn("scenarios", self.client("alice").get("/api/scenarios").json())
        self.assertIn("c-its-pki", self.client("root").get("/api/scenarios").json()["scenarios"])
        self.assertEqual(self.client("alice").post("/api/runs", json={"scenario": "c-its-pki"}).status_code, 403)

    def test_user_policy_field_errors(self):
        text = '[[stations]]\nname = "a"\nip = "192.168.98.10"\nenv = { LD_PRELOAD = "/x" }\n'
        r = self.client("alice").post("/api/scenarios/validate", json={"scenario_text": text})
        paths = {e["path"] for e in r.json()["errors"]}
        self.assertEqual(paths, {"stations[a].ip", "stations[a].env"})
        r = self.client("alice").post("/api/runs", json={"scenario_text": text})
        self.assertEqual(r.status_code, 400)
        self.assertTrue(r.json()["errors"])

    def test_overrides_go_through_the_policy(self):
        r = self.client("alice").post("/api/runs", json={"template": "pki-refill", "overrides": ['image="busybox"']})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json()["errors"][0]["path"], "image")

    # ------------------------------------------------------------ runs
    def test_run_lifecycle_with_service_credentials_and_limits(self):
        alice = self.client("alice")
        run = alice.post("/api/runs", json={"template": "pki-refill", "duration_minutes": 5}).json()
        self.assertEqual(run["state"], "queued")
        self.start(run["id"])
        got = self.runs.get(run["id"])
        self.assertEqual((got["state"], got["instance"], got["seed"]), ("running", 7, 5))
        self.assertAlmostEqual(got["deadline"], got["started_at"] + 300, delta=2)
        call = self.up_calls[-1]
        self.assertTrue(call["service_sets"][0].startswith("control.auth="))       # generated broker account
        self.assertEqual(len(call["env"]), 2)
        self.assertFalse(any(k.startswith("VNAP_RUN_") for k in os.environ))     # gone after the start
        self.assertEqual(call["limits"], self.app.state.cfg["containers"])
        # one concurrent run for users
        self.assertEqual(alice.post("/api/runs", json={"template": "pki-refill"}).status_code, 429)
        # stop: results collected, simulation removed
        with mock.patch.object(runs_module, "scenario_down") as down, mock.patch.object(self.runs, "collect_results") as collect:
            self.assertEqual(alice.post(f"/api/runs/{run['id']}/stop").status_code, 202)
            self.runs._stop(run["id"])
        self.assertTrue(down.called and collect.called)
        self.assertEqual(self.runs.get(run["id"])["state"], "stopped")
        self.assertEqual(alice.delete(f"/api/runs/{run['id']}").status_code, 200)

    def test_duration_limit(self):
        r = self.client("alice").post("/api/runs", json={"template": "pki-refill", "duration_minutes": 100000})
        self.assertEqual(r.status_code, 400)

    def test_time_limit_stops_runs(self):
        run = self.client("alice").post("/api/runs", json={"template": "pki-refill", "duration_minutes": 1}).json()
        self.start(run["id"])
        self.db.execute("UPDATE runs SET deadline = ? WHERE id = ?", (time.time() - 1, run["id"]))
        due = self.db.query("SELECT id FROM runs WHERE state = 'running' AND deadline < ?", (time.time(),))
        for r in due:
            self.runs.stop(r["id"], "time limit")
        self.assertEqual(self.runs.jobs.get_nowait(), ("start", run["id"]))   # the queued start from create
        self.assertEqual(self.runs.jobs.get_nowait(), ("stop", run["id"]))
        self.assertEqual(self.runs.get(run["id"])["stop_reason"], "time limit")

    def test_runs_are_private_unless_shared(self):
        alice, bob, vera = self.client("alice"), self.client("bob"), self.client("vera")
        run = alice.post("/api/runs", json={"template": "pki-refill"}).json()
        self.assertEqual(bob.get(f"/api/runs/{run['id']}").status_code, 404)
        self.assertEqual(bob.get("/api/runs").json(), [])
        alice.post(f"/api/runs/{run['id']}/share", json={"username": "vera"})
        self.assertEqual(vera.get(f"/api/runs/{run['id']}").status_code, 200)
        self.assertEqual(vera.post(f"/api/runs/{run['id']}/stop").status_code, 403)   # shared: read only
        self.assertEqual(len(self.client("root").get("/api/runs").json()), 1)          # admins see all

    def test_result_downloads_only_listed_files(self):
        alice = self.client("alice")
        run = alice.post("/api/runs", json={"template": "pki-refill"}).json()
        d = os.path.join(self.runs.results_dir, run["id"])
        os.makedirs(d)
        with open(os.path.join(d, "status.json"), "w") as f:
            f.write("{}")
        self.assertEqual(alice.get(f"/api/runs/{run['id']}/results").json()[0]["name"], "status.json")
        self.assertEqual(alice.get(f"/api/runs/{run['id']}/results/status.json").status_code, 200)
        self.assertEqual(alice.get(f"/api/runs/{run['id']}/results/..%2F..%2Fvnapapi.db").status_code, 404)

    def test_live_endpoints_need_a_running_run(self):
        alice = self.client("alice")
        run = alice.post("/api/runs", json={"template": "pki-refill"}).json()
        self.assertEqual(alice.get(f"/api/runs/{run['id']}/events").status_code, 409)
        self.assertEqual(alice.post(f"/api/runs/{run['id']}/control", json={"action": "change", "station": "obu1-i7"}).status_code, 409)


    # ------------------------------------------------------------ web UI
    def test_ui_is_served_with_a_strict_csp(self):
        c = TestClient(self.app)
        r = c.get("/", follow_redirects=False)
        self.assertEqual((r.status_code, r.headers["location"]), (307, "/ui/"))
        r = c.get("/ui/")
        self.assertEqual(r.status_code, 200)
        self.assertIn('src="js/main.js"', r.text)
        csp = r.headers["content-security-policy"]
        self.assertIn("script-src 'self'", csp)
        self.assertNotIn("unsafe", csp)
        self.assertIn("https://tile.openstreetmap.org", csp)
        self.assertIn("frame-ancestors 'none'", csp)
        for path in ("/ui/js/main.js", "/ui/js/views/run.js", "/ui/vendor/leaflet/leaflet.js", "/ui/app.css"):
            self.assertEqual(c.get(path).status_code, 200, path)
        self.assertEqual(c.get("/ui/../vnapapi/app.py").status_code, 404)
        self.assertEqual(c.get("/api/ui-config").json()["tile_url"], "https://tile.openstreetmap.org/{z}/{x}/{y}.png")
        self.assertEqual(c.get("/api/runs").status_code, 401)        # the UI files are public, the data is not

    def test_layout_for_the_map(self):
        alice = self.client("alice")
        run = alice.post("/api/runs", json={"template": "mixzone-random"}).json()
        r = alice.get(f"/api/runs/{run['id']}/layout")
        self.assertEqual(r.status_code, 200, r.text)
        lay = r.json()
        self.assertIn("random-turn intersection mix zone", lay["description"])
        names = [s["name"].split("-")[0] for s in lay["stations"]]
        self.assertEqual(names[:2], ["rsu", "obu1"])
        rsu, obu1 = lay["stations"][0], lay["stations"][1]
        self.assertEqual((rsu["station_type"], rsu["mobility"], rsu["pseudonyms"]), (15, {}, False))
        self.assertTrue(obu1["pseudonyms"])
        self.assertEqual(obu1["mobility"]["crossing"], [40.208106, -8.4197756])
        self.assertGreater(obu1["start"][1], -8.4197756)            # starts on the east arm
        self.assertEqual(len(lay["mix_zones"]), 1)
        self.assertTrue(lay["pki"] and lay["eavesdropper"] and lay["control"])
        self.assertEqual(self.client("bob").get(f"/api/runs/{run['id']}/layout").status_code, 404)


    def test_probe_timeout_removes_the_container(self):
        import subprocess
        from vnapapi import control
        calls = []

        def fake_run(cmd, **kw):
            calls.append(cmd)
            if cmd[:2] == ["docker", "run"]:
                raise subprocess.TimeoutExpired(cmd, kw.get("timeout"))
            return subprocess.CompletedProcess(cmd, 0, "", "")
        with mock.patch.object(control.subprocess, "run", fake_run):
            with self.assertRaises(control.ControlError):
                control.read_retained("net", "broker", "vnap/position/+", ("u", "secret"))
        name = calls[0][calls[0].index("--name") + 1]
        self.assertEqual(calls[1], ["docker", "rm", "-f", name])
        self.assertNotIn("secret", " ".join(calls[0]))              # credentials go by environment only

    def test_login_over_plain_http_is_refused_with_secure_cookies(self):
        self.app.state.cfg["server"]["cookie_secure"] = True
        body = {"username": "alice", "password": PASSWORD}
        r = TestClient(self.app, base_url="http://192.168.1.5").post("/api/auth/login", json=body)
        self.assertEqual(r.status_code, 400)
        self.assertIn("HTTPS", r.json()["detail"])
        self.assertEqual(TestClient(self.app, base_url="https://sim.example.org").post("/api/auth/login", json=body).status_code, 200)
        self.assertEqual(TestClient(self.app, base_url="http://localhost").post("/api/auth/login", json=body).status_code, 200)

    # ------------------------------------------------------------ TOTP
    def test_totp_codes_follow_rfc6238(self):
        secret = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"      # ASCII "12345678901234567890" (RFC 6238 test key)
        self.assertEqual(totp.code_at(secret, 59 // 30, digits=8), "94287082")
        self.assertEqual(totp.code_at(secret, 1111111109 // 30, digits=8), "07081804")
        self.assertEqual(totp.match(secret, "081804", now=1111111109), 1111111109 // 30)
        self.assertEqual(totp.match(secret, "081804", now=1111111109 + 30), 1111111109 // 30)   # one step of drift
        self.assertIsNone(totp.match(secret, "081804", now=1111111109 + 90))
        self.assertIsNone(totp.match(secret, "081804", last_step=1111111109 // 30, now=1111111109))  # used once

    def enable_totp(self, client):
        setup = client.post("/api/auth/totp/setup", json={"password": PASSWORD})
        self.assertEqual(setup.status_code, 200, setup.text)
        secret = setup.json()["secret"]
        self.assertTrue(setup.json()["uri"].startswith("otpauth://totp/vnap-secure%3Aalice?secret="))
        self.assertIn("<svg", setup.json()["qr_svg"])
        self.assertEqual(client.post("/api/auth/totp/enable", json={"code": "000000" if totp.code_at(secret, totp.current_step()) != "000000" else "111111"}).status_code, 400)
        r = client.post("/api/auth/totp/enable", json={"code": totp.code_at(secret, totp.current_step())})
        self.assertEqual(r.status_code, 200, r.text)
        return secret, r.json()["recovery_codes"]

    def test_totp_login(self):
        alice = self.client("alice")
        secret, recovery = self.enable_totp(alice)
        self.assertEqual(len(recovery), 10)
        row = self.db.one("SELECT * FROM users WHERE username = 'alice'")
        self.assertTrue(row["totp_secret"].startswith("v1:"))
        self.assertNotIn(secret, row["totp_secret"])                                  # encrypted at rest
        self.assertNotIn(recovery[0], str(self.db.query("SELECT * FROM totp_recovery")))  # only digests
        self.assertIsNone(self.accounts.box.open(row["totp_secret"], "totp:999"))    # bound to the user

        c = TestClient(self.app)
        body = {"username": "alice", "password": PASSWORD}
        r = c.post("/api/auth/login", json=body)
        self.assertEqual((r.status_code, r.json().get("totp_required")), (401, True))
        self.assertNotIn("vnap_session", r.cookies)
        self.assertEqual(c.post("/api/auth/login", json={**body, "totp_code": "12345"}).status_code, 401)
        # the code used to enable TOTP cannot log in again (same time step)
        self.assertEqual(c.post("/api/auth/login", json={**body, "totp_code": totp.code_at(secret, totp.current_step())}).status_code, 401)
        self.db.execute("UPDATE users SET totp_last_step = totp_last_step - 2, failed_logins = 0 WHERE username = 'alice'")
        r = c.post("/api/auth/login", json={**body, "totp_code": totp.code_at(secret, totp.current_step())})
        self.assertEqual(r.status_code, 200, r.text)
        # a recovery code works once
        self.assertEqual(TestClient(self.app).post("/api/auth/login", json={**body, "totp_code": recovery[0]}).status_code, 200)
        self.assertEqual(TestClient(self.app).post("/api/auth/login", json={**body, "totp_code": recovery[0]}).status_code, 401)
        self.assertEqual(alice.get("/api/auth/totp").json(), {"enabled": True, "recovery_codes_left": 9})
        actions = [a["action"] for a in self.db.query("SELECT action FROM audit WHERE username = 'alice'")]
        self.assertIn("totp.enabled", actions)
        self.assertIn("totp.recovery_code_used", actions)

    def test_wrong_totp_codes_lock_the_account(self):
        secret, _ = self.enable_totp(self.client("alice"))
        body = {"username": "alice", "password": PASSWORD, "totp_code": "000000" if totp.code_at(secret, totp.current_step()) != "000000" else "111111"}
        for _ in range(self.app.state.cfg["auth"]["max_failed_logins"]):
            TestClient(self.app).post("/api/auth/login", json=body)
        r = TestClient(self.app).post("/api/auth/login", json={**body, "totp_code": totp.code_at(secret, totp.current_step() + 1)})
        self.assertIn("locked", r.json()["detail"])

    def test_totp_disable_and_admin_reset(self):
        alice = self.client("alice")
        secret, recovery = self.enable_totp(alice)
        token = alice.post("/api/tokens", json={"name": "t"}).json()["token"]
        bearer = TestClient(self.app, headers={"Authorization": f"Bearer {token}"})
        self.assertEqual(bearer.post("/api/auth/totp/disable", json={"password": PASSWORD, "code": recovery[0]}).status_code, 403)
        self.assertEqual(bearer.get("/api/runs").status_code, 200)                    # tokens need no second factor
        self.assertEqual(alice.post("/api/auth/totp/disable", json={"password": "wrong-password-x", "code": recovery[0]}).status_code, 403)
        self.assertEqual(alice.post("/api/auth/totp/disable", json={"password": PASSWORD, "code": recovery[0]}).status_code, 200)
        self.assertEqual(self.client("alice").get("/api/auth/totp").json()["enabled"], False)   # password alone again

        self.enable_totp(self.client("alice"))
        root = self.client("root")
        self.assertTrue(next(u for u in root.get("/api/users").json() if u["username"] == "alice")["totp"])
        self.assertEqual(root.patch("/api/users/alice", json={"reset_totp": True}).status_code, 200)
        self.assertEqual(alice.get("/api/auth/me").status_code, 401)                  # sessions ended
        self.assertEqual(self.client("alice").get("/api/auth/totp").json()["enabled"], False)

    def test_database_migration_adds_totp_columns(self):
        import sqlite3
        from vnapapi.db import Database
        path = os.path.join(self.tmp, "old.db")
        con = sqlite3.connect(path)
        con.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL, password_hash TEXT NOT NULL, "
                    "role TEXT NOT NULL, disabled INTEGER NOT NULL DEFAULT 0, failed_logins INTEGER NOT NULL DEFAULT 0, "
                    "locked_until REAL NOT NULL DEFAULT 0, created_at REAL NOT NULL, created_by TEXT)")
        con.execute("INSERT INTO users (username, password_hash, role, created_at) VALUES ('old', 'x', 'user', 0)")
        con.commit()
        con.close()
        row = Database(path).one("SELECT * FROM users WHERE username = 'old'")
        self.assertEqual((row["totp_enabled"], row["totp_last_step"], row["totp_secret"]), (0, 0, None))

    # ------------------------------------------------------------ backups
    def test_backup_create_verify_restore(self):
        cfg = self.app.state.cfg
        alice = self.client("alice")
        with open(os.path.join(runs_module.TEMPLATE_DIR, "pki-refill.toml")) as f:
            run = alice.post("/api/runs", json={"scenario_text": f.read()}).json()
        res = os.path.join(self.runs.results_dir, run["id"])
        os.makedirs(res)
        with open(os.path.join(res, "score.json"), "w") as f:
            f.write('{"links": 3}')
        root = self.client("root")
        self.assertEqual(alice.post("/api/backups").status_code, 403)
        r = root.post("/api/backups")
        self.assertEqual(r.status_code, 201, r.text)
        self.assertEqual(r.json()["counts"]["runs"], 1)
        listed = root.get("/api/backups").json()
        self.assertEqual(listed[0]["name"], r.json()["name"])
        path = os.path.join(cfg["backup"]["dir"], listed[0]["name"])
        self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
        manifest = backup.verify(path)
        self.assertIn(f"results/{run['id']}/score.json", manifest["files"])
        self.assertIn(f"scenarios/{run['id']}.toml", manifest["files"])

        # restore into another data directory: data back, active run marked, scenario path followed
        other = {**cfg, "server": {**cfg["server"], "data_dir": os.path.join(self.tmp, "restored")}}
        result = backup.restore(other, path)
        self.assertEqual(result["runs_marked_failed"], 1)                           # it was queued
        import sqlite3
        con = sqlite3.connect(os.path.join(self.tmp, "restored", "vnapapi.db"))
        state, scenario_file = con.execute("SELECT state, scenario_file FROM runs").fetchone()
        self.assertEqual(state, "failed")
        self.assertEqual(scenario_file, os.path.join(self.tmp, "restored", "scenarios", f"{run['id']}.toml"))
        self.assertTrue(os.path.isfile(scenario_file))
        with open(os.path.join(self.tmp, "restored", "results", run["id"], "score.json")) as f:
            self.assertEqual(f.read(), '{"links": 3}')
        with self.assertRaises(backup.BackupError):                                # not over existing data
            backup.restore(other, path)
        self.assertIsNotNone(backup.restore(other, path, force=True)["previous_data"])

    def test_backup_tampering_and_pruning(self):
        import gzip
        cfg = self.app.state.cfg
        paths = [backup.create(cfg, keep=2) for _ in range(3)]
        self.assertEqual([a["path"] for a in backup.archives(cfg["backup"]["dir"])], paths[:0:-1])   # newest two kept
        with gzip.open(paths[-1]) as f:
            raw = bytearray(f.read())
        i = raw.index(b"SQLite format 3")
        raw[i + 200] ^= 0xFF                                                        # flip a byte of the database
        bad = os.path.join(self.tmp, "bad.tar.gz")
        with gzip.open(bad, "wb") as f:
            f.write(bytes(raw))
        with self.assertRaisesRegex(backup.BackupError, "checksum mismatch: vnapapi.db"):
            backup.verify(bad)

if __name__ == "__main__":
    unittest.main()
