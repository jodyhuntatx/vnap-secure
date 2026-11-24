"""What the receivers knew around the pseudonym changes (phase 2 of the overhead design), for overhead.json.

  verification  from the collector's receptions.jsonl: each station's security report for every message
                it received; failures within VERIFY_WINDOW_S after a receiver first hears a vehicle's new
                identity, and over the run
  warnings      from the warning probe (probe.jsonl) and the DENMs the collector recorded: per triggered
                DENM and receiver, the delay to the first copy received, or lost; whether the sender's
                silent period explains it. DENMs that still carry the sender's configured stationId after a
                change are counted as identity leaks (a station image without the DENM stationId fix)
  conflicts     from the recorded true positions and the reference receiver's CAMs: pairs of vehicles
                whose true time-to-collision is below CONFLICT_TTC_S, and the time in such a conflict
                during which one's latest information about the other is stale or shows no conflict

The receivers cannot link the identities themselves; the analysis maps stationIds to vehicles from the
station logs ([IDCHANGE] facilities lines), as the scoring does with certificates.
"""

import io
import json
import math
import statistics
import tarfile

from .logs import RE_IDC_FAC

VERIFY_WINDOW_S = 5.0     # failures this soon after a receiver first hears the new identity count against the change
CONFLICT_TTC_S = 4.0      # forward-collision and intersection warnings act in this range
CONFLICT_DISTANCE_M = 4.0  # centres this close count as a collision (about a car length)
DELAY_TOLERANCE_MS = 250  # scheduling slack of the probe, the broker and the station
SILENCE_SLACK_S = 0.25    # a trigger this soon before the change line may still meet the silence


def read_collector(tar_path):
    """(receptions, probe records) from the collector's archive; None for a member that is missing."""
    out = {"receptions.jsonl": None, "probe.jsonl": None}
    with tarfile.open(tar_path) as tar:
        for member in tar.getmembers():
            name = member.name.rsplit("/", 1)[-1]
            if name in out:
                rows = []
                for line in io.TextIOWrapper(tar.extractfile(member), errors="replace"):
                    try:
                        rows.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue   # a line cut off when the container stopped
                out[name] = rows
    return out["receptions.jsonl"], out["probe.jsonl"]


def identities(status, logs):
    """stationId -> (station, time it was taken: None for the configured one), from status.json and the
    station logs; and station -> [(time, new stationId)] of its full ID changes."""
    from .overhead import log_time   # noqa: PLC0415 (overhead imports this module)
    owner, changes = {}, {}
    for st in status.get("stations", []):
        if st.get("station_id") is not None:
            owner[int(st["station_id"])] = (st["name"], None)
    for name, text in logs.items():
        for line in text.splitlines():
            if m := RE_IDC_FAC.search(line):
                t = log_time(line)
                owner[int(m[2])] = (name, t)
                changes.setdefault(name, []).append((t, int(m[2])))
    return owner, changes


def verification(receptions, owner, changes):
    """Security reports per receiver, and the failures right after each full ID change."""
    per_rx, by_pair = {}, {}
    for r in receptions:
        rx = per_rx.setdefault(r["rx"], {"received": 0, "success": 0, "failed": 0, "unsecured": 0, "reports": {}})
        rx["received"] += 1
        if not r.get("sec"):
            rx["unsecured"] += 1
        elif r.get("rep") == "Success":
            rx["success"] += 1
        else:
            rx["failed"] += 1
            rx["reports"][r.get("rep")] = rx["reports"].get(r.get("rep"), 0) + 1
        sender = owner.get(r.get("from"), (None, None))[0]
        if sender:
            by_pair.setdefault((r["rx"], sender), []).append(r)
    after_change = []
    for vehicle, seq in changes.items():
        for t, new_id in seq:
            failed = heard = 0
            for (rx, sender), rows in by_pair.items():
                if sender != vehicle:
                    continue
                first = next((r["t"] for r in rows if r.get("from") == new_id), None)
                if first is None:
                    continue
                heard += 1
                failed += sum(1 for r in rows if first <= r["t"] <= first + VERIFY_WINDOW_S
                              and r.get("sec") and r.get("rep") != "Success")
            after_change.append({"vehicle": vehicle, "t": t, "receivers": heard, "failed": failed})
    total_failed = sum(v["failed"] for v in per_rx.values())
    secured = sum(v["received"] - v["unsecured"] for v in per_rx.values())
    return {
        "window_s": VERIFY_WINDOW_S,
        "receivers": [{"receiver": k, **v} for k, v in sorted(per_rx.items())],
        "received": sum(v["received"] for v in per_rx.values()),
        "failed": total_failed,
        "failure_share": round(total_failed / secured, 5) if secured else None,
        "failed_after_change": sum(c["failed"] for c in after_change),
        "changes": after_change,
    }


def warnings(probe, receptions, owner, configured, history, outages):
    """Per triggered DENM and receiver: delay of the first copy received, or lost; explained by the sender's
    silent period or not. `history`: station -> read_station_log() result (changes with silent_ms);
    `configured`: station -> configured stationId; `outages`: [(from, to)] in Unix time."""
    triggers = [p for p in probe if p.get("event") == "trigger"]
    sends = {}
    for p in probe:
        if p.get("event") == "send":
            sends.setdefault(p["seq"], []).append(p["t"])
    if not triggers:
        return None
    repeat = None   # the repetition interval and duration, from the send times of the first repeated DENM
    for times in sends.values():
        if len(times) > 1:
            repeat = round(statistics.median(b - a for a, b in zip(times, times[1:])), 2)
            break
    receivers = sorted({r["rx"] for r in receptions})
    first, leaks, leak_examples = {}, 0, []
    trig_by_seq = {p["seq"]: p for p in triggers}
    for r in receptions:
        if r.get("m") != "denm" or r.get("seq") not in trig_by_seq:
            continue
        key = (r["seq"], r["rx"])
        if key not in first or r["t"] < first[key]:
            first[key] = r["t"]
        sender = trig_by_seq[r["seq"]]["station"]
        own = configured.get(sender)
        changed = any(c["t"] is not None and c["t"] < r["t"] for c in (history.get(sender) or {}).get("changes", []))
        if own is not None and changed and own in (r.get("hdr"), r.get("orig")):
            leaks += 1
            if len(leak_examples) < 3:
                leak_examples.append({"seq": r["seq"], "rx": r["rx"], "hdr": r.get("hdr"), "orig": r.get("orig")})
    rows = []
    for p in triggers:
        t, sender = p["t"], p["station"]
        # the silent period the trigger fell into: from the change line (logged within milliseconds of the
        # routers' lock) to its end; a trigger just before the change goes out under the old identity
        silence_left = before = 0.0   # before: the silence a trigger just ahead of the change line may meet
        for c in (history.get(sender) or {}).get("changes", []):
            if c["t"] is None:
                continue
            if c["t"] <= t <= c["t"] + c["silent_ms"] / 1000:
                silence_left = max(silence_left, c["t"] + c["silent_ms"] / 1000 - t)
            elif c["t"] - SILENCE_SLACK_S <= t < c["t"]:
                before = max(before, c["t"] + c["silent_ms"] / 1000 - t)
        span = (max(sends.get(p["seq"], [t])) - t)   # the time over which copies were sent
        outage = any(lo - 1 <= t <= hi + 1 for lo, hi in outages)
        for rx in receivers:
            if rx == sender:
                continue
            got = first.get((p["seq"], rx))
            delay = None if got is None else max(0, round((got - t) * 1000))
            # a trigger just before the change: in the silence only if its first copy did not go out in time
            left = silence_left or (before if got is None or delay >= (repeat or 1.0) * 1000 else 0.0)
            # explained: on time outside a silent period; delayed by about the remaining silence; lost when
            # the silence outlasted the repetitions
            if got is None:
                explained = left * 1000 > span * 1000 - DELAY_TOLERANCE_MS
            else:
                explained = delay <= left * 1000 + (repeat or 0) * 1000 + DELAY_TOLERANCE_MS
            rows.append({"seq": p["seq"], "sender": sender, "receiver": rx, "t": t,
                         "silent_left_ms": round(left * 1000), "delay_ms": delay, "lost": got is None,
                         "explained": explained or outage, "outage": outage})
    clean = [r for r in rows if not r["outage"]]
    got = [r["delay_ms"] for r in clean if not r["lost"]]
    on_time = [r for r in clean if not r["lost"] and r["delay_ms"] < ((repeat or 1.0) * 1000)]
    silent = [r for r in clean if r["silent_left_ms"] > 0]
    return {
        "triggers": len(triggers), "deliveries": len(clean), "repeat_interval_s": repeat,
        "on_time": len(on_time), "delayed": len(got) - len(on_time), "lost": sum(1 for r in clean if r["lost"]),
        "delay_ms_mean": round(statistics.mean(got)) if got else None,
        "delay_ms_p95": _pct(got, 0.95), "delay_ms_max": max(got, default=None),
        "in_silence": len(silent), "lost_in_silence": sum(1 for r in silent if r["lost"]),
        "lost_outside_silence": sum(1 for r in clean if r["lost"] and r["silent_left_ms"] == 0),
        "unexplained": sum(1 for r in clean if not r["explained"]),
        "id_leaks": leaks, "id_leak_examples": leak_examples,
        "during_outage": len(rows) - len(clean),
        "worst": sorted((r for r in clean if r["lost"] or r["delay_ms"] >= (repeat or 1.0) * 1000),
                        key=lambda r: (not r["lost"], -(r["delay_ms"] or 0)))[:20],
    }


def _pct(values, q):
    if not values:
        return None
    s = sorted(values)
    return s[min(len(s) - 1, int(round(q * (len(s) - 1))))]


def _xy(lat, lon, lat0, lon0):
    """Local metres east and north of (lat0, lon0)."""
    return ((lon - lon0) * 111195.0 * math.cos(math.radians(lat0)), (lat - lat0) * 111195.0)


def _velocity(speed, heading):
    return speed * math.sin(math.radians(heading)), speed * math.cos(math.radians(heading))


def ttc(pa, va, pb, vb, distance=CONFLICT_DISTANCE_M):
    """Time until two points moving at constant velocity come within `distance` (0 if they already are);
    None if they never do."""
    px, py = pb[0] - pa[0], pb[1] - pa[1]
    wx, wy = vb[0] - va[0], vb[1] - va[1]
    c = px * px + py * py - distance * distance
    if c <= 0:
        return 0.0
    a, b = wx * wx + wy * wy, 2 * (px * wx + py * wy)
    if a == 0 or b >= 0:
        return None   # not closing in
    disc = b * b - 4 * a * c
    if disc < 0:
        return None   # they pass each other farther apart
    return (-b - math.sqrt(disc)) / (2 * a)


def conflicts(tracks, cams, vehicle_of, stale_s, outages, extend):
    """Conflict exposure between every pair of vehicles. `tracks`: {station id: [(t, lat, lon, speed,
    heading)]} (5 Hz), `cams`: {vehicle: [(t, cert, bytes, full, lat, lon, speed, heading, sid)]} as the
    reference receiver heard them, `vehicle_of`: station id -> vehicle name. Ticks during outages are left out."""
    ids = [sid for sid in tracks if vehicle_of.get(sid) in cams]
    if len(ids) < 2:
        return None
    lat0, lon0 = tracks[ids[0]][0][1], tracks[ids[0]][0][2]
    by_time = {}
    for sid in ids:
        for t, lat, lon, speed, heading in tracks[sid]:
            by_time.setdefault(round(t, 3), {})[sid] = (_xy(lat, lon, lat0, lon0), _velocity(speed, heading))
    ticks = sorted(by_time)
    step = statistics.median(b - a for a, b in zip(ticks, ticks[1:])) if len(ticks) > 1 else 0.2
    cursor = {v: -1 for v in cams}
    state = {}   # (receiver, other) -> current conflict: {"start", "stale_all"}
    exposure = conflict_time = 0.0
    events, pairs = [], {}
    for t in ticks:
        if any(lo <= t <= hi for lo, hi in outages):
            continue
        now = by_time[t]
        latest = {}
        for v, rows in cams.items():
            k = cursor[v]
            while k + 1 < len(rows) and rows[k + 1][0] <= t:
                k += 1
            cursor[v] = k
            latest[v] = rows[k] if k >= 0 else None
        for ra in ids:
            for rb in ids:
                if ra == rb or ra not in now or rb not in now:
                    continue
                (pa, va), (pb, vb) = now[ra], now[rb]
                true = ttc(pa, va, pb, vb)
                key = (vehicle_of[ra], vehicle_of[rb])   # what ra's vehicle knows about rb's
                in_conflict = true is not None and true < CONFLICT_TTC_S
                cam = latest.get(vehicle_of[rb])
                stale = cam is None or t - cam[0] > stale_s or cam[4] is None
                missed = stale
                if not stale:
                    lat, lon = extend(cam[4], cam[5], cam[6], cam[7], t - cam[0])
                    believed = ttc(pa, va, _xy(lat, lon, lat0, lon0), _velocity(cam[6], cam[7]))
                    missed = believed is None or believed >= CONFLICT_TTC_S
                if in_conflict:
                    conflict_time += step
                    pairs.setdefault(key, {"conflict_s": 0.0, "exposure_s": 0.0})
                    pairs[key]["conflict_s"] += step
                    if missed:
                        exposure += step
                        pairs[key]["exposure_s"] += step
                    cur = state.setdefault(key, {"start": t, "stale_all": True, "exposure_s": 0.0})
                    cur["stale_all"] = cur["stale_all"] and stale
                    cur["exposure_s"] += step if missed else 0.0
                elif key in state:
                    cur = state.pop(key)
                    events.append({"receiver": key[0], "other": key[1], "from": cur["start"], "to": t,
                                   "missed": cur["stale_all"], "exposure_s": round(cur["exposure_s"], 2)})
    for key, cur in state.items():
        events.append({"receiver": key[0], "other": key[1], "from": cur["start"], "to": ticks[-1],
                       "missed": cur["stale_all"], "exposure_s": round(cur["exposure_s"], 2)})
    return {
        "ttc_s": CONFLICT_TTC_S, "distance_m": CONFLICT_DISTANCE_M, "stale_s": stale_s,
        "conflicts": len(events), "conflict_s": round(conflict_time, 1), "exposure_s": round(exposure, 1),
        "exposure_share": round(exposure / conflict_time, 3) if conflict_time else None,
        "missed": sum(1 for e in events if e["missed"]),
        "pairs": [{"receiver": a, "other": b, "conflict_s": round(v["conflict_s"], 1), "exposure_s": round(v["exposure_s"], 1)}
                  for (a, b), v in sorted(pairs.items())],
        "events": sorted(events, key=lambda e: -e["exposure_s"])[:50],
    }
