"""vnapctl -- run and inspect Vanetza-NAP RSU/OBU simulations.

  vnapctl scenarios [--json]              list scenario files (scenarios/*.toml)
  vnapctl up <scenario> [--set path=value ...] [--wait 30s] [--dry-run] [--json]
                                          validate a scenario, start it, wait until socktap runs
  vnapctl down [<scenario>] [--force] [--keep-networks] [--dry-run] [--json]
                                          stop the simulation; refuses runs started by someone else
  vnapctl validate <scenario> [--set ...] [--as-user] [--json]
                                          check a scenario; --json gives field-level errors
  vnapctl schema                          JSON Schema of the scenario format

  vnapctl status [--json]                 what is running: stations, security, chain checks,
                                          pseudonym state, control channel, disk
  vnapctl events [--duration 10s] [--since 5m] [--kind rx,tx,...] [--station obu] [--count N] [--json]
                                          merged, timestamped event stream from MQTT and container logs
  vnapctl check [--duration 15s] [--expect 'rsu.cam.success_rate>=0.99' ...] [--no-defaults] [--json]
                                          collect events, compute metrics, return a pass/fail verdict

status, events and check are read-only. Every command finishes in bounded time. Exit codes:
0 ok/pass, 1 check failed, 2 usage error (incl. invalid scenario), 3 no simulation running,
4 degraded / not ready, 5 conflict (already running, name in use, owned by someone else).
--instance N runs or inspects a parallel copy on its own networks (10.N.x.0/24, names <name>-iN).

Stations are found by membership of the simulation network (default vanetzalan0). Requires only
python3, the docker CLI and the eclipse-mosquitto:2 image (used for MQTT subscriptions).
"""

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

from .common import EVENT_KINDS, EXIT_CONFLICT, EXIT_DEGRADED, EXIT_DOWN, EXIT_FAIL, EXIT_OK, EXIT_USAGE, ScenarioError, clock, parse_duration
from .status import Simulation, build_status, observer_reports, print_status
from .events import collect_events, describe
from .check import compute_metrics, default_expectations, evaluate
from .scenario import instance_networks, list_scenarios, load_scenario
from .schema import SCENARIO_SCHEMA
from .lifecycle import allocate_instance, scenario_down, scenario_up


def scenario_expectations(sim):
    """[check] section of the scenario file the running simulation was started from (via labels)."""
    labels = {}
    for info in sim.stations:
        labels.update(info["Config"].get("Labels") or {})
    path = labels.get("vnap.scenario_file")
    if not path or not os.path.isfile(path):
        return None
    try:
        sc = load_scenario(path, json.loads(labels.get("vnap.overrides", "[]")), int(labels.get("vnap.instance", "0")))
    except (ScenarioError, ValueError):
        return None
    expect = sc["check"]["expect"]
    if sc["instance"]:
        # scenario files name stations without the instance suffix
        for st in sc["stations"]:
            expect = [re.sub(rf"(?<![\w-]){re.escape(st['base_name'])}\.", f"{st['name']}.", e) for e in expect]
    return {"scenario": sc["name"], "defaults": sc["check"]["defaults"], "expect": expect}



def instance_arg(text):
    """--instance: a number, or "auto" (up only)."""
    if text == "auto":
        return text
    try:
        value = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"instance must be a number or 'auto', not {text!r}")
    if not 0 <= value <= 250:
        raise argparse.ArgumentTypeError("instance must be 0..250")
    return value


def main():
    # common options are accepted before or after the subcommand. They are parsed into
    # separate destinations: a subparser's defaults would otherwise overwrite values given
    # before the subcommand (and e.g. turn "--instance 2 down" into a down of instance 0)
    def common_options(parser, prefix):
        parser.add_argument("--lan", dest=prefix + "lan", default=None, help="simulation network (default vanetzalan0)")
        parser.add_argument("--ctl", dest=prefix + "ctl", default=None, help="pseudonym control network (default vnapctl0)")
        parser.add_argument("--instance", dest=prefix + "instance", type=instance_arg, default=None,
                            help="parallel instance N: networks <lan>-iN/<ctl>-iN, subnets 10.N.x.0/24, names <name>-iN; "
                                 "up --instance auto claims the lowest free N (default: VNAPCTL_INSTANCE or 0)")

    ap = argparse.ArgumentParser(prog="vnapctl", description=__doc__.split("\n\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    common_options(ap, "top_")
    common = argparse.ArgumentParser(add_help=False)
    common_options(common, "sub_")
    sub = ap.add_subparsers(dest="cmd", required=True)
    _add_parser = sub.add_parser
    sub.add_parser = lambda *a, **k: _add_parser(*a, parents=[common], **k)

    p = sub.add_parser("status", help="show what is running")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("events", help="merged event stream (MQTT + container logs)")
    p.add_argument("--duration", type=parse_duration, default=None,
                   help="live capture time (default 10s, or 0 with --since)")
    p.add_argument("--since", type=parse_duration, help="also include log events from this long ago (MQTT has no history)")
    p.add_argument("--kind", help=f"comma list of {','.join(EVENT_KINDS)}")
    p.add_argument("--station", help="comma list of station names")
    p.add_argument("--count", type=int, help="stop after this many events")
    p.add_argument("--json", action="store_true", help="JSON lines")

    p = sub.add_parser("check", help="collect events and evaluate expectations")
    p.add_argument("--duration", type=parse_duration, default=15.0, help="observation time (default 15s)")
    p.add_argument("--expect", action="append", default=[], metavar="EXPR",
                   help="e.g. 'rsu.cam.success_rate>=0.99', 'obu.pseudonym.changed>=1' (repeatable)")
    p.add_argument("--no-defaults", action="store_true", help="evaluate only --expect and scenario expectations")
    p.add_argument("--no-scenario", action="store_true", help="ignore the [check] section of the running scenario")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("scenarios", help="list scenario files")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("up", help="start a scenario")
    p.add_argument("scenario", help="scenario name (scenarios/<name>.toml) or path to a .toml file")
    p.add_argument("--set", action="append", default=[], metavar="PATH=VALUE",
                   help="override, e.g. image=vnap:r2-p4, defaults.security=certs-v2, control.client.interval=10, "
                        "stations.obu.root_cert=/vnap-certs/c-its-pki/tlm.cert (repeatable)")
    p.add_argument("--wait", type=parse_duration, default=30.0, help="wait for readiness (default 30s, 0 = no wait)")
    p.add_argument("--dry-run", action="store_true", help="validate and print the docker commands only")
    p.add_argument("--json", action="store_true")

    p.add_argument("--as-user", action="store_true", help="apply the user policy (policy.toml), as the API service does")

    p = sub.add_parser("validate", help="validate a scenario; field-level errors with --json")
    p.add_argument("scenario", help="scenario name or path to a .toml file")
    p.add_argument("--set", action="append", default=[], metavar="PATH=VALUE", help="override (repeatable)")
    p.add_argument("--as-user", action="store_true", help="apply the user policy (policy.toml)")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("schema", help="print the JSON Schema of the scenario format")

    p = sub.add_parser("down", help="stop the simulation and remove its networks")
    p.add_argument("scenario", nargs="?", help="scenario whose networks to use (default: --lan/--ctl)")
    p.add_argument("--force", action="store_true", help="also stop runs started by someone else or unlabelled runs")
    p.add_argument("--keep-networks", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--json", action="store_true")

    args = ap.parse_args()
    env_instance = os.environ.get("VNAPCTL_INSTANCE")
    try:
        default_instance = instance_arg(env_instance) if env_instance else 0
    except argparse.ArgumentTypeError as e:
        ap.error(f"VNAPCTL_INSTANCE: {e}")
    for name, default in (("lan", "vanetzalan0"), ("ctl", "vnapctl0"), ("instance", default_instance)):
        top, sub_value = getattr(args, "top_" + name), getattr(args, "sub_" + name, None)
        if top is not None and sub_value is not None and top != sub_value:
            ap.error(f"--{name} given twice with different values ({top}, {sub_value})")
        setattr(args, name, sub_value if sub_value is not None else top if top is not None else default)
    if args.instance == "auto" and args.cmd != "up":
        ap.error("--instance auto is only for up (other commands need the instance number up printed)")
    lan, ctl = instance_networks(args.lan, args.ctl, 0 if args.instance == "auto" else args.instance)

    def fail(msg, code):
        print(json.dumps({"error": msg}) if getattr(args, "json", False) else f"vnapctl: {msg}",
              file=sys.stdout if getattr(args, "json", False) else sys.stderr)
        return code

    if args.cmd == "scenarios":
        items = list_scenarios()
        if args.json:
            print(json.dumps(items, indent=2))
        else:
            for it in items:
                if it["valid"]:
                    ctl_text = (" + control channel" if it["control"] else "") + (" + run PKI" if it.get("pki") else "") \
                        + (" + eavesdropper" if it.get("eavesdropper") else "")
                    print(f"{it['name']:<24} {' '.join(it['stations'])}{ctl_text}\n{'':<24} {it['description']}")
                else:
                    print(f"{it['name']:<24} INVALID: {it['error']}")
        return EXIT_OK

    if args.cmd == "schema":
        print(json.dumps(SCENARIO_SCHEMA, indent=2))
        return EXIT_OK

    if args.cmd == "validate":
        role = "user" if args.as_user else "admin"
        try:
            sc = load_scenario(args.scenario, args.set, 0 if args.instance == "auto" else args.instance, role)
        except ScenarioError as e:
            if args.json:
                print(json.dumps({"valid": False, "errors": e.errors or [{"path": "", "message": str(e), "text": str(e)}]}, indent=2))
                return EXIT_USAGE
            return fail(str(e), EXIT_USAGE)
        summary = {"name": sc["name"], "path": sc["path"], "role": role,
                   "stations": [{"name": st["base_name"], "station_id": st["station_id"], "ip": st["ip"], "mac": st["mac"],
                                 "station_type": st["station_type"],
                                 "assigned": st["assigned"]} for st in sc["stations"]]}
        if args.json:
            print(json.dumps({"valid": True, "errors": [], "scenario": summary}, indent=2))
        else:
            print(f"valid: {sc['name']} ({len(sc['stations'])} station(s), checked as {role})")
            for st in summary["stations"]:
                if st["assigned"]:
                    shown = [f"{k} {st[k]}" if k in st else k for k in st["assigned"]]
                    print(f"  {st['name']}: assigned {', '.join(shown)}")
        return EXIT_OK

    if args.cmd == "up":
        try:
            role = "user" if args.as_user else "admin"
            claimed = None
            if args.instance == "auto":
                sc, claimed = allocate_instance(args.scenario, args.set, role, args.dry_run)
            else:
                sc = load_scenario(args.scenario, args.set, args.instance, role)
            ok, report = scenario_up(sc, args.dry_run, args.wait, claimed)
        except ScenarioError as e:
            code = EXIT_CONFLICT if "already" in str(e) or "in use" in str(e) else EXIT_USAGE
            return fail(str(e), code)
        if args.dry_run:
            return EXIT_OK
        if args.json:
            print(json.dumps({"ok": ok, "instance": sc["instance"], **report}, indent=2))
        else:
            print(f"started {sc['name']} (run {report['run_id']})" + (f" on instance {sc['instance']}" if claimed else "")
                  + ("" if ok else " -- NOT READY"))
            for name, why in report["pending"].items():
                print(f"  not ready: {name}: {why}")
            print()
            print_status(report["status"])
        return EXIT_OK if ok else EXIT_DEGRADED

    if args.cmd == "down":
        d_lan, d_ctl = lan, ctl
        if args.scenario:
            try:
                sc = load_scenario(args.scenario, (), args.instance)
            except ScenarioError as e:
                return fail(str(e), EXIT_USAGE)
            d_lan = sc["network"]["name"]
            d_ctl = sc["control"]["network"] if sc["control"] else ctl
        try:
            report = scenario_down(d_lan, d_ctl, args.force, args.dry_run, args.keep_networks)
        except ScenarioError as e:
            return fail(str(e), EXIT_CONFLICT)
        if args.dry_run:
            return EXIT_OK
        if args.json:
            print(json.dumps(report, indent=2))
        else:
            print(f"removed: {', '.join(report['removed']) or 'nothing (no simulation running)'}")
            if report["networks_removed"]:
                print(f"networks removed: {', '.join(report['networks_removed'])}")
            if report["volumes_removed"]:
                print(f"PKI volume(s) removed (with their keys): {', '.join(report['volumes_removed'])}")
            for n in report["networks_kept"]:
                print(f"network kept: {n}")
        return EXIT_OK if report["removed"] else EXIT_DOWN

    try:
        sim = Simulation(lan, ctl)
    except (RuntimeError, FileNotFoundError) as e:
        return fail(str(e), EXIT_USAGE)

    if args.cmd == "status":
        s = build_status(sim)
        print(json.dumps(s, indent=2)) if args.json else print_status(s)
        return {"ok": EXIT_OK, "degraded": EXIT_DEGRADED, "down": EXIT_DOWN}[s["health"]]

    if not sim.running:
        return fail(f"no simulation running (no stations on {lan})", EXIT_DOWN)

    if args.cmd == "events":
        kinds = set(args.kind.split(",")) if args.kind else None
        if kinds and kinds - set(EVENT_KINDS):
            return fail(f"unknown kind(s) {', '.join(kinds - set(EVENT_KINDS))}", EXIT_USAGE)
        stations = set(args.station.split(",")) if args.station else None
        duration = args.duration if args.duration is not None else (0 if args.since else 10.0)
        since = time.time() - args.since if args.since else None
        events, _, _ = collect_events(sim, duration, since, args.count, kinds, stations)
        for ev in events:
            if args.json:
                print(json.dumps(ev))
            else:
                print(f"{clock(ev['t'])}  {ev['station']:<8} {describe(ev)}")
        return EXIT_OK

    if args.cmd == "check":
        status = build_status(sim)
        scenario = None if args.no_scenario else scenario_expectations(sim)
        events, start, end = collect_events(sim, args.duration)
        status["observers"] = observer_reports(sim)  # what the eavesdropper knows at the end of the window
        # rates use the requested window: the wall time also covers container start-up and log reads
        metrics = compute_metrics(sim, status, events, args.duration)
        use_defaults = not args.no_defaults and (scenario["defaults"] if scenario else True)
        exps = (default_expectations(status) if use_defaults else []) + (scenario["expect"] if scenario else []) + args.expect
        results = [evaluate(e, metrics) for e in exps]
        if not any(e["kind"] in ("rx", "tx") for e in events):
            # stations are running but no MQTT data arrived: the measurement failed, not
            # necessarily the stations (e.g. the docker daemon stalled starting the probes)
            status["warnings"].insert(0, "no messages collected from any station broker; the probes may not "
                                         "have started (docker slow?), rerun before trusting this verdict")
        verdict = "pass" if results and all(r["ok"] for r in results) else "fail"
        if args.json:
            print(json.dumps({"verdict": verdict, "window_s": args.duration, "elapsed_s": round(end - start, 1),
                              "scenario": scenario, "results": results,
                              "metrics": dict(sorted(metrics.items())), "warnings": status["warnings"]}, indent=2))
        else:
            source = f", expectations from scenario {scenario['scenario']}" if scenario and scenario["expect"] else ""
            print(f"verdict: {verdict.upper()}  ({len(results)} expectation(s), {args.duration:g}s window, "
                  f"{len(events)} event(s){source})")
            for r in results:
                print(f"  {'ok  ' if r['ok'] else 'FAIL'}  {r['expect']:<42} {r['detail']}")
            for w in status["warnings"]:
                print(f"  WARNING: {w}")
        return EXIT_OK if verdict == "pass" else EXIT_FAIL
