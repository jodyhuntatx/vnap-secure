"""Metrics, expectations and their evaluation."""

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


def compute_metrics(sim, status, events, seconds):
    m = {}

    def inc(key, n=1):
        m[key] = m.get(key, 0) + n

    names = [s["name"] for s in status["stations"]]
    for ev in events:
        s = ev["station"]
        if ev["kind"] == "rx":
            base = f"{s}.{ev['type']}"
            inc(f"{base}.rx")
            inc(f"{base}.rx_from.{ev['from']}")
            if ev.get("secured") and ev.get("report") == "Success":
                inc(f"{base}.success")
            elif ev.get("secured"):
                inc(f"{base}.failed")
                inc(f"{base}.failed.{ev.get('report')}")
            else:
                inc(f"{base}.unsecured")
        elif ev["kind"] == "tx":
            inc(f"{s}.{ev['type']}.tx")
        elif ev["kind"] == "pseudonym" and ev["action"] in ("changed", "rejected"):
            inc(f"{s}.pseudonym.{ev['action']}")
        elif ev["kind"] == "idchange" and ev["action"] in ("network", "facilities", "lock", "unlock", "silent"):
            inc(f"{s}.idchange.{ev['action']}")
        elif ev["kind"] == "pki":
            inc(f"{s}.pki.{ev['action']}")
        elif ev["kind"] == "control":
            inc(f"control.{ev['direction']}")
            if ev.get("result"):
                inc(f"control.{ev['result']}")
        elif ev["kind"] == "error":
            inc(f"{s}.errors")
    for key in [k for k in m if k.endswith(".rx")]:
        base = key[:-3]
        m[f"{base}.success_rate"] = round(m.get(f"{base}.success", 0) / m[key], 4)
        m[f"{base}.rx_per_s"] = round(m[key] / seconds, 2) if seconds else 0
    for st in status["stations"]:
        n = st["name"]
        m.setdefault(f"{n}.running", int(st["state"] == "running"))
        m[f"{n}.chain.fail"] = sum(not c["ok"] for c in st["chain"])
        m[f"{n}.errors.total"] = len(st["errors"])
        p = st["pseudonym"]
        if p and p["control"]:
            m[f"{n}.pseudonym.control_connected"] = int(p["control"]["connected"])
        if p and p.get("refill"):
            rf = p["refill"]
            for key in ("batches", "added", "retries", "refused", "starved"):
                m[f"{n}.pki.{key}"] = rf[key]
            m[f"{n}.pki.pending"] = int(rf["pending"])
            if rf["unused"] is not None:
                m[f"{n}.pki.unused"] = rf["unused"]
            if rf.get("derivation_ms_mean") is not None:
                m[f"{n}.pki.derivation_ms_mean"] = rf["derivation_ms_mean"]
            if rf["batches"]:
                m[f"{n}.pki.refresh_ms_mean"] = rf["refresh_ms_mean"]
                m[f"{n}.pki.refresh_ms_max"] = rf["refresh_ms_max"]
    k = (status.get("control") or {}).get("pki")
    if k:
        m["pki.running"] = int(k["state"] == "running")
        m["pki.batches"] = k["batches"]
        m["pki.refused"] = k["refused"]
        if k["batches"]:
            m["pki.issue_ms_max"] = k["issue_ms_max"]
            m["pki.queue_ms_max"] = k["queue_ms_max"]
    m["stations.running"] = sum(st["state"] == "running" for st in status["stations"])
    m["stations.total"] = len(names)
    m["disk.free_gb"] = status["disk"]["free_gb"]
    for o in status.get("observers", [])[:1]:
        if "tracks" in o:
            counts = o.get("counts", {})
            m["eavesdropper.frames"] = counts.get("frames", 0)
            m["eavesdropper.decode_errors"] = counts.get("errors", 0)
            m["eavesdropper.tracks"] = o["tracks"]
            m["eavesdropper.pseudonyms"] = o["pseudonyms"]
            m["eavesdropper.linked_changes"] = o["linked_changes"]
            for kind, n in counts.get("linked_by", {}).items():
                m[f"eavesdropper.linked_by_{kind}"] = n
    return m


def default_expectations(status):
    exps = ["stations.running==stations.total", "disk.free_gb>=2"]
    running = [s for s in status["stations"] if s["state"] == "running"]
    for st in running:
        n = st["name"]
        exps += [f"{n}.chain.fail==0", f"{n}.errors.total==0"]
        for other in running:
            if other is not st:
                exps.append(f"{n}.cam.rx_from.{other['name']}>=1")
        if st["security"]["entity"] not in ("none", "") and len(running) > 1:
            exps.append(f"{n}.cam.success_rate==1")
        if st["pseudonym"] and st["pseudonym"]["control"]:
            exps.append(f"{n}.pseudonym.control_connected==1")
        if st["pseudonym"] and st["pseudonym"].get("refill"):
            exps.append(f"{n}.pki.starved==0")
    if (status.get("control") or {}).get("pki"):
        exps.append("pki.running==1")
    return exps


RE_EXPECT = re.compile(r"\s*([\w.\-]+)\s*(>=|<=|==|!=|>|<)\s*([\w.\-]+)\s*")
OPS = {">=": lambda a, b: a >= b, "<=": lambda a, b: a <= b, "==": lambda a, b: a == b,
       "!=": lambda a, b: a != b, ">": lambda a, b: a > b, "<": lambda a, b: a < b}


def evaluate(expr, metrics):
    m = RE_EXPECT.fullmatch(expr)
    if not m:
        return {"expect": expr, "ok": False, "detail": "cannot parse (use: metric OP number|metric)"}
    left, op, right = m.groups()

    def value(token):
        try:
            return float(token), None
        except ValueError:
            pass
        if token in metrics:
            return metrics[token], None
        # counters that never occurred are 0; rates and states must exist
        if re.search(r"\.(rx|tx|success|unsecured|failed(\.\w+)?|rx_from\.[\w\-]+|changed|rejected|errors|event|status"
                     r"|idchange\.(network|facilities|lock|unlock|silent))$", token):
            return 0, None
        near = sorted(k for k in metrics if k.split(".")[0] == token.split(".")[0])[:8]
        return None, f"no metric {token}" + (f" (have: {', '.join(near)})" if near else "")

    a, err_a = value(left)
    b, err_b = value(right)
    if err_a or err_b:
        return {"expect": expr, "ok": False, "detail": err_a or err_b}
    return {"expect": expr, "ok": OPS[op](a, b), "actual": a,
            "detail": f"{left} = {a:g}" + (f", {right} = {b:g}" if not re.fullmatch(r"[\d.]+", right) else "")}
