"""Shared helpers and constants of vnapctl."""

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


MQTT_IMAGE = "eclipse-mosquitto:2"
DISK_WARN_GB = 2.0
# Where stations stand unless the scenario says otherwise, and the centre of the example layouts:
# the crossing of Rua Alexandre Herculano and Rua Venancio Rodrigues, Coimbra (OSM node 149400643).
ORIGIN = (40.208106, -8.4197756)
EVENT_KINDS = ("rx", "tx", "control", "pseudonym", "idchange", "pki", "chain", "error")

EXIT_OK, EXIT_FAIL, EXIT_USAGE, EXIT_DOWN, EXIT_DEGRADED = 0, 1, 2, 3, 4

DEBUG = os.environ.get("VNAPCTL_DEBUG") == "1"
T0 = time.time()


def debug(msg):
    if DEBUG:
        print(f"[vnapctl +{time.time() - T0:6.2f}s] {msg}", file=sys.stderr)


def current_user():
    """Who runs vnapctl: the account (not $USER, which anyone can set); recorded as a run's owner."""
    import pwd
    try:
        return pwd.getpwuid(os.getuid()).pw_name
    except KeyError:
        return str(os.getuid())


def docker(*args, check=True):
    """Run a docker CLI command and return stdout (stderr merged for logs)."""
    t = time.time()
    result = subprocess.run(["docker", *args], capture_output=True, text=True)
    debug(f"docker {' '.join(args[:3])} ({time.time() - t:.2f}s)")
    if check and result.returncode != 0:
        raise RuntimeError(f"docker {' '.join(args[:2])}: {result.stderr.strip()}")
    return result.stdout + (result.stderr if args[0] == "logs" else "")


def parse_duration(text):
    m = re.fullmatch(r"(\d+(?:\.\d+)?)(ms|s|m|h)?", text.strip())
    if not m:
        raise argparse.ArgumentTypeError(f"invalid duration '{text}' (e.g. 500ms, 10s, 5m, 1h)")
    value, unit = float(m.group(1)), m.group(2) or "s"
    return value * {"ms": 0.001, "s": 1, "m": 60, "h": 3600}[unit]


def parse_docker_time(text):
    """RFC 3339 with nanoseconds (docker) -> epoch seconds."""
    m = re.match(r"(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)(?:\.(\d+))?Z", text)
    if not m:
        return None
    base = datetime.strptime(m.group(1), "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
    return base.timestamp() + float("0." + (m.group(2) or "0"))


def clock(t):
    return datetime.fromtimestamp(t).strftime("%H:%M:%S.%f")[:-3] if t else "--:--:--.---"


def env_of(info):
    env = {}
    for item in info["Config"].get("Env") or []:
        key, _, value = item.partition("=")
        env[key] = value
    return env


# sim/: the package lives in sim/vnapsim; the repository root is one level up
HERE = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
REPO = os.path.dirname(HERE)
SCENARIO_DIR = os.path.join(HERE, "scenarios")
IMAGES_DIR = os.path.join(HERE, "images")      # build contexts of the helper images
CERTS_DIR = os.path.join(REPO, "certs")        # default certs_dir (mounted at /vnap-certs)
CTL_IMAGE = "vnap-pseudo-ctl"
EAVESDROPPER_IMAGE = "vnap-eavesdropper"
MOBILITY_IMAGE = "vnap-mobility"
PKI_IMAGE = "vnap-pki"


def find_cits_pki():
    """C-ITS-PKI (its src/ goes into the vnap-pki image): CITS_PKI_DIR if set, else the git
    submodule external/C-ITS-PKI, else (transition) a checkout next to vnap-secure.
    Returns (directory, how found)."""
    if os.environ.get("CITS_PKI_DIR"):
        return os.path.realpath(os.environ["CITS_PKI_DIR"]), "CITS_PKI_DIR"
    submodule = os.path.join(REPO, "external", "C-ITS-PKI")
    sibling = os.path.realpath(os.path.join(REPO, "..", "C-ITS-PKI"))
    if not os.path.isfile(os.path.join(submodule, "src", "pki.py")) and os.path.isfile(os.path.join(sibling, "src", "pki.py")):
        return sibling, "sibling"
    return submodule, "submodule"


CITS_PKI_DIR, CITS_PKI_FOUND = find_cits_pki()
CITS_PKI_MISSING = (f"C-ITS-PKI not found at {CITS_PKI_DIR}: run 'git submodule update --init' "
                    "in vnap-secure (or set CITS_PKI_DIR)")


def cits_pki_version(directory=None):
    """Commit of the C-ITS-PKI sources ('-modified' when src/ has uncommitted changes), or None."""
    directory = directory or CITS_PKI_DIR
    git = ["git", "-c", "safe.directory=*", "-C", directory]
    try:
        head = subprocess.run(git + ["rev-parse", "--short", "HEAD"], capture_output=True, text=True, timeout=10)
        if head.returncode != 0:
            return None
        dirty = subprocess.run(git + ["status", "--porcelain", "--", "src"], capture_output=True, text=True, timeout=10)
        return head.stdout.strip() + ("-modified" if dirty.stdout.strip() else "")
    except (OSError, subprocess.SubprocessError):
        return None
PKI_KEYS = {"enabled", "etsi_version", "validity_hours", "initial", "refill_at", "batch", "ip", "key_derivation"}
SECURITY_ENTITIES = {"none", "dummy", "dummy-v2", "dummy-v3", "certs", "certs-v2", "certs-v3"}
STATION_KEYS = {"name", "ip", "station_id", "station_type", "mac", "security", "at_cert", "at_key",
                "aa_cert", "root_cert", "pseudonyms", "mobility", "env"}
CLIENT_MODES = {"periodic", "random", "once", "manual"}
EXIT_CONFLICT = 5


class ScenarioError(Exception):
    """Invalid scenario or failed start; `errors` holds field-level records when validation failed."""

    def __init__(self, text, errors=None):
        super().__init__(text)
        self.errors = list(errors or [])
