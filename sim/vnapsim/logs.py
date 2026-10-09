"""Parsing of station and PKI container logs into events."""

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

from .common import docker, parse_docker_time


RE_IDC_NET = re.compile(r"\[IDCHANGE\] network: GN address MID / MAC (\S+) -> (\S+) \(id (\w+)\)")
RE_IDC_FAC = re.compile(r"\[IDCHANGE\] facilities: stationId (\d+) -> (\d+) \(id (\w+)\)")
RE_IDC_LOCK = re.compile(r"\[IDCHANGE\] ID-LOCK for (\d+) s \(handle (\d+)\)")
RE_IDC_SILENT = re.compile(r"\[IDCHANGE\] silent period (\d+) ms")
RE_IDC_SILENT_END = re.compile(r"\[IDCHANGE\] silent period over, (\d+) frame")
RE_IDC_UNLOCK = re.compile(r"\[IDCHANGE\] ID-UNLOCK handle (\d+)")
RE_POOL_START = re.compile(r"\[PSEUDONYM\] (v\d) pool of (\d+) certificate\(s\).*starting at index (\d+) \(certificate=(\w+)\)")
RE_CHANGED = re.compile(r"\[PSEUDONYM\] changed pool index (\d+) -> (\d+) \(of (\d+)\), certificate=(\w+)")
RE_REJECTED = re.compile(r"\[PSEUDONYM\] (?:change to index (\d+) rejected|change event rejected|change rejected): (.*)")
RE_CHAIN = re.compile(r"\[(V[23])-CHAIN\] (chain certificate|own authorization ticket|batch authorization ticket) (\S+): (.*)")
# certificate refill, station side
RE_PKI_REQ = re.compile(r"\[PKI\] batch requested: request (\S+), (\d+) unused, (\d+) wanted")
RE_PKI_INST = re.compile(r"\[PKI\] batch installed: request (\S+), (\d+) certificate\(s\), refresh (\d+) ms "
                         r"\(PKI issue (\S+) ms(?:, key derivation (\d+) ms)?\), (\d+) unused")
RE_PKI_RETRY = re.compile(r"\[PKI\] no answer to request (\S+) after (\d+) s, retrying")
RE_PKI_REFUSED = re.compile(r"\[PKI\] request (\S+) refused: (.*)")
# PKI service
RE_PKI_ISSUED = re.compile(r"\[PKI\] request (\S+) from station (\d+) \((\S+), (\S+) unused\): issued (\d+) AT\(s\) "
                           r"in batch (\d+), issue (\d+) ms, queue (\d+) ms")
RE_PKI_PROVISIONED = re.compile(r"\[PKI\] provisioned in (\d+) ms")
RE_PKI_SVC_REFUSED = re.compile(r"\[PKI\] request (\S+) from (?:unknown )?station (\S+).*: (refused|issuance failed.*)")
RE_CHAIN_WARN = re.compile(r"\[(V[23])-CHAIN\] WARNING: (.*)")
RE_ERROR = re.compile(r"^(Exit[: ]|terminate called|.*Assertion .* failed|Segmentation fault|Aborted)")


def log_events(station, since=None, until=None):
    """Parse a station's container log into events (docker log timestamps)."""
    args = ["logs", "--timestamps"]
    if since is not None:
        args += ["--since", f"{since:.3f}"]
    if until is not None:
        args += ["--until", f"{until:.3f}"]
    out = docker(*args, station, check=False)
    events = []
    for raw in out.splitlines():
        stamp, _, line = raw.partition(" ")
        t = parse_docker_time(stamp)
        if t is None:
            continue
        ev = None
        if m := RE_POOL_START.search(line):
            ev = {"kind": "pseudonym", "action": "start", "version": m[1], "pool_size": int(m[2]),
                  "index": int(m[3]), "certificate": m[4]}
        elif m := RE_CHANGED.search(line):
            ev = {"kind": "pseudonym", "action": "changed", "previous": int(m[1]), "index": int(m[2]),
                  "pool_size": int(m[3]), "certificate": m[4], "wrapped": "wrapped around" in line}
        elif m := RE_REJECTED.search(line):
            ev = {"kind": "pseudonym", "action": "rejected", "error": m[2], "starved": m[2].startswith("no unused pseudonym")}
            if m[1]:
                ev["requested"] = int(m[1])
        elif m := RE_PKI_REQ.search(line):
            ev = {"kind": "pki", "action": "requested", "request": m[1], "unused": int(m[2]), "wanted": int(m[3])}
        elif m := RE_PKI_INST.search(line):
            ev = {"kind": "pki", "action": "installed", "request": m[1], "count": int(m[2]), "refresh_ms": int(m[3]),
                  "issue_ms": int(m[4]) if m[4].isdigit() else None,
                  "derivation_ms": int(m[5]) if m[5] else None, "unused": int(m[6])}
        elif m := RE_PKI_RETRY.search(line):
            ev = {"kind": "pki", "action": "retry", "request": m[1], "after_s": int(m[2])}
        elif m := RE_PKI_REFUSED.search(line):
            ev = {"kind": "pki", "action": "refused", "request": m[1], "error": m[2]}
        elif "[PSEUDONYM] control channel connected" in line:
            ev = {"kind": "pseudonym", "action": "connected"}
        elif "[PSEUDONYM] control channel lost" in line or "[PSEUDONYM] control channel connection refused" in line:
            ev = {"kind": "pseudonym", "action": "disconnected", "text": line.split("] ", 1)[1]}
        elif m := RE_IDC_NET.search(line):
            ev = {"kind": "idchange", "action": "network", "old": m[1], "new": m[2], "id": m[3]}
        elif m := RE_IDC_FAC.search(line):
            ev = {"kind": "idchange", "action": "facilities", "old": int(m[1]), "new": int(m[2]), "id": m[3]}
        elif m := RE_IDC_LOCK.search(line):
            ev = {"kind": "idchange", "action": "lock", "duration_s": int(m[1]), "handle": int(m[2])}
        elif m := RE_IDC_SILENT.search(line):
            ev = {"kind": "idchange", "action": "silent", "duration_ms": int(m[1])}
        elif m := RE_IDC_SILENT_END.search(line):
            ev = {"kind": "idchange", "action": "silent_end", "suppressed": int(m[1])}
        elif m := RE_IDC_UNLOCK.search(line):
            ev = {"kind": "idchange", "action": "unlock", "handle": int(m[1])}
        elif "[IDCHANGE] subscribed:" in line:
            ev = {"kind": "idchange", "action": "scope", "scope": "full"}
        elif "[IDCHANGE] pseudonym changes replace only the certificate" in line:
            ev = {"kind": "idchange", "action": "scope", "scope": "certificate"}
        elif "[PSEUDONYM] WARNING" in line:
            ev = {"kind": "pseudonym", "action": "warning", "text": line.split("WARNING: ", 1)[1]}
        elif m := RE_CHAIN.search(line):
            ev = {"kind": "chain", "version": m[1],
                  "role": "chain" if m[2].startswith("chain") else "batch_at" if m[2].startswith("batch") else "own_at",
                  "cert": m[3], "result": m[4], "ok": m[4] == "OK"}
        elif m := RE_CHAIN_WARN.search(line):
            ev = {"kind": "chain", "version": m[1], "role": "warning", "result": m[2], "ok": False}
        elif RE_ERROR.search(line):
            ev = {"kind": "error", "text": line.strip()}
        if ev:
            events.append({"t": t, "station": station, **ev})
    return events


def pki_log_events(name, since=None, until=None):
    """Events of the run's PKI service (issued batches, refusals, provisioning time)."""
    args = ["logs", "--timestamps"]
    if since is not None:
        args += ["--since", f"{since:.3f}"]
    if until is not None:
        args += ["--until", f"{until:.3f}"]
    events = []
    for raw in docker(*args, name, check=False).splitlines():
        stamp, _, line = raw.partition(" ")
        t = parse_docker_time(stamp)
        if t is None:
            continue
        ev = None
        if m := RE_PKI_ISSUED.search(line):
            ev = {"action": "issued", "request": m[1], "station_id": int(m[2]), "for": m[3], "count": int(m[5]),
                  "batch": int(m[6]), "issue_ms": int(m[7]), "queue_ms": int(m[8])}
        elif m := RE_PKI_PROVISIONED.search(line):
            ev = {"action": "provisioned", "duration_ms": int(m[1])}
        elif m := RE_PKI_SVC_REFUSED.search(line):
            ev = {"action": "refused", "request": m[1], "station_id": m[2], "error": m[3]}
        if ev:
            events.append({"t": t, "station": "pki", "kind": "pki", **ev})
    return events


def refill_status(env, events):
    """Certificate refill of one station from its log events (None without refill)."""
    if int(env.get("PKI_REFILL_AT", "0") or 0) <= 0:
        return None
    pk = [e for e in events if e["kind"] == "pki"]
    installed = [e for e in pk if e["action"] == "installed"]
    refresh = [e["refresh_ms"] for e in installed]
    requested = [e for e in pk if e["action"] == "requested"]
    # unused pseudonyms: the pool start, minus one per change, reset by each installed batch
    unused = None
    for e in events:
        if e["kind"] == "pseudonym" and e["action"] == "start":
            unused = e["pool_size"] - 1
        elif e["kind"] == "pseudonym" and e["action"] == "changed" and unused is not None:
            unused -= 1
        elif e["kind"] == "pki" and e["action"] == "installed":
            unused = e["unused"]
    return {
        "refill_at": int(env["PKI_REFILL_AT"]), "batch": int(env.get("PKI_BATCH_SIZE", "8") or 8),
        "unused": unused, "requests": len(requested), "batches": len(installed),
        "added": sum(e["count"] for e in installed),
        "retries": sum(e["action"] == "retry" for e in pk), "refused": sum(e["action"] == "refused" for e in pk),
        "refresh_ms_mean": round(sum(refresh) / len(refresh)) if refresh else None,
        "derivation_ms_mean": (round(sum(d) / len(d)) if (d := [e["derivation_ms"] for e in installed
                                                               if e.get("derivation_ms") is not None]) else None),
        "key_derivation": "station" if env.get("PKI_CATERPILLAR_KEY") else "pki",
        "refresh_ms_max": max(refresh) if refresh else None, "refresh_ms_last": refresh[-1] if refresh else None,
        "starved": sum(1 for e in events if e["kind"] == "pseudonym" and e["action"] == "rejected" and e.get("starved")),
        "pending": bool(requested) and (not installed or requested[-1]["t"] > installed[-1]["t"]),
    }
