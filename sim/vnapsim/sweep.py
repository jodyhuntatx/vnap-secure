"""Sweeps: one scenario over factor levels and seeds, each run saved like `vnapctl collect` (phase 3 of the
overhead design, PRD section 10.4), and the cost per change from them.

  vnapctl sweep DIR --plan PLAN [--dry-run]   run what is not yet in DIR, then analyse
  vnapctl sweep DIR --analyse                 analyse again (sweep.json, sweep.txt)

A plan (TOML) names the runs:

  description = "..."
  duration_s  = 180                  # observation time of each run, after it is ready
  seeds       = [101, 102, 103]      # control.mobility_seed: every cell drives the same paths per seed
  set         = ["image=vnap:r2-p21"]   # overrides for every run

  [[group]]
  name     = "periodic"
  scenario = "crossing:periodic"
  baseline = ['control.client.mode="manual"']    # the run without pseudonym changes, one per seed
  rate_factor = "interval"                        # the factor that sets the change rate (optional)
  fit_exclude = ["interval-5_silence-6-12"]       # cells in the table but not in the slopes (optional)
  [group.factors.interval]                        # a cell per combination of factor levels
  "60" = ["control.client.interval=60"]
  "10" = ["control.client.interval=10"]
  [group.factors.silence]
  none = ["defaults.pseudonyms.silent_min_ms=0", "defaults.pseudonyms.silent_max_ms=0"]

Runs go to <out>/<group>/<cell>/seed-<seed>/, cells named "baseline" or "interval-60_silence-none". A run
that has metrics.json is not run again, so a stopped sweep resumes where it was; a failed one leaves
sweep-error.txt and is retried on the next start.

The analysis reads every run's overhead.json, fits each measure against the change rate per seed (least
squares over the baseline and the cells that differ only in the change rate), and reports the slope as the
cost per change with its spread over seeds. Measures of a vehicle are fitted against its own change rate
(changes per second per vehicle); the RSU's and the PKI's against the run's (changes per second of all
vehicles). Without a factor that sets the rate (a mix zone, where the laps set it), each cell against the
baseline gives the same slope from two points. A cell where the effect saturates (vehicles changing about
as often as their silent period lasts are silent nearly all the time) is not on the line; the plan's
fit_exclude leaves such cells out of the slopes and marks them in the table.
"""

import itertools
import json
import os
import statistics
import subprocess
import sys
import time
import tomllib

HERE = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
VNAPCTL = os.path.join(HERE, "vnapctl")

# (key in the run's row, label, unit of the slope, which change rate it is fitted against)
MEASURES = [
    ("cpu_vehicle_ms_per_s", "vehicle CPU", "ms per change", "vehicle"),
    ("cpu_rsu_ms_per_s", "RSU CPU", "ms per change", "run"),
    ("cpu_pki_ms_per_s", "PKI CPU", "ms per change", "run"),
    ("rsu_verifies_per_s", "RSU verifications", "per change", "run"),
    ("stale_share", "stale share", "s per change", "vehicle"),
    ("delivery_ratio", "delivery ratio", "", None),
    ("cam_bytes_mean", "mean CAM size", "", None),
    ("tracking_success", "tracking success", "", None),
    ("chain_checks_per_change", "chain checks per change", "", None),
    ("gap_ms_mean", "reception gap", "", None),
]


class PlanError(ValueError):
    pass


def load_plan(path):
    with open(path, "rb") as f:
        text = f.read()
    plan = tomllib.loads(text.decode())
    plan["_text"] = text.decode()
    for key in ("duration_s", "seeds", "group"):
        if key not in plan:
            raise PlanError(f"{path}: missing '{key}'")
    for g in plan["group"]:
        for key in ("name", "scenario"):
            if key not in g:
                raise PlanError(f"{path}: a [[group]] without '{key}'")
    return plan


def runs(plan):
    """Every run of the plan: dicts with group, cell, levels ({factor: level}), seed and sets."""
    out = []
    for g in plan["group"]:
        factors = g.get("factors") or {}
        cells = [("baseline", None, list(g.get("baseline") or []))] if "baseline" in g else []
        for combo in itertools.product(*[list(levels.items()) for levels in factors.values()]):
            levels = {f: lv for f, (lv, _) in zip(factors, combo)}
            cells.append(("_".join(f"{f}-{lv}" for f, lv in levels.items()), levels,
                          [s for _, sets in combo for s in sets]))
        for seed in plan["seeds"]:
            for cell, levels, sets in cells:
                out.append({"group": g["name"], "scenario": g["scenario"], "cell": cell, "levels": levels, "seed": seed,
                            "sets": list(plan.get("set") or []) + list(g.get("set") or []) + sets
                            + [f"control.mobility_seed={seed}"]})
    return out


def run_dir(out, r):
    return os.path.join(out, r["group"], r["cell"], f"seed-{r['seed']}")


def _vnapctl(*args, timeout=600):
    p = subprocess.run([VNAPCTL, *args], capture_output=True, text=True, timeout=timeout)
    return p.returncode, p.stdout + p.stderr


def run_one(r, d, duration, instance, log):
    """Start, observe, collect, stop. Returns None or an error text."""
    os.makedirs(d, exist_ok=True)
    inst = ["--instance", str(instance)]
    _vnapctl("-q", "down", r["scenario"], *inst)   # leftovers of an interrupted sweep
    code, text = _vnapctl("-q", "up", r["scenario"], *inst, "--wait", "60",
                          *[a for s in r["sets"] for a in ("--set", s)])
    if code not in (0, 4):   # 4: up but not every station ready; the analysis says what was missing
        _vnapctl("-q", "down", r["scenario"], *inst)
        return f"up failed ({code}):\n{text}"
    log(f"  running {duration:g} s ...")
    time.sleep(duration)
    code, text = _vnapctl("-q", "collect", d, *inst, timeout=900)
    _vnapctl("-q", "down", r["scenario"], *inst)
    if not os.path.isfile(os.path.join(d, "metrics.json")):
        return f"collect failed ({code}):\n{text}"
    with open(os.path.join(d, "sweep-run.json"), "w") as f:
        json.dump({**r, "duration_s": duration, "collected_at": time.time()}, f, indent=1)
    return None


def run_plan(plan, out, instance=0, dry_run=False, log=print):
    todo = runs(plan)
    done = [r for r in todo if os.path.isfile(os.path.join(run_dir(out, r), "metrics.json"))]
    left = [r for r in todo if r not in done]
    per_run = plan["duration_s"] + 75
    log(f"{len(todo)} run(s), {len(done)} done, {len(left)} to go: about {len(left) * per_run / 3600:.1f} h")
    if dry_run:
        for r in left:
            log(f"  {r['group']}/{r['cell']}/seed-{r['seed']}: vnapctl up {r['scenario']} "
                + " ".join(f"--set '{s}'" for s in r["sets"]))
        return 0
    os.makedirs(out, exist_ok=True)
    with open(os.path.join(out, "plan.toml"), "w") as f:
        f.write(plan["_text"])
    failed = 0
    for i, r in enumerate(left, 1):
        d = run_dir(out, r)
        log(f"[{i}/{len(left)}] {r['group']}/{r['cell']}/seed-{r['seed']}")
        error = run_one(r, d, plan["duration_s"], instance, log)
        path = os.path.join(d, "sweep-error.txt")
        if error:
            failed += 1
            with open(path, "w") as f:
                f.write(error)
            log(f"  FAILED: {error.splitlines()[0]}")
        elif os.path.exists(path):
            os.remove(path)
    log(f"{len(left) - failed} run(s) collected, {failed} failed")
    return 1 if failed else 0


# ------------------------------------------------------------------------------------------- analysis
def row(d):
    """The figures of one collected run, or None."""
    try:
        with open(os.path.join(d, "overhead.json")) as f:
            o = json.load(f)
        with open(os.path.join(d, "sweep-run.json")) as f:
            r = json.load(f)
    except (OSError, ValueError):
        return None
    s = o["summary"]
    cpu = o.get("cpu") or {}
    span = max([st["span_s"] for st in cpu.get("stations", []) if st.get("span_s")] or [o.get("duration_s") or 0])
    vehicles = s.get("vehicles") or 0
    rsu = next((st for st in (o.get("counters") or {}).get("stations", [])
                if any(c["station"] == st["station"] and c["role"] == "rsu" for c in cpu.get("stations", []))), None)
    out = {"group": r["group"], "cell": r["cell"], "levels": r["levels"], "seed": r["seed"],
           "changes": s.get("changes") or 0, "vehicles": vehicles, "span_s": span,
           "rate_vehicle": (s.get("changes") or 0) / vehicles / span if vehicles and span else 0.0,
           "rate_run": (s.get("changes") or 0) / span if span else 0.0,
           "rsu_verifies_per_s": rsu["verifies_per_s"] if rsu else None,
           "outages": len(o.get("outages") or [])}
    for key, *_ in MEASURES:
        if key in s:
            out[key] = s[key]
    return out


def fit(points):
    """Least-squares slope and intercept of [(x, y)]; None with fewer than two distinct x."""
    pts = [(x, y) for x, y in points if x is not None and y is not None]
    if len({x for x, _ in pts}) < 2:
        return None
    mx, my = statistics.mean(x for x, _ in pts), statistics.mean(y for _, y in pts)
    sxx = sum((x - mx) ** 2 for x, _ in pts)
    slope = sum((x - mx) * (y - my) for x, y in pts) / sxx
    return slope, my - slope * mx


def spread(values):
    vals = [v for v in values if v is not None]
    if not vals:
        return None
    return {"mean": statistics.mean(vals), "sd": statistics.stdev(vals) if len(vals) > 1 else None, "n": len(vals),
            "values": vals}


def analyse(out, plan=None):
    rows = []
    for root, _, files in os.walk(out):
        if "sweep-run.json" in files and "overhead.json" in files:
            r = row(root)
            if r:
                rows.append(r)
    rate_factor = {g["name"]: g.get("rate_factor") for g in (plan or {}).get("group", [])}
    exclude = {g["name"]: set(g.get("fit_exclude", [])) for g in (plan or {}).get("group", [])}
    order = {}   # cells in the plan's order
    for r in (runs(plan) if plan else []):
        order.setdefault((r["group"], r["cell"]), len(order))
    groups = {}
    for r in rows:
        groups.setdefault(r["group"], []).append(r)
    result = {"runs": len(rows), "groups": []}
    for name, rs in sorted(groups.items()):
        rf = rate_factor.get(name)
        cells = {}
        for r in rs:
            cells.setdefault(r["cell"], []).append(r)
        cell_table = []
        for cell, crs in sorted(cells.items(), key=lambda kv: (order.get((name, kv[0]), len(order)), kv[0])):
            cell_table.append({"cell": cell, "levels": crs[0]["levels"], "runs": len(crs),
                               "fitted": cell not in exclude.get(name, set()),
                               "changes_per_min_vehicle": spread([60 * r["rate_vehicle"] for r in crs]),
                               "outages": sum(r["outages"] for r in crs),
                               **{key: spread([r.get(key) for r in crs]) for key, *_ in MEASURES}})
        # series: cells that differ only in the rate factor (all cells, if there is none), each with the baseline
        series = {}
        for r in rs:
            if r["cell"] == "baseline" or r["cell"] in exclude.get(name, set()):
                continue
            key = tuple(sorted((f, lv) for f, lv in (r["levels"] or {}).items() if f != rf)) if rf else (("cell", r["cell"]),)
            series.setdefault(key, []).append(r)
        slopes = []
        for key, srs in sorted(series.items()):
            members = srs + [r for r in rs if r["cell"] == "baseline"]
            entry = {"series": dict(key), "measures": {}}
            for m, label, unit, against in MEASURES:
                if against is None:
                    continue
                per_seed = []
                for seed in sorted({r["seed"] for r in members}):
                    f = fit([(r["rate_vehicle"] if against == "vehicle" else r["rate_run"], r.get(m))
                             for r in members if r["seed"] == seed])
                    if f:
                        per_seed.append(f[0])
                entry["measures"][m] = {"label": label, "unit": unit, "against": against, "slope": spread(per_seed)}
            slopes.append(entry)
        result["groups"].append({"group": name, "rate_factor": rf, "cells": cell_table, "slopes": slopes})
    return result


def write_analysis(out, plan=None):
    result = analyse(out, plan)
    with open(os.path.join(out, "sweep.json"), "w") as f:
        json.dump(result, f, indent=1)
    text = format_text(result)
    with open(os.path.join(out, "sweep.txt"), "w") as f:
        f.write(text + "\n")
    return result, text


def _fmt(sp, scale=1.0, digits=1):
    if not sp:
        return "-"
    m = sp["mean"] * scale
    sd = "" if sp["sd"] is None else f" ± {sp['sd'] * scale:.{digits}f}"
    return f"{m:.{digits}f}{sd}"


def format_text(result):
    lines = [f"{result['runs']} run(s)"]
    for g in result["groups"]:
        lines += ["", f"group {g['group']}" + (f" (change rate set by '{g['rate_factor']}')" if g["rate_factor"] else "")]
        lines.append(f"  {'cell':<28} {'runs':>4} {'ch/min':>7} {'veh CPU':>9} {'RSU CPU':>9} {'PKI CPU':>8} "
                     f"{'stale %':>8} {'track %':>8} {'gap ms':>7} {'outages':>7}")
        for c in g["cells"]:
            cell = c["cell"] + ("" if c.get("fitted", True) else " *")
            lines.append(f"  {cell:<28} {c['runs']:>4} {_fmt(c['changes_per_min_vehicle'], digits=2):>7} "
                         f"{_fmt(c['cpu_vehicle_ms_per_s']):>9} {_fmt(c['cpu_rsu_ms_per_s']):>9} "
                         f"{_fmt(c['cpu_pki_ms_per_s'], digits=2):>8} {_fmt(c['stale_share'], 100):>8} "
                         f"{_fmt(c['tracking_success'], 100, 0):>8} {_fmt(c['gap_ms_mean'], digits=0):>7} {c['outages']:>7}")
        if not all(c.get("fitted", True) for c in g["cells"]):
            lines.append("  * not in the slopes (fit_exclude: the effect saturates)")
        lines.append("  cost per change (slope against the change rate, mean ± sd over seeds):")
        for s in g["slopes"]:
            name = ", ".join(f"{k} {v}" for k, v in s["series"].items()) or "all"
            parts = []
            for m, e in s["measures"].items():
                if e["slope"]:
                    scale, digits = (1.0, 1) if "CPU" in e["label"] else (1.0, 2)
                    parts.append(f"{e['label']} {_fmt(e['slope'], scale, digits)} {e['unit']}")
            lines.append(f"    {name}: " + "; ".join(parts))
    return "\n".join(lines)


def main(args, log=print):
    if args.analyse:
        path = args.plan or os.path.join(args.dir, "plan.toml")
        plan = load_plan(path) if os.path.isfile(path) else None
        result, text = write_analysis(args.dir, plan)
        print(json.dumps(result, indent=1) if args.json else text)
        return 0
    if not args.plan:
        log("vnapctl sweep: --plan PLAN is needed (or --analyse)")
        return 2
    plan = load_plan(args.plan)
    code = run_plan(plan, args.dir, args.instance if args.instance != "auto" else 0, args.dry_run, log)
    if not args.dry_run:
        _, text = write_analysis(args.dir, plan)
        log(text)
    return code


if __name__ == "__main__":
    sys.exit("use: vnapctl sweep")
