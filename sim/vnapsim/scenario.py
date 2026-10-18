"""Scenario files: loading, overrides and validation."""

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

from .common import CERTS_DIR, CITS_PKI_DIR, CITS_PKI_MISSING, CLIENT_MODES, HERE, PKI_IMAGE, PKI_KEYS, SCENARIO_DIR, SECURITY_ENTITIES, STATION_KEYS, ScenarioError


def instance_networks(lan, ctl, instance):
    return (f"{lan}-i{instance}", f"{ctl}-i{instance}") if instance else (lan, ctl)


# words that name a field when a message starts with them ("ip 10.0.0.1 not in ...", "pseudonyms.first ...")
FIELD_WORDS = STATION_KEYS | PKI_KEYS | {
    "subnet", "mode", "center", "radius_m", "stations", "route", "speed_kmh", "start_s", "loop", "crossing",
    "arm_m", "start_arm", "link_by", "id_change", "silent_min_ms", "silent_max_ms", "initial", "refill_at", "batch",
    "first", "count", "cert", "key", "min_interval_ms", "timing_window", "timing_tolerance_ms", "link_window",
    "link_distance", "summary_interval", "verbose"}
PATHLIKE = re.compile(r"^[A-Za-z_][\w\-]*(?:\[[^\]]*\])?(?:\.[A-Za-z_][\w\-]*(?:\[[^\]]*\])?)*$")


def error_path(text):
    """Field path of a validation message: the location before ': ' (stations[obu1], control.subnet,
    pki, ...), refined by a field the message starts with (pseudonyms.first, ip, mobility.route)."""
    head, sep, rest = text.partition(": ")
    base, msg = (head, rest) if sep and PATHLIKE.match(head) else ("", text)
    m = re.match(r"([a-z_]+(?:\.[a-z_]+)*)", msg)
    first = m[1] if m else ""
    if base:
        return f"{base}.{first}" if first and first.split(".")[0] in FIELD_WORDS else base
    if "." in first:
        return first
    words = msg.split()
    if words[:2] == ["eavesdropper", "ip"]:
        return "eavesdropper.ip"
    if words and words[0] in ("pki", "control", "entrypoint", "eavesdropper", "network", "image", "certs_dir"):
        return words[0]
    if msg.startswith(("no stations", "duplicate station", "stations with")):
        return "stations"
    return ""


class Errors(list):
    """Validation messages (printed as before) with a field-level record for each: path, message, text."""

    def __init__(self):
        super().__init__()
        self.records = []

    def append(self, text, path=None):
        """`path` overrides the path derived from the text where the text alone is too coarse."""
        super().append(text)
        head, sep, rest = text.partition(": ")
        message = rest if sep and PATHLIKE.match(head) else text
        self.records.append({"path": path or error_path(text), "message": message, "text": text})


POLICY_FILE = os.path.join(HERE, "policy.toml")
DEFAULT_POLICY = {"approved_images": ["vnap:latest"], "allowed_env": ["VANETZA_LATITUDE", "VANETZA_LONGITUDE"]}


def load_policy(path=POLICY_FILE):
    """Allow-lists for user scenarios (policy.toml next to vnapctl; defaults without it)."""
    policy = dict(DEFAULT_POLICY)
    if os.path.isfile(path):
        with open(path, "rb") as f:
            policy.update(tomllib.load(f))
    return policy


def policy_violations(data, policy, schema=None, path="", out=None):
    """Fields a user may not set: the schema marks them admin (except approved images and allowed
    environment variables) or assigned (filled in by the service). Returns messages "<path>: ..."."""
    from .schema import SCENARIO_SCHEMA
    schema = schema or SCENARIO_SCHEMA
    out = [] if out is None else out
    props = dict(schema.get("properties", {}))
    for option in schema.get("oneOf", []):
        props.update(option.get("properties", {}))
    for key, value in (data.items() if isinstance(data, dict) else []):
        sub = props.get(key)
        if sub is None:
            continue  # unknown keys are the validator's business
        where = f"{path}.{key}" if path else key
        mark = sub.get("x-vnap", {})
        if mark.get("admin"):
            if key == "image" and not path and value in policy["approved_images"]:
                continue
            if key == "env" and isinstance(value, dict) and set(value) <= set(policy["allowed_env"]):
                continue
            hint = (f" (approved: {', '.join(policy['approved_images'])})" if key == "image" else
                    f" (allowed: {', '.join(policy['allowed_env'])})" if key == "env" else "")
            out.append(f"{where}: only administrators may set this{hint}")
        elif mark.get("assigned"):
            out.append(f"{where}: set by the service; leave it out")
        elif isinstance(value, dict):
            policy_violations(value, policy, sub, where, out)
        elif isinstance(value, list) and isinstance(sub.get("items"), dict):
            for i, item in enumerate(value):
                label = item.get("name", i) if isinstance(item, dict) else i
                policy_violations(item, policy, sub["items"], f"{where}[{label}]", out)
    return out


RESERVED_HOSTS = {0, 1, 99, 255}  # network, gateway, the eavesdropper's default, broadcast


def assign_identities(raw_stations, subnet):
    """Fill in what a station leaves out: station_id (next free from 1), ip (next free host of the
    scenario's subnet: .10, .20, ... .240, then the others), mac (from the station ID, as in the
    example scenarios) and station_type (5, passenger car). Returns (stations, assigned fields)."""
    try:
        net = ipaddress.ip_network(subnet)
    except ValueError:
        net = None
    used_ids = {int(r["station_id"]) for r in raw_stations if isinstance(r, dict) and isinstance(r.get("station_id"), int)}
    used_hosts = set()
    for r in raw_stations:
        if isinstance(r, dict) and isinstance(r.get("ip"), str) and net:
            try:
                addr = ipaddress.ip_address(r["ip"])
                if addr in net:
                    used_hosts.add(int(addr) - int(net.network_address))
            except ValueError:
                pass
    candidates = list(range(10, 250, 10)) + [h for h in range(2, 255) if h % 10]
    out, assigned = [], []
    for r in raw_stations:
        if not isinstance(r, dict):
            out.append(r)
            assigned.append([])
            continue
        r, filled = dict(r), []
        if "station_id" not in r:
            sid = 1
            while sid in used_ids:
                sid += 1
            used_ids.add(sid)
            r["station_id"], filled = sid, filled + ["station_id"]
        if "ip" not in r and net:
            host = next((h for h in candidates if h not in used_hosts and h not in RESERVED_HOSTS
                         and h < net.num_addresses - 1), None)
            if host is not None:
                used_hosts.add(host)
                r["ip"], filled = str(net.network_address + host), filled + ["ip"]
        if "mac" not in r and isinstance(r.get("station_id"), int):
            sid = r["station_id"]
            r["mac"] = (f"6e:06:e0:03:{sid >> 8 & 255:02x}:{sid & 255:02x}" if sid < 65536
                        else "6e:06:" + ":".join(f"{sid >> s & 255:02x}" for s in (24, 16, 8, 0)))
            filled.append("mac")
        if "station_type" not in r:
            r["station_type"], filled = 5, filled + ["station_type"]
        out.append(r)
        assigned.append(filled)
    return out, assigned


def remap_ip(addr, instance):
    """Instance N moves 192.168.C.D (or any a.b.C.D) to 10.N.C.D, so instances never overlap."""
    if not instance:
        return addr
    ip, _, prefix = addr.partition("/")
    parts = ip.split(".")
    return f"10.{instance}.{parts[2]}.{parts[3]}" + (f"/{prefix}" if prefix else "")


def parse_value(text):
    try:
        return tomllib.loads(f"v = {text}")["v"]
    except tomllib.TOMLDecodeError:
        return text


def apply_override(data, assignment):
    """--set a.b.c=value. An entry of an array of tables is addressed by its name or its index from 0:
    stations.<name>.<key>, control.mix_zones.0.min_vehicles. A whole value, an array included, is
    replaced with its TOML text: control.mix_zones=[]."""
    path, eq, raw = assignment.partition("=")
    if not eq or not path:
        raise ScenarioError(f"--set needs path=value, got '{assignment}'")
    keys = path.strip().split(".")
    node = data
    i = 0
    while i < len(keys) - 1:
        key = keys[i]
        if isinstance(node.get(key), list):
            entry, items = keys[i + 1], node[key]
            match = [s for s in items if isinstance(s, dict) and s.get("name") == entry]
            if not match and entry.isdigit() and int(entry) < len(items) and isinstance(items[int(entry)], dict):
                match = [items[int(entry)]]
            if not match:
                what = "station" if key == "stations" else f"entry of {key} (a name or an index from 0)"
                raise ScenarioError(f"--set {path}: no {what} '{entry}'")
            node = match[0]
            i += 2
            if i == len(keys):
                raise ScenarioError(f"--set {path}: give a key of the entry, or replace the whole {key}")
            continue
        node = node.setdefault(key, {})
        if not isinstance(node, dict):
            raise ScenarioError(f"--set {path}: '{key}' is not a table")
        i += 1
    node[keys[-1]] = parse_value(raw.strip())


def resolve_scenario_path(ref):
    for candidate in (ref, os.path.join(SCENARIO_DIR, ref), os.path.join(SCENARIO_DIR, ref + ".toml")):
        if os.path.isfile(candidate):
            return os.path.realpath(candidate)
    raise ScenarioError(f"scenario '{ref}' not found (vnapctl scenarios lists them)")


def split_variant(ref):
    """"crossing:min3" -> ("crossing", "min3"); a reference without a variant -> (ref, None)."""
    base, sep, variant = ref.rpartition(":")
    if sep and base and re.fullmatch(r"[A-Za-z0-9_-]+", variant):
        return base, variant
    return ref, None


def variant_sets(data, variant, where=""):
    """The overrides of [variants.<variant>], checked; [] without a variant."""
    variants = data.get("variants", {})
    if not isinstance(variants, dict):
        raise ScenarioError(f"{where}variants must be a table of [variants.<name>] tables")
    if variant is None:
        return []
    v = variants.get(variant)
    if v is None:
        known = ", ".join(sorted(variants)) or "none"
        raise ScenarioError(f"{where}no variant '{variant}' (variants: {known})")
    if not isinstance(v, dict) or set(v) - {"description", "set"} or not (
            isinstance(v.get("set", []), list) and all(isinstance(s, str) for s in v.get("set", []))):
        raise ScenarioError(f"{where}variants.{variant} needs description = \"...\" and set = [\"key=value\", ...]")
    return list(v.get("set", []))


def variant_overrides(path, variant):
    """The overrides a variant of the scenario file stands for ([] without a variant)."""
    if not variant:
        return []
    with open(path, "rb") as f:
        try:
            return variant_sets(tomllib.load(f), variant)
        except tomllib.TOMLDecodeError as e:
            raise ScenarioError(f"{path}: {e}")


def scenario_variants(path):
    """{name: description} of a scenario file's variants (empty when it has none or cannot be read)."""
    try:
        with open(path, "rb") as f:
            variants = tomllib.load(f).get("variants", {})
    except (OSError, tomllib.TOMLDecodeError):
        return {}
    if not isinstance(variants, dict):
        return {}
    return {name: (v.get("description", "") if isinstance(v, dict) else "") for name, v in variants.items()}


def load_scenario(ref, sets=(), instance=0, role="admin", policy=None, service_sets=(), variant=None):
    """Read, override, validate and resolve a scenario file into a flat description. A variant
    ("name:variant" or `variant`) applies the overrides of [variants.<variant>] before `sets`."""
    if variant is None:
        ref, variant = split_variant(ref) if not os.path.isfile(ref) else (ref, None)
    path = resolve_scenario_path(ref)
    with open(path, "rb") as f:
        try:
            data = tomllib.load(f)
        except tomllib.TOMLDecodeError as e:
            raise ScenarioError(f"{path}: {e}")
    for assignment in variant_sets(data, variant, f"{path}: "):
        try:
            apply_override(data, assignment)
        except ScenarioError as e:
            raise ScenarioError(f"{path}: variant {variant}: {e}")
    for assignment in sets:
        apply_override(data, assignment)

    errors = Errors()
    if role == "user":
        for message in policy_violations(data, policy or load_policy()):
            errors.append(message)
    # the service's own settings (e.g. generated broker credentials): after the user policy
    for assignment in service_sets:
        apply_override(data, assignment)
    base = os.path.dirname(path)
    # default: the repository's certs/; an explicit certs_dir is relative to the scenario file
    certs_dir = os.path.realpath(os.path.join(base, data["certs_dir"]) if "certs_dir" in data
                                 else CERTS_DIR)
    entrypoint = data.get("entrypoint", "native")
    if entrypoint not in ("native", "stock"):
        errors.append("entrypoint must be 'native' (patched image) or 'stock' (unpatched, mounts sim/stock-entrypoint.sh)")
    net = data.get("network", {})
    lan_name, ctl_name = instance_networks(net.get("name", "vanetzalan0"),
                                           data.get("control", {}).get("network", "vnapctl0"), instance)
    try:
        lan_subnet = ipaddress.ip_network(remap_ip(net.get("subnet", "192.168.98.0/24"), instance))
    except ValueError as e:
        errors.append(f"network.subnet: {e}")
        lan_subnet = None
    suffix = f"-i{instance}" if instance else ""

    def cert_path(value, where):
        if value is None:
            return None
        if not isinstance(value, str) or not value.startswith("/vnap-certs/"):
            errors.append(f"{where}: '{value}' must be a path under /vnap-certs/ (the mounted certs_dir)")
            return value
        if not os.path.isfile(os.path.join(certs_dir, value[len("/vnap-certs/"):])):
            errors.append(f"{where}: {value} not found in {certs_dir}")
        return value

    defaults = data.get("defaults", {})
    # defaults.pseudonyms: keys for every station that has a pseudonyms table (an RSU without one gets no pool)
    pseudo_defaults = defaults.get("pseudonyms", {})
    if not isinstance(pseudo_defaults, dict):
        errors.append("defaults.pseudonyms must be a table", "defaults.pseudonyms")
        pseudo_defaults = {}
    pki_raw = data.get("pki")
    pki_on = isinstance(pki_raw, dict) and pki_raw.get("enabled", True)
    pki_defaults = {k: (pki_raw or {}).get(k, v) for k, v in (("initial", 8), ("refill_at", 2), ("batch", 8))}
    stations = []
    raw_stations, assigned = assign_identities(data.get("stations", []), net.get("subnet", "192.168.98.0/24"))
    for i, raw in enumerate(raw_stations):
        unknown = set(raw) - STATION_KEYS
        where = f"stations[{raw.get('name', i)}]"
        if unknown:
            errors.append(f"{where}: unknown key(s) {', '.join(sorted(unknown))}")
        missing = [k for k in ("name", "ip", "station_id", "station_type", "mac") if k not in raw]
        if missing:
            errors.append(f"{where}: missing {', '.join(missing)}")
            continue
        if not isinstance(raw["name"], str):
            errors.append(f"{where}: name must be text in quotes")
            continue
        st = {k: raw.get(k, defaults.get(k)) for k in ("security", "aa_cert", "root_cert")}
        st.update({"name": raw["name"] + suffix, "base_name": raw["name"], "ip": remap_ip(raw["ip"], instance),
                   "station_id": int(raw["station_id"]), "station_type": int(raw["station_type"]),
                   "mac": raw["mac"], "at_cert": raw.get("at_cert"), "at_key": raw.get("at_key"),
                   "env": {**defaults.get("env", {}), **raw.get("env", {})}})
        st["assigned"] = assigned[i]
        st["security"] = st["security"] or "none"
        if st["security"] not in SECURITY_ENTITIES:
            errors.append(f"{where}: security '{st['security']}' not one of {', '.join(sorted(SECURITY_ENTITIES))}")
        if lan_subnet and ipaddress.ip_address(st["ip"]) not in lan_subnet:
            errors.append(f"{where}: ip {st['ip']} not in {lan_subnet}")
        if bool(st["at_cert"]) != bool(st["at_key"]):
            errors.append(f"{where}: at_cert and at_key go together")
        pseudo = raw.get("pseudonyms")
        if pseudo is not None and isinstance(pseudo, dict) and isinstance(pseudo_defaults, dict):
            pseudo = {**pseudo_defaults, **pseudo}   # the station's own keys win
        if pki_on:
            # certificates come from the run's own PKI, not from files
            given = sorted(k for k in ("at_cert", "at_key", "aa_cert", "root_cert") if k in raw or k in defaults)
            if given:
                errors.append(f"{where}: with [pki] the certificates come from the run's PKI: remove {', '.join(given)}")
            st.update({"at_cert": None, "at_key": None, "aa_cert": None, "root_cert": None})
        if pseudo is not None:  # an empty table means "a pool with the defaults"
            if st["at_cert"]:
                errors.append(f"{where}: at_cert and pseudonyms cannot be combined")
            if entrypoint != "native":
                errors.append(f"{where}: pseudonyms need entrypoint = 'native'")
            pool = None
            if pki_on:
                files = sorted(set(pseudo) & {"cert", "key", "count", "first"})
                if files:
                    errors.append(f"{where}: with [pki] the pool comes from the run's PKI: remove pseudonyms.{', '.join(files)}")
                policy = {k: pseudo.get(k, pki_defaults[k]) for k in ("initial", "refill_at", "batch")}
                if not all(isinstance(v, int) for v in policy.values()):
                    errors.append(f"{where}: pseudonyms.initial, refill_at and batch must be integers", f"{where}.pseudonyms")
                elif not (1 <= policy["initial"] <= 256 and 1 <= policy["batch"] <= 256
                          and 0 <= policy["refill_at"] < policy["initial"]):
                    errors.append(f"{where}: need 1 <= initial <= 256, 1 <= batch <= 256 and 0 <= refill_at < initial",
                                  f"{where}.pseudonyms")
                else:
                    pool = [(f"/vnap-certs/stations/{raw['name']}/bke_at_{k}.cert",
                             f"/vnap-certs/stations/{raw['name']}/bke_at_{k}_sign.der") for k in range(policy["initial"])]
                    st["pki"] = {"certificates": "bke", **policy}
            else:
                count = int(pseudo.get("count", 0))
                if set(pseudo) & {"initial", "refill_at", "batch"}:
                    errors.append(f"{where}: pseudonyms.initial/refill_at/batch need a [pki] section")
                if count < 1 or "{i}" not in pseudo.get("cert", "") or "{i}" not in pseudo.get("key", ""):
                    errors.append(f"{where}: pseudonyms needs cert/key patterns with {{i}} and count >= 1")
                else:
                    first = int(pseudo.get("first", 0))  # pool offset: stations can share one numbered set
                    if first < 0:
                        errors.append(f"{where}: pseudonyms.first must be >= 0")
                    pool = [(cert_path(pseudo["cert"].format(i=n), where), cert_path(pseudo["key"].format(i=n), where))
                            for n in range(first, first + count)]
            if pool:
                id_change = pseudo.get("id_change", "full")
                if id_change not in ("full", "certificate"):
                    errors.append(f"{where}: pseudonyms.id_change must be 'full' or 'certificate'")
                silent_min = int(pseudo.get("silent_min_ms", 0))
                silent_max = int(pseudo.get("silent_max_ms", silent_min))
                if silent_min < 0 or silent_max < silent_min:
                    errors.append(f"{where}: pseudonyms.silent_min_ms/silent_max_ms must satisfy 0 <= min <= max")
                if silent_max and id_change != "full":
                    errors.append(f"{where}: a silent period needs pseudonyms.id_change = 'full'")
                st["pseudonyms"] = {"pool": pool, "min_interval_ms": int(pseudo.get("min_interval_ms", 1000)),
                                    "id_change": id_change, "silent_min_ms": silent_min, "silent_max_ms": silent_max}
        mob = raw.get("mobility")
        if mob is not None:
            st["mobility"] = check_mobility(mob, where, errors)
            if entrypoint != "native":
                errors.append(f"{where}: mobility needs entrypoint = 'native'")
        st["dynamic_mix_zone"] = raw.get("dynamic_mix_zone")   # checked with [control.dynamic_mix_zone]
        st["color"] = raw.get("color")   # for maps only: the simulation does not use it
        if st["color"] is not None and not (isinstance(st["color"], str) and re.fullmatch(r"#[0-9a-fA-F]{6}", st["color"])):
            errors.append(f"{where}: color must be \"#rrggbb\"")
        if pki_on and st["security"].startswith("certs"):
            st["aa_cert"], st["root_cert"] = "/vnap-certs/public/aa.cert", "/vnap-certs/public/root_ca.cert"
            if not st.get("pki"):  # no pseudonym pool: a regular authorization ticket
                st["at_cert"] = f"/vnap-certs/stations/{raw['name']}/at.cert"
                st["at_key"] = f"/vnap-certs/stations/{raw['name']}/at.der"
                st["pki"] = {"certificates": "regular"}
        elif not pki_on:
            cert_path(st["at_cert"], where)
            cert_path(st["at_key"], where)
            cert_path(st["aa_cert"], where)
            cert_path(st["root_cert"], where)
        needs_chain = entrypoint == "native" and (st["at_cert"] or st.get("pseudonyms"))
        if needs_chain and not (st["aa_cert"] and st["root_cert"]):
            errors.append(f"{where}: the native entrypoint needs aa_cert and root_cert with certificates")
        if (st["at_cert"] or st.get("pseudonyms")) and not st["security"].startswith("certs"):
            errors.append(f"{where}: certificates given but security is '{st['security']}'")
        stations.append(st)
    if len(stations) < 1:
        errors.append("no stations")
    for key in ("name", "ip", "station_id", "mac"):
        values = [s[key] for s in stations]
        dupes = sorted({str(v) for v in values if values.count(v) > 1})
        if dupes:
            errors.append(f"duplicate station {key}: {', '.join(dupes)}")

    control = None
    ctl_raw = data.get("control")
    if isinstance(ctl_raw, dict):  # an empty [control] is a control channel with the defaults
        try:
            ctl_subnet = ipaddress.ip_network(remap_ip(ctl_raw.get("subnet", "192.168.99.0/24"), instance))
        except ValueError as e:
            errors.append(f"control.subnet: {e}")
            ctl_subnet = None
        client = dict(ctl_raw.get("client", {}))
        mode = client.get("mode", "periodic")
        if mode not in CLIENT_MODES:
            errors.append(f"control.client.mode '{mode}' not one of {', '.join(sorted(CLIENT_MODES))}")
        auth = ctl_raw.get("auth")
        control = {
            "network": ctl_name, "subnet": str(ctl_subnet) if ctl_subnet else None,
            "broker": {"name": "pseudo-broker" + suffix, "ip": remap_ip(ctl_raw.get("broker_ip", "192.168.99.2"), instance)},
            "client": {"name": "pseudo-client" + suffix, "ip": remap_ip(ctl_raw.get("client_ip", "192.168.99.3"), instance),
                       "mode": mode, "interval": client.get("interval", 30),
                       "min_interval": client.get("min_interval", 10), "max_interval": client.get("max_interval", 60),
                       "count": client.get("count", 0), "delay": client.get("delay", 5), "index": client.get("index"),
                       "stations": client.get("stations") or [s["station_id"] for s in stations if s.get("pseudonyms")]},
            "auth": auth,
        }
        if ctl_subnet:
            for role in ("broker", "client"):
                if ipaddress.ip_address(control[role]["ip"]) not in ctl_subnet:
                    errors.append(f"control.{role}_ip {control[role]['ip']} not in {ctl_subnet}")
        if auth and not (auth.get("username_env") and auth.get("password_env")):
            errors.append("control.auth needs username_env and password_env (names of environment variables)")
        if ctl_name == lan_name:
            errors.append("control network must differ from the simulation network")
        if any(s.get("mobility") for s in stations):
            control["mobility"] = {"name": "mobility" + suffix,
                                   "ip": remap_ip(ctl_raw.get("mobility_ip", "192.168.99.4"), instance),
                                   "rate_hz": float(ctl_raw.get("mobility_rate_hz", 5)),
                                   "seed": ctl_raw.get("mobility_seed")}
            if control["mobility"]["seed"] is not None and not isinstance(control["mobility"]["seed"], int):
                errors.append("control.mobility_seed must be an integer")
            if ctl_subnet and ipaddress.ip_address(control["mobility"]["ip"]) not in ctl_subnet:
                errors.append(f"control.mobility_ip {control['mobility']['ip']} not in {ctl_subnet}")
            if not 0 < control["mobility"]["rate_hz"] <= 50:
                errors.append("control.mobility_rate_hz must be in (0, 50]")
        control.setdefault("mobility", {})["mix_zones"] = check_mix_zones(ctl_raw.get("mix_zones", []), stations, errors)
        if control["mobility"]["mix_zones"] and "name" not in control["mobility"]:
            errors.append("control.mix_zones need stations with mobility (zones trigger on vehicle positions)")
        control["mobility"]["dynamic_mix_zone"] = check_dynamic_mix_zone(ctl_raw.get("dynamic_mix_zone"), stations, errors)
        if control["mobility"]["dynamic_mix_zone"] and "name" not in control["mobility"]:
            errors.append("control.dynamic_mix_zone needs stations with mobility (it triggers on vehicle positions)")
        if not control["mobility"].get("name"):
            control["mobility"] = None
    elif any(s.get("mobility") for s in stations):
        errors.append("stations with mobility need a [control] section (positions travel on the control channel)")
    elif any(s.get("pseudonyms") for s in stations):
        pass  # allowed: the pool stays at index 0 (vnapctl status warns)

    eavesdropper = None
    eav_raw = data.get("eavesdropper")
    if isinstance(eav_raw, dict) and eav_raw.get("enabled", True):  # an empty section enables it
        default_ip = str(lan_subnet.network_address + 99) if lan_subnet else None
        eavesdropper = {"name": eav_raw.get("name", "eavesdropper") + suffix,
                        "ip": remap_ip(eav_raw["ip"], instance) if "ip" in eav_raw else default_ip,
                        "verbose": bool(eav_raw.get("verbose", False)),
                        "summary_interval": eav_raw.get("summary_interval", 30),
                        "link_window": eav_raw.get("link_window", 3.0),
                        "link_distance": eav_raw.get("link_distance", 50.0),
                        "link_by": eav_raw.get("link_by", ["position", "timing"]),
                        "timing_window": eav_raw.get("timing_window", 20.0),
                        "timing_tolerance_ms": eav_raw.get("timing_tolerance_ms", 25.0)}
        if not (isinstance(eavesdropper["link_by"], list) and set(eavesdropper["link_by"]) <= {"position", "timing"}):
            errors.append('eavesdropper.link_by must be a list of "position" and/or "timing" (empty: identifiers only)')
        if lan_subnet and eavesdropper["ip"] and ipaddress.ip_address(eavesdropper["ip"]) not in lan_subnet:
            errors.append(f"eavesdropper ip {eavesdropper['ip']} not in {lan_subnet}")
        if eavesdropper["ip"] in [s["ip"] for s in stations]:
            errors.append(f"eavesdropper ip {eavesdropper['ip']} is used by a station")

    pki = None
    if pki_on:
        unknown = set(pki_raw) - PKI_KEYS
        if unknown:
            errors.append(f"pki: unknown key(s) {', '.join(sorted(unknown))}")
        users = [st for st in stations if st.get("pki")]
        if not users:
            errors.append("pki: no station uses certificates (security certs-v2/certs-v3)")
        versions = {"v3" if st["security"] == "certs-v3" else "v2" for st in users}
        etsi = pki_raw.get("etsi_version") or (versions.pop() if len(versions) == 1 else "v3")
        if etsi not in ("v2", "v3"):
            errors.append("pki.etsi_version must be 'v2' or 'v3'")
        mismatched = [st["base_name"] for st in users if ("v3" if st["security"] == "certs-v3" else "v2") != etsi]
        if mismatched:
            errors.append(f"pki: etsi_version {etsi} does not match the security of {', '.join(mismatched)} "
                          "(certs-v3 for v3, certs-v2 for v2)")
        if entrypoint != "native":
            errors.append("pki needs entrypoint = 'native'")
        if not control:
            errors.append("pki needs a [control] section (the PKI service and refill requests use its broker)")
        derivation = pki_raw.get("key_derivation", "station")
        if derivation not in ("station", "pki"):
            errors.append("pki.key_derivation must be 'station' (the vehicle derives its keys) or 'pki'")
        validity = pki_raw.get("validity_hours", 24)
        if not isinstance(validity, int) or not 1 <= validity <= 8760:
            errors.append("pki.validity_hours must be an integer from 1 to 8760")
        pki = {"name": "pki" + suffix, "ip": remap_ip(pki_raw.get("ip", "192.168.99.5"), instance),
               "etsi_version": etsi, "validity_hours": validity, "key_derivation": derivation,
               "stations": [{"name": st["base_name"], "station_id": st["station_id"], "certificates": st["pki"]["certificates"],
                             **({"initial": st["pki"]["initial"], "batch": st["pki"]["batch"]}
                                if st["pki"]["certificates"] == "bke" else {})} for st in users]}
        if control and control.get("subnet"):
            if ipaddress.ip_address(pki["ip"]) not in ipaddress.ip_network(control["subnet"]):
                errors.append(f"pki.ip {pki['ip']} not in {control['subnet']}")
            taken = {control["broker"]["ip"], control["client"]["ip"]} | (
                {control["mobility"]["ip"]} if control.get("mobility") else set())
            if pki["ip"] in taken:
                errors.append(f"pki.ip {pki['ip']} is used by another control container")
        if not os.path.isfile(os.path.join(CITS_PKI_DIR, "src", "pki.py")) and \
                subprocess.run(["docker", "image", "inspect", PKI_IMAGE], capture_output=True).returncode != 0:
            errors.append(f"pki: {CITS_PKI_MISSING}, and there is no {PKI_IMAGE} image")
    elif pki_raw is not None and not isinstance(pki_raw, dict):
        errors.append("pki must be a table ([pki])")

    check = data.get("check", {})
    if errors:
        raise ScenarioError(f"{path}:\n  " + "\n  ".join(errors), errors.records)
    name = data.get("name", os.path.splitext(os.path.basename(path))[0])
    description = data.get("description", "")
    if variant:
        description = f"{description} · variant {variant}: {data['variants'][variant].get('description', '')}".rstrip(": ")
    return {
        "name": f"{name}:{variant}" if variant else name, "variant": variant,
        "description": description, "path": path, "instance": instance,
        "image": data.get("image", "vnap:latest"), "entrypoint": entrypoint, "certs_dir": certs_dir,
        "network": {"name": lan_name, "subnet": str(lan_subnet)}, "stations": stations, "control": control,
        "eavesdropper": eavesdropper, "pki": pki,
        "check": {"defaults": check.get("defaults", True), "expect": list(check.get("expect", []))},
        "overrides": list(sets),
    }


def check_mix_zones(raw, stations, errors):
    """Validate [[control.mix_zones]]: pseudonym change events when a vehicle enters a zone
    (with min_vehicles: once that many watched vehicles are inside)."""
    if not isinstance(raw, list):
        errors.append("control.mix_zones must be an array of tables ([[control.mix_zones]])")
        return []
    by_id = {s["station_id"]: s for s in stations}
    candidates = [s["station_id"] for s in stations if s.get("mobility") and s.get("pseudonyms")]
    zones = []
    for i, z in enumerate(raw):
        where = f"control.mix_zones[{z.get('name', i) if isinstance(z, dict) else i}]"
        if not isinstance(z, dict):
            errors.append(f"{where}: must be a table")
            continue
        unknown = set(z) - {"name", "center", "radius_m", "stations", "min_vehicles"}
        if unknown:
            errors.append(f"{where}: unknown key(s) {', '.join(sorted(unknown))}")
        center = z.get("center")
        if not (isinstance(center, list) and len(center) == 2 and all(isinstance(c, (int, float)) for c in center)
                and -90 <= center[0] <= 90 and -180 <= center[1] <= 180):
            errors.append(f"{where}: center must be [lat, lon] in degrees")
            continue
        radius = z.get("radius_m", 40)
        if not isinstance(radius, (int, float)) or radius <= 0:
            errors.append(f"{where}: radius_m must be > 0")
        ids = z.get("stations", candidates)
        bad = [s for s in ids if s not in by_id or not (by_id[s].get("mobility") and by_id[s].get("pseudonyms"))]
        if bad:
            errors.append(f"{where}: station(s) {bad} need both mobility and pseudonyms")
        if not ids:
            errors.append(f"{where}: no station with mobility and pseudonyms to watch")
        minimum = z.get("min_vehicles", 1)
        if not isinstance(minimum, int) or isinstance(minimum, bool) or minimum < 1:
            errors.append(f"{where}: min_vehicles must be an integer >= 1")
        elif ids and not bad and minimum > len(ids):
            errors.append(f"{where}: min_vehicles {minimum} is more than the {len(ids)} station(s) the zone watches")
        zones.append({"name": str(z.get("name", f"zone{i}")), "center": [float(c) for c in center],
                      "radius_m": radius, "stations": list(ids), "min_vehicles": minimum})
    return zones


def check_dynamic_mix_zone(raw, stations, errors):
    """Validate [control.dynamic_mix_zone] (run scope: every moving station with pseudonyms takes
    part) and the stations' dynamic_mix_zone tables (one station's enabled and min_vehicles).
    Returns the mobility client's configuration, or None when no station takes part."""
    def integer(value):
        return isinstance(value, int) and not isinstance(value, bool)
    where = "control.dynamic_mix_zone"
    if raw is not None and not isinstance(raw, dict):
        errors.append(f"{where}: must be a table")
        raw = None
    run = raw or {}
    unknown = set(run) - {"enabled", "min_vehicles", "radius_m", "cooldown_s"}
    if unknown:
        errors.append(f"{where}: unknown key(s) {', '.join(sorted(unknown))}")
    everyone = run.get("enabled", raw is not None)
    if not isinstance(everyone, bool):
        errors.append(f"{where}: enabled must be true or false")
    minimum = run.get("min_vehicles", 2)
    if not integer(minimum) or minimum < 1:
        errors.append(f"{where}: min_vehicles must be an integer >= 1")
        minimum = 2
    radius, cooldown = run.get("radius_m", 50), run.get("cooldown_s", 30)
    if not isinstance(radius, (int, float)) or isinstance(radius, bool) or radius <= 0:
        errors.append(f"{where}: radius_m must be > 0")
    if not isinstance(cooldown, (int, float)) or isinstance(cooldown, bool) or cooldown < 1:
        errors.append(f"{where}: cooldown_s must be >= 1")
    members = []
    for st in stations:
        own, here = st.get("dynamic_mix_zone"), f"stations[{st['base_name']}]"
        if own is not None and not isinstance(own, dict):
            errors.append(f"{here}: dynamic_mix_zone must be a table {{ enabled = true, min_vehicles = 2 }}")
            continue
        if own is not None and set(own) - {"enabled", "min_vehicles"}:
            errors.append(f"{here}: unknown dynamic_mix_zone key(s) {', '.join(sorted(set(own) - {'enabled', 'min_vehicles'}))}")
        takes_part = own.get("enabled", True) if own is not None else everyone
        if not isinstance(takes_part, bool):
            errors.append(f"{here}: dynamic_mix_zone.enabled must be true or false")
            continue
        can = bool(st.get("mobility") and st.get("pseudonyms"))
        if takes_part and own is not None and not can:
            errors.append(f"{here}: dynamic_mix_zone needs both mobility and pseudonyms")
        if not (takes_part and can):
            continue   # run scope: stations that cannot take part (an RSU) are left out
        own_minimum = (own or {}).get("min_vehicles", minimum)
        if not integer(own_minimum) or own_minimum < 1:
            errors.append(f"{here}: dynamic_mix_zone.min_vehicles must be an integer >= 1")
            own_minimum = minimum
        members.append((st, here, own_minimum))
    if everyone is True and not members:
        errors.append(f"{where}: no station with mobility and pseudonyms takes part")
    for st, here, own_minimum in members:
        if own_minimum > len(members):
            errors.append(f"{here}: dynamic_mix_zone min_vehicles {own_minimum} is more than the {len(members)} station(s) taking part")
    if not members:
        return None
    return {"radius_m": radius, "cooldown_s": cooldown,
            "stations": [{"station_id": st["station_id"], "min_vehicles": n} for st, _, n in members]}


def check_mobility(mob, where, errors):
    """Validate a station's mobility table; returns the normalized description."""
    if not isinstance(mob, dict):
        errors.append(f"{where}: mobility must be a table {{ route = [[lat, lon], ...], speed_kmh = ... }}")
        return None
    common = check_speed_start(mob, where, errors)
    if "crossing" in mob:
        return check_crossing(mob, where, errors, common)
    unknown = set(mob) - {"route", "speed_kmh", "start_s", "loop"}
    if unknown:
        errors.append(f"{where}: unknown mobility key(s) {', '.join(sorted(unknown))}")
    route = mob.get("route")
    ok = isinstance(route, list) and len(route) >= 2 and all(
        isinstance(p, list) and len(p) == 2 and all(isinstance(c, (int, float)) for c in p)
        and -90 <= p[0] <= 90 and -180 <= p[1] <= 180 for p in route)
    if not ok:
        errors.append(f"{where}: mobility.route needs at least two [lat, lon] waypoints (degrees)")
        return None
    if all(p == route[0] for p in route):
        errors.append(f"{where}: mobility.route has zero length")
    route = [[float(c) for c in p] for p in route]
    return {"route": route, **common, "loop": bool(mob.get("loop", False)), "start": route[0]}


def check_speed_start(mob, where, errors):
    speed = mob.get("speed_kmh", 50)
    if not isinstance(speed, (int, float)) or not 0 < speed <= 589.7:
        errors.append(f"{where}: mobility.speed_kmh must be in (0, 589.7] (CAM speed range)")
    start = mob.get("start_s", 0)
    if not isinstance(start, (int, float)) or start < 0:
        errors.append(f"{where}: mobility.start_s must be >= 0")
    return {"speed_kmh": speed, "start_s": start}


CROSSING_ARMS = {"north": (1, 0), "east": (0, 1), "south": (-1, 0), "west": (0, -1)}


def check_crossing(mob, where, errors, common):
    """mobility = { crossing = [lat, lon], arm_m, start_arm, ... }: laps with random turns."""
    unknown = set(mob) - {"crossing", "arm_m", "start_arm", "speed_kmh", "start_s"}
    if unknown:
        errors.append(f"{where}: unknown mobility key(s) {', '.join(sorted(unknown))} (route and crossing exclude each other)")
    center = mob["crossing"]
    if not (isinstance(center, list) and len(center) == 2 and all(isinstance(c, (int, float)) for c in center)
            and -89 <= center[0] <= 89 and -180 <= center[1] <= 180):
        errors.append(f"{where}: mobility.crossing must be [lat, lon] in degrees")
        return None
    arm = mob.get("arm_m", 150)
    if not isinstance(arm, (int, float)) or not 10 <= arm <= 5000:
        errors.append(f"{where}: mobility.arm_m must be in [10, 5000]")
        return None
    start_arm = mob.get("start_arm", "east")
    if start_arm not in CROSSING_ARMS:
        errors.append(f"{where}: mobility.start_arm must be one of {', '.join(CROSSING_ARMS)}")
        return None
    north, east = (c * arm for c in CROSSING_ARMS[start_arm])
    # same conversion as the mobility client: the station starts where its first lap begins
    first = [center[0] + north / 111195.0, center[1] + east / (111195.0 * math.cos(math.radians(center[0])))]
    return {"crossing": [float(c) for c in center], "arm_m": arm, "start_arm": start_arm, **common, "start": first}


CONTROLS_SUBDIR = "controls"   # pass/fail controls, outside the foundational scenarios


def catalogue_refs(controls=True):
    """Names of the catalogue: the foundational scenarios, then the controls as controls/<name>."""
    def names(directory):
        return sorted(f[:-5] for f in os.listdir(directory) if f.endswith(".toml")) if os.path.isdir(directory) else []
    refs = names(SCENARIO_DIR)
    if controls:
        refs += [f"{CONTROLS_SUBDIR}/{n}" for n in names(os.path.join(SCENARIO_DIR, CONTROLS_SUBDIR))]
    return refs


def list_scenarios():
    out = []
    for ref in catalogue_refs():
        try:
            sc = load_scenario(ref)
            out.append({"name": ref, "description": sc["description"], "image": sc["image"],
                        "variants": scenario_variants(sc["path"]),
                        "stations": [f"{s['name']}:{s['security']}" + (f"+pool{len(s['pseudonyms']['pool'])}" if s.get("pseudonyms") else "")
                                     for s in sc["stations"]],
                        "control": bool(sc["control"]), "eavesdropper": bool(sc["eavesdropper"]), "pki": bool(sc["pki"]),
                        "mobility": any(s.get("mobility") for s in sc["stations"]), "valid": True})
        except ScenarioError as e:
            out.append({"name": ref, "valid": False, "error": str(e)})
    return out
