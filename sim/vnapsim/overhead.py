"""Privacy and cost of pseudonym changes in one run, from its results directory (overhead.json).

Iteration 1 of Specs/VNAP-Secure-Pseudonym-Change-Overhead-Design.docx: only data every run
already keeps.

  station logs (<station>.log)   when each vehicle changed pseudonym, to which certificate, and
                                 its silent periods (the ground truth, as for scoring)
  eavesdropper-logs.tar          messages.jsonl: every frame on the link. On the lossless link
                                 every receiver hears the same frames, so the eavesdropper serves
                                 as the reference receiver
  score.json                     which changes the eavesdropper linked, correctly or wrongly
  status.json                    the stations, and the refill figures

Per change: reception gap (last CAM under the old certificate to the first under the new one),
CAMs missing in it, the logged silent period, and the eavesdropper's outcome. Per vehicle:
pseudonym lifetime, share of time stale (latest CAM older than STALE_S), inter-CAM gaps,
delivery ratio, full-certificate share and CAM size. Per run: tracking success ratio and change
synchronization ratio. Not yet: position error and conflict exposure (need the true positions),
verification failures, warnings, CPU (later iterations of the design).

  python3 -m vnapsim.overhead <results dir> [--json] [--write]
"""

import argparse
import io
import json
import os
import statistics
import sys
import tarfile
from datetime import datetime

from .logs import RE_CHANGED, RE_IDC_SILENT, RE_POOL_START, RE_REJECTED

VERSION = 1
STALE_S = 1.0          # the longest CAM interval of EN 302 637-2
SYNC_WINDOW_S = 2.0    # changes of two vehicles this close count as synchronized
GAP_MARK = 1.5         # timeline: inter-CAM gaps longer than this many median intervals are drawn


def log_time(line):
    """Docker's --timestamps prefix (RFC 3339, nanoseconds) as a Unix time, or None."""
    stamp = line.split(" ", 1)[0]
    if not stamp.endswith("Z") or "T" not in stamp:
        return None
    head, _, frac = stamp[:-1].partition(".")
    try:
        t = datetime.fromisoformat(head + "+00:00").timestamp()
    except ValueError:
        return None
    return t + (float("0." + frac[:6]) if frac else 0.0)


def read_station_log(path):
    """The pseudonym history of one station: start certificate, changes, rejections, silent periods."""
    out = {"start": None, "changes": [], "rejected": 0, "silent": []}
    with open(path, errors="replace") as f:
        for line in f:
            if "[PSEUDONYM]" not in line and "[IDCHANGE] silent period" not in line:
                continue
            t = log_time(line)
            if (m := RE_POOL_START.search(line)) and out["start"] is None:
                out["start"] = {"t": t, "cert": m[4]}
            elif m := RE_CHANGED.search(line):
                out["changes"].append({"t": t, "cert": m[4], "index": int(m[2])})
            elif RE_REJECTED.search(line):
                out["rejected"] += 1
            elif m := RE_IDC_SILENT.search(line):
                out["silent"].append({"t": t, "ms": int(m[1])})
    return out


def read_frames(tar_path):
    """CAMs from messages.jsonl in the eavesdropper's archive: [(t, cert, bytes, full certificate)]."""
    frames = []
    with tarfile.open(tar_path) as tar:
        member = next((m for m in tar.getmembers() if m.name.endswith("messages.jsonl")), None)
        if member is None:
            return None
        for line in io.TextIOWrapper(tar.extractfile(member), errors="replace"):
            try:
                fr = json.loads(line)
            except json.JSONDecodeError:
                continue
            if fr.get("message") == "CAM" and fr.get("cert_digest"):
                frames.append((fr["t"], fr["cert_digest"], fr.get("length", 0), fr.get("signer") == "certificate"))
    frames.sort()
    return frames


def pct(values, q):
    if not values:
        return None
    s = sorted(values)
    return s[min(len(s) - 1, int(round(q * (len(s) - 1))))]


def r3(x):
    return None if x is None else round(x, 3)


def analyse(results_dir):
    """The analysis of one results directory, as a dict (what overhead.json holds)."""
    def load(name):
        try:
            with open(os.path.join(results_dir, name)) as f:
                return json.load(f)
        except (OSError, ValueError):
            return None
    status = load("status.json") or {}
    score = load("score.json")
    vehicles = [st for st in status.get("stations", []) if st.get("pseudonym")]
    history = {}
    for st in vehicles:
        path = os.path.join(results_dir, f"{st['name']}.log")
        if os.path.isfile(path):
            history[st["name"]] = read_station_log(path)
    tar = os.path.join(results_dir, "eavesdropper-logs.tar")
    frames = read_frames(tar) if os.path.isfile(tar) else None

    owner = {}   # certificate -> vehicle, from the logs (ground truth)
    for name, hist in history.items():
        for c in ([hist["start"]] if hist["start"] else []) + hist["changes"]:
            owner[c["cert"]] = name
    by_vehicle = {name: [] for name in history}
    for t, cert, size, full in frames or []:
        if cert in owner:
            by_vehicle[owner[cert]].append((t, cert, size, full))
    links = {}   # new certificate -> the eavesdropper's link to it
    for link in (score or {}).get("link_list", []):
        links[link["new"]] = link
    t0 = min([f[0] for f in frames or []] + [c["t"] for h in history.values() for c in h["changes"] if c["t"]], default=None)
    t_end = max([f[0] for f in frames or []], default=None)

    changes, per_vehicle, timeline = [], [], []
    all_change_times = sorted((c["t"], name) for name, h in history.items() for c in h["changes"] if c["t"] is not None)
    for name, hist in history.items():
        cams = by_vehicle[name]
        times = [c[0] for c in cams]
        gaps = [b - a for a, b in zip(times, times[1:])]
        median = statistics.median(gaps) if gaps else None
        last_by_cert, first_by_cert = {}, {}
        for t, cert, _, _ in cams:
            first_by_cert.setdefault(cert, t)
            last_by_cert[cert] = t
        previous = hist["start"]["cert"] if hist["start"] else None
        lifetimes, own = [], []
        last_t = hist["start"]["t"] if hist["start"] else None
        for c in hist["changes"]:
            # the station logs the silent period right before or after the change line
            silent = next((s["ms"] for s in hist["silent"] if c["t"] is not None and s["t"] is not None
                           and -1.0 <= s["t"] - c["t"] < 2.0), 0)
            old_last, new_first = last_by_cert.get(previous), first_by_cert.get(c["cert"])
            gap = new_first - old_last if old_last is not None and new_first is not None else None
            link = links.get(c["cert"])
            if link is None:
                outcome = "not linked"
            else:
                outcome = "linked correctly" if link.get("correct") else "linked wrongly"
            near = [n for t, n in all_change_times if n != name and abs(t - c["t"]) <= SYNC_WINDOW_S] if c["t"] else []
            rec = {"vehicle": name, "t": r3(c["t"] - t0) if t0 is not None and c["t"] else None,
                   "old": previous, "new": c["cert"], "silent_ms": silent,
                   "gap_ms": None if gap is None else round(gap * 1000),
                   "cams_missing": None if gap is None or not median else max(0, round(gap / median) - 1),
                   "outcome": outcome, "technique": link.get("technique") if link else None,
                   "linked_to": link.get("old_station") if link and not link.get("correct") else None,
                   "synchronized_with": sorted(set(near))}
            changes.append(rec)
            own.append(rec)
            if last_t is not None and c["t"] is not None:
                lifetimes.append(c["t"] - last_t)
            last_t, previous = c["t"], c["cert"]
        stale = sum(max(0.0, g - STALE_S) for g in gaps)
        window = (times[-1] - times[0]) if len(times) > 1 else 0
        missing = sum(max(0, round(g / median) - 1) for g in gaps) if median else 0
        per_vehicle.append({
            "vehicle": name, "changes": len(hist["changes"]), "rejected": hist["rejected"], "cams": len(cams),
            "lifetime_s_mean": r3(statistics.mean(lifetimes)) if lifetimes else None,
            "lifetime_s_min": r3(min(lifetimes)) if lifetimes else None,
            "lifetime_s_max": r3(max(lifetimes)) if lifetimes else None,
            "cam_interval_ms_median": round(median * 1000) if median else None,
            "gap_ms_p95": round(pct(gaps, 0.95) * 1000) if gaps else None,
            "gap_ms_max": round(max(gaps) * 1000) if gaps else None,
            "stale_share": r3(stale / window) if window else None,
            "delivery_ratio": r3(len(cams) / (len(cams) + missing)) if cams else None,
            "full_cert_share": r3(sum(1 for c in cams if c[3]) / len(cams)) if cams else None,
            "cam_bytes_mean": round(statistics.mean(c[2] for c in cams)) if cams else None,
            "refill": (next((st["pseudonym"].get("refill") for st in vehicles if st["name"] == name), None)),
        })
        # the timeline: long gaps between CAMs (with what caused them) and the changes
        long_gaps = []
        if median:
            for a, b in zip(times, times[1:]):
                if b - a > GAP_MARK * median and b - a > 0.3:
                    silent = any(s["t"] is not None and a - 0.5 <= s["t"] <= b for s in hist["silent"])
                    long_gaps.append([r3(a - t0), r3(b - t0), "silent" if silent else "gap"])
        timeline.append({"vehicle": name, "first": r3(times[0] - t0) if times else None,
                         "last": r3(times[-1] - t0) if times else None, "gaps": long_gaps,
                         "changes": [[r["t"], r["outcome"]] for r in own]})

    measured = [c for c in changes if c["gap_ms"] is not None]
    correct = sum(1 for c in changes if c["outcome"] == "linked correctly")
    shares = [v["stale_share"] for v in per_vehicle if v["stale_share"] is not None]
    delivery = [v["delivery_ratio"] for v in per_vehicle if v["delivery_ratio"] is not None]
    return {
        # the eavesdropper serves as the reference receiver; without one, or before it heard a CAM, there
        # are no reception figures
        "version": VERSION, "reference_receiver": "eavesdropper" if frames else None,
        "cams_recorded": None if frames is None else len(frames),
        "settings": {"stale_s": STALE_S, "sync_window_s": SYNC_WINDOW_S},
        "duration_s": r3(t_end - t0) if t_end is not None and t0 is not None else None,
        "summary": {
            "vehicles": len(history), "changes": len(changes),
            "tracking_success": r3(correct / len(changes)) if changes and score is not None else None,
            "linked_correctly": correct if score is not None else None,
            "linked_wrongly": sum(1 for c in changes if c["outcome"] == "linked wrongly") if score is not None else None,
            "gap_ms_mean": round(statistics.mean(c["gap_ms"] for c in measured)) if measured else None,
            "gap_ms_max": max((c["gap_ms"] for c in measured), default=None),
            "stale_share": r3(statistics.mean(shares)) if shares else None,
            "delivery_ratio": r3(statistics.mean(delivery)) if delivery else None,
            "sync_ratio": r3(sum(1 for c in changes if c["synchronized_with"]) / len(changes)) if changes else None,
            "full_cert_share": r3(statistics.mean(v["full_cert_share"] for v in per_vehicle if v["full_cert_share"] is not None))
            if any(v["full_cert_share"] is not None for v in per_vehicle) else None,
            "cam_bytes_mean": round(statistics.mean(v["cam_bytes_mean"] for v in per_vehicle if v["cam_bytes_mean"]))
            if any(v["cam_bytes_mean"] for v in per_vehicle) else None,
        },
        "vehicles": per_vehicle, "changes": changes, "timeline": timeline,
    }


def write(results_dir):
    """Analyse and write overhead.json; returns the analysis."""
    result = analyse(results_dir)
    tmp = os.path.join(results_dir, "overhead.json.tmp")
    with open(tmp, "w") as f:
        json.dump(result, f, indent=1)
    os.replace(tmp, os.path.join(results_dir, "overhead.json"))
    return result


def load_or_analyse(results_dir):
    """overhead.json of the current version, computed and written when missing or older."""
    path = os.path.join(results_dir, "overhead.json")
    try:
        with open(path) as f:
            data = json.load(f)
        if data.get("version") == VERSION:
            return data
    except (OSError, ValueError):
        pass
    return write(results_dir)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="vnapsim.overhead", description=__doc__.split("\n\n")[0])
    ap.add_argument("dir")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--write", action="store_true", help="also write overhead.json into the directory")
    args = ap.parse_args(argv)
    result = write(args.dir) if args.write else analyse(args.dir)
    if args.json:
        print(json.dumps(result, indent=1))
        return 0
    print(format_text(result))
    return 0


def format_text(result):
    s = result["summary"]
    pctf = lambda x: "-" if x is None else f"{x * 100:.0f} %"   # noqa: E731
    lines = [f"{s['vehicles']} vehicle(s), {s['changes']} pseudonym change(s)"
             + ("" if result["reference_receiver"] else " (no CAMs recorded by an eavesdropper: no reception figures)"),
             f"tracking success {pctf(s['tracking_success'])} ({s['linked_correctly']} linked correctly, {s['linked_wrongly']} wrongly)",
             f"synchronized changes {pctf(s['sync_ratio'])}"]
    if result["reference_receiver"]:
        lines += [f"reception gap mean {s['gap_ms_mean']} ms, max {s['gap_ms_max']} ms; stale {pctf(s['stale_share'])}; "
                  f"delivery {pctf(s['delivery_ratio'])}",
                  f"CAMs with the full certificate {pctf(s['full_cert_share'])}, mean CAM {s['cam_bytes_mean']} bytes"]
    for c in result["changes"]:
        gap = "-" if c["gap_ms"] is None else f"{c['gap_ms']} ms"
        lines.append(f"  {c['t'] if c['t'] is not None else '-':>8} s  {c['vehicle']:<12} silent {c['silent_ms']:>5} ms  "
                     f"gap {gap:>8}  missing {c['cams_missing'] if c['cams_missing'] is not None else '-'}  {c['outcome']}"
                     + (f" ({c['technique']})" if c["technique"] else ""))
    return "\n".join(lines)


if __name__ == "__main__":
    sys.exit(main())
