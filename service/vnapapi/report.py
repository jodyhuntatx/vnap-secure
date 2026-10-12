"""Summary report of a stopped run (report.docx), in the form of the test summaries in results/.

Built only from the files collected in the run's results directory (run.json, status.json,
score.json, scenario.toml and the container logs), so it can be made again later:

    python3 -m vnapapi.report <results directory>

The report states what was measured. Its last section, "Suggested next runs", comes from the
rules in service/recommendations.toml: fixed text, printed when its conditions hold for the
run. `--rules` in front of the directory lists the run's facts and what each rule made of them.
"""

import calendar
import json
import os
import re
import sys
import time
import tomllib
import zipfile
from collections import Counter
from xml.sax.saxutils import escape

REPORT_NAME = "report.docx"
CATALOGUE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "recommendations.toml")

RE_SILENT = re.compile(r"\[IDCHANGE\] silent period (\d+) ms")
RE_MOB_STATION = re.compile(r"\[MOBILITY\] station (\d+): (.*?)(?:, topic \S+)?$")
RE_MOB_ZONE = re.compile(r"\[MOBILITY\] t=\s*[\d.]+ s station (\d+) (in mix zone (\S+?):? |left mix zone (\S+) without a change|"
                         r"in a dynamic mix zone)")
RE_EVIDENCE = re.compile(r"^(?:position continuity|timing phase) \((.*?)(?:; old identity silent since)?(?:, next \d+ ms)?\)$")
STATION_TYPES = {0: "unknown", 1: "pedestrian", 2: "cyclist", 3: "moped", 4: "motorcycle", 5: "passenger car", 6: "bus",
                 7: "light truck", 8: "heavy truck", 9: "trailer", 10: "special vehicle", 11: "tram", 15: "roadside unit"}


# ---------------------------------------------------------------- inputs
def _json(directory, name):
    try:
        with open(os.path.join(directory, name)) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def _lines(directory, name):
    try:
        with open(os.path.join(directory, name), errors="replace") as f:
            return f.read().splitlines()
    except OSError:
        return []


def _utc(t, fmt="%Y-%m-%d %H:%M:%S"):
    return time.strftime(fmt, time.gmtime(t))


def _duration(seconds):
    seconds = int(round(seconds))
    if seconds < 120:
        return f"{seconds} s"
    return f"{seconds // 60} min {seconds % 60} s" if seconds % 60 else f"{seconds // 60} min"


def _pct(part, whole):
    return f"{round(100 * part / whole)} %" if whole else "-"


def _plural(n, word):
    return f"{n} {word}" if n == 1 else f"{n} {word}{'es' if word.endswith('ch') else 's'}"


def _and(items):
    items = [str(i) for i in items]
    return " and ".join([", ".join(items[:-1]), items[-1]]) if len(items) > 1 else "".join(items)


def _last_log_time(directory):
    """The latest container log timestamp: when a run without a recorded stop time ended."""
    latest = 0.0
    for name in os.listdir(directory):
        if name.endswith(".log"):
            for line in reversed(_lines(directory, name)[-5:]):
                try:
                    latest = max(latest, calendar.timegm(time.strptime(line[:19], "%Y-%m-%dT%H:%M:%S")))
                    break
                except ValueError:
                    continue
    return latest


def _short(name):
    """Container name without the instance suffix: v1-i1 -> v1."""
    return re.sub(r"-i\d+$", "", name or "?")


def gather(directory):
    """Everything the report needs, from the files of a results directory."""
    run = _json(directory, "run.json") or {}
    status = _json(directory, "status.json") or {}
    score = _json(directory, "score.json")
    try:
        with open(os.path.join(directory, "scenario.toml"), "rb") as f:
            scenario = tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError):
        scenario = {}
    declared = {s.get("name"): s for s in scenario.get("stations", []) if isinstance(s, dict)}
    control = status.get("control") or {}
    mobility = control.get("mobility") or {}
    started = run.get("started_at") or 0
    stopped = run.get("stopped_at") or _last_log_time(directory) or time.time()

    movement, zone_changes, zone_skipped, dynamic_changes = {}, Counter(), Counter(), Counter()
    for line in _lines(directory, f"{mobility.get('name')}.log") if mobility else []:
        if m := RE_MOB_ZONE.search(line):
            if m[4]:
                zone_skipped[m[4]] += 1
            elif m[3]:
                zone_changes[m[3]] += 1
            else:
                dynamic_changes[int(m[1])] += 1
        elif m := RE_MOB_STATION.search(line):
            movement.setdefault(int(m[1]), m[2])

    stations = []
    for st in status.get("stations", []):
        name = _short(st["name"])
        decl = declared.get(name, {})
        log = _lines(directory, f"{st['name']}.log")
        silent = [int(m[1]) / 1000 for line in log if (m := RE_SILENT.search(line))]
        p = st.get("pseudonym") or {}
        env = decl.get("env") or {}
        stations.append({
            "name": name, "container": st["name"], "id": st.get("station_id"), "type": st.get("station_type"),
            "rsu": st.get("station_type") == 15, "state": st.get("state"),
            "pool": (st.get("security") or {}).get("pseudonym_pool"),
            "changes": p.get("changes", 0), "rejected": p.get("rejected", 0),
            "wrapped": sum("wrapped around" in line for line in log),
            "scope": (p.get("id_change") or {}).get("scope"), "refill": p.get("refill"),
            "silent": silent, "silent_cfg": ((decl.get("pseudonyms") or {}).get("silent_min_ms"),
                                             (decl.get("pseudonyms") or {}).get("silent_max_ms")),
            "movement": movement.get(st.get("station_id")),
            "position": (env.get("VANETZA_LATITUDE"), env.get("VANETZA_LONGITUDE")),
            "dynamic": dynamic_changes.get(st.get("station_id"), 0),
        })
    return {"directory": directory, "run": run, "status": status, "score": score, "scenario": scenario, "stations": stations,
            "vehicles": [s for s in stations if s["pool"]], "control": control, "mobility": mobility,
            "started": started, "stopped": stopped, "seconds": max(0.0, stopped - started) if started else 0.0,
            "zone_changes": zone_changes, "zone_skipped": zone_skipped}


# ---------------------------------------------------------------- content
def _silent_text(lo, hi):
    if not hi:
        return "no silent period"
    return f"{lo / 1000:g}-{hi / 1000:g} s" if lo != hi else f"{hi / 1000:g} s"


def _triggers(d):
    """What caused pseudonym changes in this run."""
    out = []
    client = d["control"].get("client") or {}
    if client.get("mode") == "periodic":
        out.append(f"the control client sends a change event to stations {client.get('stations')} every {client.get('interval')} s")
    elif client.get("mode") == "random":
        out.append(f"the control client sends change events to stations {client.get('stations')} at random times")
    for z in d["mobility"].get("mix_zones") or []:
        need = z.get("min_vehicles") or 1
        out.append(f"mix zone {z.get('name')} ({z.get('radius_m'):g} m radius): a change event when a vehicle enters"
                   + (f", once {need} vehicles are inside" if need > 1 else ""))
    dyn = d["mobility"].get("dynamic_mix_zone")
    if dyn:
        mins = sorted({s.get("min_vehicles") for s in dyn.get("stations", []) if s.get("min_vehicles")})
        out.append(f"dynamic mix zone: a change event for a vehicle when {'/'.join(str(m) for m in mins) or '2'} or more "
                   f"participating vehicles are within {dyn.get('radius_m'):g} m of each other "
                   f"(at most once per {dyn.get('cooldown_s'):g} s per vehicle)")
    if not out:
        out.append("only on request (Control tab or API); no automatic trigger was configured")
    return out


def _health(d):
    status, run = d["status"], d["run"]
    chain = [c for st in status.get("stations", []) for c in st.get("chain", [])]
    failed = sum(not c.get("ok") for c in chain)
    parts = [f"status at stop: {status.get('health', 'unknown')}",
             f"{len(chain)} chain checks, " + ("all OK" if not failed else f"{failed} failed")]
    for o in (status.get("observers") or [])[:1]:
        if "counts" in o:
            parts.append(f"{o['counts'].get('errors', 0)} undecodable of {o['counts'].get('frames', 0)} frames")
    check = run.get("check")
    if check:
        parts.insert(0, f"last check: {check.get('verdict')} ({_plural(check.get('expectations', 0), 'expectation')})")
    return "; ".join(parts)


def _evidence(link):
    out = []
    for e in link.get("evidence") or []:
        m = RE_EVIDENCE.match(e)
        out.append(m[1].replace(" CAM interval(s)", " CAMs") if m else e)
    return "; ".join(out) or "-"


def _repeats(d):
    """Kept runs of the same owner with exactly this scenario (this one included), and their seeds."""
    try:
        with open(os.path.join(d["directory"], "scenario.toml"), "rb") as f:
            mine = f.read()
    except OSError:
        return 1, 1
    parent = os.path.dirname(os.path.abspath(d["directory"]))
    seeds, count = set(), 0
    for name in os.listdir(parent):
        other = _json(os.path.join(parent, name), "run.json")
        if not other or other.get("owner") != d["run"].get("owner"):
            continue
        try:
            with open(os.path.join(parent, name, "scenario.toml"), "rb") as f:
                same = f.read() == mine
        except OSError:
            continue
        if same:
            count += 1
            seeds.add(other.get("seed"))
    return max(count, 1), max(len(seeds), 1)


def facts(d):
    """The run as numbers, for the conditions and texts of recommendations.toml."""
    score, vehicles, scenario = d["score"], d["vehicles"], d["scenario"]
    n = len(vehicles)
    silent = [s for v in vehicles for s in v["silent"]]
    cfg_max = [v["silent_cfg"][1] or 0 for v in vehicles]
    zones = d["mobility"].get("mix_zones") or []
    dyn = d["mobility"].get("dynamic_mix_zone")
    pki = d["control"].get("pki")
    eav = scenario.get("eavesdropper") if isinstance(scenario.get("eavesdropper"), dict) else {}
    declared = {s.get("name"): s for s in scenario.get("stations", []) if isinstance(s, dict)}
    speeds = {(declared.get(v["name"], {}).get("mobility") or {}).get("speed_kmh") for v in vehicles}
    repeats, seeds = _repeats(d)
    f = {
        "duration_s": round(d["seconds"], 1), "stations": len(d["stations"]), "vehicles": n,
        "changes": sum(v["changes"] for v in vehicles), "changes.rejected": sum(v["rejected"] for v in vehicles),
        "changes.wrapped": sum(v["wrapped"] for v in vehicles),
        "changes.per_vehicle_min": min((v["changes"] for v in vehicles), default=0),
        "silent.periods": len(silent), "silent.mean_s": round(sum(silent) / len(silent), 2) if silent else 0,
        "silent.max_s": max(silent, default=0), "silent.configured_max_s": max(cfg_max, default=0) / 1000,
        "silent.share": round(sum(silent) / (d["seconds"] * n), 4) if silent and d["seconds"] and n else 0,
        "zone.count": len(zones), "zone.changes": sum(d["zone_changes"].values()), "zone.skipped": sum(d["zone_skipped"].values()),
        "zone.min_vehicles": max((z.get("min_vehicles") or 1 for z in zones), default=0),
        "dynamic": int(bool(dyn)), "dynamic.changes": sum(v["dynamic"] for v in vehicles),
        "pki": int(bool(pki)), "pki.batches": (pki or {}).get("batches", 0),
        "pki.starved": sum(v["refill"]["starved"] for v in vehicles if v["refill"]),
        "pool.min": min((v["pool"] for v in vehicles), default=0),
        "speeds.distinct": len(speeds - {None}), "random_turns": sum("random turns" in (v["movement"] or "") for v in vehicles),
        "warnings": len(d["status"].get("warnings") or []), "repeats": repeats, "repeats.seeds": seeds,
        "eavesdropper": int(score is not None), "chance": round(1 / n, 4) if n else 0,
    }
    # without an eavesdropper its facts are 0, so every rule can be evaluated; rules about it ask for eavesdropper==1
    f.update({key: 0 for key in ("links", "links.correct", "links.wrong", "links.correct_share", "links.over_chance",
                                 "pattern.explained", "pattern.share", "pattern.pairings", "followed.longest", "tracks",
                                 "tracks.mixed", "eavesdropper.link_window_s", "eavesdropper.techniques")})
    f.update({f"{kind}.{key}": 0 for kind in ("identifier", "position", "timing") for key in ("links", "correct", "correct_share")})
    if score is not None:
        cars = set(score["stations"])
        tracks = [t for t in score["tracks"] if cars & set(t["stations"])]
        links, correct, wrong = score["links"], score["correct"], score["wrong"]
        pairs = Counter((link["old_station"], link["new_station"]) for link in score["link_list"] if not link["correct"])
        explained = sum(max(c for (o, _), c in pairs.items() if o == old) for old in {o for o, _ in pairs})
        share = correct / links if links else 0
        f.update({
            "links": links, "links.correct": correct, "links.wrong": wrong, "links.correct_share": round(share, 4),
            "links.over_chance": round(share * n, 2) if n else 0,
            "pattern.explained": explained, "pattern.share": round(explained / wrong, 4) if wrong else 0, "pattern.pairings": len(pairs),
            "followed.longest": score["longest_followed"]["changes"],
            "tracks": len(tracks), "tracks.mixed": sum(len(set(t["stations"])) > 1 for t in tracks),
            "eavesdropper.link_window_s": eav.get("link_window", 3.0),
            "eavesdropper.techniques": len(eav.get("link_by", ["position", "timing"])),
        })
        for kind in ("identifier", "position", "timing"):
            k = score["by_technique"].get(kind, {"correct": 0, "wrong": 0})
            total = k["correct"] + k["wrong"]
            f.update({f"{kind}.links": total, f"{kind}.correct": k["correct"],
                      f"{kind}.correct_share": round(k["correct"] / total, 4) if total else 0})
    return f


RE_CONDITION = re.compile(r"\s*([A-Za-z_][\w.]*)\s*(>=|<=|==|!=|>|<)\s*([\w.\-]+)\s*")
RE_PLACEHOLDER = re.compile(r"\{([A-Za-z_][\w.]*)(?::([^{}]*))?\}")
OPS = {">=": lambda a, b: a >= b, "<=": lambda a, b: a <= b, "==": lambda a, b: a == b,
       "!=": lambda a, b: a != b, ">": lambda a, b: a > b, "<": lambda a, b: a < b}


def _holds(condition, f):
    """(True/False, None) or (None, why the condition cannot be evaluated)."""
    m = RE_CONDITION.fullmatch(condition) if isinstance(condition, str) else None
    if not m:
        return None, f"cannot parse {condition!r} (use: fact OP number|fact)"
    left, op, right = m.groups()
    if left not in f:
        return None, f"no fact {left}"
    try:
        value = float(right)
    except ValueError:
        if right not in f:
            return None, f"no fact {right}"
        value = f[right]
    return OPS[op](f[left], value), None


def _fill(text, f):
    def sub(m):
        if m[1] not in f:
            raise KeyError(m[1])
        out = format(f[m[1]], m[2] or "")
        return out[:-1] + " %" if out.endswith("%") else out   # as in the rest of the report
    return RE_PLACEHOLDER.sub(sub, text)


def recommend(f, catalogue=None):
    """(the suggestions that hold: [(title, text)], one line per rule on what became of it)."""
    try:
        with open(catalogue or CATALOGUE, "rb") as file:
            cat = tomllib.load(file)
    except OSError:
        return None, ["no catalogue"]
    except tomllib.TOMLDecodeError as e:
        return None, [f"catalogue not readable: {e}"]
    chosen, notes = [], []
    rules = [r for r in cat.get("rule", []) if isinstance(r, dict)]
    for rule in sorted(rules, key=lambda r: -float(r.get("priority", 0) if isinstance(r.get("priority", 0), (int, float)) else 0)):
        rid = rule.get("id", "?")
        results = [_holds(c, f) for c in (rule.get("when") if isinstance(rule.get("when"), list) else [None])]
        problems = [why for _, why in results if why]
        if not problems and all(ok for ok, _ in results):
            try:
                chosen.append((str(rule.get("title", rid)), _fill(str(rule.get("text", "")), f)))
                notes.append(f"{rid}: holds")
            except (KeyError, ValueError) as e:
                notes.append(f"{rid}: NOT USED, text: {'no fact ' + str(e.args[0]) if isinstance(e, KeyError) else e}")
        elif problems:
            notes.append(f"{rid}: NOT USED, {'; '.join(problems)}")
        else:
            failed = [c for c, (ok, _) in zip(rule["when"], results) if not ok]
            notes.append(f"{rid}: no ({', '.join(failed)})")
    limit = cat.get("limit", 6)
    return chosen[:limit if isinstance(limit, int) and limit > 0 else 6], notes


def build(d, suggestions=None):
    """The report as a list of blocks: (kind, ...) for write_docx."""
    run, status, score, scenario = d["run"], d["status"], d["score"], d["scenario"]
    vehicles, n = d["vehicles"], len(d["vehicles"])
    name = run.get("name") or run.get("scenario") or run.get("id") or "run"
    pki = d["control"].get("pki")
    observer = (status.get("observers") or [None])[0]
    eav = scenario.get("eavesdropper") if isinstance(scenario.get("eavesdropper"), dict) else {}
    changes = sum(v["changes"] for v in vehicles)
    silent = [s for v in vehicles for s in v["silent"]]
    b = [("title", f"{name}: Run Summary")]
    if run.get("description"):
        b.append(("subtitle", run["description"]))

    # ---- header table
    rows = [["Date", (f"{_utc(d['started'])} to {_utc(d['stopped'], '%H:%M:%S')} UTC ({_duration(d['seconds'])} of traffic)"
                      if d["started"] else "not started")],
            ["Run", f"{run.get('id')}, started by {run.get('owner')}, instance {run.get('instance')}"
                    + (f"; stopped: {run['stop_reason']}" if run.get("stop_reason") else "")],
            ["Scenario", "scenario.toml in the run's results" + (f"; mobility seed {run['seed']}" if run.get("seed") is not None else "")
                         + (f"; overrides: {', '.join(run['overrides'])}" if run.get("overrides") else "")],
            ["Station image", str(run.get("image"))]]
    entities = sorted({(st.get("security") or {}).get("entity") or "none" for st in status.get("stations", [])})
    security = ", ".join(entities)
    if pki:
        security += (f"; the run's own PKI (C-ITS-PKI {pki.get('cits_pki') or 'unknown version'}): "
                     + (f"{pki.get('certificates', 0)} authorization tickets issued in {_plural(pki['batches'], 'batch')}"
                        if pki.get("batches") else "no authorization tickets issued") + " after the initial pools")
    if n:
        pools = sorted({v["pool"] for v in vehicles})
        security += f"; initial pool of {'/'.join(str(p) for p in pools)} pseudonym certificates per vehicle"
    rows.append(["Security", security])
    if observer:
        how = _and(["identifiers"] + list(eav.get("link_by", ["position"]) if eav else ["position"]))
        rows.append(["Eavesdropper", f"passive listener on the V2X network; links by {how}"
                                     + (f" ({eav['link_window']:g} s link window)" if eav.get("link_window") else "")])
    rows.append(["Health", _health(d)])
    b.append(("table", ["Item", "Value"], rows, [0.2, 0.8]))

    # ---- 1. purpose
    b.append(("h1", "1. Purpose"))
    b.append(("p", run.get("description") or "No description was given for this run."))
    b.append(("p", "This summary was generated by the simulation service when the run stopped, from the collected results "
                   "(status, container logs and, with an eavesdropper, the scoring against ground truth). It states what was "
                   "measured. The suggested next runs at the end are chosen by fixed rules from those measurements, "
                   "not by judgement."))

    # ---- 2. setup
    b.append(("h1", "2. Setup"))
    rows = []
    for s in d["stations"]:
        role = ("RSU" if s["rsu"] else "OBU") + f", station {s['id']}"
        if not s["rsu"] and s["type"] in STATION_TYPES:
            role += f" ({STATION_TYPES[s['type']]})"
        lat, lon = s["position"]
        move = s["movement"] or (f"static at {lat}, {lon}" if lat and lon else "static")
        if s["pool"]:
            rf = s["refill"]
            pool = f"{s['pool']} initial" + (f", {rf['batch']} more at {rf['refill_at']} unused" if rf else "")
            if s["silent_cfg"][1] is not None:
                pool += "; " + ("silent " if s["silent_cfg"][1] else "") + _silent_text(*s["silent_cfg"])
        else:
            pool = "regular AT (no changes)"
        rows.append([s["name"], role, move, pool])
    b.append(("table", ["Station", "Role", "Movement", "Pseudonym pool"], rows, [0.12, 0.2, 0.4, 0.28]))
    if d["mobility"]:
        rate = (scenario.get("control") or {}).get("mobility_rate_hz")
        b.append(("bullet", [("Movement: ", True),
                             (f"the mobility client publishes each vehicle's position on the control channel"
                              + (f" {rate:g} times per second" if rate else "")
                              + (f"; random choices use seed {d['mobility'].get('seed')}." if d["mobility"].get("seed") is not None else "."),
                              False)]))
    if n:
        scopes = {v["scope"] for v in vehicles}
        scope = ("each change is a full ETSI ID change (certificate, MAC, GeoNetworking address and stationId)"
                 if scopes == {"full"} else "each change replaces " + " / ".join(sorted(str(s) for s in scopes)))
        b.append(("bullet", [("ID changes: ", True), ("; ".join(_triggers(d)) + f". In this run {scope}.", False)]))
        cfg = sorted({_silent_text(*v["silent_cfg"]) for v in vehicles if v["silent_cfg"][1] is not None})
        text = ("configured: " + ", ".join(cfg) if cfg else "not configured in the scenario")
        if silent:
            text += f"; measured {min(silent):.1f}-{max(silent):.1f} s, mean {sum(silent) / len(silent):.1f} s over {len(silent)} periods"
        b.append(("bullet", [("Silent period after a change: ", True), (text + ".", False)]))
    if observer:
        text = "passive listener on the V2X network only (raw frames, no keys, no control channel)."
        if eav:
            text += (f" It links a new identity to a track that went silent by {_and(eav.get('link_by', ['position']))}"
                     + (f", within {eav['link_window']:g} s" if eav.get("link_window") else "")
                     + (f" and {eav['link_distance']:g} m plus speed times gap" if eav.get("link_distance") else "") + ".")
        b.append(("bullet", [("Eavesdropper: ", True), (text, False)]))
        b.append(("bullet", [("Ground truth: ", True),
                             ("the certificate each station used, from the stations' own [PSEUDONYM] log lines, "
                              "which the eavesdropper cannot see.", False)]))

    # ---- 3. results
    b.append(("h1", "3. Results"))
    rows = [["Traffic", _duration(d["seconds"])],
            ["Stations", f"{len(d['stations'])} ({n} with pseudonyms); "
                         + ("all running at stop" if all(s["state"] == "running" for s in d["stations"]) else
                            "not running at stop: " + ", ".join(s["name"] for s in d["stations"] if s["state"] != "running"))]]
    if n:
        per = sorted({v["changes"] for v in vehicles})
        rows.append(["Pseudonym changes", f"{changes}" + (f" ({per[0]}" + (f"-{per[-1]}" if len(per) > 1 else "") + " per vehicle)"
                                                          if changes else "")])
        rejected, wrapped = sum(v["rejected"] for v in vehicles), sum(v["wrapped"] for v in vehicles)
        if rejected:
            rows.append(["Change events rejected", str(rejected)])
        if wrapped:
            rows.append(["Changes back to an already used certificate", f"{wrapped} (the pool wrapped around)"])
        for zone in sorted(set(d["zone_changes"]) | set(d["zone_skipped"])):
            rows.append([f"Mix zone {zone}", f"{d['zone_changes'][zone]} entries with a change event"
                                             + (f", {d['zone_skipped'][zone]} passages without (too few vehicles inside)"
                                                if d["zone_skipped"][zone] else "")])
        if d["mobility"].get("dynamic_mix_zone"):
            rows.append(["Dynamic mix zone change events", str(sum(v["dynamic"] for v in vehicles))])
        if silent and d["seconds"]:
            rows.append(["Time silent", f"{_pct(sum(silent), d['seconds'] * n)} of the vehicles' time "
                                        f"({sum(silent) / n:.0f} s per vehicle on average)"])
    if pki and pki.get("batches"):
        refresh = [v["refill"]["refresh_ms_mean"] for v in vehicles if v["refill"] and v["refill"].get("refresh_ms_mean") is not None]
        starved = sum(v["refill"]["starved"] for v in vehicles if v["refill"])
        rows.append(["Certificate refill", f"{pki['certificates']} tickets in {_plural(pki['batches'], 'batch')}; PKI issue time mean "
                                           f"{pki.get('issue_ms_mean')} ms, max {pki.get('issue_ms_max')} ms"
                                           + (f"; refresh at the stations mean {round(sum(refresh) / len(refresh))} ms" if refresh else "")
                                           + f"; {starved} changes refused for lack of certificates"])
    cars = set(score["stations"]) if score else set()
    tracks = [t for t in score["tracks"] if cars & set(t["stations"])] if score else []
    if score:
        links, correct, wrong = score["links"], score["correct"], score["wrong"]
        rows.append(["Links made by the eavesdropper", f"{links}" + (f" for {changes} changes" if changes else "")])
        rows.append(["Correct links (same vehicle)", f"{correct} ({_pct(correct, links)})"])
        rows.append(["Wrong links (two different vehicles joined)", f"{wrong} ({_pct(wrong, links)})"])
        for kind, k in sorted(score["by_technique"].items()):
            rows.append([f"  of which by {kind}", f"{k['correct'] + k['wrong']}: {k['correct']} correct, {k['wrong']} wrong"])
        rows.append(["Vehicle tracks at the end", f"{len(tracks)} (for {_plural(len(cars), 'real vehicle')})"])
        lf = score["longest_followed"]
        rows.append(["Longest a vehicle was followed", (f"{_plural(lf['changes'], 'change')} in a row ({_short(lf['station'])}, track {lf['track']})"
                                                        if lf["changes"] else "through no change")])
    b.append(("table", ["Measure", "Value"], rows, [0.4, 0.6]))

    section = 1
    if n:
        b.append(("h2", f"3.{section} Per vehicle"))
        section += 1
        rows = []
        for v in vehicles:
            rf = v["refill"]
            rows.append([v["name"], str(v["changes"]), str(v["rejected"]),
                         (f"{len(v['silent'])}, mean {sum(v['silent']) / len(v['silent']):.1f} s" if v["silent"] else "0"),
                         _pct(sum(v["silent"]), d["seconds"]) if v["silent"] else "0 %",
                         (f"{rf['batches']} ({rf['added']} tickets)" if rf else "-"),
                         str(sum(v["container"] in t["stations"] for t in tracks)) if score else "-"])
        b.append(("table", ["Vehicle", "ID changes", "Rejected", "Silent periods", "Time silent", "Refills", "Tracks it appears in"],
                  rows, [0.13, 0.12, 0.11, 0.2, 0.13, 0.17, 0.14]))
    if score is None:
        b.append(("p", "No eavesdropper took part in this run, so there is no linkage to score."))
    elif score["link_list"]:
        b.append(("h2", f"3.{section} Every link, scored against ground truth"))
        section += 1
        rows = [[(link.get("time") or "")[11:19], str(link.get("track")), _short(link["old_station"]), _short(link["new_station"]),
                 link.get("technique", "?"), _evidence(link), "correct" if link["correct"] else "WRONG"]
                for link in score["link_list"]]
        b.append(("table", ["Time (UTC)", "Track", "Old identity", "New identity", "Technique", "Evidence", "Verdict"], rows,
                  [0.12, 0.09, 0.13, 0.13, 0.13, 0.28, 0.12]))
        pairs = Counter((_short(link["old_station"]), _short(link["new_station"])) for link in score["link_list"] if not link["correct"])
        if pairs:
            b.append(("h2", f"3.{section} Wrong links by vehicle pair"))
            section += 1
            b.append(("table", ["Old identity belonged to", "New identity belonged to", "Links"],
                      [[old, new, str(count)] for (old, new), count in sorted(pairs.items(), key=lambda kv: (-kv[1], kv[0]))],
                      [0.4, 0.4, 0.2]))
    if tracks:
        b.append(("h2", f"3.{section} What the eavesdropper's tracks contain"))
        section += 1
        b.append(("p", "Each row is one track at the end of the run; the vehicles are listed in the order its identities were seen."))
        b.append(("table", ["Track", "Identities", "Vehicles behind them, in order"],
                  [[t["track"], str(t["identities"]), " -> ".join(_short(s) for s in t["stations"])] for t in tracks],
                  [0.12, 0.13, 0.75]))

    # ---- observations and conclusion
    obs, conclusion = [], []
    if score and score["links"]:
        chance = f"; picking one of the {len(cars)} vehicles at random is right about {_pct(1, len(cars))} of the time" if len(cars) > 1 else ""
        obs.append(("Linking. ", f"The eavesdropper made {_plural(links, 'link')}, {correct} correct ({_pct(correct, links)}) and "
                                 f"{wrong} wrong{chance}."))
        for kind, k in sorted(score["by_technique"].items()):
            total = k["correct"] + k["wrong"]
            obs.append((f"By {kind}. ", f"{total} links, {k['correct']} correct ({_pct(k['correct'], total)})."))
        lf = score["longest_followed"]
        obs.append(("Following a vehicle. ", (f"The longest correct stretch was {_plural(lf['changes'], 'change')} in a row "
                                              f"({_short(lf['station'])}, track {lf['track']})." if lf["changes"]
                                              else "No vehicle was followed correctly through a change.")))
        mixed = sum(len(set(t["stations"])) > 1 for t in tracks)
        obs.append(("Tracks. ", f"{len(tracks)} tracks for {_plural(len(cars), 'vehicle')}; {mixed} of them join identities of "
                                "more than one vehicle, so a route rebuilt from such a track belongs to no single vehicle."))
        if wrong >= 8:
            # do the mistakes follow one fixed pairing (old car -> always the same new car)? Then an attacker can undo them.
            best = sum(max(c for (o, _), c in pairs.items() if o == old) for old in {o for o, _ in pairs})
            obs.append(("Pattern of the mistakes. ",
                        f"A fixed rule 'old vehicle X is really new vehicle Y' explains {best} of the {wrong} wrong links "
                        f"({_pct(best, wrong)}), spread over {_plural(len(pairs), 'pairing')}. "
                        + ("The mistakes are that regular, so an attacker who knows the traffic pattern could undo most of them."
                           if best / wrong >= 0.8 else "No single rule turns most of them into correct links.")))
        conclusion.append(f"In this run of {_duration(d['seconds'])} the eavesdropper linked {links} of {changes} pseudonym changes "
                          f"and got {correct} of them right ({_pct(correct, links)})"
                          + (f", against about {_pct(1, len(cars))} for guessing among {len(cars)} vehicles" if len(cars) > 1 else "")
                          + f". The longest it followed one vehicle correctly was {_plural(lf['changes'], 'change')}.")
    elif score:
        if changes:
            obs.append(("Linking. ", f"The eavesdropper linked none of the {_plural(changes, 'pseudonym change')}: every new "
                                     "identity looked like a new vehicle to it."))
        conclusion.append(f"In this run of {_duration(d['seconds'])} there were {_plural(changes, 'pseudonym change')} and the "
                          f"eavesdropper linked none of them; it ended with {len(tracks)} tracks for {_plural(len(cars), 'vehicle')}.")
    else:
        conclusion.append(f"The run lasted {_duration(d['seconds'])} with {_plural(len(d['stations']), 'station')} and "
                          f"{_plural(changes, 'pseudonym change')}. Without an eavesdropper it gives no measure of linkability.")
    skipped = sum(d["zone_skipped"].values())
    if skipped:
        obs.append(("Mix zone threshold. ", f"{skipped} passages through a mix zone caused no change because too few vehicles "
                                            f"were inside ({sum(d['zone_changes'].values())} entries did)."))
    if n and not changes:
        obs.append(("No changes. ", "No vehicle changed its pseudonym, so there was nothing to link. Check the trigger (control client mode, mix zones and their "
                                    "minimum number of vehicles) if changes were expected."))
    if silent and d["seconds"]:
        share = _pct(sum(silent), d["seconds"] * n)
        obs.append(("Safety cost. ", f"While silent a vehicle sends no CAMs: the vehicles were off the air for {share} of the run."))
        conclusion.append(f"The silent periods took the vehicles off the air for {share} of the time.")
    for w in (status.get("warnings") or [])[:10]:
        obs.append(("Warning at stop. ", w))
    if (status.get("warnings") or []):
        conclusion.append(f"The status at stop had {_plural(len(status['warnings']), 'warning')}, listed above.")
    if obs:
        b.append(("h2", f"3.{section} Observations"))
        b += [("bullet", [(head, True), (text, False)]) for head, text in obs]
    b.append(("h1", "4. Conclusion"))
    b.append(("p", " ".join(conclusion)))

    # ---- 5. limitations
    b.append(("h1", "5. Limitations of this run"))
    limits = [f"One run of {_duration(d['seconds'])}"
              + (f" with {_plural(n, 'vehicle')}" if n else "")
              + (f" and one mobility seed ({run['seed']})" if run.get("seed") is not None else "")
              + "; the counts are not statistics. Repeated runs with other seeds are needed."]
    if d["mobility"]:
        limits.append("Idealized motion: exact positions without GPS noise, and each vehicle at its configured constant speed.")
    if n > 1:
        limits.append("The vehicles send the same dimensions in their CAMs; real vehicles differ, "
                      "which an eavesdropper can use as a fingerprint.")
    if observer:
        limits.append("Simple attacker: the eavesdropper decides each new identity greedily. It does not use heading, turn "
                      "statistics, multiple hypotheses or vehicle attributes; a stronger one could do better.")
    if d["mobility"].get("mix_zones") or d["mobility"].get("dynamic_mix_zone"):
        limits.append("Mix zone changes are triggered by the mobility client from the vehicles' true positions; a real vehicle "
                      "would have to decide from its own position and the CAMs it receives.")
    if any(v["wrapped"] for v in vehicles):
        limits.append("Pool wrap-around: some changes returned to an already used certificate, which an eavesdropper can "
                      "recognise by identifier; reuse is a linking risk of its own.")
    b += [("bullet", text) for text in limits]

    # ---- 6. suggested next runs (None: no catalogue)
    if suggestions is not None:
        b.append(("h1", "6. Suggested next runs"))
        if suggestions:
            b.append(("p", "Chosen by fixed rules (service/recommendations.toml) from the measurements above; each names the "
                           "number that triggered it. They are starting points, not conclusions: one run is thin evidence."))
            b += [("number", [(title.rstrip() + " ", True), (text, False)]) for title, text in suggestions]
        else:
            b.append(("p", "No rule of the catalogue (service/recommendations.toml) applies to this run."))
    return b


# ---------------------------------------------------------------- docx
W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
PAGE_W, MARGIN = 12240, 1247
ACCENT, BAND = "4F81BD", "D3DFEE"

CONTENT_TYPES = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
    '<Default Extension="xml" ContentType="application/xml"/>'
    '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
    '<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>'
    '<Override PartName="/word/numbering.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.numbering+xml"/>'
    '<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>'
    '</Types>')
RELS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
    '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>'
    '</Relationships>')
DOC_RELS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
    '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/numbering" Target="numbering.xml"/>'
    '</Relationships>')


def _style(style_id, name, ppr="", rpr="", based="Normal", default=False):
    return (f'<w:style w:type="paragraph" w:styleId="{style_id}"' + (' w:default="1"' if default else "") + f'><w:name w:val="{name}"/>'
            + (f'<w:basedOn w:val="{based}"/><w:next w:val="Normal"/>' if not default else "") + "<w:qFormat/>"
            + (f"<w:pPr>{ppr}</w:pPr>" if ppr else "") + (f"<w:rPr>{rpr}</w:rPr>" if rpr else "") + "</w:style>")


STYLES = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    f'<w:styles xmlns:w="{W_NS}"><w:docDefaults><w:rPrDefault><w:rPr>'
    '<w:rFonts w:ascii="Calibri" w:hAnsi="Calibri" w:eastAsia="Calibri" w:cs="Calibri"/><w:sz w:val="22"/><w:szCs w:val="22"/>'
    '<w:lang w:val="en-GB"/></w:rPr></w:rPrDefault>'
    '<w:pPrDefault><w:pPr><w:spacing w:after="200" w:line="276" w:lineRule="auto"/></w:pPr></w:pPrDefault></w:docDefaults>'
    + _style("Normal", "Normal", default=True)
    + _style("Title", "Title",
             f'<w:pBdr><w:bottom w:val="single" w:sz="8" w:space="4" w:color="{ACCENT}"/></w:pBdr><w:spacing w:after="300" w:line="240" w:lineRule="auto"/>',
             '<w:color w:val="17365D"/><w:spacing w:val="5"/><w:sz w:val="52"/><w:szCs w:val="52"/>')
    + _style("Heading1", "heading 1", '<w:keepNext/><w:spacing w:before="480" w:after="0"/><w:outlineLvl w:val="0"/>',
             '<w:b/><w:color w:val="365F91"/><w:sz w:val="28"/><w:szCs w:val="28"/>')
    + _style("Heading2", "heading 2", '<w:keepNext/><w:spacing w:before="200" w:after="0"/><w:outlineLvl w:val="1"/>',
             f'<w:b/><w:color w:val="{ACCENT}"/><w:sz w:val="26"/><w:szCs w:val="26"/>')
    + _style("ListBullet", "List Bullet", '<w:numPr><w:ilvl w:val="0"/><w:numId w:val="1"/></w:numPr><w:contextualSpacing/>')
    + _style("ListNumber", "List Number", '<w:numPr><w:ilvl w:val="0"/><w:numId w:val="2"/></w:numPr><w:contextualSpacing/>')
    + "</w:styles>")
NUMBERING = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    f'<w:numbering xmlns:w="{W_NS}"><w:abstractNum w:abstractNumId="0"><w:multiLevelType w:val="singleLevel"/>'
    '<w:lvl w:ilvl="0"><w:start w:val="1"/><w:numFmt w:val="bullet"/><w:lvlText w:val="&#8226;"/><w:lvlJc w:val="left"/>'
    '<w:pPr><w:ind w:left="360" w:hanging="360"/></w:pPr></w:lvl></w:abstractNum>'
    '<w:abstractNum w:abstractNumId="1"><w:multiLevelType w:val="singleLevel"/>'
    '<w:lvl w:ilvl="0"><w:start w:val="1"/><w:numFmt w:val="decimal"/><w:lvlText w:val="%1."/><w:lvlJc w:val="left"/>'
    '<w:pPr><w:ind w:left="360" w:hanging="360"/></w:pPr></w:lvl></w:abstractNum>'
    '<w:num w:numId="1"><w:abstractNumId w:val="0"/></w:num><w:num w:numId="2"><w:abstractNumId w:val="1"/></w:num></w:numbering>')


def _runs(content, rpr=""):
    """content: text, or a list of (text, bold)."""
    parts = [(content, False)] if isinstance(content, str) else content
    out = ""
    for text, bold in parts:
        # XML 1.0 has no control characters; log text may
        text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", str(text))
        props = ("<w:b/>" if bold else "") + rpr
        out += "<w:r>" + (f"<w:rPr>{props}</w:rPr>" if props else "") + f'<w:t xml:space="preserve">{escape(text)}</w:t></w:r>'
    return out


def _paragraph(content, style=None, rpr=""):
    return "<w:p>" + (f'<w:pPr><w:pStyle w:val="{style}"/></w:pPr>' if style else "") + _runs(content, rpr) + "</w:p>"


def _table(header, rows, widths):
    """A grid like Word's 'Light Grid Accent 1': bold header row and first column, banded rows."""
    total = PAGE_W - 2 * MARGIN
    dxa = [int(total * w) for w in widths]
    border = "".join(f'<w:{side} w:val="single" w:sz="8" w:space="0" w:color="{ACCENT}"/>'
                     for side in ("top", "left", "bottom", "right", "insideH", "insideV"))
    out = (f'<w:tbl><w:tblPr><w:tblW w:w="{sum(dxa)}" w:type="dxa"/><w:jc w:val="center"/><w:tblBorders>{border}</w:tblBorders>'
           '<w:tblLayout w:type="fixed"/><w:tblCellMar><w:left w:w="108" w:type="dxa"/><w:right w:w="108" w:type="dxa"/></w:tblCellMar>'
           "</w:tblPr><w:tblGrid>" + "".join(f'<w:gridCol w:w="{w}"/>' for w in dxa) + "</w:tblGrid>")
    for r, row in enumerate([header] + rows):
        out += "<w:tr>" + ('<w:trPr><w:tblHeader/></w:trPr>' if r == 0 else "")
        for c, cell in enumerate(row):
            props = f'<w:tcW w:w="{dxa[c]}" w:type="dxa"/>'
            if r == 0:
                props += f'<w:tcBorders><w:bottom w:val="single" w:sz="18" w:space="0" w:color="{ACCENT}"/></w:tcBorders>'
            elif r % 2:
                props += f'<w:shd w:val="clear" w:color="auto" w:fill="{BAND}"/>'
            out += (f"<w:tc><w:tcPr>{props}</w:tcPr>"
                    '<w:p><w:pPr><w:spacing w:after="0" w:line="240" w:lineRule="auto"/></w:pPr>'
                    + _runs([(cell, r == 0 or c == 0)], '<w:sz w:val="19"/><w:szCs w:val="19"/>') + "</w:p></w:tc>")
        out += "</w:tr>"
    return out + "</w:tbl><w:p/>"


def write_docx(blocks, path, title="Run summary"):
    body = ""
    for kind, *args in blocks:
        if kind == "table":
            body += _table(*args)
        elif kind == "subtitle":
            body += _paragraph(args[0], rpr="<w:i/>")
        else:
            body += _paragraph(args[0], {"title": "Title", "h1": "Heading1", "h2": "Heading2", "bullet": "ListBullet",
                                         "number": "ListNumber"}.get(kind))
    document = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                f'<w:document xmlns:w="{W_NS}"><w:body>{body}'
                f'<w:sectPr><w:pgSz w:w="{PAGE_W}" w:h="15840"/><w:pgMar w:top="1134" w:right="{MARGIN}" w:bottom="1134" '
                f'w:left="{MARGIN}" w:header="720" w:footer="720" w:gutter="0"/></w:sectPr></w:body></w:document>')
    stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    core = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
            'xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" '
            'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
            f"<dc:title>{escape(title)}</dc:title><dc:creator>vnap-secure simulation service</dc:creator>"
            f'<dcterms:created xsi:type="dcterms:W3CDTF">{stamp}</dcterms:created>'
            f'<dcterms:modified xsi:type="dcterms:W3CDTF">{stamp}</dcterms:modified></cp:coreProperties>')
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in (("[Content_Types].xml", CONTENT_TYPES), ("_rels/.rels", RELS), ("docProps/core.xml", core),
                           ("word/document.xml", document), ("word/_rels/document.xml.rels", DOC_RELS),
                           ("word/styles.xml", STYLES), ("word/numbering.xml", NUMBERING)):
            z.writestr(name, data)


def write_report(directory, catalogue=None):
    """Write report.docx into a results directory; returns its path."""
    d = gather(directory)
    path = os.path.join(directory, REPORT_NAME)
    suggestions, _ = recommend(facts(d), catalogue)
    write_docx(build(d, suggestions), path, title=f"{d['run'].get('name') or d['run'].get('id') or 'run'}: Run Summary")
    return path


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if a != "--rules"]
    if len(args) != 1 or not os.path.isdir(args[0]):
        sys.exit("usage: python3 -m vnapapi.report [--rules] <results directory of a run>\n"
                 "  --rules: list the run's facts and what each rule of recommendations.toml made of them; write nothing")
    if "--rules" in sys.argv[1:]:
        run_facts = facts(gather(args[0]))
        print("facts:")
        for key in sorted(run_facts):
            print(f"  {key} = {run_facts[key]:g}")
        chosen, notes = recommend(run_facts)
        print(f"rules ({CATALOGUE}), by priority:")
        for note in notes:
            print(f"  {note}")
        print(f"printed in the report: {len(chosen or [])}")
        sys.exit(1 if any("NOT USED" in note or "catalogue" in note for note in notes) else 0)
    print(write_report(args[0]))
