"""Discovery of a running simulation and its status."""

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

from .common import DISK_WARN_GB, clock, docker, env_of
from .logs import log_events, pki_log_events, refill_status


class Simulation:
    """Containers of one simulation, found through its docker networks."""

    def __init__(self, lan, ctl):
        self.lan, self.ctl = lan, ctl
        self.stations = []   # docker inspect dicts of V2X stations (have VANETZA_STATION_ID)
        self.broker = None   # control channel broker
        self.client = None   # control channel event client
        self.mobility = None  # mobility client (position updates on the control channel)
        self.pki = None       # the run's PKI service (certificate authority, batch refill)
        self.observers = []  # passive listeners (eavesdropper) on the simulation network
        self.networks = {}
        members = {}
        for net in (lan, ctl):
            try:
                self.networks[net] = json.loads(docker("network", "inspect", net))[0]
            except (RuntimeError, IndexError, json.JSONDecodeError):
                continue
            out = docker("ps", "-a", "--filter", f"network={net}", "--format", "{{.Names}}", check=False)
            members[net] = {n for n in out.split() if n}
        names = set().union(*members.values()) if members else set()
        # containers can vanish between listing and inspecting (e.g. short-lived MQTT probes):
        # docker inspect then fails for those but still prints the others
        out = docker("inspect", *sorted(names), check=False) if names else ""
        try:
            infos = json.loads(out) if out.strip() else []
        except json.JSONDecodeError:
            infos = []
        infos = [i for i in infos if not any(k.startswith("vnapctl.probe") for k in (i["Config"].get("Labels") or {}))]
        for info in infos:
            env, name = env_of(info), Simulation.name(info)
            cmd = info["Config"].get("Cmd") or []
            # stations only count on the simulation network, control containers only on the control network
            role = (info["Config"].get("Labels") or {}).get("vnap.role")
            if "VANETZA_STATION_ID" in env and name in members.get(lan, ()):
                self.stations.append(info)
            elif role == "mobility" and name in members.get(ctl, ()):
                self.mobility = info
            elif role == "pki" and name in members.get(ctl, ()):
                self.pki = info
            elif role == "eavesdropper" and name in members.get(lan, ()):
                self.observers.append(info)
            elif name in members.get(ctl, ()) and "broker" in cmd:
                self.broker = info
            elif name in members.get(ctl, ()) and "client" in cmd:
                self.client = info
        self.stations.sort(key=lambda i: int(env_of(i).get("VANETZA_STATION_ID", 0)))

    @property
    def running(self):
        return bool(self.stations)

    @staticmethod
    def name(info):
        return info["Name"].lstrip("/")

    def ip(self, info, net):
        return ((info["NetworkSettings"]["Networks"] or {}).get(net) or {}).get("IPAddress", "")

    def station_by_id(self):
        """Station ID -> station name, including the IDs a station took on full ID changes
        (its [IDCHANGE] facilities log lines are the trusted ground truth)."""
        ids = {int(env_of(i)["VANETZA_STATION_ID"]): self.name(i) for i in self.stations}
        for info in self.stations:
            if "PSEUDO_CERT_0" in env_of(info):
                for ev in log_events(self.name(info)):
                    if ev["kind"] == "idchange" and ev["action"] == "facilities":
                        ids[ev["new"]] = self.name(info)
        return ids

    def control_auth(self):
        """Broker account for our own subscriptions (never printed)."""
        if not self.broker:
            return None
        env = env_of(self.broker)
        if env.get("CONTROL_USERNAME"):
            return env["CONTROL_USERNAME"], env.get("CONTROL_PASSWORD", "")
        return None




def station_status(sim, info):
    env, name = env_of(info), Simulation.name(info)
    state = info["State"]
    security = env.get("VANETZA_SECURITY") or "none"
    pool = sorted(k for k in env if re.fullmatch(r"PSEUDO_CERT_\d+", k))
    st = {
        "name": name,
        "station_id": int(env.get("VANETZA_STATION_ID", 0)),
        "station_type": int(env.get("VANETZA_STATION_TYPE", 0) or 0),
        "state": state["Status"],
        "started_at": None if state.get("StartedAt", "").startswith("0001") else state.get("StartedAt"),
        "exit_code": state.get("ExitCode") if state["Status"] != "running" else None,
        "image": info["Config"]["Image"],
        "image_id": info["Image"].split(":")[-1][:12],
        "mac": env.get("VANETZA_MAC_ADDRESS"),
        "networks": {n: v.get("IPAddress") for n, v in (info["NetworkSettings"]["Networks"] or {}).items()},
        "security": {
            "entity": security,
            "mode": env.get("SECURITY") or "config",
            "at_cert": env.get("AT_CERT"),
            "aa_cert": env.get("AA_CERT"),
            "root_cert": env.get("ROOT_CERT"),
            "pseudonym_pool": len(pool) or None,
        },
        "chain": [], "pseudonym": None, "errors": [],
    }
    events = log_events(name)
    st["chain"] = [{k: e[k] for k in ("role", "cert", "result", "ok") if k in e} for e in events if e["kind"] == "chain"]
    st["errors"] = [e["text"] for e in events if e["kind"] == "error"]
    ps = [e for e in events if e["kind"] == "pseudonym"]
    idc = [e for e in events if e["kind"] == "idchange"]
    if pool or ps:
        current = next((e for e in reversed(ps) if e["action"] in ("changed", "start")), None)
        conn = next((e for e in reversed(ps) if e["action"] in ("connected", "disconnected")), None)
        st["pseudonym"] = {
            "pool_size": current["pool_size"] if current else len(pool),
            "index": current["index"] if current else None,
            "certificate": current["certificate"] if current else None,
            "last_change": clock(current["t"]) if current and current["action"] == "changed" else None,
            "changes": sum(e["action"] == "changed" for e in ps),
            "rejected": sum(e["action"] == "rejected" for e in ps),
            "control": {
                "broker": env.get("PSEUDO_CONTROL_BROKER"),
                "topic": env.get("PSEUDO_CONTROL_TOPIC") or f"vnap/pseudonym/{st['station_id']}",
                "auth": bool(env.get("PSEUDO_CONTROL_USERNAME")),
                "connected": bool(conn and conn["action"] == "connected"),
            } if env.get("PSEUDO_CONTROL_BROKER") else None,
        }
        scope = next((e["scope"] for e in reversed(idc) if e["action"] == "scope"), None)
        net = next((e for e in reversed(idc) if e["action"] == "network"), None)
        fac = next((e for e in reversed(idc) if e["action"] == "facilities"), None)
        st["pseudonym"]["refill"] = refill_status(env, events)
        st["pseudonym"]["id_change"] = {
            "scope": scope or "certificate (image without ID change notification)",
            "mac": net["new"] if net else st["mac"],
            "station_id": fac["new"] if fac else st["station_id"],
            "full_changes": sum(e["action"] == "facilities" for e in idc),
            "locks": sum(e["action"] == "lock" for e in idc),
            "silent_periods": sum(e["action"] == "silent" for e in idc),
            "silent_until": None,
        }
        last_silent = next((e for e in reversed(idc) if e["action"] == "silent"), None)
        if last_silent and last_silent["t"] + last_silent["duration_ms"] / 1000.0 > time.time():
            st["pseudonym"]["id_change"]["silent_until"] = clock(last_silent["t"] + last_silent["duration_ms"] / 1000.0)
    return st


def observer_reports(sim):
    """What each eavesdropper has worked out so far (its tracks.json, refreshed every 5 s)."""
    reports = []
    for info in sim.observers:
        name = Simulation.name(info)
        rep = {"name": name, "state": info["State"]["Status"], "ip": sim.ip(info, sim.lan)}
        if rep["state"] == "running":
            out = docker("exec", name, "python3", "-c",
                         "import json; d=json.load(open('/logs/tracks.json')); t=d['tracks']; print(json.dumps("
                         "{'counts': d.get('counts', {}), 'tracks': len(t), 'pseudonyms': sum(len(x['pseudonyms']) for x in t),"
                         " 'linked_changes': sum(x['linked_pseudonym_changes'] for x in t),"
                         " 'per_track': [[x['track'], x['station_types'], len(x['pseudonyms']), x['linked_pseudonym_changes']] for x in t]}))",
                         check=False).strip()
            try:
                rep.update(json.loads(out))
            except json.JSONDecodeError:
                rep["note"] = "no report yet"
        reports.append(rep)
    return reports


def build_status(sim):
    labels = {}
    for info in [c for c in (sim.broker, sim.client, sim.mobility, sim.pki) if c] + sim.stations:
        labels.update({k: v for k, v in (info["Config"].get("Labels") or {}).items() if k.startswith("vnap.")})
    stations = [station_status(sim, i) for i in sim.stations]
    control = None
    if sim.broker or sim.client or sim.mobility or sim.pki:
        control = {}
        if sim.broker:
            env = env_of(sim.broker)
            control["broker"] = {"name": Simulation.name(sim.broker), "state": sim.broker["State"]["Status"],
                                 "ip": sim.ip(sim.broker, sim.ctl), "auth": bool(env.get("CONTROL_USERNAME"))}
        if sim.client:
            env = env_of(sim.client)
            control["client"] = {"name": Simulation.name(sim.client), "state": sim.client["State"]["Status"],
                                 "mode": env.get("MODE", "periodic"), "stations": env.get("STATIONS", "2"),
                                 "interval": env.get("INTERVAL") if env.get("MODE", "periodic") == "periodic" else None}
        if sim.mobility:
            try:
                mcfg = json.loads(env_of(sim.mobility).get("MOBILITY_CONFIG", "{}"))
            except json.JSONDecodeError:
                mcfg = {}
            vehicles = mcfg.get("vehicles", [])
            control["mobility"] = {"name": Simulation.name(sim.mobility), "state": sim.mobility["State"]["Status"],
                                   "ip": sim.ip(sim.mobility, sim.ctl),
                                   "vehicles": [{"station_id": v.get("station_id"), "waypoints": len(v.get("route", [])),
                                                 "speed_kmh": v.get("speed_kmh"), "loop": v.get("loop", False),
                                                 "random_turns": "crossing" in v}
                                                for v in vehicles],
                                   "seed": mcfg.get("seed"),
                                   "mix_zones": [{k: z.get(k) for k in ("name", "center", "radius_m", "stations")}
                                                 for z in mcfg.get("mix_zones", [])]}
        if sim.pki:
            pe = pki_log_events(Simulation.name(sim.pki))
            issued = [e for e in pe if e["action"] == "issued"]
            prov = next((e for e in pe if e["action"] == "provisioned"), None)
            control["pki"] = {"name": Simulation.name(sim.pki), "state": sim.pki["State"]["Status"],
                              "cits_pki": (sim.pki["Config"].get("Labels") or {}).get("vnap.cits_pki"),
                              "ip": sim.ip(sim.pki, sim.ctl), "provisioned_ms": prov["duration_ms"] if prov else None,
                              "batches": len(issued), "certificates": sum(e["count"] for e in issued),
                              "refused": sum(e["action"] == "refused" for e in pe),
                              "issue_ms_mean": round(sum(e["issue_ms"] for e in issued) / len(issued)) if issued else None,
                              "issue_ms_max": max((e["issue_ms"] for e in issued), default=None),
                              "queue_ms_max": max((e["queue_ms"] for e in issued), default=None)}
    free_gb = shutil.disk_usage("/").free / 1e9

    warnings = []
    for st in stations:
        if st["state"] != "running":
            warnings.append(f"{st['name']} is {st['state']} (exit code {st['exit_code']})")
        for c in st["chain"]:
            if not c["ok"]:
                warnings.append(f"{st['name']}: chain check " + (f"{c['cert']} {c['result']}" if c.get("cert") else c["result"]))
        for e in st["errors"]:
            warnings.append(f"{st['name']}: {e}")
        p = st["pseudonym"]
        if p and st["security"]["pseudonym_pool"]:
            if not p["control"]:
                warnings.append(f"{st['name']}: pseudonym pool without control broker, pseudonym never changes")
            elif not p["control"]["connected"]:
                warnings.append(f"{st['name']}: control channel not connected")
        if p and p.get("refill") and p["refill"]["starved"]:
            warnings.append(f"{st['name']}: {p['refill']['starved']} pseudonym change(s) refused for lack of certificates")
    for part in (control or {}).values():
        if part["state"] != "running":
            warnings.append(f"{part['name']} is {part['state']}")
    if free_gb < DISK_WARN_GB:
        warnings.append(f"only {free_gb:.1f} GB free on / (docker builds need several GB)")

    return {
        "health": "down" if not stations else ("degraded" if warnings else "ok"),
        "run": {
            "scenario": labels.get("vnap.scenario"),
            "started_by": labels.get("vnap.started_by"),
            "started_at": labels.get("vnap.started_at") or min((s["started_at"] for s in stations if s["started_at"]), default=None),
            "managed_by": labels.get("vnap.managed_by"),
            "scenario_file": labels.get("vnap.scenario_file"),
            "run_id": labels.get("vnap.run_id"),
            "instance": int(labels.get("vnap.instance", "0")),
            "overrides": json.loads(labels.get("vnap.overrides", "[]")),
        },
        "networks": {n: (v.get("IPAM", {}).get("Config") or [{}])[0].get("Subnet") for n, v in sim.networks.items()},
        "stations": stations,
        "control": control,
        "observers": observer_reports(sim),
        "disk": {"free_gb": round(free_gb, 1), "low": free_gb < DISK_WARN_GB},
        "warnings": warnings,
    }


def print_status(s):
    run = s["run"]
    print(f"health: {s['health']}")
    if not s["stations"]:
        print("no simulation running (no stations on the simulation network)")
        return
    owner = f", started by {run['started_by']}" if run["started_by"] else ""
    via = f" via {run['managed_by']}" if run["managed_by"] else ""
    print(f"run: scenario {run['scenario'] or '(unlabelled)'}{owner}{via}, since {run['started_at']}"
          + (f", instance {run['instance']}" if run["instance"] else ""))
    if run["overrides"]:
        print(f"overrides: {' '.join(run['overrides'])}")
    print("networks: " + ", ".join(f"{n} {sub}" for n, sub in s["networks"].items()))
    for st in s["stations"]:
        sec = st["security"]
        ips = " ".join(f"{ip}" for ip in st["networks"].values())
        print(f"\n{st['name']}  id {st['station_id']} type {st['station_type']}  {st['state']}  "
              f"{st['image']} ({st['image_id']})  {ips}")
        certs = f"pool of {sec['pseudonym_pool']}" if sec["pseudonym_pool"] else (sec["at_cert"] or "-")
        print(f"  security: {sec['entity']} ({sec['mode']}), AT {certs}, AA {sec['aa_cert'] or '-'}, root {sec['root_cert'] or '-'}")
        if st["chain"]:
            bad = [c for c in st["chain"] if not c["ok"]]
            print(f"  chain checks: {len(st['chain']) - len(bad)}/{len(st['chain'])} OK"
                  + "".join(f"\n    FAIL {c['cert']}: {c['result']}" if c.get("cert") else f"\n    WARNING {c['result']}"
                            for c in bad))
        p = st["pseudonym"]
        if p:
            ctl = p["control"]
            ctl_text = (f"control {ctl['broker']} topic {ctl['topic']} "
                        f"{'connected' if ctl['connected'] else 'NOT connected'}{', auth' if ctl['auth'] else ', no auth'}"
                        if ctl else "no control channel")
            print(f"  pseudonym: index {p['index']}/{p['pool_size']} cert {p['certificate']}, "
                  f"{p['changes']} change(s), {p['rejected']} rejected, last {p['last_change'] or '-'}; {ctl_text}")
            rf = p.get("refill")
            if rf:
                refresh = (f"refresh mean {rf['refresh_ms_mean']} ms, max {rf['refresh_ms_max']} ms"
                           + (f" (key derivation {rf['derivation_ms_mean']} ms)" if rf.get("derivation_ms_mean") is not None else "")
                           if rf["batches"] else "no batch yet")
                print(f"  refill: {rf['unused']} unused (request at {rf['refill_at']}, batches of {rf['batch']}); "
                      f"{rf['batches']} batch(es), {rf['added']} certificate(s) added, {refresh}; "
                      f"{rf['retries']} retr{'y' if rf['retries'] == 1 else 'ies'}, {rf['starved']} starved change(s)"
                      + (", request pending" if rf["pending"] else ""))
            idc = p.get("id_change")
            if idc:
                silent = f", silent until {idc['silent_until']}" if idc.get("silent_until") else ""
                print(f"  identity: stationId {idc['station_id']}, MAC {idc['mac']}; ID change scope {idc['scope']}, "
                      f"{idc['full_changes']} full change(s), {idc.get('silent_periods', 0)} silent period(s), "
                      f"{idc['locks']} ID lock(s){silent}")
        for e in st["errors"]:
            print(f"  ERROR {e}")
    if s["control"]:
        c = s["control"]
        print()
        if "broker" in c:
            b = c["broker"]
            print(f"control broker: {b['name']} {b['state']} {b['ip']} {'auth' if b['auth'] else 'anonymous'}")
        if "client" in c:
            cl = c["client"]
            every = f" every {cl['interval']}s" if cl["interval"] else ""
            print(f"control client: {cl['name']} {cl['state']} mode {cl['mode']}{every} stations [{cl['stations']}]")
        if "pki" in c:
            k = c["pki"]
            issue = (f", issue mean {k['issue_ms_mean']} ms, max {k['issue_ms_max']} ms, queue max {k['queue_ms_max']} ms"
                     if k["batches"] else "")
            print(f"run PKI: {k['name']} {k['state']} {k['ip']}: provisioned in {k['provisioned_ms']} ms; "
                  f"{k['batches']} refill batch(es), {k['certificates']} certificate(s){issue}"
                  + (f"; {k['refused']} refused" if k["refused"] else "")
                  + (f"; C-ITS-PKI {k['cits_pki']}" if k.get("cits_pki") else ""))
        if "mobility" in c:
            mo = c["mobility"]
            print(f"mobility client: {mo['name']} {mo['state']} {mo['ip']}: " + ", ".join(
                f"station {v['station_id']} " + ("random turns" if v.get("random_turns") else f"{v['waypoints']} waypoints")
                + f" {v['speed_kmh']} km/h{' loop' if v['loop'] else ''}"
                for v in mo["vehicles"]) + (f" (seed {mo['seed']})" if any(v.get("random_turns") for v in mo["vehicles"]) else ""))
            for z in mo.get("mix_zones", []):
                print(f"  mix zone {z['name']}: {z['center'][0]:.6f} {z['center'][1]:.6f} radius {z['radius_m']} m, "
                      f"pseudonym change on entry for stations {z['stations']}")
    for o in s.get("observers", []):
        print()
        if "tracks" in o:
            counts = o.get("counts", {})
            by = counts.get("linked_by")
            by_text = (" (" + ", ".join(f"{n} by {k}" for k, n in by.items() if n) + ")") if by and any(by.values()) else ""
            print(f"eavesdropper: {o['name']} {o['state']} {o['ip']}: {counts.get('frames', 0)} frames "
                  f"({counts.get('errors', 0)} undecodable), {o['tracks']} track(s), {o['pseudonyms']} pseudonym(s), "
                  f"{o['linked_changes']} linked pseudonym change(s){by_text}")
            for track, types, pseudonyms, linked in o["per_track"]:
                print(f"  {track} {'/'.join(types) or '?'}: {pseudonyms} pseudonym(s), {linked} linked change(s)")
        else:
            print(f"eavesdropper: {o['name']} {o['state']} {o['ip']} ({o.get('note', 'no report')})")
    print(f"\ndisk: {s['disk']['free_gb']} GB free on /")
    for w in s["warnings"]:
        print(f"WARNING: {w}")
