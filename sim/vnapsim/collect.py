"""The results of a running simulation, kept after it stops: what `vnapctl collect` and the service
write into a results directory, and the analyses made from it.

  <container>.log          every container's log (docker logs --timestamps)
  eavesdropper-logs.tar    the eavesdropper's /logs: messages.jsonl (every frame), events.jsonl, tracks.json
  positions.jsonl          the vehicles' true positions (the mobility client's recorder)
  pki-issued.jsonl         the run's PKI issue log (digests only, never keys)
  cert-sizes.json          the sizes of the stations' authorization tickets and keys (not their content)
  status.json, score.json  the state of the run and the scoring of the eavesdropper
  overhead.json            privacy and cost of the pseudonym changes (vnapsim.overhead)
  metrics.json             the `vnapctl check` metrics over the whole run, from the files above

The analyses read only the directory, so they can be made again on stored runs (`vnapctl overhead`,
`vnapctl collect --analyse`). A failing analysis writes <name>-error.txt and never loses the raw files.
"""

import json
import os
import shutil
import subprocess
import time
from datetime import datetime, timezone
import traceback

from .check import compute_metrics
from .common import docker
from .logs import parse_pki_log, parse_station_log
from .overhead import RE_CHANGED, RE_POOL_START, read_frames, write as write_overhead
from .scoring import score_run
from .status import Simulation, build_status

METRICS_VERSION = 1


def collect(sim, out):
    """Write the raw results of the running simulation `sim` into `out`; returns its status."""
    os.makedirs(out, mode=0o700, exist_ok=True)
    containers = sim.stations + [c for c in (sim.broker, sim.client, sim.mobility, sim.pki) if c] + sim.observers
    for info in containers:
        name = Simulation.name(info)
        with open(os.path.join(out, f"{name}.log"), "w") as f:
            f.write(docker("logs", "--timestamps", name, check=False))
    for info in sim.observers:
        # /logs is a tmpfs, which docker cp cannot read: tar it from inside (the same layout, logs/...)
        with open(os.path.join(out, "eavesdropper-logs.tar"), "wb") as f:
            subprocess.run(["docker", "exec", Simulation.name(info), "tar", "-C", "/", "-cf", "-", "logs"],
                           stdout=f, stderr=subprocess.DEVNULL)
    if sim.mobility:
        positions = docker("exec", Simulation.name(sim.mobility), "cat", "/logs/positions.jsonl", check=False)
        if positions:
            with open(os.path.join(out, "positions.jsonl"), "w") as f:
                f.write(positions)
    if sim.pki:
        with open(os.path.join(out, "pki-issued.jsonl"), "w") as f:
            f.write(docker("exec", Simulation.name(sim.pki), "cat", "/pki/private/issued.jsonl", check=False))
    sizes = cert_sizes(sim)
    if sizes:
        with open(os.path.join(out, "cert-sizes.json"), "w") as f:
            json.dump(sizes, f, indent=1)
    status = build_status(sim)
    with open(os.path.join(out, "status.json"), "w") as f:
        json.dump(status, f, indent=1)
    score = score_run(sim)
    if score is not None:
        with open(os.path.join(out, "score.json"), "w") as f:
            json.dump(score, f, indent=1)
    return status


def cert_sizes(sim):
    """Sizes in bytes of each pseudonym station's initial authorization tickets and keys, read in its container
    (the files of PSEUDO_CERT_n / PSEUDO_KEY_n): {station: {"at": [...], "key": [...]}}. Refilled ones are the
    same kind and size."""
    out = {}
    for info in sim.stations:
        env = dict(e.split("=", 1) for e in (info["Config"].get("Env") or []) if "=" in e)
        certs = [env[k] for k in sorted(env) if k.startswith("PSEUDO_CERT_")]
        keys = [env[k] for k in sorted(env) if k.startswith("PSEUDO_KEY_")]
        if not certs:
            continue
        text = docker("exec", Simulation.name(info), "sh", "-c",
                      'for f in "$@"; do wc -c < "$f" 2>/dev/null || echo -; done', "sh", *certs, *keys, check=False)
        values = [int(v) if v.strip().isdigit() else None for v in text.split()]
        if len(values) == len(certs) + len(keys):
            out[Simulation.name(info)] = {"at": values[:len(certs)], "key": values[len(certs):]}
    return out


def parse_label_time(text):
    """The vnap.started_at label (UTC, ISO 8601) as a Unix time, as the service's run.json has it; None if unreadable."""
    try:
        return datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp()
    except (TypeError, ValueError):
        return None


def write_run_record(sim, out):
    """run.json and scenario.toml for a command-line run, from the labels vnapctl up put on its containers
    (the service writes its own, with the run's database record). stopped_at is the time of collection."""
    labels = (sim.stations[0]["Config"].get("Labels") or {}) if sim.stations else {}
    record = {"run_id": labels.get("vnap.run_id"), "scenario": labels.get("vnap.scenario"),
              "variant": labels.get("vnap.variant"), "overrides": json.loads(labels.get("vnap.overrides") or "[]"),
              "instance": labels.get("vnap.instance"), "started_by": labels.get("vnap.started_by"),
              "started_at": parse_label_time(labels.get("vnap.started_at")), "stopped_at": time.time(),
              "stop_reason": "vnapctl collect",
              "seed": ((json.loads(_read(out, "status.json") or "{}").get("control") or {}).get("mobility") or {}).get("seed")}
    with open(os.path.join(out, "run.json"), "w") as f:
        json.dump(record, f, indent=1)
    path = labels.get("vnap.scenario_file")
    if path and os.path.isfile(path):
        shutil.copy(path, os.path.join(out, "scenario.toml"))
    return record


def analyse(out):
    """overhead.json, then metrics.json (which uses it), from a results directory. A failure is written to
    <name>-error.txt; returns the names that failed."""
    failed = []
    for name, step in (("overhead", write_overhead), ("metrics", write_metrics)):
        error = os.path.join(out, f"{name}-error.txt")
        try:
            step(out)
            if os.path.exists(error):
                os.remove(error)
        except Exception:  # noqa: BLE001  (the raw results stay; the error is kept next to them)
            with open(error, "w") as f:
                f.write(traceback.format_exc())
            failed.append(name)
    return failed


def _read(out, name):
    try:
        with open(os.path.join(out, name), errors="replace") as f:
            return f.read()
    except OSError:
        return None


def write_metrics(out):
    """metrics.json: the `vnapctl check` metrics over the whole run from the saved logs (not only over a
    check window), the CAMs each station sent as the reference receiver heard them, and the per-vehicle
    cost figures of overhead.json under check-style names."""
    status = json.loads(_read(out, "status.json") or "{}")
    if not status.get("stations"):
        raise ValueError("no status.json with stations")
    events, owner = [], {}
    for st in status["stations"]:
        text = _read(out, f"{st['name']}.log") or ""
        events += parse_station_log(text, st["name"])
        for line in text.splitlines():
            if (m := RE_POOL_START.search(line)) or (m := RE_CHANGED.search(line)):
                owner[m[4]] = st["name"]
    pki = (status.get("control") or {}).get("pki")
    if pki:
        events += [dict(ev, station="pki") for ev in parse_pki_log(_read(out, f"{pki['name']}.log") or "")]
    times = [ev["t"] for ev in events if ev["station"] != "pki"]
    seconds = (max(times) - min(times)) if times else 0
    m = compute_metrics(None, status, events, seconds)

    # tx: CAMs on the link per station (vehicles by their certificates, the others by station id)
    tar = os.path.join(out, "eavesdropper-logs.tar")
    frames = read_frames(tar) if os.path.isfile(tar) else None
    if frames:
        by_id = {st.get("station_id"): st["name"] for st in status["stations"]}
        for fr in frames:
            name = owner.get(fr[1]) or by_id.get(fr[8])
            if name:
                m[f"{name}.cam.tx"] = m.get(f"{name}.cam.tx", 0) + 1

    o = json.loads(_read(out, "overhead.json") or "{}")
    for v in o.get("vehicles", []):
        n = v["vehicle"]
        own = [c for c in o.get("changes", []) if c["vehicle"] == n and c["gap_ms"] is not None and not c.get("outage")]
        for key, value in (("gap_ms_max", max((c["gap_ms"] for c in own), default=None)),
                           ("gap_ms_mean", round(sum(c["gap_ms"] for c in own) / len(own)) if own else None),
                           ("stale_share", v.get("stale_share")), ("delivery_ratio", v.get("delivery_ratio")),
                           ("ipg_ms_p95", v.get("gap_ms_p95")), ("ipg_ms_max", v.get("gap_ms_max")),
                           ("pos_error_m_p95", v.get("pos_error_m_p95")),
                           ("pseudonym.lifetime_s_mean", v.get("lifetime_s_mean")),
                           ("pool_bytes", v.get("pool_bytes")), ("followed_s_max", v.get("followed_s_max"))):
            if value is not None:
                m[f"{n}.{key}"] = value
    s = o.get("summary") or {}
    for key, value in (("msg.cam.bytes_mean", s.get("cam_bytes_mean")), ("msg.cert_bytes", s.get("cert_bytes")),
                       ("privacy.tracking_success", s.get("tracking_success")), ("privacy.sync_ratio", s.get("sync_ratio")),
                       ("privacy.followed_s_mean", s.get("followed_s_mean")), ("privacy.followed_s_max", s.get("followed_s_max")),
                       ("privacy.followed_m_max", s.get("followed_m_max")),
                       ("changes.total", s.get("changes")), ("changes.unexplained", s.get("unexplained")),
                       ("changes.during_outage", s.get("outage_changes")),
                       ("pos_error_m_p95", s.get("pos_error_m_p95"))):
        if value is not None:
            m[key] = value
    if o:
        m["outages"] = len(o.get("outages", []))
        m["outages.s"] = round(sum(x["s"] for x in o.get("outages", [])), 3)

    result = {
        "version": METRICS_VERSION,
        "window": {"from": min(times) if times else None, "to": max(times) if times else None, "seconds": round(seconds, 3)},
        # rx and verification come from the stations' MQTT output, which only a live check records
        "not_collected": ["<station>.<message>.rx, .rx_from.*, .success_rate, .failed (verification collector, "
                          "phase 2 of the overhead design)", "control.* (the control channel, recorded live only)"],
        "metrics": dict(sorted(m.items())),
    }
    tmp = os.path.join(out, "metrics.json.tmp")
    with open(tmp, "w") as f:
        json.dump(result, f, indent=1)
    os.replace(tmp, os.path.join(out, "metrics.json"))
    return result
