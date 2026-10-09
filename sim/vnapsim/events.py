"""Live event collection (MQTT probes) and descriptions."""

import argparse
import hashlib
import math
import random
import ipaddress
import json
import re
import shutil
import subprocess
import sys
import threading
import os
import time
import tomllib
from datetime import datetime, timezone

from .common import MQTT_IMAGE, debug
from .logs import log_events, pki_log_events
from .status import Simulation


class MqttProbe(threading.Thread):
    """Subscribe to a broker for a bounded time via a throwaway mosquitto_sub container."""

    def __init__(self, sim, network, host, topics, label, seconds, sink, run_id, auth=None):
        super().__init__(daemon=True)
        self.sim, self.label, self.sink = sim, label, sink
        cmd = ["docker", "run", "--rm", "--network", network, "--label", f"vnapctl.probe={run_id}", MQTT_IMAGE,
               "mosquitto_sub", "-h", host, "-F", "%U %t %p", "-W", str(max(1, int(seconds + 0.999)))]
        for t in topics:
            cmd += ["-t", t]
        if auth:
            cmd += ["-u", auth[0], "-P", auth[1]]
        self.cmd = cmd
        self.proc = None

    def run(self):
        self.proc = subprocess.Popen(self.cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
        first = True
        for line in self.proc.stdout:
            if first:
                debug(f"probe {self.label}: first message")
                first = False
            parts = line.rstrip("\n").split(" ", 2)
            if len(parts) == 3:
                self.sink(self.label, float(parts[0]), parts[1], parts[2])
        self.proc.wait()
        debug(f"probe {self.label}: exited ({self.proc.returncode})")

    def stop(self):
        """Stop reading: kill the local docker client. The container ends by itself (-W)."""
        if self.proc and self.proc.poll() is None:
            self.proc.kill()

    @staticmethod
    def kill_all(run_id):
        """Remove this run's probe containers (running, or created but never started when the
        daemon stalls), without waiting for the daemon. A stalled daemon can still create a probe
        after the first sweep (it then stays in "created" for good), so sweep again later."""
        sweep = f"docker ps -aq --filter label=vnapctl.probe={run_id} | xargs -r docker rm -f -v"
        subprocess.Popen(["sh", "-c", f"for i in 1 2 3 4; do {sweep}; sleep 15; done"],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)


def mqtt_event(sim, ids, label, t, topic, payload):
    """Normalize one MQTT message into an event."""
    try:
        msg = json.loads(payload)
    except json.JSONDecodeError:
        msg = {"raw": payload[:200]}
    parts = topic.split("/")
    if parts[0] == "vanetza" and len(parts) == 3 and parts[1] in ("out", "own"):
        ev = {"t": t, "station": label, "kind": "rx" if parts[1] == "out" else "tx", "type": parts[2]}
        if parts[1] == "out":
            sender = msg.get("stationID")
            report = msg.get("security_report") or {}
            ev.update({"from": ids.get(sender, str(sender)), "from_id": sender, "from_addr": msg.get("stationAddr"),
                       "secured": msg.get("secured"), "report": report.get("description"),
                       "size": msg.get("packet_size"), "rssi": msg.get("rssi")})
        return ev
    if parts[:2] == ["vnap", "pseudonym"] and len(parts) == 4:
        station = ids.get(int(parts[2]), parts[2]) if parts[2].isdigit() else parts[2]
        ev = {"t": t, "station": station, "kind": "control", "direction": "event" if parts[3] == "change" else "status"}
        ev.update({k: msg[k] for k in ("event_id", "index", "reason", "result", "error", "previous", "certificate", "raw")
                   if k in msg})
        return ev
    return None


def collect_events(sim, duration, since=None, count=None, kinds=None, stations=None):
    """Live MQTT events for `duration` seconds plus log events from `since` (or the window start)."""
    ids = sim.station_by_id()
    events, lock, done = [], threading.Lock(), threading.Event()

    def keep(ev):
        return ev and (not kinds or ev["kind"] in kinds) and (not stations or ev["station"] in stations)

    def sink(label, t, topic, payload):
        ev = mqtt_event(sim, ids, label, t, topic, payload)
        if keep(ev):
            with lock:
                events.append(ev)
                if count and len(events) >= count:
                    done.set()

    start = time.time()
    run_id = f"{int(start * 1000)}-{threading.get_ident()}"
    probes = []
    if duration > 0:
        if not kinds or kinds & {"rx", "tx"}:
            for info in sim.stations:
                name = Simulation.name(info)
                if info["State"]["Status"] == "running":  # station filter is applied to the events
                    probes.append(MqttProbe(sim, sim.lan, sim.ip(info, sim.lan), ["vanetza/out/#", "vanetza/own/#"],
                                            name, duration, sink, run_id))
        if sim.broker and sim.broker["State"]["Status"] == "running" and (not kinds or "control" in kinds):
            probes.append(MqttProbe(sim, sim.ctl, sim.ip(sim.broker, sim.ctl), ["vnap/pseudonym/#"],
                                    "control", duration, sink, run_id, sim.control_auth()))
        for p in probes:
            p.start()
        # mosquitto_sub -W ends each probe `duration` after it connects; wait for that
        # (container start-up adds ~1 s), and stop early only when --count is reached
        deadline = start + duration + 8
        while any(p.is_alive() for p in probes) and not done.is_set() and time.time() < deadline:
            done.wait(0.2)
        if any(p.is_alive() for p in probes):
            debug("stopping probes" + (" (--count reached)" if done.is_set() else " (deadline)"))
            MqttProbe.kill_all(run_id)
            for p in probes:
                p.stop()
            for p in probes:
                p.join(timeout=1)

    log_since = since if since is not None else start
    for info in sim.stations:
        for ev in log_events(Simulation.name(info), since=log_since):
            if keep(ev):
                events.append(ev)
    if sim.pki:
        for ev in pki_log_events(Simulation.name(sim.pki), since=log_since):
            if keep(ev):
                events.append(ev)
    # station IDs may have changed during the window (full ID change): name senders afterwards
    final_ids = sim.station_by_id()
    for ev in events:
        if ev["kind"] == "rx" and ev.get("from_id") in final_ids:
            ev["from"] = final_ids[ev["from_id"]]
    events.sort(key=lambda e: e["t"])
    if count:
        events = events[:count]
    return events, start, time.time()


def describe(ev):
    k = ev["kind"]
    if k == "rx":
        sec = ev["report"] if ev.get("secured") else "unsecured"
        return f"rx {ev['type']} from {ev['from']}  {sec}  {ev.get('size')}B"
    if k == "tx":
        return f"tx {ev['type']}"
    if k == "control":
        if ev["direction"] == "event":
            idx = f" index {ev['index']}" if "index" in ev else ""
            return f"control event {ev.get('event_id', '-')}{idx} ({ev.get('reason', '')})"
        detail = f"{ev.get('previous')} -> {ev.get('index')}" if ev.get("result") == "changed" else ev.get("error", "")
        return f"control status {ev.get('event_id', '-')} {ev.get('result')} {detail}"
    if k == "pseudonym":
        a = ev["action"]
        if a == "changed":
            return f"pseudonym changed {ev['previous']} -> {ev['index']} cert {ev['certificate']}" + (" (wrapped)" if ev["wrapped"] else "")
        if a == "start":
            return f"pseudonym pool {ev['pool_size']} ({ev['version']}), index {ev['index']} cert {ev['certificate']}"
        if a == "rejected":
            return f"pseudonym change rejected: {ev['error']}"
        return f"pseudonym {a}" + (f": {ev['text']}" if "text" in ev else "")
    if k == "idchange":
        a = ev["action"]
        if a in ("network", "facilities"):
            what = "GN address/MAC" if a == "network" else "stationId"
            return f"ID change ({a}): {what} {ev['old']} -> {ev['new']} (id {ev['id']})"
        if a == "lock":
            return f"ID-LOCK {ev['duration_s']} s (handle {ev['handle']})"
        if a == "unlock":
            return f"ID-UNLOCK handle {ev['handle']}"
        if a == "silent":
            return f"silent period {ev['duration_ms']} ms (no frames sent)"
        if a == "silent_end":
            return f"silent period over, {ev['suppressed']} frame(s) suppressed"
        return f"ID change scope: {ev.get('scope')}"
    if k == "pki":
        a = ev["action"]
        if a == "requested":
            return f"certificate batch requested ({ev['request']}): {ev['unused']} unused, {ev['wanted']} wanted"
        if a == "installed":
            return (f"certificate batch installed ({ev['request']}): {ev['count']} certificate(s), refresh {ev['refresh_ms']} ms"
                    f" (PKI issue {ev['issue_ms']} ms" + (f", key derivation {ev['derivation_ms']} ms" if ev.get("derivation_ms") is not None else "")
                    + f"), {ev['unused']} unused")
        if a == "retry":
            return f"no answer to batch request {ev['request']} after {ev['after_s']} s, retrying"
        if a == "issued":
            return (f"PKI issued {ev['count']} certificate(s) to {ev['for']} ({ev['request']}), batch {ev['batch']}, "
                    f"issue {ev['issue_ms']} ms, queue {ev['queue_ms']} ms")
        if a == "provisioned":
            return f"PKI provisioned in {ev['duration_ms']} ms"
        return f"PKI {a} {ev.get('request', '')}: {ev.get('error', '')}"
    if k == "chain":
        # the station verifies a certificate's signature chain up to the root CA before using it
        role, cert = ev["role"], ev.get("cert", "")
        if role == "batch_at":
            parts = cert.rstrip("/").split("/")
            what = (f"refill certificate {parts[-1].removesuffix('.cert')} of batch request {parts[-2]}"
                    if len(parts) >= 2 else f"refill certificate {cert}")
        elif role == "own_at":
            what = f"own pseudonym certificate {cert}"
        elif role == "chain":
            what = f"CA certificate {cert}"
        else:
            return f"certificate check warning: {ev['result']}"
        return f"certificate check, {what}: chain to the root CA {ev['result']}"
    if k == "error":
        return f"ERROR {ev['text']}"
    return json.dumps(ev)
