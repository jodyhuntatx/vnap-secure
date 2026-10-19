"""Privacy and cost of pseudonym changes in one run, from its results directory (overhead.json).

Phase 1 of Specs/VNAP-Secure-Pseudonym-Change-Overhead-Design.docx: only data every run keeps.

  station logs (<station>.log)   when each vehicle changed pseudonym, to which certificate, and
                                 its silent periods (the ground truth, as for scoring)
  eavesdropper-logs.tar          messages.jsonl: every frame on the link. On the lossless link
                                 every receiver hears the same frames, so the eavesdropper serves
                                 as the reference receiver
  positions.jsonl                the vehicles' true positions, 5 times a second (the mobility
                                 client's recorder); without it no position measures
  score.json                     which changes the eavesdropper linked, correctly or wrongly
  status.json                    the stations, pool sizes and the refill figures
  cert-sizes.json                sizes of the stations' AT and key files (else the AT size is
                                 inferred from CAMs with and without the certificate)

Per change: reception gap (last CAM under the old certificate to the first under the new one),
CAMs missing in it, the logged silent period, whether those explain the gap, the position error
during it, ghost time, the history lost with the old identity, and the eavesdropper's outcome.
Per vehicle: pseudonym lifetime, share of time stale (latest CAM older than STALE_S), inter-CAM
gaps, delivery ratio, position error, full-certificate share, CAM size and pool storage. Per run:
tracking success ratio, time and distance followed, change synchronization ratio, and outages:
stretches where the reference receiver heard nothing from stations that were not silent, or the
recorder stalled (the host, not the stations; gaps that overlap one are not counted against
the change). Not yet: verification failures, warnings, conflict exposure, CPU (later phases).

  python3 -m vnapsim.overhead <results dir> [--json] [--write]
"""

import argparse
import bisect
import io
import json
import math
import os
import statistics
import sys
import tarfile
from datetime import datetime

from .logs import RE_CHANGED, RE_IDC_SILENT, RE_POOL_START, RE_REJECTED

VERSION = 2
STALE_S = 1.0          # the longest CAM interval of EN 302 637-2
SYNC_WINDOW_S = 2.0    # changes of two vehicles this close count as synchronized
GAP_MARK = 1.5         # timeline: inter-CAM gaps longer than this many median intervals are drawn
TOLERANCE_MS = 250     # scheduling slack before a gap counts as longer than its causes explain
GHOST_TIMEOUT_S = 1.1  # assumed neighbour-table timeout: a little over the longest CAM interval
OUTAGE_S = 2.5         # no frame from anyone for this long, though a station should have sent: link or host down
EXPECTED_S = 1.5       # ... should have sent: this long outside its silent periods (more than one CAM interval)
STALL_S = 1.0          # the recorder ticks 5 times a second; a tick this late means the host stalled
BURST_S = 0.5          # after an outage, frames read from the socket buffer at once
KEY_BYTES = 32         # ECDSA P-256 private key, stored with each authorization ticket
EARTH_RADIUS = 6371008.8


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


def distance(a, b):
    """Great-circle distance in m between (lat, lon) points (haversine)."""
    la1, lo1, la2, lo2 = map(math.radians, (*a, *b))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 2 * EARTH_RADIUS * math.asin(math.sqrt(min(1.0, h)))


def extend(lat, lon, speed, heading, dt):
    """The position after dt seconds at constant speed (m/s) and heading (degrees, 0 = north)."""
    d = speed * dt
    north, east = d * math.cos(math.radians(heading)), d * math.sin(math.radians(heading))
    return lat + north / 111195.0, lon + east / (111195.0 * math.cos(math.radians(lat)))


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
    """CAMs from messages.jsonl in the eavesdropper's archive, sorted by time:
    [(t, cert, bytes, full certificate, lat, lon, speed, heading, CAM stationId)]."""
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
                frames.append((fr["t"], fr["cert_digest"], fr.get("length", 0), fr.get("signer") == "certificate",
                               fr.get("cam_lat"), fr.get("cam_lon"), fr.get("cam_speed_mps") or 0.0,
                               fr.get("cam_heading_deg") or 0.0, fr.get("station_id")))
    frames.sort()
    return frames


def read_positions(path):
    """The recorder's true positions: ({station id: [(t, lat, lon, speed, heading)]}, [tick times])."""
    tracks, ticks = {}, []
    with open(path, errors="replace") as f:
        for line in f:
            try:
                rec = json.loads(line)
                t = float(rec["t"])
            except (ValueError, KeyError, TypeError):
                continue   # a line cut off when the container stopped
            ticks.append(t)
            for sid, (lat, lon, speed, heading) in rec.get("v", {}).items():
                tracks.setdefault(int(sid), []).append((t, lat, lon, speed, heading))
    return tracks, ticks


def pct(values, q):
    if not values:
        return None
    s = sorted(values)
    return s[min(len(s) - 1, int(round(q * (len(s) - 1))))]


def r3(x):
    return None if x is None else round(x, 3)


def r1(x):
    return None if x is None else round(x, 1)


def covered(a, b, intervals):
    """Length of [a, b] covered by the union of the given intervals."""
    total, end = 0.0, a
    for lo, hi in sorted(intervals):
        lo, hi = max(lo, end), min(hi, b)
        if hi > lo:
            total += hi - lo
            end = hi
    return total


def find_outages(frames, owner, silent_windows, ticks):
    """Stretches the reference receiver heard nothing from a station that should have sent (not in a
    silent period, heard before and after), and stretches the position recorder stalled: the link or
    the host was down, not the change. [(from, to, seen by)], merged where they overlap."""
    found = []
    times = [f[0] for f in frames]
    first, last = {}, {}   # sender (vehicle name, or certificate of a station without pseudonyms) -> times
    for t, cert, *_ in frames:
        sender = owner.get(cert, cert)
        first.setdefault(sender, t)
        last[sender] = t
    for a, b in zip(times, times[1:]):
        if b - a <= OUTAGE_S:
            continue
        for sender in first:
            if first[sender] <= a and last[sender] >= b:
                # it should have sent at least one CAM in the time not excused by a silent period
                free = (b - a) - covered(a, b, silent_windows.get(sender, []))
                if free > EXPECTED_S:
                    found.append([a, b, "receiver"])
                    break
    found += [[a, b, "recorder"] for a, b in zip(ticks, ticks[1:]) if b - a > STALL_S]
    merged = []
    for a, b, by in sorted(found):
        if merged and a <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], b)
            if by not in merged[-1][2]:
                merged[-1][2].append(by)
        else:
            merged.append([a, b, [by]])
    return merged


def truth_at(track, times, t):
    """The recorded true position at time t (the latest tick at or before it), or None."""
    i = bisect.bisect_right(times, t) - 1
    return track[i] if i >= 0 else None


def path_length(track, times, a, b):
    """Distance driven between times a and b along the recorded positions."""
    i, j = bisect.bisect_left(times, a), bisect.bisect_right(times, b)
    pts = track[i:j]
    return sum(distance(p[1:3], q[1:3]) for p, q in zip(pts, pts[1:]))


def belief_errors(cams, track):
    """At every recorder tick while the vehicle is on the air: (t, error of the last known position,
    error of that position extended by speed and heading), against the true position."""
    out, k = [], -1
    if not cams or not track:
        return out
    for t, lat, lon, _, _ in track:
        if t < cams[0][0] or t > cams[-1][0]:
            continue
        while k + 1 < len(cams) and cams[k + 1][0] <= t:
            k += 1
        c = cams[k]
        if c[4] is None or c[5] is None:
            continue
        out.append((t, distance((c[4], c[5]), (lat, lon)),
                    distance(extend(c[4], c[5], c[6], c[7], t - c[0]), (lat, lon))))
    return out


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
    pos_path = os.path.join(results_dir, "positions.jsonl")
    tracks, ticks = read_positions(pos_path) if os.path.isfile(pos_path) else ({}, [])
    station_id = {st["name"]: st.get("station_id") for st in vehicles}

    owner = {}   # certificate -> vehicle, from the logs (ground truth)
    for name, hist in history.items():
        for c in ([hist["start"]] if hist["start"] else []) + hist["changes"]:
            owner[c["cert"]] = name
    by_vehicle = {name: [] for name in history}
    for fr in frames or []:
        if fr[1] in owner:
            by_vehicle[owner[fr[1]]].append(fr)
    links = {}   # new certificate -> the eavesdropper's link to it
    for link in (score or {}).get("link_list", []):
        links[link["new"]] = link
    t0 = min([f[0] for f in frames or []] + [c["t"] for h in history.values() for c in h["changes"] if c["t"]], default=None)
    t_end = max([f[0] for f in frames or []], default=None)
    rel = lambda t: r3(t - t0) if t is not None and t0 is not None else None   # noqa: E731

    # the silent period of each change: the station logs it right before or after the change line
    for hist in history.values():
        for c in hist["changes"]:
            c["silent_ms"] = next((s["ms"] for s in hist["silent"] if c["t"] is not None and s["t"] is not None
                                   and -1.0 <= s["t"] - c["t"] < 2.0), 0)
    silent_windows = {name: [(c["t"], c["t"] + c["silent_ms"] / 1000 + 2 * STALE_S) for c in hist["changes"] if c["t"]]
                      for name, hist in history.items()}
    outages = find_outages(frames or [], owner, silent_windows, ticks)

    def in_outage(a, b):
        # frames buffered during a stall may be stamped in a burst right after it (older eavesdroppers stamp
        # them when read, not when received): the first BURST_S after an outage still belong to it
        return any(a < hi + BURST_S and b > lo for lo, hi, _ in outages)

    changes, per_vehicle, timeline, chains_all, gap_errors_all = [], [], [], [], []
    all_change_times = sorted((c["t"], name) for name, h in history.items() for c in h["changes"] if c["t"] is not None)
    sizes = {True: [], False: []}
    for fr in frames or []:
        if fr[1] in owner:
            sizes[fr[3]].append(fr[2])
    files = load("cert-sizes.json") or {}   # the stations' AT and key files, measured at collection
    at_files = [n for v in files.values() for n in v.get("at", []) if n]
    key_files = [n for v in files.values() for n in v.get("key", []) if n]
    if at_files:
        cert_bytes = round(statistics.mean(at_files))
    elif sizes[True] and sizes[False]:   # what the certificate adds to a CAM against the 8-byte digest
        cert_bytes = round(statistics.mean(sizes[True]) - statistics.mean(sizes[False])) + 8
    else:
        cert_bytes = None
    key_bytes = round(statistics.mean(key_files)) if key_files else KEY_BYTES
    for name, hist in history.items():
        cams = by_vehicle[name]
        times = [c[0] for c in cams]
        gaps = [b - a for a, b in zip(times, times[1:])]
        median = statistics.median(gaps) if gaps else None
        last_by_cert, first_by_cert, count_by_cert = {}, {}, {}
        for fr in cams:
            first_by_cert.setdefault(fr[1], fr[0])
            last_by_cert[fr[1]] = fr[0]
            count_by_cert[fr[1]] = count_by_cert.get(fr[1], 0) + 1
        track = tracks.get(station_id.get(name)) or []
        track_times = [p[0] for p in track]
        errors = belief_errors(cams, track)
        err_times = [e[0] for e in errors]
        previous = hist["start"]["cert"] if hist["start"] else None
        lifetimes, own, gap_errors = [], [], []
        last_t = hist["start"]["t"] if hist["start"] else None
        for c in hist["changes"]:
            old_last, new_first = last_by_cert.get(previous), first_by_cert.get(c["cert"])
            gap = new_first - old_last if old_last is not None and new_first is not None else None
            link = links.get(c["cert"])
            if link is None:
                outcome = "not linked"
            else:
                outcome = "linked correctly" if link.get("correct") else "linked wrongly"
            near = [n for t, n in all_change_times if n != name and abs(t - c["t"]) <= SYNC_WINDOW_S] if c["t"] else []
            # explained: the silent period plus two CAM intervals (the CAM timer restarts at a random phase)
            excess = (round(gap * 1000) - c["silent_ms"] - 2 * round(median * 1000)
                      if gap is not None and median else None)
            outage = gap is not None and (in_outage(old_last, new_first) or in_outage(c["t"], c["t"]))
            during = errors[bisect.bisect_left(err_times, old_last):bisect.bisect_right(err_times, new_first)] \
                if gap is not None else []
            gap_errors += during
            rec = {"vehicle": name, "t": rel(c["t"]),
                   "old": previous, "new": c["cert"], "silent_ms": c["silent_ms"],
                   "gap_ms": None if gap is None else round(gap * 1000),
                   "cams_missing": None if gap is None or not median else max(0, round(gap / median) - 1),
                   "explained": None if excess is None else (excess <= TOLERANCE_MS or outage),
                   "excess_ms": excess if excess is not None and excess > TOLERANCE_MS else None,
                   "outage": outage,
                   "pos_error_m_max": r1(max((e[1] for e in during), default=None)) if during else None,
                   "pos_error_m_max_extended": r1(max((e[2] for e in during), default=None)) if during else None,
                   # the old entry outlives the change in a neighbour table until its timeout; it overlaps the new
                   # one only when the new identity is heard before then (no silent period)
                   "ghost_ms": None if gap is None else max(0, round((GHOST_TIMEOUT_S - gap) * 1000)),
                   # what a receiver knew under the old identity and can no longer attach to the vehicle
                   "history_s": r3(old_last - first_by_cert[previous]) if old_last is not None else None,
                   "history_cams": count_by_cert.get(previous),
                   "outcome": outcome, "technique": link.get("technique") if link else None,
                   "linked_to": link.get("old_station") if link and not link.get("correct") else None,
                   "synchronized_with": sorted(set(near))}
            changes.append(rec)
            own.append(rec)
            if last_t is not None and c["t"] is not None:
                lifetimes.append(c["t"] - last_t)
            last_t, previous = c["t"], c["cert"]
        gap_errors_all += gap_errors
        # time and distance followed: chains of identities the eavesdropper linked correctly one to the next
        sequence = ([hist["start"]["cert"]] if hist["start"] else []) + [c["cert"] for c in hist["changes"]]
        chains, chain = [], []
        for i, cert in enumerate(sequence):
            if chain and not (links.get(cert) or {}).get("correct"):
                chains.append(chain)
                chain = []
            chain.append(cert)
        if chain:
            chains.append(chain)
        followed = []
        for ch in chains:
            heard = [x for x in ch if x in first_by_cert]
            if heard:
                a, b = first_by_cert[heard[0]], last_by_cert[heard[-1]]
                followed.append({"identities": len(ch), "s": b - a,
                                 "m": path_length(track, track_times, a, b) if track else None})
        chains_all += followed
        # inter-CAM gaps that overlap an outage are left out with their time: the link or host was down
        kept = [b - a for a, b in zip(times, times[1:]) if not in_outage(a, b)]
        lost = sum(b - a for a, b in zip(times, times[1:]) if in_outage(a, b))
        stale = sum(max(0.0, g - STALE_S) for g in kept)
        window = (times[-1] - times[0] - lost) if len(times) > 1 else 0
        missing = sum(max(0, round(g / median) - 1) for g in kept) if median else 0
        pool = (next((st.get("security") or {} for st in vehicles if st["name"] == name), {})).get("pseudonym_pool")
        refill = next((st["pseudonym"].get("refill") for st in vehicles if st["name"] == name), None)
        peak = (pool or 0) + ((refill or {}).get("added") or 0) if pool else None
        per_vehicle.append({
            "vehicle": name, "changes": len(hist["changes"]), "rejected": hist["rejected"], "cams": len(cams),
            "lifetime_s_mean": r3(statistics.mean(lifetimes)) if lifetimes else None,
            "lifetime_s_min": r3(min(lifetimes)) if lifetimes else None,
            "lifetime_s_max": r3(max(lifetimes)) if lifetimes else None,
            "cam_interval_ms_median": round(median * 1000) if median else None,
            "gap_ms_p95": round(pct(kept, 0.95) * 1000) if kept else None,
            "gap_ms_max": round(max(kept) * 1000) if kept else None,
            "stale_share": r3(stale / window) if window else None,
            "delivery_ratio": r3(len(cams) / (len(cams) + missing)) if cams else None,
            "full_cert_share": r3(sum(1 for c in cams if c[3]) / len(cams)) if cams else None,
            "cam_bytes_mean": round(statistics.mean(c[2] for c in cams)) if cams else None,
            "pos_error_m_p95": r1(pct([e[1] for e in gap_errors], 0.95)),
            "pos_error_m_p95_extended": r1(pct([e[2] for e in gap_errors], 0.95)),
            "pos_error_m_p95_all": r1(pct([e[1] for e in errors], 0.95)),
            "followed_s_max": r1(max((f["s"] for f in followed), default=None)) if followed else None,
            "followed_m_max": r1(max((f["m"] for f in followed if f["m"] is not None), default=None))
            if any(f["m"] is not None for f in followed) else None,
            "distance_m": r1(path_length(track, track_times, times[0], times[-1])) if track and times else None,
            # certificates held at most: the initial pool and every refilled one (the station keeps used ones)
            "pool_peak": peak,
            "pool_bytes": peak * (cert_bytes + key_bytes) if peak and cert_bytes else None,
            "refill": refill,
        })
        # the timeline: long gaps between CAMs (with what caused them) and the changes
        long_gaps = []
        if median:
            for a, b in zip(times, times[1:]):
                if b - a > GAP_MARK * median and b - a > 0.3:
                    if in_outage(a, b):
                        kind = "outage"
                    elif any(s["t"] is not None and a - 0.5 <= s["t"] <= b for s in hist["silent"]):
                        kind = "silent"
                    else:
                        kind = "gap"
                    long_gaps.append([rel(a), rel(b), kind])
        timeline.append({"vehicle": name, "first": rel(times[0]) if times else None,
                         "last": rel(times[-1]) if times else None, "gaps": long_gaps,
                         "changes": [[r["t"], r["outcome"]] for r in own]})

    measured = [c for c in changes if c["gap_ms"] is not None]
    clean = [c for c in measured if not c["outage"]]
    correct = sum(1 for c in changes if c["outcome"] == "linked correctly")
    shares = [v["stale_share"] for v in per_vehicle if v["stale_share"] is not None]
    delivery = [v["delivery_ratio"] for v in per_vehicle if v["delivery_ratio"] is not None]
    mean_of = lambda key: (round(statistics.mean(v[key] for v in per_vehicle if v[key])) if any(v[key] for v in per_vehicle)  # noqa: E731
                           else None)
    followed_m = [f["m"] for f in chains_all if f["m"] is not None]
    return {
        # the eavesdropper serves as the reference receiver; without one, or before it heard a CAM, there
        # are no reception figures; without the recorder no position figures
        "version": VERSION, "reference_receiver": "eavesdropper" if frames else None,
        "positions": "recorder" if ticks else None,
        "cams_recorded": None if frames is None else len(frames),
        "settings": {"stale_s": STALE_S, "sync_window_s": SYNC_WINDOW_S, "tolerance_ms": TOLERANCE_MS,
                     "ghost_timeout_s": GHOST_TIMEOUT_S, "outage_s": OUTAGE_S, "stall_s": STALL_S},
        "duration_s": r3(t_end - t0) if t_end is not None and t0 is not None else None,
        "summary": {
            "vehicles": len(history), "changes": len(changes),
            "tracking_success": r3(correct / len(changes)) if changes and score is not None else None,
            "linked_correctly": correct if score is not None else None,
            "linked_wrongly": sum(1 for c in changes if c["outcome"] == "linked wrongly") if score is not None else None,
            "followed_s_mean": r1(statistics.mean(f["s"] for f in chains_all)) if chains_all and score is not None else None,
            "followed_s_max": r1(max(f["s"] for f in chains_all)) if chains_all and score is not None else None,
            "followed_m_mean": r1(statistics.mean(followed_m)) if followed_m and score is not None else None,
            "followed_m_max": r1(max(followed_m)) if followed_m and score is not None else None,
            # gaps that overlap an outage of the link or host are left out: the change did not cause them
            "gap_ms_mean": round(statistics.mean(c["gap_ms"] for c in clean)) if clean else None,
            "gap_ms_max": max((c["gap_ms"] for c in clean), default=None),
            "unexplained": sum(1 for c in measured if c["explained"] is False),
            "outage_changes": len(measured) - len(clean),
            "stale_share": r3(statistics.mean(shares)) if shares else None,
            "delivery_ratio": r3(statistics.mean(delivery)) if delivery else None,
            "sync_ratio": r3(sum(1 for c in changes if c["synchronized_with"]) / len(changes)) if changes else None,
            "full_cert_share": r3(statistics.mean(v["full_cert_share"] for v in per_vehicle if v["full_cert_share"] is not None))
            if any(v["full_cert_share"] is not None for v in per_vehicle) else None,
            "cam_bytes_mean": mean_of("cam_bytes_mean"),
            "cam_bytes_digest": round(statistics.mean(sizes[False])) if sizes[False] else None,
            "cam_bytes_certificate": round(statistics.mean(sizes[True])) if sizes[True] else None,
            "cert_bytes": cert_bytes, "key_bytes": key_bytes if cert_bytes else None,
            "cert_bytes_source": "files" if at_files else "frames" if cert_bytes else None,
            "pool_bytes_mean": mean_of("pool_bytes"),
            "pos_error_m_p95": r1(pct([e[1] for e in gap_errors_all], 0.95)),
            "pos_error_m_p95_extended": r1(pct([e[2] for e in gap_errors_all], 0.95)),
            "ghost_ms_mean": round(statistics.mean(c["ghost_ms"] for c in clean)) if clean else None,
            "history_s_mean": r1(statistics.mean(c["history_s"] for c in measured if c["history_s"] is not None))
            if any(c["history_s"] is not None for c in measured) else None,
            "history_cams": sum(c["history_cams"] or 0 for c in measured) if measured else None,
        },
        "outages": [{"from": rel(a), "to": rel(b), "s": r3(b - a), "seen_by": by} for a, b, by in outages],
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
    num = lambda x, unit: "-" if x is None else f"{x} {unit}"   # noqa: E731
    lines = [f"{s['vehicles']} vehicle(s), {s['changes']} pseudonym change(s)"
             + ("" if result["reference_receiver"] else " (no CAMs recorded by an eavesdropper: no reception figures)")
             + ("" if result.get("positions") else " (no recorded positions: no position figures)"),
             f"tracking success {pctf(s['tracking_success'])} ({s['linked_correctly']} linked correctly, {s['linked_wrongly']} wrongly); "
             f"followed mean {num(s['followed_s_mean'], 's')}, longest {num(s['followed_s_max'], 's')}"
             + (f" ({num(s['followed_m_max'], 'm')})" if s.get("followed_m_max") is not None else ""),
             f"synchronized changes {pctf(s['sync_ratio'])}"]
    if result["reference_receiver"]:
        lines += [f"reception gap mean {s['gap_ms_mean']} ms, max {s['gap_ms_max']} ms; stale {pctf(s['stale_share'])}; "
                  f"delivery {pctf(s['delivery_ratio'])}; ghost mean {num(s['ghost_ms_mean'], 'ms')}",
                  f"unexplained gaps {s['unexplained']}; gaps during an outage {s['outage_changes']}",
                  f"CAMs with the full certificate {pctf(s['full_cert_share'])}, mean CAM {s['cam_bytes_mean']} bytes "
                  f"(digest {num(s['cam_bytes_digest'], 'B')}, certificate {num(s['cam_bytes_certificate'], 'B')}); "
                  f"pool storage {num(s['pool_bytes_mean'], 'bytes')} per vehicle"]
    if result.get("positions"):
        lines.append(f"position error during gaps p95 {num(s['pos_error_m_p95'], 'm')} (last known), "
                     f"{num(s['pos_error_m_p95_extended'], 'm')} (extended)")
    for o in result.get("outages", []):
        lines.append(f"  outage {o['from']} s to {o['to']} s ({o['s']} s), seen by {', '.join(o['seen_by'])}")
    for c in result["changes"]:
        gap = "-" if c["gap_ms"] is None else f"{c['gap_ms']} ms"
        note = " outage" if c["outage"] else (f" UNEXPLAINED +{c['excess_ms']} ms" if c["explained"] is False else "")
        lines.append(f"  {c['t'] if c['t'] is not None else '-':>8} s  {c['vehicle']:<12} silent {c['silent_ms']:>5} ms  "
                     f"gap {gap:>8}  missing {c['cams_missing'] if c['cams_missing'] is not None else '-'}  {c['outcome']}"
                     + (f" ({c['technique']})" if c["technique"] else "") + note)
    return "\n".join(lines)


if __name__ == "__main__":
    sys.exit(main())
