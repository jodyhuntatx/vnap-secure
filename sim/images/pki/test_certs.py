"""The repository's committed v3 test certificate set: inspect its validity, and generate it with
C-ITS-PKI. Runs in the vnap-pki image (C-ITS-PKI at $CITS_PKI_HOME); vnapsim.testcerts
calls it. The v2 set is made with Vanetza's certify in the station image, not here.

  test_certs.py inspect DIR                    JSON: every *.cert in DIR (v3) with its validity
  test_certs.py generate-v3 DIR [--root-years 10 --tlm-years 10 --ea-years 5 --aa-years 5
                                 --at-hours 168 --count 24]

generate-v3 writes the files certs/c-its-pki/ holds: root_ca, tlm, ea and aa certificates, a
regular AT (at.cert, at.der) and butterfly ATs bke_at_<j>.cert with bke_at_<j>_sign.der. The CA
private keys are not written: the set only has to verify, not to issue more.
"""

import argparse
import contextlib
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.environ.get("CITS_PKI_HOME", "/opt/cits-pki"))

from src.types import DurationChoice, EtsiVersion, PsidSsp, its_time32_to_unix, now_its_time32  # noqa: E402

# Vanetza's unit lengths (a year is 31,556,952 s), so that "expired" means what the stations decide
UNIT_SECONDS = {DurationChoice.MICROSECONDS: 1e-6, DurationChoice.MILLISECONDS: 1e-3, DurationChoice.SECONDS: 1,
                DurationChoice.MINUTES: 60, DurationChoice.HOURS: 3600, DurationChoice.SIXTY_HOURS: 216000,
                DurationChoice.YEARS: 31556952}
ROLES = {"root_ca": "root CA", "tlm": "trust list manager", "ea": "enrolment authority", "aa": "authorization authority"}


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def describe(path):
    """{file, role, valid_from, valid_until, expired, bytes} of one v3 (COER) certificate."""
    from src.encoding import decode_certificate
    data = path.read_bytes()
    cert, _ = decode_certificate(data, version=EtsiVersion.V2_2_1)
    vp = cert.tbs.validity_period
    start = its_time32_to_unix(vp.start)
    end = start + vp.duration.value * UNIT_SECONDS[vp.duration.choice]
    return {"file": path.name, "role": ROLES.get(path.stem.rstrip("0123456789_"), "authorization ticket"),
            "valid_from": iso(start), "valid_until": iso(end), "expired": end <= time.time(), "bytes": len(data)}


def inspect(directory):
    out = []
    for path in sorted(Path(directory).glob("*.cert"), key=lambda p: (len(p.name), p.name)):
        try:
            out.append(describe(path))
        except Exception as e:  # noqa: BLE001
            out.append({"file": path.name, "error": str(e)})
    return out


def generate_v3(directory, root_years, tlm_years, ea_years, aa_years, at_hours, count):
    from src.crypto import generate_keypair, random_bytes
    from src.pki import CITSPKI
    from src.types import PublicKeyAlgorithm
    out = Path(directory)
    out.mkdir(parents=True, exist_ok=True)
    algo = PublicKeyAlgorithm.ECDSA_NIST_P256
    pki = CITSPKI(algorithm=algo, version=EtsiVersion.V2_2_1)
    certs = pki.initialise(root_validity_years=root_years, tlm_validity_years=tlm_years,
                           ea_validity_years=ea_years, aa_validity_years=aa_years)
    for name in ("root_ca", "tlm", "ea", "aa"):
        (out / f"{name}.cert").write_bytes(certs[name])
    psids = [PsidSsp(psid=36), PsidSsp(psid=37)]   # CAM, DENM: as gen-vnap-certs.sh
    at = pki.issue_authorization_ticket(app_psids=psids, validity_hours=at_hours)
    (out / "at.cert").write_bytes(at["at"])
    (out / "at.der").write_bytes(at["priv_key_der"])
    cat_sign, _ = generate_keypair(algo)
    cat_enc, _ = generate_keypair(algo)
    tickets = pki.issue_butterfly_authorization_tickets(
        caterpillar_sign_priv=cat_sign, sign_expansion_key=random_bytes(16),
        i_value=now_its_time32() // (7 * 86400), count=count, mode="original",
        caterpillar_enc_priv=cat_enc, enc_expansion_key=random_bytes(16),
        app_psids=psids, validity_hours=at_hours)
    for t in tickets:
        (out / f"bke_at_{t['j']}.cert").write_bytes(t["at"])
        (out / f"bke_at_{t['j']}_sign.der").write_bytes(t["priv_key_der"])


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("inspect")
    p.add_argument("dir")
    p = sub.add_parser("generate-v3")
    p.add_argument("dir")
    for name, default in (("root-years", 10), ("tlm-years", 10), ("ea-years", 5), ("aa-years", 5),
                          ("at-hours", 168), ("count", 24)):
        p.add_argument(f"--{name}", type=int, default=default)
    args = ap.parse_args()
    if args.cmd == "inspect":
        print(json.dumps(inspect(args.dir)))
    else:
        with contextlib.redirect_stdout(sys.stderr):   # C-ITS-PKI reports its steps on stdout
            generate_v3(args.dir, args.root_years, args.tlm_years, args.ea_years, args.aa_years, args.at_hours, args.count)
        print(json.dumps(inspect(args.dir)))


if __name__ == "__main__":
    main()
