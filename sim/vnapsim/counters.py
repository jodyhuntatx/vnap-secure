"""The stations' security operation counters (phase 4 of the overhead design), for overhead.json.

Station images from vnap:r2-p20 log, every second, the cumulative count and total time of each kind
of security operation ([COUNTERS] lines, socktap --op-counters-interval), and the time of every
committed ID change ([IDCHANGE] completed in <us> us):

  sign, verify        outgoing messages signed, received messages verified (the whole service)
  verify_failed       ... verified without Success; unknown_signer: of which the signer was only a digest
                      of a certificate the receiver had not seen (a cache miss)
  ecdsa_sign/_verify  the backend's ECDSA operations (messages and certificates)
  chain_check         received ATs checked against their AA the first time they are seen
  batch_check         ATs of a refill batch checked against AA and root before they enter the pool
  derivation          butterfly private keys derived on the station
  id_change           committed ID changes, PREPARE to the last COMMIT

The differences between two lines say what happened in between. A change costs the vehicle its ID
change and its neighbours a chain check of the new AT each (and, while they have not yet seen it,
unknown signers); a refill costs the vehicle the derivations and batch checks. Neighbour work is
attributed to a change when it falls in the change's window, from the change to the end of its silent
period plus WINDOW_S; work in the windows of several changes is shared equally. Counts carry over to
other hardware, times only compare runs on one host.
"""

import json
import re
import statistics

RE_COUNTERS = re.compile(r"\[COUNTERS\] (\{.*\})")
RE_COMPLETED = re.compile(r"\[IDCHANGE\] completed in (\d+) us")
RE_INSTALLED = re.compile(r"\[PKI\] batch installed: request (\S+), (\d+) certificate\(s\)")

WINDOW_S = 3.0        # a neighbour checks the new AT when it first hears it: this soon after the silence
LEAD_S = 0.5          # ... and the counter line before the change may already hold it
REFILL_MATCH_S = 2.0  # derivations and batch checks belong to the batch installed this close
NEIGHBOUR = ("chain_check", "unknown_signer", "verify_failed")
REFILL = ("derivation", "batch_check")


def read(text):
    """(snapshots [(t, {name: [count, us]})], ID changes [(t, us)], refills [(t, request, certificates)])."""
    from .overhead import log_time   # noqa: PLC0415 (overhead imports this module)
    snaps, completed, installed = [], [], []
    for line in text.splitlines():
        if "[COUNTERS]" not in line and "[IDCHANGE] completed" not in line and "batch installed" not in line:
            continue
        t = log_time(line)
        if t is None:
            continue
        if m := RE_COUNTERS.search(line):
            try:
                snaps.append((t, json.loads(m[1])))
            except ValueError:
                pass
        elif m := RE_COMPLETED.search(line):
            completed.append((t, int(m[1])))
        elif m := RE_INSTALLED.search(line):
            installed.append((t, m[1], int(m[2])))
    return snaps, completed, installed


def deltas(snaps):
    """[(from, to, {name: [count, us]})] between consecutive lines; the first from the start of the process."""
    out, prev_t, prev = [], None, {}
    for t, vals in snaps:
        lo = prev_t if prev_t is not None else t - 1.0
        d = {k: [v[0] - prev.get(k, [0, 0])[0], v[1] - prev.get(k, [0, 0])[1]] for k, v in vals.items()}
        out.append((lo, t, d))
        prev_t, prev = t, vals
    return out


def _mean_us(c):
    return round(c[1] / c[0], 1) if c and c[0] else None


def analyse(logs, changes):
    """The counters of a run. `logs`: {station: log text}; `changes`: [{"vehicle", "t", "silent_ms"}] in Unix
    time. Returns None when no station logged counters (an image before vnap:r2-p20)."""
    data = {name: read(text) for name, text in logs.items()}
    data = {name: d for name, d in data.items() if d[0]}
    if not data:
        return None

    stations = []
    for name, (snaps, completed, _) in sorted(data.items()):
        (t_first, _), (t_last, last) = snaps[0], snaps[-1]
        span = t_last - t_first
        stations.append({
            "station": name, "span_s": round(span, 1),
            "totals": last,
            "sign_us_mean": _mean_us(last.get("sign")), "verify_us_mean": _mean_us(last.get("verify")),
            "ecdsa_sign_us_mean": _mean_us(last.get("ecdsa_sign")),
            "ecdsa_verify_us_mean": _mean_us(last.get("ecdsa_verify")),
            "chain_check_us_mean": _mean_us(last.get("chain_check")),
            "signs_per_s": round(last.get("sign", [0])[0] / span, 2) if span > 0 else None,
            "verifies_per_s": round(last.get("verify", [0])[0] / span, 2) if span > 0 else None,
        })

    # per change: the vehicle's own ID change, and what its neighbours did in its window
    windows = []
    for c in changes:
        if c.get("t") is None:
            continue
        own = data.get(c["vehicle"])
        took = None
        if own:
            near = [(abs(t - c["t"]), us) for t, us in own[1] if abs(t - c["t"]) <= 1.0]
            took = min(near)[1] if near else None
        windows.append({"c": c, "lo": c["t"] - LEAD_S, "hi": c["t"] + (c.get("silent_ms") or 0) / 1000 + WINDOW_S,
                        "id_change_us": took, "receivers": 0, **{k: 0.0 for k in NEIGHBOUR}, "chain_check_us": 0.0})
    for name, (snaps, _, _) in data.items():
        for w in windows:
            if w["c"]["vehicle"] != name:
                w["receivers"] += 1
        for lo, hi, d in deltas(snaps):
            if not any(d.get(k, [0])[0] for k in NEIGHBOUR):
                continue
            hit = [w for w in windows if w["c"]["vehicle"] != name and lo < w["hi"] and hi > w["lo"]]
            for w in hit:
                for k in NEIGHBOUR:
                    w[k] += d.get(k, [0])[0] / len(hit)
                w["chain_check_us"] += d.get("chain_check", [0, 0])[1] / len(hit)
    per_change = [{"vehicle": w["c"]["vehicle"], "t": w["c"]["t"], "id_change_us": w["id_change_us"],
                   "receivers": w["receivers"], **{k: round(w[k], 2) for k in NEIGHBOUR},
                   "chain_check_us": round(w["chain_check_us"])} for w in windows]

    # per refill: the derivations and batch checks of the batch installed closest to them
    refills = []
    for name, (snaps, _, installed) in sorted(data.items()):
        rows = [{"station": name, "t": t, "request": req, "certificates": n, **{k: [0, 0] for k in REFILL}}
                for t, req, n in installed]
        for lo, hi, d in deltas(snaps):
            if not rows or not any(d.get(k, [0])[0] for k in REFILL):
                continue
            mid = (lo + hi) / 2
            best = min(rows, key=lambda r: abs(r["t"] - mid))
            if abs(best["t"] - mid) > REFILL_MATCH_S + (hi - lo) / 2:
                continue
            for k in REFILL:
                best[k] = [best[k][0] + d.get(k, [0, 0])[0], best[k][1] + d.get(k, [0, 0])[1]]
        refills += rows

    took = [c["id_change_us"] for c in per_change if c["id_change_us"] is not None]
    heard = [c for c in per_change if c["receivers"]]
    total = lambda key: [sum(s["totals"].get(key, [0, 0])[i] for s in stations) for i in (0, 1)]  # noqa: E731
    per_check = total("chain_check")
    derived, batch = total("derivation"), total("batch_check")
    return {
        "stations": stations, "changes": per_change, "refills": refills,
        "settings": {"window_s": WINDOW_S, "lead_s": LEAD_S},
        "summary": {
            "id_change_us_mean": round(statistics.mean(took)) if took else None,
            "id_change_us_max": max(took) if took else None,
            # every neighbour should check a new AT once: chain checks per change near the number of receivers
            "chain_checks_per_change": round(statistics.mean(c["chain_check"] for c in heard), 2) if heard else None,
            "receivers_per_change": round(statistics.mean(c["receivers"] for c in heard), 2) if heard else None,
            "unknown_signer_per_change": round(statistics.mean(c["unknown_signer"] for c in heard), 2) if heard else None,
            "chain_check_us_mean": _mean_us(per_check),
            "chain_check_us_per_change": round(statistics.mean(c["chain_check_us"] for c in heard)) if heard else None,
            "refills": len(refills),
            "derivations": derived[0], "derivation_us_mean": _mean_us(derived),
            "batch_checks": batch[0], "batch_check_us_mean": _mean_us(batch),
            "sign_us_mean": _mean_us(total("sign")), "verify_us_mean": _mean_us(total("verify")),
            "ecdsa_sign_us_mean": _mean_us(total("ecdsa_sign")), "ecdsa_verify_us_mean": _mean_us(total("ecdsa_verify")),
            "signs": total("sign")[0], "verifies": total("verify")[0],
        },
    }
