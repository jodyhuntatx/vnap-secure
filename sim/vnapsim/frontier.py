"""The joint analysis of privacy against safety (phase 6 of the overhead design, PRD 10.9.6 and 10.9.7).

  vnapctl frontier DIR [--json]      DIR: a sweep (vnapctl sweep) whose runs have overhead.json

Each group of the sweep's plan is a condition (a traffic setting); each cell but the baseline is a policy.
Per run the two primary measures are the tracking success of the strongest attacker and the stale share
(PRD 10.9.3). The attackers are the eavesdropper as it ran ("A0") and the guessing attacker replayed on
the same frames ("guess", vnapsim.guessing); the strongest is the one with the higher mean for the policy.

- Unit: the run. Means come with 95 % intervals from a bootstrap over the seeds (BOOTSTRAP resamples).
- Hard limit: a policy that lost or delayed warnings beyond what its silent periods explain
  (denm_unexplained) is reported but left off the frontier.
- Frontier: a policy is dominated when another is no worse on both primary measures and better on at
  least one by more than chance: the bootstrap interval of the paired difference over seeds excludes 0.
- Sensitivity: the frontier again with the stale threshold at 0.5 s and 2 s, and with each attacker alone.
- Seeds: the number of seeds per policy for a 95 % interval of tracking success of at most +/- HALF_WIDTH,
  from the spread over the seeds run (n = (1.96 sd / HALF_WIDTH)^2), for the strongest attacker and for
  each attacker alone (the sensitivity frontiers use them): the largest of these.

Writes frontier.json, frontier.txt and frontier-<condition>.svg (stale share across, tracking success up,
the frontier through the undominated policies, the guessing attacker's level as hollow markers).
"""

import json
import math
import os
import random
import statistics

from .overhead import load_or_analyse
from .sweep import load_plan, paused

BOOTSTRAP = 2000
HALF_WIDTH = 0.1
STALE = {"1.0": "stale_share", "0.5": "stale_share_0_5", "2.0": "stale_share_2_0"}
ATTACKERS = {"A0": "tracking_success", "guess": "guess_tracking_success"}
SECONDARY = [("conflict_exposure_s", "conflict exposure (s)"), ("conflict_missed", "conflicts missed"),
             ("denm_lost", "warnings lost"), ("denm_delayed", "warnings delayed"), ("delivery_ratio", "delivery ratio"),
             ("gap_ms_mean", "reception gap (ms)"), ("pos_error_m_p95_extended", "position error p95, extended (m)"),
             ("followed_s_max", "longest time followed (s)"), ("sync_ratio", "synchronized changes")]


def read_runs(out):
    """[{group, cell, seed, summary}] of every collected run of the sweep, without paused ones."""
    rows = []
    for root, _, files in os.walk(out):
        if "sweep-run.json" not in files or "overhead.json" not in files or paused(root):
            continue
        with open(os.path.join(root, "sweep-run.json")) as f:
            r = json.load(f)
        o = load_or_analyse(root)   # recomputed when older: the guessing attacker and stale thresholds are newer
        rows.append({"group": r["group"], "cell": r["cell"], "seed": r["seed"], "summary": o.get("summary") or {},
                     "version": o.get("version")})
    return rows


def _boot_indices(n, rng):
    return [rng.randrange(n) for _ in range(n)]


def interval(values, seed="ci"):
    """Mean and 95 % bootstrap interval over runs; None without values."""
    vals = [v for v in values if v is not None]
    if not vals:
        return None
    rng = random.Random(seed)
    means = sorted(statistics.mean(vals[i] for i in _boot_indices(len(vals), rng)) for _ in range(BOOTSTRAP))
    return {"mean": statistics.mean(vals), "lo": means[int(0.025 * BOOTSTRAP)], "hi": means[int(0.975 * BOOTSTRAP) - 1],
            "sd": statistics.stdev(vals) if len(vals) > 1 else None, "n": len(vals)}


def paired_better(a, b, seed="diff"):
    """Is policy a better (lower) than b beyond chance? a, b: {seed: value}. The bootstrap interval of the
    mean of b - a over the seeds both have lies above 0."""
    seeds = sorted(set(a) & set(b))
    diffs = [b[s] - a[s] for s in seeds if a[s] is not None and b[s] is not None]
    if len(diffs) < 2:
        return False
    rng = random.Random(seed)
    means = sorted(statistics.mean(diffs[i] for i in _boot_indices(len(diffs), rng)) for _ in range(BOOTSTRAP))
    return means[int(0.025 * BOOTSTRAP)] > 0


def frontier(policies, track_key, stale_key):
    """Names of the undominated policies among those within the hard limit."""
    has = lambda p, k: any(v is not None for v in p["per_seed"][k].values())   # noqa: E731
    eligible = [p for p in policies if p["within_limit"] and has(p, track_key) and has(p, stale_key)]
    out = []
    for p in eligible:
        dominated = False
        for q in eligible:
            if q is p:
                continue
            mt = lambda x, k: statistics.mean(v for v in x["per_seed"][k].values() if v is not None)   # noqa: E731
            no_worse = mt(q, track_key) <= mt(p, track_key) and mt(q, stale_key) <= mt(p, stale_key)
            if no_worse and (paired_better(q["per_seed"][track_key], p["per_seed"][track_key])
                             or paired_better(q["per_seed"][stale_key], p["per_seed"][stale_key])):
                dominated = True
                break
        if not dominated:
            out.append(p["policy"])
    return out


def seeds_needed(sd, half_width=HALF_WIDTH):
    return None if sd is None else max(2, math.ceil((1.96 * sd / half_width) ** 2))


def analyse(out, plan=None):
    rows = read_runs(out)
    order = {}
    for g in (plan or {}).get("group", []):
        for i, c in enumerate(g.get("_cells", [])):
            order[(g["name"], c)] = i
    result = {"runs": len(rows), "bootstrap": BOOTSTRAP, "half_width": HALF_WIDTH, "conditions": []}
    for group in sorted({r["group"] for r in rows}):
        rs = [r for r in rows if r["group"] == group]
        base = [r for r in rs if r["cell"] == "baseline"]
        policies = []
        for cell in sorted({r["cell"] for r in rs if r["cell"] != "baseline"}, key=lambda c: (order.get((group, c), 99), c)):
            crs = [r for r in rs if r["cell"] == cell]
            per_seed = {key: {r["seed"]: r["summary"].get(key) for r in crs}
                        for key in list(ATTACKERS.values()) + list(STALE.values())}
            means = {a: statistics.mean([v for v in per_seed[k].values() if v is not None] or [float("nan")])
                     for a, k in ATTACKERS.items()}
            strongest = max((a for a in ATTACKERS if not math.isnan(means[a])), key=lambda a: means[a], default="A0")
            per_seed["strongest"] = per_seed[ATTACKERS[strongest]]
            unexplained = sum(r["summary"].get("denm_unexplained") or 0 for r in crs)
            probe = any(r["summary"].get("denm_unexplained") is not None for r in crs)
            track = interval(list(per_seed["strongest"].values()), f"{group}/{cell}/track")
            by_attacker = {a: interval(list(per_seed[k].values()), f"{group}/{cell}/{a}") for a, k in ATTACKERS.items()}
            # seeds: enough for every measure the frontiers are drawn on, the strongest and each attacker alone
            sds = {m: x["sd"] for m, x in [("strongest", track)] + list(by_attacker.items()) if x and x["sd"] is not None}
            driver = max(sds, key=sds.get, default=None)   # the most spread measure needs the most seeds
            needs = {driver: seeds_needed(sds[driver])} if driver else {}
            policies.append({
                "policy": cell, "runs": len(crs), "seeds": sorted(r["seed"] for r in crs), "strongest": strongest,
                "tracking_success": track,
                "by_attacker": by_attacker,
                "stale_share": {th: interval(list(per_seed[k].values()), f"{group}/{cell}/{th}") for th, k in STALE.items()},
                "secondary": {k: interval([r["summary"].get(k) for r in crs], f"{group}/{cell}/{k}") for k, _ in SECONDARY},
                "changes_per_run": statistics.mean(r["summary"].get("changes") or 0 for r in crs),
                "warnings_measured": probe, "warnings_unexplained": unexplained, "within_limit": unexplained == 0,
                "seeds_needed": needs[driver] if driver else None, "seeds_driven_by": driver,
                "seeds_sd": sds.get(driver),
                "per_seed": per_seed,
            })
        front = {"primary": frontier(policies, "strongest", STALE["1.0"])}
        for th, key in STALE.items():
            if th != "1.0":
                front[f"stale {th} s"] = frontier(policies, "strongest", key)
        for a, key in ATTACKERS.items():
            front[f"attacker {a}"] = frontier(policies, key, STALE["1.0"])
        needed = [p["seeds_needed"] for p in policies if p["seeds_needed"] is not None]
        result["conditions"].append({
            "condition": group, "policies": policies, "frontier": front,
            "baseline": {"runs": len(base), "stale_share": interval([r["summary"].get("stale_share") for r in base], f"{group}/base"),
                         "secondary": {k: interval([r["summary"].get(k) for r in base], f"{group}/base/{k}") for k, _ in SECONDARY}},
            "seeds_needed": max(needed) if needed else None,
            "versions": sorted({r["version"] for r in rs if r["version"] is not None}),
        })
    for c in result["conditions"]:
        for p in c["policies"]:
            p["per_seed"] = {k: {str(s): v for s, v in d.items()} for k, d in p["per_seed"].items()}
    return result


def _ci(x, scale=1.0, digits=2):
    if not x:
        return "-"
    return f"{x['mean'] * scale:.{digits}f} [{x['lo'] * scale:.{digits}f}, {x['hi'] * scale:.{digits}f}]"


def format_text(result):
    lines = [f"{result['runs']} run(s); 95 % intervals from {result['bootstrap']} bootstrap resamples over seeds"]
    for c in result["conditions"]:
        lines += ["", f"condition {c['condition']}: baseline {c['baseline']['runs']} run(s), stale share "
                      f"{_ci(c['baseline']['stale_share'], 100, 1)} %"]
        lines.append(f"  {'policy':<30} {'runs':>4} {'attacker':>8} {'tracking success':>22} {'chance (guess)':>22} "
                     f"{'stale share %':>22}  warnings")
        for p in c["policies"]:
            warn = ("-" if not p["warnings_measured"] else "ok" if p["within_limit"]
                    else f"{p['warnings_unexplained']} unexplained (off the frontier)")
            mark = " *" if p["policy"] in c["frontier"]["primary"] else ""
            lines.append(f"  {p['policy'] + mark:<30} {p['runs']:>4} {p['strongest']:>8} {_ci(p['tracking_success']):>22} "
                         f"{_ci(p['by_attacker']['guess']):>22} {_ci(p['stale_share']['1.0'], 100, 1):>22}  {warn}")
        lines.append("  * on the frontier (no other policy better on both primary measures beyond chance)")
        for name, front in c["frontier"].items():
            lines.append(f"  frontier, {name}: {', '.join(front) or '-'}")
        lines.append("  secondary (mean [95 % interval]):")
        for key, label in SECONDARY:
            vals = [f"{p['policy']} {_ci(p['secondary'][key], 1, 1)}" for p in c["policies"] if p["secondary"][key]]
            if vals:
                b = c["baseline"]["secondary"].get(key)
                lines.append(f"    {label}: " + "; ".join(vals) + (f"; baseline {_ci(b, 1, 1)}" if b else ""))
        lines.append(f"  seeds per policy for tracking success within +/- {result['half_width']:g} (95 %), by the strongest "
                     "attacker and each alone: "
                     + ", ".join(f"{p['policy']} {p['seeds_needed']} ({p['seeds_driven_by']}, sd {p['seeds_sd']:.3f})"
                                 if p["seeds_needed"] is not None else f"{p['policy']} -" for p in c["policies"])
                     + f"; settled: {c['seeds_needed'] if c['seeds_needed'] is not None else '-'}")
    return "\n".join(lines)


def svg(condition):
    """Stale share across, tracking success up: one point per policy with its intervals, the frontier, and the
    guessing attacker's level per policy as a hollow marker."""
    W, H, L, B, T, R = 640, 420, 60, 46, 16, 16
    pts = [p for p in condition["policies"] if p["tracking_success"] and p["stale_share"]["1.0"]]
    xmax = max([p["stale_share"]["1.0"]["hi"] for p in pts] + [0.05]) * 1.15
    X = lambda v: L + (W - L - R) * v / xmax   # noqa: E731
    Y = lambda v: H - B - (H - B - T) * v       # noqa: E731
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" font-family="sans-serif" font-size="11">',
           '<rect width="100%" height="100%" fill="#fff"/>',
           f'<line x1="{L}" y1="{H - B}" x2="{W - R}" y2="{H - B}" stroke="#333"/>',
           f'<line x1="{L}" y1="{T}" x2="{L}" y2="{H - B}" stroke="#333"/>']
    for k in range(6):
        v = k / 5
        out.append(f'<text x="{L - 6}" y="{Y(v) + 4:.1f}" text-anchor="end">{v:.1f}</text>'
                   f'<line x1="{L}" y1="{Y(v):.1f}" x2="{W - R}" y2="{Y(v):.1f}" stroke="#eee"/>')
        xv = xmax * k / 5
        out.append(f'<text x="{X(xv):.1f}" y="{H - B + 16}" text-anchor="middle">{100 * xv:.0f} %</text>')
    out.append(f'<text x="{(L + W - R) / 2}" y="{H - 8}" text-anchor="middle">stale share (information older than 1 s)</text>'
               f'<text x="14" y="{(T + H - B) / 2}" text-anchor="middle" transform="rotate(-90 14 {(T + H - B) / 2})">'
               'tracking success (strongest attacker)</text>')
    front = sorted((p for p in pts if p["policy"] in condition["frontier"]["primary"]), key=lambda p: p["stale_share"]["1.0"]["mean"])
    if len(front) > 1:
        out.append('<polyline fill="none" stroke="#1f5fae" stroke-dasharray="4 3" points="'
                   + " ".join(f'{X(p["stale_share"]["1.0"]["mean"]):.1f},{Y(p["tracking_success"]["mean"]):.1f}' for p in front) + '"/>')
    for p in pts:
        s, t, g = p["stale_share"]["1.0"], p["tracking_success"], p["by_attacker"]["guess"]
        colour = "#1f5fae" if p["policy"] in condition["frontier"]["primary"] else "#888"
        x, y = X(s["mean"]), Y(t["mean"])
        out.append(f'<line x1="{X(s["lo"]):.1f}" y1="{y:.1f}" x2="{X(s["hi"]):.1f}" y2="{y:.1f}" stroke="{colour}"/>'
                   f'<line x1="{x:.1f}" y1="{Y(t["lo"]):.1f}" x2="{x:.1f}" y2="{Y(t["hi"]):.1f}" stroke="{colour}"/>'
                   f'<circle cx="{x:.1f}" cy="{y:.1f}" r="4" fill="{colour}"/>'
                   f'<text x="{x + 7:.1f}" y="{y - 6:.1f}">{p["policy"]}</text>')
        if g:
            out.append(f'<circle cx="{x:.1f}" cy="{Y(g["mean"]):.1f}" r="4" fill="none" stroke="{colour}"/>')
    out.append(f'<text x="{W - R}" y="{T + 10}" text-anchor="end" fill="#555">filled: strongest attacker; hollow: guessing '
               'attacker (chance); blue: frontier</text></svg>')
    return "".join(out)


def write_analysis(out, plan=None):
    result = analyse(out, plan)
    with open(os.path.join(out, "frontier.json"), "w") as f:
        json.dump(result, f, indent=1)
    text = format_text(result)
    with open(os.path.join(out, "frontier.txt"), "w") as f:
        f.write(text + "\n")
    for c in result["conditions"]:
        with open(os.path.join(out, f"frontier-{c['condition']}.svg"), "w") as f:
            f.write(svg(c))
    return result, text


def main(args, log=print):
    path = os.path.join(args.dir, "plan.toml")
    plan = load_plan(path) if os.path.isfile(path) else None
    if plan:
        from .sweep import runs   # noqa: PLC0415
        for g in plan.get("group", []):
            g["_cells"] = list(dict.fromkeys(r["cell"] for r in runs(plan) if r["group"] == g["name"]))
    result, text = write_analysis(args.dir, plan)
    log(json.dumps(result, indent=1) if args.json else text)
    return 0
