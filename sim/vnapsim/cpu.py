"""The stations' CPU time (phase 3 of the overhead design), for overhead.json.

The collector samples the cumulative CPU time of every station container and of the run's PKI from
their cgroups about 10 times a second (collector-logs.tar: cpu.jsonl, {"t", "u": {name: usage_usec}}).
The cgroup holds everything the container runs, so this catches work the operation counters do not
(MQTT, JSON, logging, the router), and it does not depend on the station code.

Per station: CPU time over the run as milliseconds of CPU per second (1000 = one core), the 95th
percentile over 1 s windows, and a 1 s timeline. The cost of a change is not read from a window around
it: a change costs a few milliseconds against about 60 ms/s of other work, and when vehicles change
together their silent periods lower everyone's load at the same moment. It comes from the sweep instead
(vnapsim.sweep): the rate against the change rate over runs, with a run without changes as the baseline.
Times are for comparisons on one host. A paused VM leaves a gap between samples (settings.gap_s_max) and
makes the rates too low; the sweep reruns such runs.
"""

import bisect
import io
import json
import statistics
import tarfile


def read(tar_path):
    """Samples from the collector's tar: (times, {name: [usage_usec per sample or None]}); None without cpu.jsonl."""
    try:
        with tarfile.open(tar_path) as tar:
            member = next((m for m in tar.getmembers() if m.name.endswith("cpu.jsonl")), None)
            if member is None:
                return None
            text = io.TextIOWrapper(tar.extractfile(member), errors="replace").read()
    except (OSError, tarfile.TarError):
        return None
    rows = []
    for line in text.splitlines():
        try:
            rec = json.loads(line)
            rows.append((float(rec["t"]), rec.get("u") or {}))
        except (ValueError, KeyError, TypeError):
            continue
    if not rows:
        return None
    rows.sort(key=lambda r: r[0])
    names = sorted({n for _, u in rows for n in u})
    return [t for t, _ in rows], {n: [u.get(n) for _, u in rows] for n in names}


class Series:
    """One container's cumulative CPU time, interpolated between samples (microseconds)."""

    def __init__(self, times, values):
        pts = [(t, v) for t, v in zip(times, values) if v is not None]
        self.t = [p[0] for p in pts]
        self.u = [p[1] for p in pts]

    def at(self, t):
        if not self.t:
            return None
        if t <= self.t[0]:
            return self.u[0]
        if t >= self.t[-1]:
            return self.u[-1]
        i = bisect.bisect_right(self.t, t)
        t0, t1, u0, u1 = self.t[i - 1], self.t[i], self.u[i - 1], self.u[i]
        return u0 + (u1 - u0) * (t - t0) / (t1 - t0) if t1 > t0 else u0

    def used_ms(self, a, b):
        """CPU milliseconds between a and b, within the sampled span."""
        a, b = max(a, self.t[0]), min(b, self.t[-1])
        return (self.at(b) - self.at(a)) / 1000 if b > a else 0.0

    def span(self):
        return (self.t[0], self.t[-1]) if self.t else (None, None)


def _r(x, n=1):
    return None if x is None else round(x, n)


def analyse(samples, roles):
    """`samples` from read(); `roles`: {name: "vehicle" | "rsu" | "pki" | ...}. None without samples."""
    if not samples:
        return None
    times, values = samples
    series = {n: Series(times, v) for n, v in values.items()}
    series = {n: s for n, s in series.items() if len(s.t) >= 2}
    if not series:
        return None
    t0 = min(s.t[0] for s in series.values())
    stations = []
    for name, s in sorted(series.items()):
        lo, hi = s.span()
        per_s = [s.used_ms(lo + k, lo + k + 1.0) for k in range(int(hi - lo))]
        stations.append({
            "station": name, "role": roles.get(name), "span_s": _r(hi - lo),
            "cpu_s": _r(s.used_ms(lo, hi) / 1000, 2),
            "ms_per_s": _r(s.used_ms(lo, hi) / (hi - lo), 2) if hi > lo else None,
            "ms_per_s_p95": _r(sorted(per_s)[int(0.95 * (len(per_s) - 1))], 2) if per_s else None,
            # 1 s windows from the first sample of the run: [seconds after it, ms]
            "timeline": [[_r(lo - t0 + k), _r(v)] for k, v in enumerate(per_s)],
        })
    by_role = lambda role: [st["ms_per_s"] for st in stations if st["role"] == role and st["ms_per_s"] is not None]  # noqa: E731
    mean = lambda xs: _r(statistics.mean(xs), 2) if xs else None  # noqa: E731
    return {
        "settings": {"samples": len(times),
                     "interval_ms": _r(1000 * (times[-1] - times[0]) / (len(times) - 1)) if len(times) > 1 else None,
                     # a long gap between samples: the host (a VM) was paused, and the rates over the run are too low
                     "gap_s_max": _r(max((b - a for a, b in zip(times, times[1:])), default=0.0), 2)},
        "stations": stations,
        "summary": {
            "vehicle_ms_per_s": mean(by_role("vehicle")), "rsu_ms_per_s": mean(by_role("rsu")),
            "pki_ms_per_s": mean(by_role("pki")),
            "stations_ms_per_s": _r(sum(st["ms_per_s"] or 0 for st in stations if st["role"] != "pki"), 2),
        },
    }
