"""Starting and stopping simulations (up/down), helper images, the run's PKI."""

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

from .scenario import load_scenario
from .common import current_user, CITS_PKI_DIR, ORIGIN, CTL_IMAGE, EAVESDROPPER_IMAGE, HERE, MOBILITY_IMAGE, MQTT_IMAGE, PKI_IMAGE, ScenarioError, debug, docker
from .status import Simulation, build_status


class Runner:
    """Runs (or, with dry_run, prints) docker commands; remembers what to roll back."""

    def __init__(self, dry_run, secret_env, limits=None):
        self.dry_run = dry_run
        self.env = {**os.environ, **secret_env}  # secrets reach docker via "-e NAME", never argv
        self.created_containers, self.created_networks, self.created_volumes = [], [], []
        # resource limits per container role (label vnap.role), e.g. {"station": {"cpus": 1,
        # "memory": "512m"}, "default": {...}}: applied to every "docker run/create"
        self.limits = limits or {}

    def limit_args(self, args):
        role = next((a.split("=", 1)[1] for a in args if a.startswith("vnap.role=")), None)
        spec = self.limits.get(role) or self.limits.get("default") or {}
        out = []
        for key in ("cpus", "memory", "pids_limit"):
            if spec.get(key) is not None:
                out += [f"--{key.replace('_', '-')}", str(spec[key])]
        return out

    def run(self, args, stdin_cmd=None):
        if args and args[0] in ("run", "create") and self.limits:
            args = [args[0], *self.limit_args(args), *args[1:]]
        if self.dry_run:
            print(("  " + " ".join(stdin_cmd) + " | " if stdin_cmd else "  ") + "docker " + " ".join(
                a if " " not in a else repr(a) for a in args))
            return ""
        debug("docker " + " ".join(args[:4]))
        feeder = subprocess.Popen(stdin_cmd, stdout=subprocess.PIPE) if stdin_cmd else None
        result = subprocess.run(["docker", *args], capture_output=True, text=True, env=self.env,
                                stdin=feeder.stdout if feeder else None)
        if feeder:
            feeder.wait()
        if result.returncode != 0:
            raise RuntimeError(f"docker {' '.join(args[:3])}: {result.stderr.strip()}")
        return result.stdout

    def rollback(self):
        if self.created_containers:
            subprocess.run(["docker", "rm", "-f", "-v", *self.created_containers], capture_output=True)
        for net in reversed(self.created_networks):
            subprocess.run(["docker", "network", "rm", net], capture_output=True)
        if self.created_volumes:  # the run's PKI material, private keys included
            subprocess.run(["docker", "volume", "rm", "-f", *self.created_volumes], capture_output=True)


def station_env(sc, st):
    env = {"VANETZA_STATION_ID": st["station_id"], "VANETZA_STATION_TYPE": st["station_type"],
           "VANETZA_MAC_ADDRESS": st["mac"], "VANETZA_INTERFACE": "br0",
           "START_EMBEDDED_MOSQUITTO": "true", "SUPPORT_MAC_BLOCKING": "true",
           # bridge the interface with the V2X address, whatever docker names it (control network)
           "VANETZA_BRIDGE_IP": st["ip"],
           "VANETZA_SECURITY": st["security"]}
    native = sc["entrypoint"] == "native"
    secret_names = []
    if st.get("pseudonyms"):
        env["SECURITY"] = "pseudonyms"
        for n, (cert, key) in enumerate(st["pseudonyms"]["pool"]):
            env[f"PSEUDO_CERT_{n}"], env[f"PSEUDO_KEY_{n}"] = cert, key
        env["PSEUDO_MIN_INTERVAL"] = st["pseudonyms"]["min_interval_ms"]
        env["PSEUDO_ID_CHANGE"] = st["pseudonyms"]["id_change"]
        env["PSEUDO_SILENT_MIN_MS"] = st["pseudonyms"]["silent_min_ms"]
        env["PSEUDO_SILENT_MAX_MS"] = st["pseudonyms"]["silent_max_ms"]
        if sc["control"]:
            env["PSEUDO_CONTROL_BROKER"] = sc["control"]["broker"]["name"]
            if sc["control"]["auth"]:
                secret_names = ["PSEUDO_CONTROL_USERNAME", "PSEUDO_CONTROL_PASSWORD"]
        policy = st.get("pki") or {}
        if policy.get("certificates") == "bke" and policy["refill_at"] > 0:
            env["PKI_REFILL_AT"], env["PKI_BATCH_SIZE"] = policy["refill_at"], policy["batch"]
            if sc["pki"]["key_derivation"] == "station":
                # the vehicle's own butterfly secrets: the station derives its certificates' keys
                own = f"/vnap-certs/stations/{st['base_name']}"
                env["PKI_CATERPILLAR_KEY"], env["PKI_EXPANSION_KEY"] = f"{own}/caterpillar_sign.key", f"{own}/sign_expansion.key"
    elif st["at_cert"]:
        if native:
            env["SECURITY"] = "certs"
        env["AT_CERT"], env["AT_KEY"] = st["at_cert"], st["at_key"]
    if st.get("mobility") and sc["control"]:
        # start at the first waypoint; the mobility client takes over from there
        env["VANETZA_LATITUDE"], env["VANETZA_LONGITUDE"] = st["mobility"]["start"]
        env["VANETZA_USE_HARDCODED_GPS"] = "true"
        env["POSITION_CONTROL_BROKER"] = sc["control"]["broker"]["name"]
        if sc["control"]["auth"]:
            secret_names += ["POSITION_CONTROL_USERNAME", "POSITION_CONTROL_PASSWORD"]
    else:
        # a fixed position: the scenario's, or ORIGIN instead of the image's built-in 40, -8
        env["VANETZA_LATITUDE"], env["VANETZA_LONGITUDE"] = str(ORIGIN[0]), str(ORIGIN[1])
    for key in ("aa_cert", "root_cert"):
        if st[key] and (st["at_cert"] or st.get("pseudonyms")):
            env[key.upper()] = st[key]
    env.update(st["env"])
    return env, secret_names


def run_labels(sc, run_id, role):
    labels = {"vnap.managed_by": "vnapctl", "vnap.scenario": sc["name"], "vnap.scenario_file": sc["path"],
              "vnap.started_by": current_user(),
              "vnap.started_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
              "vnap.run_id": run_id, "vnap.role": role, "vnap.instance": str(sc["instance"])}
    if sc["overrides"]:
        labels["vnap.overrides"] = json.dumps(sc["overrides"])
    out = []
    for k, v in labels.items():
        out += ["--label", f"{k}={v}"]
    return out


def context_hash(directory, exclude=(), h=None):
    """Digest of a helper image's build context (file names and contents)."""
    h = h or hashlib.sha256()
    skip = set(exclude) | {"__pycache__"}
    for root, dirs, files in os.walk(directory):
        dirs[:] = sorted(d for d in dirs if d not in skip)
        for f in sorted(files):
            path = os.path.join(root, f)
            h.update(os.path.relpath(path, directory).encode() + b"\0")
            with open(path, "rb") as fh:
                h.update(fh.read())
    return h.hexdigest()[:16]


def ensure_image(r, image, subdir, exclude=(), extra=None):
    """Helper image built from vnap-docker/<subdir>, tagged by the digest of its sources
    (<image>:<digest>) and built only if that tag is missing: different versions of the sources
    (e.g. two users' checkouts) get different tags and never overwrite each other. `extra` =
    (directory, member, name in the context) adds one more directory, e.g. C-ITS-PKI's src/.
    Returns the image reference to run."""
    directory = os.path.join(HERE, subdir)
    h = hashlib.sha256()
    if extra:
        if not os.path.isdir(os.path.join(extra[0], extra[1])):
            # sources not here: use the newest image built before, if any
            newest = docker("images", "--format", "{{.Repository}}:{{.Tag}}", image, check=False).split()
            if newest:
                return newest[0]
            raise ScenarioError(f"cannot build {image}: {os.path.join(extra[0], extra[1])} not found")
        context_hash(os.path.join(extra[0], extra[1]), exclude, h)
    ref = f"{image}:{context_hash(directory, exclude, h)}"
    if subprocess.run(["docker", "image", "inspect", ref], capture_output=True).returncode == 0:
        return ref
    tar = ["tar", "-C", directory, "--exclude=__pycache__", *(f"--exclude={e}" for e in exclude), "-c", "."]
    if extra:
        # GNU tar applies --transform to every member: only names starting with extra[1] change
        tar += ["-C", extra[0], f"--transform=s,^{extra[1]},{extra[2]},", extra[1]]
    # build context on stdin: snap docker cannot read /mnt/hgfs; also tag :latest for other tools
    r.run(["build", "-q", "-t", ref, "-t", f"{image}:latest", "-"], stdin_cmd=tar)
    return ref


def container_exists(name):
    return bool(docker("ps", "-aq", "--filter", f"name=^/{re.escape(name)}$", check=False).strip())


def network_subnet(name):
    try:
        info = json.loads(docker("network", "inspect", name))[0]
    except (RuntimeError, IndexError, json.JSONDecodeError):
        return None
    return (info.get("IPAM", {}).get("Config") or [{}])[0].get("Subnet")


def allocate_instance(ref, sets=(), role="admin", dry_run=False, first=1, last=250, policy=None, service_sets=()):
    """Claim the lowest free instance N >= 1 for a scenario, atomically: creating the instance's
    simulation network fails if it exists, so concurrent `up --instance auto` never share an N.
    Returns (scenario for N, claimed network name or None in a dry run)."""
    for n in range(first, last + 1):
        sc = load_scenario(ref, sets, n, role, policy, service_sets)
        lan = sc["network"]["name"]
        if network_subnet(lan):
            continue
        if dry_run:
            return sc, None
        result = subprocess.run(["docker", "network", "create", lan, "--subnet", sc["network"]["subnet"],
                                 "--label", "vnap.managed_by=vnapctl", "--label", f"vnap.instance={n}"],
                                capture_output=True, text=True)
        if result.returncode == 0:
            return sc, lan
        if "already exists" not in result.stderr:
            raise ScenarioError(f"cannot create network {lan}: {result.stderr.strip()}")
    raise ScenarioError(f"no free instance between {first} and {last}")


def scenario_up(sc, dry_run=False, wait=30.0, claimed=None, limits=None):
    """Start a scenario. Returns (ok, report dict)."""
    lan, ctl = sc["network"]["name"], (sc["control"] or {}).get("network", "vnapctl0-unused")
    sim = Simulation(lan, ctl)
    if sim.running or sim.broker or sim.client or sim.mobility:
        s = build_status(sim)
        who = s["run"]
        raise ScenarioError(f"a simulation is already running on {lan}: scenario {who['scenario'] or '(unlabelled)'}"
                            f", started by {who['started_by'] or 'unknown'} at {who['started_at']}. "
                            f"Stop it with 'vnapctl down', or start another instance with --instance N.")
    names = [s["name"] for s in sc["stations"]] + ([sc["control"]["broker"]["name"], sc["control"]["client"]["name"]]
                                                     if sc["control"] else []) \
        + ([sc["eavesdropper"]["name"]] if sc["eavesdropper"] else []) \
        + ([sc["control"]["mobility"]["name"]] if sc["control"] and sc["control"].get("mobility") else []) \
        + ([sc["pki"]["name"]] if sc["pki"] else [])
    taken = [n for n in names if container_exists(n)]
    if taken:
        raise ScenarioError(f"container name(s) already in use: {', '.join(taken)} (remove them or use --instance N)")
    for name, subnet in ((lan, sc["network"]["subnet"]),) + (((ctl, sc["control"]["subnet"]),) if sc["control"] else ()):
        existing = network_subnet(name)
        if existing and existing != subnet:
            raise ScenarioError(f"network {name} exists with subnet {existing}, scenario wants {subnet}")
    free_gb = shutil.disk_usage("/").free / 1e9
    if free_gb < 0.5:
        raise ScenarioError(f"only {free_gb:.1f} GB free on /, refusing to start")
    if not dry_run:
        if subprocess.run(["docker", "image", "inspect", sc["image"]], capture_output=True).returncode != 0:
            raise ScenarioError(f"image {sc['image']} not found (build it with docker-build.sh)")

    secret_env = {}
    if sc["control"] and sc["control"]["auth"]:
        auth = sc["control"]["auth"]
        user, password = os.environ.get(auth["username_env"]), os.environ.get(auth["password_env"])
        if not (user and password) and not dry_run:
            raise ScenarioError(f"control.auth: set the environment variables {auth['username_env']} and {auth['password_env']}")
        secret_env = {"CONTROL_USERNAME": user or "", "CONTROL_PASSWORD": password or "",
                      "PSEUDO_CONTROL_USERNAME": user or "", "PSEUDO_CONTROL_PASSWORD": password or "",
                      "POSITION_CONTROL_USERNAME": user or "", "POSITION_CONTROL_PASSWORD": password or ""}

    run_id = f"{sc['name']}-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
    r = Runner(dry_run, secret_env, limits)
    if claimed:  # the network --instance auto created: it belongs to this run (rollback, down)
        r.created_networks.append(claimed)
    if dry_run:
        print(f"dry run: scenario {sc['name']} ({sc['path']}), run id {run_id}")
    try:
        for name, subnet in ((lan, sc["network"]["subnet"]),) + (((ctl, sc["control"]["subnet"]),) if sc["control"] else ()):
            if not network_subnet(name):
                r.run(["network", "create", name, "--subnet", subnet, "--label", "vnap.managed_by=vnapctl"])
                r.created_networks.append(name)
        c = sc["control"]
        if c:
            ctl_image = ensure_image(r, CTL_IMAGE, "pseudo-ctl")
            auth_args = ["-e", "CONTROL_USERNAME", "-e", "CONTROL_PASSWORD"] if c["auth"] else []
            r.run(["run", "-d", "--name", c["broker"]["name"], "--network", ctl, "--ip", c["broker"]["ip"],
                   *run_labels(sc, run_id, "broker"), *auth_args, ctl_image, "broker"])
            r.created_containers.append(c["broker"]["name"])
        pki_volume = start_pki(r, sc, run_id, ctl, dry_run) if sc["pki"] else None
        for st in sc["stations"]:
            env, secret_names = station_env(sc, st)
            args = ["create", "--name", st["name"], *run_labels(sc, run_id, "station"),
                    "--network", lan, "--ip", st["ip"], "--cap-add", "NET_ADMIN"]
            if not pki_volume:
                args += ["--volume", f"{sc['certs_dir']}:/vnap-certs:ro"]
            elif st.get("pki"):
                # only the public certificates and the station's own directory, read-only: the CA and
                # caterpillar keys (private/) and the other stations' keys stay out of reach
                base = st["base_name"]
                args += ["--mount", f"type=volume,src={pki_volume},dst=/vnap-certs/public,volume-subpath=public,readonly",
                         "--mount", f"type=volume,src={pki_volume},dst=/vnap-certs/stations/{base},"
                                    f"volume-subpath=stations/{base},readonly"]
            for k, v in env.items():
                args += ["-e", f"{k}={v}"]
            for k in secret_names:
                args += ["-e", k]
            if sc["entrypoint"] == "stock":
                args += ["--volume", f"{os.path.join(HERE, 'r2-entrypoint.sh')}:/r2-entrypoint.sh:ro",
                         "--entrypoint", "/bin/sh", sc["image"], "/r2-entrypoint.sh"]
            else:
                args += [sc["image"]]
            r.run(args)
            r.created_containers.append(st["name"])
            if c and (st.get("pseudonyms") or st.get("mobility")):
                host = st["ip"].split(".")[-1]
                ctl_ip = str(ipaddress.ip_network(c["subnet"]).network_address + int(host))
                r.run(["network", "connect", "--ip", ctl_ip, ctl, st["name"]])
            r.run(["start", st["name"]])
        if c:
            cl = c["client"]
            args = ["run", "-d", "--name", cl["name"], "--network", ctl, "--ip", cl["ip"],
                    *run_labels(sc, run_id, "client"), *(["-e", "CONTROL_USERNAME", "-e", "CONTROL_PASSWORD"] if c["auth"] else []),
                    "-e", f"CONTROL_BROKER={c['broker']['name']}", "-e", f"STATIONS={' '.join(str(s) for s in cl['stations'])}",
                    "-e", f"MODE={cl['mode']}", "-e", f"INTERVAL={cl['interval']}", "-e", f"MIN_INTERVAL={cl['min_interval']}",
                    "-e", f"MAX_INTERVAL={cl['max_interval']}", "-e", f"COUNT={cl['count']}", "-e", f"DELAY={cl['delay']}"]
            if cl["index"] is not None:
                args += ["-e", f"INDEX={cl['index']}"]
            r.run(args + [ctl_image, "client"])
            r.created_containers.append(cl["name"])
        if c and c.get("mobility"):
            mo = c["mobility"]
            mobility_image = ensure_image(r, MOBILITY_IMAGE, "mobility")
            config = {"broker": c["broker"]["name"], "port": 1883, "rate_hz": mo["rate_hz"],
                      "vehicles": [{"station_id": st["station_id"], **st["mobility"]}
                                   for st in sc["stations"] if st.get("mobility")],
                      "mix_zones": mo["mix_zones"], "pseudonym_topic": "vnap/pseudonym",
                      # random turns: a fixed seed repeats a run; otherwise a new one, recorded in the config
                      "seed": mo["seed"] if mo["seed"] is not None else random.randrange(1 << 31)}
            r.run(["run", "-d", "--name", mo["name"], "--network", ctl, "--ip", mo["ip"],
                   *run_labels(sc, run_id, "mobility"),
                   *(["-e", "CONTROL_USERNAME", "-e", "CONTROL_PASSWORD"] if c["auth"] else []),
                   "-e", "MOBILITY_CONFIG=" + json.dumps(config, separators=(",", ":")), mobility_image])
            r.created_containers.append(mo["name"])
        ev = sc["eavesdropper"]
        if ev:
            eavesdropper_image = ensure_image(r, EAVESDROPPER_IMAGE, "eavesdropper", exclude=("logs",))
            # only the message network and raw frames: no control network, no keys, no MQTT
            r.run(["run", "-d", "--name", ev["name"], "--network", lan, "--ip", ev["ip"], "--cap-add", "NET_RAW",
                   *run_labels(sc, run_id, "eavesdropper"),
                   "-e", f"EAVESDROP_SUMMARY={ev['summary_interval']}", "-e", f"EAVESDROP_VERBOSE={'1' if ev['verbose'] else '0'}",
                   eavesdropper_image, "--link-window", str(ev["link_window"]), "--link-distance", str(ev["link_distance"]),
                   "--link-by", ",".join(ev["link_by"]) or "none", "--timing-window", str(ev["timing_window"]),
                   "--timing-tolerance", str(ev["timing_tolerance_ms"])])
            r.created_containers.append(ev["name"])
    except (RuntimeError, ScenarioError) as e:
        if not dry_run:
            r.rollback()
        raise ScenarioError(f"start failed, rolled back: {e}")
    if dry_run:
        return True, {"dry_run": True, "run_id": run_id}

    pending = wait_ready(sc, lan, wait)
    status = build_status(Simulation(lan, ctl))
    # ready = containers running and exchanging messages; health (e.g. expected chain failures of a
    # negative control) is reported by status/check, not by up
    return not pending, {"run_id": run_id, "pending": pending, "status": status}


def pki_volume_name(run_id):
    return "vnap-pki-" + re.sub(r"[^a-z0-9_.-]", "-", run_id.lower())


def start_pki(r, sc, run_id, ctl, dry_run, timeout=90):
    """Start the run's PKI service: it creates the CA hierarchy and the stations' certificates on a
    fresh volume (provision), then answers batch requests (serve). Returns the volume name once the
    certificates exist, so stations can be created."""
    p, c = sc["pki"], sc["control"]
    pki_image = ensure_image(r, PKI_IMAGE, "pki", extra=(CITS_PKI_DIR, "src", "cits-pki/src"))
    volume = pki_volume_name(run_id)
    r.run(["volume", "create", "--label", "vnap.managed_by=vnapctl", "--label", f"vnap.run_id={run_id}",
           "--label", f"vnap.instance={sc['instance']}", "--label", "vnap.role=pki-volume", volume])
    r.created_volumes.append(volume)
    config = {"etsi_version": p["etsi_version"], "validity_hours": p["validity_hours"], "psids": [36, 37],
              "key_derivation": p["key_derivation"], "stations": p["stations"]}
    env = ["-e", f"CONTROL_BROKER={c['broker']['name']}", "-e", "PKI_CONFIG=" + json.dumps(config, separators=(",", ":"))]
    if p["key_derivation"] == "station":
        # provision once with the whole volume (it writes the vehicles' directories), then serve with
        # only private/ and public/: the running PKI cannot read the vehicles' keys
        r.run(["run", "--rm", "--name", p["name"] + "-provision", "--network", "none",
               "--mount", f"type=volume,src={volume},dst=/pki", *env, pki_image, "provision"])
        mounts = ["--mount", f"type=volume,src={volume},dst=/pki/private,volume-subpath=private",
                  "--mount", f"type=volume,src={volume},dst=/pki/public,volume-subpath=public,readonly"]
        command, ready = "serve", "[PKI] ready"
    else:
        mounts = ["--mount", f"type=volume,src={volume},dst=/pki"]
        command, ready = "run", "[PKI] provisioned"
    r.run(["run", "-d", "--name", p["name"], "--network", ctl, "--ip", p["ip"], *run_labels(sc, run_id, "pki"), *mounts,
           *(["-e", "CONTROL_USERNAME", "-e", "CONTROL_PASSWORD"] if c["auth"] else []), *env, pki_image, command])
    r.created_containers.append(p["name"])
    if dry_run:
        return volume
    deadline = time.time() + timeout
    while time.time() < deadline:
        logs = docker("logs", p["name"], check=False)
        if ready in logs:
            return volume
        state = docker("inspect", "-f", "{{.State.Status}}", p["name"], check=False).strip()
        if state not in ("running", "created"):
            raise ScenarioError(f"PKI service {state}: " + " | ".join(logs.strip().splitlines()[-3:]))
        time.sleep(0.5)
    raise ScenarioError(f"PKI service did not finish provisioning within {timeout} s")


def wait_ready(sc, lan, wait):
    """Wait until every station's socktap publishes on its embedded broker (sent or received
    message) and pseudonym stations have joined the control channel. socktap's stdout is
    block-buffered, so its log is not a usable readiness signal; [PSEUDONYM] lines go to stderr."""
    deadline = time.time() + wait
    ready, lock = set(), threading.Lock()

    def probe(st):
        while time.time() < deadline:
            remaining = max(1, int(deadline - time.time()))
            result = subprocess.run(["docker", "run", "--rm", "--network", lan, MQTT_IMAGE, "mosquitto_sub",
                                     "-h", st["ip"], "-t", "vanetza/own/#", "-t", "vanetza/out/#",
                                     "-C", "1", "-W", str(remaining)], capture_output=True, text=True)
            if result.returncode == 0 and result.stdout.strip():
                with lock:
                    ready.add(st["name"])
                return
            time.sleep(1)  # broker not up yet (the entrypoint starts it after a delay)

    threads = [threading.Thread(target=probe, args=(st,), daemon=True) for st in sc["stations"]] if wait > 0 else []
    for t in threads:
        t.start()
    for t in threads:
        t.join(max(0, deadline - time.time()) + 3)

    pending = {}
    for st in sc["stations"]:
        state = docker("inspect", "-f", "{{.State.Status}}", st["name"], check=False).strip()
        if state != "running":
            pending[st["name"]] = f"container {state}"
        elif wait > 0 and st["name"] not in ready:
            pending[st["name"]] = "no messages on its MQTT broker yet"
        elif wait > 0 and st.get("pseudonyms") and sc["control"]:
            # the control channel connects within a second of socktap start; allow a little more
            for _ in range(5):
                if "[PSEUDONYM] control channel connected" in docker("logs", st["name"], check=False):
                    break
                time.sleep(1)
            else:
                pending[st["name"]] = "control channel not connected"
        if wait > 0 and st.get("mobility") and sc["control"] and st["name"] not in pending:
            for _ in range(5):
                if "[MOBILITY] first position update" in docker("logs", st["name"], check=False):
                    break
                time.sleep(1)
            else:
                pending[st["name"]] = "no position update from the mobility client"
    return pending


def scenario_down(lan, ctl, force=False, dry_run=False, keep_networks=False):
    """Remove the simulation on lan/ctl. Returns report dict; raises ScenarioError on refusal."""
    sim = Simulation(lan, ctl)
    containers = sim.stations + [c for c in (sim.broker, sim.client, sim.mobility, sim.pki) if c] + sim.observers
    # probes and helpers from this tool are not part of the run
    names = [Simulation.name(c) for c in containers]
    owners = {(c["Config"].get("Labels") or {}).get("vnap.started_by") for c in containers}
    me = current_user()
    if containers and not force:
        if None in owners:
            raise ScenarioError(f"refusing to stop {', '.join(names)}: not started by vnapctl or run-r2-sim.sh with "
                                f"labels, so the owner is unknown. Check 'vnapctl status' and use --force.")
        others = owners - {me}
        if others:
            raise ScenarioError(f"refusing to stop {', '.join(names)}: started by {', '.join(sorted(others))}. Use --force.")
    report = {"removed": names, "networks_removed": [], "networks_kept": [], "volumes_removed": []}
    if dry_run:
        print("dry run:")
        if names:
            print("  docker rm -f -v " + " ".join(names))
    elif names:
        # -v: also their anonymous volumes (mosquitto data/log, eavesdropper /logs), which
        # otherwise accumulate; bind mounts such as vnap-certs are not affected
        docker("rm", "-f", "-v", *names)
    # the run's PKI volume (CA, enrolment and certificate keys) goes with the run
    m = re.search(r"-i(\d+)$", lan)
    instance = int(m[1]) if m else 0
    volumes = docker("volume", "ls", "-q", "--filter", "label=vnap.managed_by=vnapctl", "--filter", "label=vnap.role=pki-volume",
                     "--filter", f"label=vnap.instance={instance}", check=False).split()
    if volumes and dry_run:
        print("  docker volume rm " + " ".join(volumes))
    elif volumes:
        docker("volume", "rm", "-f", *volumes, check=False)
        report["volumes_removed"] = volumes
    if not keep_networks:
        for net in (lan, ctl):
            if net not in sim.networks:
                continue
            attached = [v.get("Name") for v in (json.loads(docker("network", "inspect", net, check=False) or "[{}]")[0]
                                               .get("Containers") or {}).values()] if not dry_run else []
            attached = [a for a in attached if a not in names]
            if attached:
                report["networks_kept"].append(f"{net} (still used by {', '.join(attached)})")
            elif dry_run:
                print(f"  docker network rm {net}")
            else:
                docker("network", "rm", net, check=False)
                report["networks_removed"].append(net)
    return report
