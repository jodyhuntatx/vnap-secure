"""The committed test certificate sets in certs/: their validity, and regenerating them.

  v3  certs/c-its-pki/  C-ITS-PKI (sim/images/pki/test_certs.py in the vnap-pki image)
  v2  certs/certify/    Vanetza's certify (in the station image)

Lifetimes: no ETSI document in the project's references gives a default. TS 103 097 fixes no
certificate lifetime and TS 102 941 leaves validity to deployment policy (C-ITS-PKI's review,
docs/privacy-and-deployment-notes.md section 2). The defaults are therefore those of the tool that
makes each set, and every one can be changed. A regeneration builds the new set in a scratch
directory, checks it, and only then replaces the files of the set; it writes generated.json with
the lifetimes used. Running simulations keep the certificates they loaded.
"""

import fcntl
import json
import os
import re
import secrets
import shutil
import subprocess
import time
from datetime import datetime, timezone

from .common import CERTS_DIR, CITS_PKI_DIR, PKI_IMAGE, ScenarioError, cits_pki_version

NO_ETSI_DEFAULT = ("no ETSI default: TS 103 097 fixes no certificate lifetime and TS 102 941 leaves it to "
                   "deployment policy, so the default is the tool's own")

SETS = {
    "v3": {
        "dir": "c-its-pki", "security": "certs-v3", "tool": "C-ITS-PKI",
        "format": "ETSI TS 103 097 V1.3.1+ (IEEE 1609.2 COER)",
        "lifetimes": [
            {"key": "root_years", "entity": "root CA", "unit": "years", "default": 10, "min": 1, "max": 50},
            {"key": "tlm_years", "entity": "trust list manager", "unit": "years", "default": 10, "min": 1, "max": 50},
            {"key": "ea_years", "entity": "enrolment authority", "unit": "years", "default": 5, "min": 1, "max": 50},
            {"key": "aa_years", "entity": "authorization authority", "unit": "years", "default": 5, "min": 1, "max": 50},
            {"key": "at_hours", "entity": "authorization tickets (at.cert, bke_at_*)", "unit": "hours", "default": 168,
             "min": 1, "max": 65535},
        ],
        "basis": f"{NO_ETSI_DEFAULT} (C-ITS-PKI: root and TLM 10 years, EA and AA 5 years, AT 168 hours).",
    },
    "v2": {
        "dir": "certify", "security": "certs-v2", "tool": "Vanetza certify",
        "format": "ETSI TS 103 097 V1.2.1",
        "lifetimes": [
            {"key": "root_days", "entity": "root CA", "unit": "days", "default": 365, "min": 1, "max": 18250},
            {"key": "aa_days", "entity": "authorization authority", "unit": "days", "default": 180, "min": 1, "max": 18250},
            {"key": "at_days", "entity": "authorization tickets (rsu, obu)", "unit": "days", "default": 7, "min": 1,
             "max": 18250},
        ],
        "basis": f"{NO_ETSI_DEFAULT} (certify: root 365 days, AA 180 days, AT 7 days).",
    },
}
V3_TICKETS = 24      # bke_at_0 .. bke_at_23, as gen-vnap-certs.sh
V2_STATIONS = ("rsu", "obu")
HOURS_PER_YEAR = 8765.82   # Vanetza's year (31,556,952 s)
STATION_IMAGE = "vnap:latest"


def set_dir(name):
    return os.path.join(CERTS_DIR, SETS[name]["dir"])


def expected_files(name):
    if name == "v3":
        return (["root_ca.cert", "tlm.cert", "ea.cert", "aa.cert", "at.cert", "at.der"]
                + [f"bke_at_{j}{ext}" for j in range(V3_TICKETS) for ext in (".cert", "_sign.der")])
    return [f"{base}{ext}" for base in ("root_ca_vnap", "aa_vnap", *(f"ticket_vnap_{s}" for s in V2_STATIONS))
            for ext in (".cert", ".key")]


def lifetimes(name, given=None):
    """The lifetimes to use: defaults, with `given` ({key: int}) on top. ValueError lists every problem."""
    spec = {s["key"]: s for s in SETS[name]["lifetimes"]}
    given = dict(given or {})
    errors = [f"unknown lifetime '{k}' for {name} (known: {', '.join(spec)})" for k in given if k not in spec]
    values = {}
    for key, s in spec.items():
        v = given.get(key, s["default"])
        if isinstance(v, str) and v.strip().isdigit():
            v = int(v)
        if not isinstance(v, int) or isinstance(v, bool) or not s["min"] <= v <= s["max"]:
            errors.append(f"{key}: a whole number of {s['unit']} from {s['min']} to {s['max']}")
        values[key] = v
    if not errors:   # a certificate must not outlive the one that signed it
        if name == "v3":
            for key in ("ea_years", "aa_years"):
                if values[key] > values["root_years"]:
                    errors.append(f"{key} ({values[key]}) exceeds root_years ({values['root_years']}): the root CA signs it")
            if values["at_hours"] > values["aa_years"] * HOURS_PER_YEAR:
                errors.append(f"at_hours ({values['at_hours']}) exceeds the AA's {values['aa_years']} year(s): the AA signs the tickets")
        else:
            if values["aa_days"] > values["root_days"]:
                errors.append(f"aa_days ({values['aa_days']}) exceeds root_days ({values['root_days']}): the root CA signs it")
            if values["at_days"] > values["aa_days"]:
                errors.append(f"at_days ({values['at_days']}) exceeds aa_days ({values['aa_days']}): the AA signs the tickets")
    if errors:
        raise ValueError("; ".join(errors))
    return values


# ------------------------------------------------------------------ docker helpers
def _pki_image():
    from .lifecycle import Runner, ensure_image   # lifecycle imports a lot; only needed here
    return ensure_image(Runner(False, {}), PKI_IMAGE, "pki", extra=(CITS_PKI_DIR, "src", "cits-pki/src"),
                        labels={"vnap.cits_pki": cits_pki_version()})


def _docker(args, what):
    result = subprocess.run(["docker", *args], capture_output=True, text=True)
    if result.returncode != 0:
        raise ScenarioError(f"{what}: {(result.stderr or result.stdout).strip()[-800:]}")
    return result.stdout


def _user():
    return ["--user", f"{os.getuid()}:{os.getgid()}"]   # the files belong to whoever regenerates them


def _inspect_v3(directory):
    out = _docker(["run", "--rm", "--network", "none", *_user(), "-v", f"{directory}:/certs:ro",
                   "--entrypoint", "python3", _pki_image(), "/usr/local/bin/test_certs.py", "inspect", "/certs"],
                  "inspecting the v3 certificates")
    return json.loads(out)


V2_VALIDITY = re.compile(r"Validity starts (\S+ \S+) and ends (\S+ \S+)")


def _inspect_v2(directory):
    files = sorted(f for f in os.listdir(directory) if f.endswith(".cert"))
    script = "; ".join(f'echo "== {f}"; certify show-certificate --certificate /certs/{f}' for f in files)
    out = _docker(["run", "--rm", "--network", "none", *_user(), "-v", f"{directory}:/certs:ro",
                   "--entrypoint", "sh", STATION_IMAGE, "-c", script], "inspecting the v2 certificates")
    certs = []
    for block in out.split("== ")[1:]:
        name, _, text = block.partition("\n")
        m = V2_VALIDITY.search(text)
        subject = re.search(r"Subject: [^(]*\(([^)]*)\)", text)
        if not m:
            certs.append({"file": name.strip(), "error": text.strip()[:200] or "unreadable"})
            continue
        start, end = (datetime.strptime(t, "%Y-%b-%d %H:%M:%S").replace(tzinfo=timezone.utc) for t in m.groups())
        certs.append({"file": name.strip(), "role": (subject[1].lower() if subject else ""),
                      "valid_from": start.strftime("%Y-%m-%dT%H:%M:%SZ"), "valid_until": end.strftime("%Y-%m-%dT%H:%M:%SZ"),
                      "expired": end.timestamp() <= time.time(),
                      "bytes": os.path.getsize(os.path.join(directory, name.strip()))})
    return certs


def inspect(name, directory=None):
    """{set, dir, certs: [...], expires, expired, missing, generated} of one set (runs docker)."""
    directory = directory or set_dir(name)
    certs = (_inspect_v3 if name == "v3" else _inspect_v2)(directory) if os.path.isdir(directory) else []
    ends = [c["valid_until"] for c in certs if "valid_until" in c]
    generated = None
    try:
        with open(os.path.join(directory, "generated.json")) as f:
            generated = json.load(f)
    except (OSError, ValueError):
        pass
    present = set(os.listdir(directory)) if os.path.isdir(directory) else set()
    return {"set": name, "dir": os.path.relpath(directory, os.path.dirname(CERTS_DIR)), **{k: SETS[name][k] for k in
            ("security", "tool", "format")}, "certs": certs, "expires": min(ends) if ends else None,
            "expired": any(c.get("expired") for c in certs) or any("error" in c for c in certs),
            "missing": [f for f in expected_files(name) if f not in present], "generated": generated}


# ------------------------------------------------------------------ regeneration
def _generate_v3(tmp, v):
    _docker(["run", "--rm", "--network", "none", *_user(), "-v", f"{tmp}:/out", "--entrypoint", "python3", _pki_image(),
             "/usr/local/bin/test_certs.py", "generate-v3", "/out", "--root-years", str(v["root_years"]),
             "--tlm-years", str(v["tlm_years"]), "--ea-years", str(v["ea_years"]), "--aa-years", str(v["aa_years"]),
             "--at-hours", str(v["at_hours"]), "--count", str(V3_TICKETS)], "generating the v3 set")


def _generate_v2(tmp, v):
    # the steps of scripts/certs/gen-certify.sh, with the lifetimes as --days
    tickets = " ".join(
        f"certify generate-key --output ticket_vnap_{s}.key && "
        f"certify generate-ticket --output ticket_vnap_{s}.cert --subject-key ticket_vnap_{s}.key "
        f"--sign-key aa_vnap.key --sign-cert aa_vnap.cert --days {v['at_days']} "
        f"--cam-permissions 1111111111111100 --denm-permissions 000000000000000000000000 &&" for s in V2_STATIONS)
    script = ("set -e; cd /out && certify generate-key --output root_ca_vnap.key && "
              "certify generate-root --output root_ca_vnap.cert --subject-key root_ca_vnap.key "
              f"--subject-name 'Test root CA' --days {v['root_days']} --aid 36 --aid 37 --aid 141 && "
              "certify generate-key --output aa_vnap.key && "
              "certify generate-aa --output aa_vnap.cert --sign-key root_ca_vnap.key --sign-cert root_ca_vnap.cert "
              f"--subject-key aa_vnap.key --subject-name 'Authorization Authority' --days {v['aa_days']} "
              f"--aid 36 --aid 37 --aid 141 && {tickets} true")
    _docker(["run", "--rm", "--network", "none", *_user(), "-v", f"{tmp}:/out", "--entrypoint", "sh", STATION_IMAGE,
             "-c", script], "generating the v2 set")


def regenerate(name, given=None, by=None):
    """Make a new set with the given lifetimes and replace the committed one. Returns inspect()."""
    if name not in SETS:
        raise ValueError(f"unknown set '{name}' (v2 or v3)")
    values = lifetimes(name, given)
    target = set_dir(name)
    os.makedirs(CERTS_DIR, exist_ok=True)
    with open(os.path.join(CERTS_DIR, ".regen.lock"), "w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ScenarioError("another regeneration is running")
        # scratch directory next to the set: under the user's home, which snap docker can mount
        tmp = os.path.join(CERTS_DIR, f".regen-{name}-{secrets.token_hex(4)}")
        os.makedirs(tmp)
        try:
            (_generate_v3 if name == "v3" else _generate_v2)(tmp, values)
            check = inspect(name, tmp)
            problems = check["missing"] + [c["file"] for c in check["certs"] if c.get("expired") or "error" in c]
            if problems:
                raise ScenarioError(f"the new {name} set is incomplete or invalid: {', '.join(problems)}")
            record = {"generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "by": by,
                      "tool": SETS[name]["tool"], "lifetimes": values,
                      **({"cits_pki": cits_pki_version()} if name == "v3" else {})}
            with open(os.path.join(tmp, "generated.json"), "w") as f:
                json.dump(record, f, indent=1)
                f.write("\n")
            os.makedirs(target, exist_ok=True)
            for f in os.listdir(target):   # the old set: certificates, keys and the record, nothing else
                if f.endswith((".cert", ".der", ".key")) or f == "generated.json":
                    os.remove(os.path.join(target, f))
            for f in os.listdir(tmp):
                shutil.move(os.path.join(tmp, f), os.path.join(target, f))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    return inspect(name)
