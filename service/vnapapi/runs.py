"""Runs: validation, limits, a job queue that starts and stops simulations with vnapsim,
automatic stop at the time limit, and collection of results."""

import json
import os
import queue
import re
import secrets
import shutil
import subprocess
import threading
import time
import tomllib
import traceback

from .config import SIM_DIR
from .report import write_report
import sys
sys.path.insert(0, SIM_DIR)
from vnapsim.common import SCENARIO_DIR, ScenarioError, docker  # noqa: E402
from vnapsim.lifecycle import allocate_instance, scenario_down, scenario_up  # noqa: E402
from vnapsim.scenario import load_policy, load_scenario, resolve_scenario_path  # noqa: E402
from vnapsim.scoring import score_run  # noqa: E402
from vnapsim.status import Simulation, build_status  # noqa: E402

TEMPLATE_DIR = os.path.join(SCENARIO_DIR, "templates")
ACTIVE = ("queued", "starting", "running", "stopping")
MAX_SAVED_TEMPLATES = 100   # per user


class RunError(Exception):
    """Refused request (limits, validation); `errors` holds field-level validation errors."""

    def __init__(self, message, errors=None, status=400):
        super().__init__(message)
        self.errors, self.status = errors or [], status


def parse_text(text):
    """Scenario text as a dictionary; RunError with a field-level error when it is not TOML."""
    if len(text) > 100_000:
        raise RunError("scenario_text too long")
    try:
        return tomllib.loads(text)
    except tomllib.TOMLDecodeError as e:
        message = str(e)
        if "Illegal character '\\n'" in message:   # a line break inside "..."
            message += ': a string in "..." must stay on one line; write several lines as \"\"\"...\"\"\"'
        raise RunError(f"scenario_text: {message}", [{"path": "", "message": message, "text": message}])


def genericize(text):
    """Scenario text with generic names: stations become rsu1.. (station type 15) and vehicle1..,
    mix zones zone1.. The text keeps its comments and layout. Station names are also replaced
    where [check] expectations start with them. Returns (text, {old name: new name})."""
    data = parse_text(text)
    stations, zones, rsus, vehicles = {}, {}, 0, 0
    for st in data.get("stations") or []:
        if isinstance(st, dict) and isinstance(st.get("name"), str) and st["name"] not in stations:
            if st.get("station_type") == 15:
                rsus += 1
                stations[st["name"]] = f"rsu{rsus}"
            else:
                vehicles += 1
                stations[st["name"]] = f"vehicle{vehicles}"
    control = data.get("control") if isinstance(data.get("control"), dict) else {}
    for i, zone in enumerate(control.get("mix_zones") or [], 1):
        if isinstance(zone, dict) and isinstance(zone.get("name"), str):
            zones.setdefault(zone["name"], f"zone{i}")
    names = zones | stations
    if not names:
        return text, {}
    # all names in one pass: a new name may be another station's old name
    out = re.sub(r"""(\bname\s*=\s*)(["'])(%s)\2""" % "|".join(map(re.escape, sorted(names, key=len, reverse=True))),
                 lambda m: f"{m.group(1)}{m.group(2)}{names[m.group(3)]}{m.group(2)}", text)
    if stations:
        prefix = re.compile(r"(?<![\w.-])(%s)(?=\.)" % "|".join(map(re.escape, sorted(stations, key=len, reverse=True))))
        out = re.sub(r"\bexpect\s*=\s*\[.*?\]", lambda m: prefix.sub(lambda n: stations[n.group(1)], m.group(0)), out, flags=re.S)
    after = parse_text(out)
    if [s.get("name") for s in after.get("stations") or []] != [stations.get(s.get("name"), s.get("name")) for s in data.get("stations") or []]:
        raise RunError("cannot replace the names in this text: write each station as name = \"...\"")
    return out, names


def policy_role(user):
    return "admin" if user["role"] == "admin" else "user"


def dir_size(path):
    total = 0
    for root, _, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total


class RunManager:
    def __init__(self, db, cfg, start_workers=True):
        self.db, self.cfg = db, cfg
        data = cfg["server"]["data_dir"]
        self.scenario_dir = os.path.join(data, "scenarios")
        self.results_dir = os.path.join(data, "results")
        for d in (self.scenario_dir, self.results_dir):
            os.makedirs(d, mode=0o700, exist_ok=True)
        self.jobs = queue.Queue()
        self.stop_event = threading.Event()
        self.limits = cfg["containers"]
        self.policy = load_policy()
        self._recover()
        if start_workers:
            for i in range(int(cfg["worker"]["threads"])):
                threading.Thread(target=self._worker, name=f"run-worker-{i}", daemon=True).start()
            threading.Thread(target=self._watch_deadlines, name="run-deadlines", daemon=True).start()

    # ------------------------------------------------------------ scenarios
    def catalogue(self, user):
        """Scenario names a user may start by name: templates for users, also the full catalogue for admins."""
        names = {"templates": sorted(f[:-5] for f in os.listdir(TEMPLATE_DIR) if f.endswith(".toml"))
                 if os.path.isdir(TEMPLATE_DIR) else []}
        if user["role"] == "admin":
            names["scenarios"] = sorted(f[:-5] for f in os.listdir(SCENARIO_DIR) if f.endswith(".toml"))
        return names

    def scenario_path(self, user, scenario=None, template=None, text=None, run_id=None):
        """File of the requested scenario (user scenario text is stored per run)."""
        given = [x for x in (scenario, template, text) if x]
        if len(given) != 1:
            raise RunError("give exactly one of scenario, template, scenario_text")
        if text is not None:
            parse_text(text)
            path = os.path.join(self.scenario_dir, f"{run_id or 'check-' + secrets.token_hex(6)}.toml")
            with open(path, "w") as f:
                f.write(text)
            return path
        name = template or scenario
        if not name.replace("-", "").replace("_", "").isalnum():
            raise RunError("scenario names are letters, digits, '-' and '_'")
        if template:
            path = os.path.join(TEMPLATE_DIR, name + ".toml")
            if not os.path.isfile(path):
                raise RunError(f"unknown template {name}", status=404)
            return path
        if user["role"] != "admin":
            raise RunError("users start templates or their own scenario_text; catalogue scenarios are for admins", status=403)
        try:
            return resolve_scenario_path(name)
        except ScenarioError as e:
            raise RunError(str(e), status=404)

    def saved_templates(self, user):
        """Templates saved by users: one's own and the shared ones; admins see all."""
        return self.db.query(
            "SELECT t.id, t.name, u.username AS owner, t.shared, t.updated_at FROM templates t JOIN users u ON u.id = t.owner_id "
            "WHERE t.owner_id = ? OR t.shared OR ? ORDER BY t.name, u.username", (user["id"], user["role"] == "admin"))

    def saved_template(self, user, template_id, write=False):
        row = self.db.one("SELECT t.*, u.username AS owner FROM templates t JOIN users u ON u.id = t.owner_id WHERE t.id = ?",
                          (template_id,))
        mine = row and (row["owner_id"] == user["id"] or user["role"] == "admin")
        if not row or not (mine or (row["shared"] and not write)):
            raise RunError("no such template" if not row or not row["shared"] else "only the owner can do this",
                           status=404 if not row or not row["shared"] else 403)
        return row

    def save_template(self, user, name, text, shared=False, make_generic=False, replace=False):
        """Save scenario text as a template of this user. It must be valid for the user, so that it can
        be started as it is. Returns (template, {old name: new name})."""
        if user["role"] == "viewer":
            raise RunError("viewers cannot save templates", status=403)
        if not 1 <= len(name) <= 64 or not name.replace("-", "").replace("_", "").isalnum():
            raise RunError("template names are 1 to 64 letters, digits, '-' and '_'")
        renamed = {}
        if make_generic:
            text, renamed = genericize(text)
        path = self.scenario_path(user, text=text)
        try:
            self.validate(user, path, [])
        finally:
            os.remove(path)
        now = time.time()
        old = self.db.one("SELECT id FROM templates WHERE owner_id = ? AND name = ?", (user["id"], name))
        if old and not replace:
            raise RunError(f"you already have a template {name}", status=409)
        if old:
            self.db.execute("UPDATE templates SET text = ?, shared = ?, updated_at = ? WHERE id = ?", (text, int(shared), now, old["id"]))
            template_id = old["id"]
        else:
            if self.db.one("SELECT COUNT(*) AS n FROM templates WHERE owner_id = ?", (user["id"],))["n"] >= MAX_SAVED_TEMPLATES:
                raise RunError(f"limit reached: {MAX_SAVED_TEMPLATES} saved templates; delete old ones first", status=429)
            template_id = self.db.execute("INSERT INTO templates (owner_id, name, text, shared, created_at, updated_at) "
                                          "VALUES (?, ?, ?, ?, ?, ?)", (user["id"], name, text, int(shared), now, now))
        return self.saved_template(user, template_id), renamed

    def delete_template(self, user, template_id):
        self.saved_template(user, template_id, write=True)
        self.db.execute("DELETE FROM templates WHERE id = ?", (template_id,))

    def validate(self, user, path, overrides):
        try:
            return load_scenario(path, overrides, 0, policy_role(user), self.policy)
        except ScenarioError as e:
            raise RunError("invalid scenario", e.errors or [{"path": "", "message": str(e), "text": str(e)}])

    # ------------------------------------------------------------ runs
    def create(self, user, scenario=None, template=None, scenario_text=None, overrides=(), duration_minutes=None, name=None):
        if user["role"] == "viewer":
            raise RunError("viewers cannot start runs", status=403)
        limits = self.cfg["limits"]["admin" if user["role"] == "admin" else "user"]
        active = self.db.one(f"SELECT COUNT(*) AS n FROM runs WHERE owner_id = ? AND state IN ({','.join('?' * len(ACTIVE))})",
                             (user["id"], *ACTIVE))["n"]
        if active >= limits["concurrent_runs"]:
            raise RunError(f"limit reached: {limits['concurrent_runs']} run(s) at the same time", status=429)
        used = sum(dir_size(os.path.join(self.results_dir, r["id"]))
                   for r in self.db.query("SELECT id FROM runs WHERE owner_id = ?", (user["id"],)))
        if used >= limits["results_mb"] * 1024 * 1024:
            raise RunError(f"results quota of {limits['results_mb']} MB used: delete old runs first", status=429)
        minutes = duration_minutes or limits["max_duration_minutes"]
        if not 1 <= minutes <= limits["max_duration_minutes"]:
            raise RunError(f"duration_minutes must be 1..{limits['max_duration_minutes']}")
        overrides = list(overrides or [])
        if len(overrides) > 50 or any(not isinstance(o, str) or len(o) > 500 for o in overrides):
            raise RunError("at most 50 overrides of at most 500 characters")
        name = " ".join((name or "").split())
        if len(name) > 100:
            raise RunError("name: at most 100 characters")
        # validate first, so that a refused scenario uses no run number
        path = self.scenario_path(user, scenario, template, scenario_text)
        try:
            sc = self.validate(user, path, overrides)
        except Exception:
            if scenario_text is not None:
                os.remove(path)
            raise
        unnamed = scenario_text is not None and sc["name"] == os.path.splitext(os.path.basename(path))[0]
        run_id = self.new_id(user["username"], name or (None if unnamed else sc["name"]))
        if scenario_text is not None:   # the run's own scenario file is named after the run
            stored = os.path.join(self.scenario_dir, f"{run_id}.toml")
            os.rename(path, stored)
            path = stored
        if unnamed:
            sc["name"] = run_id
        self.db.execute("INSERT INTO runs (id, owner_id, scenario, scenario_file, overrides, state, created_at, deadline, "
                        "name, description) VALUES (?, ?, ?, ?, ?, 'queued', ?, ?, ?, ?)",
                        (run_id, user["id"], sc["name"], path, json.dumps(overrides), time.time(), minutes * 60,
                         name or sc["name"], sc["description"]))
        self.jobs.put(("start", run_id))
        return self.get(run_id)

    def new_id(self, username, name):
        """<user>-<scenario name>-<n>: n counts that user's runs of that name, from 1. User and name are reduced to
        letters, digits, '.', '_' and '-' (the id is part of URLs and file names); without a name it is "run"."""
        def part(text, length, default):
            return re.sub(r"-{2,}", "-", re.sub(r"[^A-Za-z0-9._-]", "-", text or "")).strip("-.")[:length].strip("-.") or default
        # one counter per "<user>-<name>": two pairs that give the same text would give the same ids, so they share it
        base = f"{part(username, 30, 'user')}-{part(name, 60, 'run')}"
        while True:
            run_id = f"{base}-{self.db.next_run_number(base)}"
            # an id is never reused: not one of a kept run, nor one whose files are still there
            if not (self.db.one("SELECT id FROM runs WHERE id = ?", (run_id,))
                    or os.path.exists(os.path.join(self.results_dir, run_id))
                    or os.path.exists(os.path.join(self.scenario_dir, f"{run_id}.toml"))):
                return run_id

    def get(self, run_id):
        run = self.db.one("SELECT r.*, u.username AS owner, u.role AS owner_role FROM runs r JOIN users u ON u.id = r.owner_id "
                          "WHERE r.id = ?", (run_id,))
        if run:
            run["overrides"] = json.loads(run["overrides"])
            run["name"] = run["name"] or run["scenario"]   # runs from before names: the scenario's
            run["start_status"] = json.loads(run["start_status"]) if run["start_status"] else None
            run["shared_with"] = [r["username"] for r in self.db.query(
                "SELECT u.username FROM run_shares s JOIN users u ON u.id = s.user_id WHERE s.run_id = ?", (run_id,))]
        return run

    def visible(self, user):
        if user["role"] == "admin":
            ids = self.db.query("SELECT id FROM runs ORDER BY created_at DESC")
        else:
            ids = self.db.query("SELECT id FROM runs WHERE owner_id = ? OR id IN (SELECT run_id FROM run_shares WHERE user_id = ?) "
                                "ORDER BY created_at DESC", (user["id"], user["id"]))
        return [self.get(r["id"]) for r in ids]

    def rename(self, run_id, name=None, description=None):
        """Change the run's name and description (None: keep). An empty name gives the scenario's name again."""
        if name is not None:
            name = " ".join(name.split())
            if len(name) > 100:
                raise RunError("name: at most 100 characters")
            self.db.execute("UPDATE runs SET name = ? WHERE id = ?", (name or None, run_id))
        if description is not None:
            description = description.strip()
            if len(description) > 2000:
                raise RunError("description: at most 2000 characters")
            self.db.execute("UPDATE runs SET description = ? WHERE id = ?", (description, run_id))
        run = self.get(run_id)
        record = os.path.join(self.results_dir, run_id, "run.json")
        if os.path.isfile(record):   # results already collected: keep their record current
            with open(record) as f:
                data = json.load(f)
            data.update(name=run["name"], description=run["description"])
            with open(record, "w") as f:
                json.dump(data, f, indent=1)
        return run

    def stop(self, run_id, reason):
        run = self.get(run_id)
        if run["state"] not in ("queued", "starting", "running"):
            raise RunError(f"run is {run['state']}")
        if run["state"] == "queued":
            self.db.execute("UPDATE runs SET state = 'stopped', stop_reason = ?, stopped_at = ? WHERE id = ?",
                            (reason, time.time(), run_id))
            return
        self.db.execute("UPDATE runs SET stop_reason = ? WHERE id = ?", (reason, run_id))
        self.jobs.put(("stop", run_id))

    def delete(self, run_id):
        run = self.get(run_id)
        if run["state"] in ACTIVE:
            raise RunError("stop the run first", status=409)
        shutil.rmtree(os.path.join(self.results_dir, run_id), ignore_errors=True)
        if run["scenario_file"].startswith(self.scenario_dir):
            try:
                os.remove(run["scenario_file"])
            except OSError:
                pass
        self.db.execute("DELETE FROM runs WHERE id = ?", (run_id,))

    def simulation(self, run):
        if run["state"] != "running" or not run["lan"]:
            raise RunError(f"run is {run['state']}", status=409)
        return Simulation(run["lan"], run["ctl"] or "vnapctl0-unused")

    # ------------------------------------------------------------ jobs
    def _worker(self):
        while not self.stop_event.is_set():
            try:
                kind, run_id = self.jobs.get(timeout=1)
            except queue.Empty:
                continue
            try:
                (self._start if kind == "start" else self._stop)(run_id)
            except Exception:  # keep the worker alive; the run records the error
                self.db.execute("UPDATE runs SET state = 'failed', error = ? WHERE id = ?",
                                (traceback.format_exc(limit=3)[-2000:], run_id))

    def _start(self, run_id):
        run = self.get(run_id)
        if not run or run["state"] != "queued":
            return
        self.db.execute("UPDATE runs SET state = 'starting' WHERE id = ?", (run_id,))
        owner = self.db.one("SELECT * FROM users WHERE id = ?", (run["owner_id"],))
        service_sets, env_names = [], []
        with open(run["scenario_file"], "rb") as f:
            has_control = "control" in tomllib.load(f)
        if has_control:
            # the run's broker account: generated here, never shown to users
            token = secrets.token_hex(6)   # not the run id: environment variable names are letters, digits and '_'
            user_env, pass_env = f"VNAP_RUN_{token}_USER", f"VNAP_RUN_{token}_PASS"
            os.environ[user_env], os.environ[pass_env] = f"run-{run_id}", secrets.token_urlsafe(24)
            env_names = [user_env, pass_env]
            service_sets.append(f'control.auth={{username_env = "{user_env}", password_env = "{pass_env}"}}')
        try:
            sc, claimed = allocate_instance(run["scenario_file"], run["overrides"], policy_role(owner), policy=self.policy,
                                            service_sets=service_sets)
            ok, report = scenario_up(sc, wait=30.0, claimed=claimed, limits=self.limits)
        except (ScenarioError, RuntimeError) as e:
            self.db.execute("UPDATE runs SET state = 'failed', error = ?, stopped_at = ? WHERE id = ?", (str(e), time.time(), run_id))
            return
        finally:
            for name in env_names:
                os.environ.pop(name, None)
        status = report["status"]
        seed = ((status.get("control") or {}).get("mobility") or {}).get("seed")
        now = time.time()
        self.db.execute("UPDATE runs SET state = 'running', instance = ?, lan = ?, ctl = ?, vnap_run_id = ?, image = ?, seed = ?, "
                        "started_at = ?, deadline = ? + deadline, start_status = ?, error = ? WHERE id = ?",
                        (sc["instance"], sc["network"]["name"], (sc["control"] or {}).get("network"), report["run_id"],
                         sc["image"], seed, now, now, json.dumps(status),
                         None if ok else "not ready: " + json.dumps(report["pending"]), run_id))

    def _stop(self, run_id):
        run = self.get(run_id)
        if not run or run["state"] != "running":
            return
        self.db.execute("UPDATE runs SET state = 'stopping' WHERE id = ?", (run_id,))
        try:
            self.collect_results(run)
        finally:
            scenario_down(run["lan"], run["ctl"] or "vnapctl0-unused", force=True)
            self.db.execute("UPDATE runs SET state = 'stopped', stopped_at = ? WHERE id = ?", (time.time(), run_id))

    def _watch_deadlines(self):
        while not self.stop_event.wait(10):
            for run in self.db.query("SELECT id FROM runs WHERE state = 'running' AND deadline < ?", (time.time(),)):
                try:
                    self.stop(run["id"], "time limit")
                except RunError:
                    pass

    def _recover(self):
        """Jobs of a previous service process did not finish: mark them; running runs continue."""
        self.db.execute("UPDATE runs SET state = 'failed', error = 'service restarted before the job finished' "
                        "WHERE state IN ('queued', 'starting')")
        for run in self.db.query("SELECT id FROM runs WHERE state = 'stopping'"):
            self.db.execute("UPDATE runs SET state = 'running' WHERE id = ?", (run["id"],))
            self.jobs.put(("stop", run["id"]))

    # ------------------------------------------------------------ results
    def collect_results(self, run):
        """Logs, status, eavesdropper logs and scoring of a running simulation, kept after it stops.
        The run's PKI material (keys) is never collected; only its issue log (digests)."""
        out = os.path.join(self.results_dir, run["id"])
        os.makedirs(out, mode=0o700, exist_ok=True)
        sim = Simulation(run["lan"], run["ctl"] or "vnapctl0-unused")
        containers = sim.stations + [c for c in (sim.broker, sim.client, sim.mobility, sim.pki) if c] + sim.observers
        for info in containers:
            name = Simulation.name(info)
            with open(os.path.join(out, f"{name}.log"), "w") as f:
                f.write(docker("logs", "--timestamps", name, check=False))
        for info in sim.observers:
            with open(os.path.join(out, "eavesdropper-logs.tar"), "wb") as f:
                subprocess.run(["docker", "cp", f"{Simulation.name(info)}:/logs", "-"], stdout=f, stderr=subprocess.DEVNULL)
        if sim.pki:
            with open(os.path.join(out, "pki-issued.jsonl"), "w") as f:
                f.write(docker("exec", Simulation.name(sim.pki), "cat", "/pki/private/issued.jsonl", check=False))
        with open(os.path.join(out, "status.json"), "w") as f:
            json.dump(build_status(sim), f, indent=1)
        score = score_run(sim)
        if score is not None:
            with open(os.path.join(out, "score.json"), "w") as f:
                json.dump(score, f, indent=1)
        shutil.copy(run["scenario_file"], os.path.join(out, "scenario.toml"))
        record = {k: run[k] for k in ("id", "owner", "name", "description", "scenario", "overrides", "instance", "vnap_run_id", "image",
                                      "seed", "created_at", "started_at")}
        if sim.pki:   # the C-ITS-PKI version that issued the run's certificates
            record["cits_pki"] = (sim.pki["Config"].get("Labels") or {}).get("vnap.cits_pki")
        record["stopped_at"], record["stop_reason"] = time.time(), run.get("stop_reason")
        check = self.db.one("SELECT verdict, result, created_at FROM checks WHERE run_id = ? ORDER BY id DESC LIMIT 1", (run["id"],))
        if check:
            record["check"] = {"verdict": check["verdict"], "created_at": check["created_at"],
                               "expectations": len(json.loads(check["result"]).get("expectations", []))}
        with open(os.path.join(out, "run.json"), "w") as f:
            json.dump(record, f, indent=1)
        try:   # the summary is built from the files above; a failure must not lose them
            write_report(out)
        except Exception:
            with open(os.path.join(out, "report-error.txt"), "w") as f:
                f.write(traceback.format_exc())

    def results(self, run_id):
        d = os.path.join(self.results_dir, run_id)
        return sorted(f for f in os.listdir(d)) if os.path.isdir(d) else []

    def shutdown(self):
        self.stop_event.set()
