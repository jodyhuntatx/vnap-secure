#!/usr/bin/env python3
"""Verification collector, warning probe (phase 2 of the pseudonym-change overhead design) and CPU
sampler (phase 3).

Collector: subscribes to every station's embedded broker on the message network and records, for
each message the station received, what the station made of it: sender stationId, security report,
size and the station's receive time. These are the receivers' own views, next to the
eavesdropper's reference view of the link.

Probe: triggers DENMs on chosen stations at random times (about one per `interval_s` each) by
publishing on their `vanetza/in/denm`, and repeats each one every `repeat_interval_s` for
`repeat_duration_s`, as the DEN basic service repeats a DENM (EN 302 637-3). A DENM triggered
during a silent period is dropped with every other frame; its repetitions arrive after the silence
(delayed) or not at all (lost). The collector's DENM records at the receivers tell which.

CPU sampler: reads the cumulative CPU time (cpu.stat usage_usec) of the stations' containers from
their cgroup directories, mounted read-only at /cpu/<name>, every `interval_ms`. The cgroups belong
to the containers, so the stations' own code is not involved and the sampler's work is not counted.

  COLLECTOR_CONFIG  JSON: {"stations": [{"name", "ip", "station_id"}], "topics": [...],
                           "probe": null | {"stations": [names], "interval_s", "repeat_interval_s",
                                            "repeat_duration_s", "seed"},
                           "cpu": null | {"interval_ms", "containers": [names under /cpu]}}

Output in /logs (a tmpfs, collected before the run is removed):
  receptions.jsonl  {"t", "rx", "m", "from", "sec", "rep", "len"} per received message; DENMs also
                    "hdr" (header stationId), "orig" and "seq" (actionId)
  probe.jsonl       {"t", "station", "seq", "event": "trigger" | "send", "n"} per DENM sent
  cpu.jsonl         {"t", "u": {name: usage_usec}} per sample
"""

import heapq
import json
import os
import random
import signal
import sys
import threading
import time

import paho.mqtt.client as mqtt

LOG_DIR = "/logs"
CPU_DIR = "/cpu"
ITS_EPOCH = 1072915200   # 2004-01-01T00:00:00Z: TimestampIts counts from here (milliseconds; in vanetza's JSON, seconds)
SEQ_MAX = 65535


class Writer:
    """Line-buffered JSONL from several threads."""

    def __init__(self, path):
        self.f = open(path, "a", buffering=1)
        self.lock = threading.Lock()

    def write(self, rec):
        line = json.dumps(rec, separators=(",", ":")) + "\n"
        with self.lock:
            self.f.write(line)


def reception(station, topic, msg, now):
    """One received message as a compact record, or None for a topic we do not record."""
    kind = topic.rsplit("/", 1)[-1]
    report = msg.get("security_report") or {}
    rec = {"t": msg.get("timestamp") or now, "rx": station, "m": kind, "from": msg.get("stationID"),
           "sec": bool(msg.get("secured")), "rep": report.get("description"), "len": msg.get("packet_size")}
    if kind == "denm":
        fields = msg.get("fields") or {}
        action = ((fields.get("denm") or {}).get("management") or {}).get("actionId") or {}
        rec.update({"hdr": (fields.get("header") or {}).get("stationId"),
                    "orig": action.get("originatingStationId"), "seq": action.get("sequenceNumber")})
    return rec


def denm_payload(station_id, seq, reference_s, validity_s):
    """A DENM (payload only: socktap adds the header with the current stationId). The actionId carries the
    configured stationId, which the station replaces with its current one; `$latitude`/`$longitude` are
    filled by the station with its own position."""
    return ('{"management":{"actionId":{"originatingStationId":%d,"sequenceNumber":%d},'
            '"detectionTime":%.3f,"referenceTime":%.3f,'
            '"eventPosition":{"latitude":$latitude,"longitude":$longitude,'
            '"positionConfidenceEllipse":{"semiMajorConfidence":5,"semiMinorConfidence":5,"semiMajorOrientation":0},'
            '"altitude":{"altitudeValue":100,"altitudeConfidence":4}},'
            '"validityDuration":%d,"stationType":5},'
            '"situation":{"informationQuality":7,"eventType":{"ccAndScc":{"collisionRisk97":1}}}}'
            % (station_id, seq, reference_s, reference_s, validity_s))


def usage_usec(path):
    """Cumulative CPU time of a cgroup in microseconds (cpu.stat), or None while it cannot be read."""
    try:
        with open(path) as f:
            for line in f:
                if line.startswith("usage_usec "):
                    return int(line.split()[1])
    except (OSError, ValueError):
        pass
    return None


def run_cpu(cfg, running):
    """Sample the containers' CPU time until stopped; a container whose cgroup is gone (stopped) is left out."""
    names = [n for n in cfg.get("containers") or [] if os.path.isdir(os.path.join(CPU_DIR, n))]
    if not names:
        print("[CPU] no cgroup mounted: no CPU samples", flush=True)
        return
    interval = max(10, int(cfg.get("interval_ms") or 100)) / 1000
    out = Writer(os.path.join(LOG_DIR, "cpu.jsonl"))
    paths = {n: os.path.join(CPU_DIR, n, "cpu.stat") for n in names}
    print(f"[CPU] sampling {', '.join(names)} every {interval * 1000:g} ms", flush=True)
    due = time.monotonic()
    while running.is_set():
        now = time.time()
        u = {n: v for n, p in paths.items() if (v := usage_usec(p)) is not None}
        out.write({"t": round(now, 4), "u": u})
        due += interval
        time.sleep(max(0.0, due - time.monotonic()))


def main():
    try:
        cfg = json.loads(os.environ["COLLECTOR_CONFIG"])
        stations = {s["name"]: s for s in cfg["stations"]}
    except (KeyError, ValueError, TypeError) as e:
        sys.exit(f"collector: bad COLLECTOR_CONFIG: {e}")
    topics = cfg.get("topics") or ["vanetza/out/cam", "vanetza/out/denm"]
    probe = cfg.get("probe")
    os.makedirs(LOG_DIR, exist_ok=True)
    out = Writer(os.path.join(LOG_DIR, "receptions.jsonl"))
    sent = Writer(os.path.join(LOG_DIR, "probe.jsonl")) if probe else None

    connected, lock, live = set(), threading.Lock(), threading.Event()
    clients = {}

    def on_connect(client, name, flags, rc, props):
        if rc != 0:
            print(f"[COLLECTOR] {name}: connect refused ({rc})", flush=True)
            return
        for topic in topics:
            client.subscribe(topic, qos=0)
        with lock:
            first = name not in connected
            connected.add(name)
            if first:
                print(f"[COLLECTOR] {name}: subscribed to {', '.join(topics)}", flush=True)
            if len(connected) == len(stations) and not live.is_set():
                live.set()
                print(f"[COLLECTOR] live: {len(stations)} station(s)", flush=True)

    def on_message(client, name, message):
        try:
            msg = json.loads(message.payload)
        except ValueError:
            return
        rec = reception(name, message.topic, msg, time.time())
        if rec:
            out.write(rec)

    for name, st in stations.items():
        c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"vnap-collector-{name}", userdata=name)
        c.on_connect, c.on_message = on_connect, on_message
        c.on_disconnect = lambda cl, n, f, rc, p: print(f"[COLLECTOR] {n}: disconnected ({rc}), reconnecting", flush=True)
        # the station's embedded broker starts a few seconds after the container
        c.reconnect_delay_set(1, 5)
        c.connect_async(st["ip"], 1883, 30)
        c.loop_start()
        clients[name] = c

    running = threading.Event()
    running.set()
    signal.signal(signal.SIGTERM, lambda *_: running.clear())
    signal.signal(signal.SIGINT, lambda *_: running.clear())

    if cfg.get("cpu"):
        threading.Thread(target=run_cpu, args=(cfg["cpu"], running), daemon=True).start()
    if not probe:
        while running.is_set():
            time.sleep(0.5)
        return
    run_probe(probe, stations, clients, sent, live, running)


def run_probe(probe, stations, clients, sent, live, running):
    rng = random.Random(probe.get("seed"))
    interval = float(probe["interval_s"])
    repeat, duration = float(probe["repeat_interval_s"]), float(probe["repeat_duration_s"])
    validity = max(1, int(round(duration)))
    senders = [n for n in probe["stations"] if n in stations]
    print(f"[PROBE] DENMs on {', '.join(senders)}: one per {interval:g} s on average each, repeated every "
          f"{repeat:g} s for {duration:g} s", flush=True)
    while running.is_set() and not live.wait(0.5):
        pass
    queue, seq = [], 0   # (time, order, kind, station, seq, n, payload)
    order = 0
    start = time.time()
    for name in senders:
        # a random phase per station, so that the stations do not trigger together
        heapq.heappush(queue, (start + rng.uniform(0, interval), order, "trigger", name, 0, 0, None))
        order += 1
    while running.is_set():
        wait = queue[0][0] - time.time()   # read the clock once: a second read may already be past `when`
        if wait > 0:
            time.sleep(min(0.05, wait))
            continue
        _, _, kind, name, s, n, payload = heapq.heappop(queue)
        now = time.time()
        if kind == "trigger":
            seq = seq % SEQ_MAX + 1   # unique within the run while fewer than 65535 are triggered
            s, n = seq, 0
            payload = denm_payload(stations[name]["station_id"], s, now - ITS_EPOCH, validity)
            sent.write({"t": round(now, 6), "station": name, "seq": s, "event": "trigger"})
            # the next trigger: uniform around the mean interval
            heapq.heappush(queue, (now + rng.uniform(0.5, 1.5) * interval, order, "trigger", name, 0, 0, None))
            order += 1
        clients[name].publish("vanetza/in/denm", payload, qos=0)
        sent.write({"t": round(now, 6), "station": name, "seq": s, "event": "send", "n": n})
        if (n + 1) * repeat < duration:
            heapq.heappush(queue, (now + repeat, order, "send", name, s, n + 1, payload))
            order += 1


if __name__ == "__main__":
    main()
