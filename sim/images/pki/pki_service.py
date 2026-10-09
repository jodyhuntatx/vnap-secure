#!/usr/bin/env python3
"""Per-run PKI service (vnap-secure): one certificate authority per simulation run.

Wraps C-ITS-PKI (root CA, TLM, EA, AA; butterfly key expansion per IEEE 1609.2.1 /
ETSI TS 102 941). Commands:

  provision  create the run's CA hierarchy and enrol the stations: road-side units get a
             regular authorization ticket (AT), pseudonym stations caterpillar keys and an
             initial batch of butterfly ATs
  serve      answer certificate batch requests on the run's control broker
  run        provision, then serve

Configuration (--config, JSON text or @file, default: environment variable PKI_CONFIG):
  {"etsi_version": "v3", "validity_hours": 24, "psids": [36, 37], "key_derivation": "station",
   "stations": [{"name": "rsu", "station_id": 1, "certificates": "regular"},
                {"name": "obu", "station_id": 2, "certificates": "bke", "initial": 8, "batch": 8}]}

key_derivation (butterfly ATs):
  station  (default) the vehicle keeps its caterpillar private key; the PKI knows only the
           caterpillar public key and the expansion key, certifies cocoon key + r*G and returns
           the offsets r, from which the station derives the private keys (IEEE 1609.2.1). The
           PKI never holds an AT private key. Provisioning plays the vehicle once to derive the
           initial batch into the station's directory (the keys are not kept on the PKI side).
  pki      the PKI generates the caterpillar keys too and returns finished private keys
           (simpler; the PKI then knows every AT key)

Layout of --dir (default /pki):
  public/                 root_ca.cert, tlm.cert, ea.cert, aa.cert (what stations trust)
  stations/<name>/        the station's own files: at.cert + at.der, or bke_at_<k>.cert +
                          bke_at_<k>_sign.der; with station key derivation also the vehicle's
                          caterpillar_sign.key, sign_expansion.key and ec_sign.key
  private/ca/             CA private keys and pki_meta.json (never mounted into stations)
  private/stations/<name> enrolment certificate, caterpillar public key (or keys) and expansion
                          key, batch state
  private/issued.jsonl    every issued AT: station, HashedId8, batch, i-period (ground truth)

With station key derivation, vnapctl runs "provision" once with the whole directory and then
"serve" with only private/ and public/ mounted: the serving PKI cannot read the vehicles'
directories (caterpillar private keys, initial AT keys).

Refill protocol (MQTT, QoS 1, topic prefix --topic, default vnap/pki):
  <prefix>/<station id>/request  {"request_id": ..., "unused": n, "count": m}
  <prefix>/<station id>/batch    {"request_id": ..., "i_period": i, "certificates": [base64 COER],
                                  "issue_ms": t, "queue_ms": q, plus "indices": [j] and
                                  "offsets": [hex r] (station key derivation) or "keys": [base64
                                  PKCS#8 DER] (key_derivation pki)}
                              or {"request_id": ..., "error": "..."}
Broker account (optional): CONTROL_USERNAME / CONTROL_PASSWORD.
"""
import argparse
import base64
import hashlib
import json
import os
import queue
import shutil
import signal
import sys
import threading
import time
from pathlib import Path

from cryptography.hazmat.primitives import serialization

sys.path.insert(0, os.environ.get("CITS_PKI_HOME", "/opt/cits-pki"))
from src.certificates import issue_butterfly_authorization_tickets as aa_issue_butterfly  # noqa: E402
from src.crypto import (bke_butterfly_private_key, bke_cocoon_private_key, bke_cocoon_public_key,  # noqa: E402
                        deserialize_private_key, generate_keypair, public_key_to_point, random_bytes,
                        serialize_private_key)
from src.pki import CITSPKI, PKIEntity  # noqa: E402
from src.types import (Certificate, CertificateId, CertificateType, CertIdChoice, Duration, DurationChoice,  # noqa: E402
                       EtsiVersion, IssuerChoice, IssuerIdentifier, PsidSsp, PublicKeyAlgorithm,
                       ToBeSignedCertificate, ValidityPeriod, now_its_time32)

MAX_BATCH = 256


def log(msg):
    print(f"[PKI] {msg}", flush=True)


def hashed_id8(cert_bytes):
    """HashedId8: the last 8 bytes of SHA-256 over the certificate encoding (IEEE 1609.2 / TS 103 097)."""
    return hashlib.sha256(cert_bytes).digest()[-8:].hex()


def write_private(path, data):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.write_bytes(data)
    os.chmod(path, 0o600)


def load_config(text):
    if not text:
        sys.exit("pki: no configuration (--config or PKI_CONFIG)")
    if text.startswith("@"):
        text = Path(text[1:]).read_text()
    cfg = json.loads(text)
    names = set()
    for st in cfg.get("stations", []):
        if st.get("certificates") not in ("regular", "bke"):
            sys.exit(f"pki: station {st.get('name')}: certificates must be 'regular' or 'bke'")
        if st["name"] in names or "/" in st["name"]:
            sys.exit(f"pki: bad or duplicate station name {st['name']!r}")
        names.add(st["name"])
    return cfg


class RunPKI:
    def __init__(self, directory, cfg):
        self.dir = Path(directory)
        self.cfg = cfg
        self.version = EtsiVersion.V2_2_1 if cfg.get("etsi_version", "v3") == "v3" else EtsiVersion.V1_2_1
        self.algo = PublicKeyAlgorithm.ECDSA_NIST_P256
        self.validity_hours = int(cfg.get("validity_hours", 24))
        self.psids = [PsidSsp(psid=int(p)) for p in cfg.get("psids", [36, 37])]
        self.mode = cfg.get("bke_mode", "original")
        self.derivation = cfg.get("key_derivation", "station")
        if self.derivation not in ("station", "pki"):
            sys.exit("pki: key_derivation must be 'station' or 'pki'")
        self.pki = None
        self.lock = threading.Lock()   # issuance and state files: one request at a time

    # ---------------------------------------------------------------- provisioning
    def provision(self):
        t0 = time.monotonic()
        if (self.dir / "private" / "ca" / "pki_meta.json").exists():
            sys.exit(f"pki: {self.dir} already holds a PKI (one CA per run; use a fresh directory)")
        self.pki = CITSPKI(algorithm=self.algo, version=self.version)
        self.pki.initialise(root_ca_name="VNAP-Run-Root-CA", tlm_name="VNAP-Run-TLM",
                            ea_name="VNAP-Run-EA", aa_name="VNAP-Run-AA")
        ca = self.dir / "private" / "ca"
        self.pki.save(str(ca))
        for f in ca.glob("*.key"):
            os.chmod(f, 0o600)
        public = self.dir / "public"
        public.mkdir(parents=True, exist_ok=True)
        for name in ("root_ca", "tlm", "ea", "aa"):
            shutil.copy(ca / f"{name}.cert", public / f"{name}.cert")
        log(f"CA hierarchy created: root {hashed_id8((public / 'root_ca.cert').read_bytes())}, "
            f"AA {hashed_id8((public / 'aa.cert').read_bytes())} ({'v3' if self.version == EtsiVersion.V2_2_1 else 'v2'})")
        for st in self.cfg.get("stations", []):
            if st["certificates"] == "regular":
                self.issue_regular(st)
            else:
                self.enrol(st)
                n = int(st.get("initial", 8))
                out = self.dir / "stations" / st["name"]
                out.mkdir(parents=True, exist_ok=True)
                if self.derivation == "station":
                    batch, issue_ms = self.issue_batch_public(st["name"], n)
                    # the vehicle's part, once at provisioning: derive the initial keys from its own secrets
                    cat = deserialize_private_key((out / "caterpillar_sign.key").read_bytes())
                    exp = (out / "sign_expansion.key").read_bytes()
                    for k, t in enumerate(batch):
                        key = bke_butterfly_private_key(bke_cocoon_private_key(cat, exp, t["i"], t["j"]), t["offset"])
                        if public_key_to_point(key.public_key()).compressed != t["certificate"].tbs.verify_key_indicator.point.compressed:
                            sys.exit(f"pki: derived key {k} of {st['name']} does not match its certificate")
                        (out / f"bke_at_{k}.cert").write_bytes(t["at"])
                        write_private(out / f"bke_at_{k}_sign.der", serialize_private_key(key))
                    del cat
                else:
                    batch, issue_ms = self.issue_batch(st["name"], n)
                    for k, t in enumerate(batch):
                        (out / f"bke_at_{k}.cert").write_bytes(t["at"])
                        write_private(out / f"bke_at_{k}_sign.der", t["priv_key_der"])
                log(f"station {st['name']} (id {st.get('station_id')}): enrolled, initial batch of {n} AT(s) "
                    f"in {issue_ms:.0f} ms (key derivation: {self.derivation})")
        elapsed = (time.monotonic() - t0) * 1000
        (self.dir / "private" / "provision.json").write_text(json.dumps({"provisioned_ms": round(elapsed)}))
        log(f"provisioned in {elapsed:.0f} ms")

    def issue_regular(self, st):
        at = self.pki.issue_authorization_ticket(app_psids=self.psids, validity_hours=self.validity_hours)
        out = self.dir / "stations" / st["name"]
        out.mkdir(parents=True, exist_ok=True)
        (out / "at.cert").write_bytes(at["at"])
        write_private(out / "at.der", at["priv_key_der"])
        self.record(st["name"], st.get("station_id"), [at["at"]], batch=0, i_period=None, kind="regular")
        log(f"station {st['name']} (id {st.get('station_id')}): regular AT {hashed_id8(at['at'])}")

    def enrol(self, st):
        name = st["name"]
        priv = self.dir / "private" / "stations" / name
        ec = self.pki.enrol_its_station(name=f"vnap-{name}")
        write_private(priv / "ec.cert", ec["ec"])
        cat, _ = generate_keypair(self.algo)
        expansion = random_bytes(16)
        write_private(priv / "sign_expansion.key", expansion)   # the EA/RA knows the expansion key
        if self.derivation == "station":
            # the vehicle's secrets go to the vehicle only; the PKI keeps the public key
            vehicle = self.dir / "stations" / name
            write_private(vehicle / "ec_sign.key", ec["priv_key_der"])
            write_private(vehicle / "caterpillar_sign.key", serialize_private_key(cat))
            write_private(vehicle / "sign_expansion.key", expansion)
            os.chmod(vehicle, 0o755)
            write_private(priv / "caterpillar_sign.pub", cat.public_key().public_bytes(
                serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo))
            del cat
        else:
            write_private(priv / "ec_sign.key", ec["priv_key_der"])
            write_private(priv / "caterpillar_sign.key", serialize_private_key(cat))
        if self.derivation == "pki" and self.mode == "original":
            cat_enc, _ = generate_keypair(self.algo)
            write_private(priv / "caterpillar_enc.key", serialize_private_key(cat_enc))
            write_private(priv / "enc_expansion.key", random_bytes(16))
        state = {"name": name, "station_id": st.get("station_id"), "batch_size": int(st.get("batch", 8)),
                 "key_derivation": self.derivation,
                 "i_base": now_its_time32() // (7 * 86400), "batches": 0, "issued": 0}
        write_private(priv / "state.json", json.dumps(state, indent=1).encode())

    # ---------------------------------------------------------------- issuance
    def load_aa(self):
        """CA state from disk (serve without provision in the same process)."""
        meta = json.loads((self.dir / "private" / "ca" / "pki_meta.json").read_text())
        self.version = EtsiVersion(meta["etsi_version"])
        self.pki = CITSPKI(algorithm=PublicKeyAlgorithm(meta["algorithm"]), region_ids=meta.get("region_ids"),
                           version=self.version)
        # only the encoded AA certificate is needed (issuer digest, v3 signing input), as in C-ITS-PKI's CLI
        vp = ValidityPeriod(start=0, duration=Duration(DurationChoice.YEARS, 1))
        tbs = ToBeSignedCertificate(id=CertificateId(CertIdChoice.NONE), craca_id=b"\x00\x00\x00", crl_series=0,
                                    validity_period=vp)
        aa_cert = Certificate(version=2, cert_type=CertificateType.EXPLICIT, issuer=IssuerIdentifier(IssuerChoice.SELF),
                              tbs=tbs)
        aa_cert.encoded = (self.dir / "private" / "ca" / "aa.cert").read_bytes()
        aa_key = deserialize_private_key((self.dir / "private" / "ca" / "aa_sign.key").read_bytes())
        self.pki.aa = PKIEntity(name="AA", sign_priv_key=aa_key, sign_pub_key=aa_key.public_key(),
                                certificate=aa_cert, algorithm=self.algo)

    def station_by_id(self, station_id):
        for d in (self.dir / "private" / "stations").glob("*/state.json"):
            state = json.loads(d.read_text())
            if state.get("station_id") == station_id:
                return state["name"]
        return None

    def issue_batch(self, name, count):
        """One butterfly batch for station `name` in its next i-period. Returns (tickets, issue ms)."""
        priv = self.dir / "private" / "stations" / name
        with self.lock:
            state = json.loads((priv / "state.json").read_text())
            i_value = state["i_base"] + state["batches"]
            cat_enc = enc_exp = None
            if self.mode == "original":
                cat_enc = deserialize_private_key((priv / "caterpillar_enc.key").read_bytes())
                enc_exp = (priv / "enc_expansion.key").read_bytes()
            t0 = time.monotonic()
            tickets = self.pki.issue_butterfly_authorization_tickets(
                caterpillar_sign_priv=deserialize_private_key((priv / "caterpillar_sign.key").read_bytes()),
                sign_expansion_key=(priv / "sign_expansion.key").read_bytes(),
                i_value=i_value, count=count, mode=self.mode,
                caterpillar_enc_priv=cat_enc, enc_expansion_key=enc_exp,
                app_psids=self.psids, validity_hours=self.validity_hours)
            issue_ms = (time.monotonic() - t0) * 1000
            state["batches"] += 1
            state["issued"] += count
            write_private(priv / "state.json", json.dumps(state, indent=1).encode())
            self.record(name, state.get("station_id"), [t["at"] for t in tickets], state["batches"], i_value, "bke")
        return tickets, issue_ms

    def issue_batch_public(self, name, count):
        """RA/AA side only (station key derivation): cocoon public keys from the vehicle's caterpillar
        public key, certified with fresh offsets. Returns ([{at, certificate, i, j, offset}], issue ms)."""
        priv = self.dir / "private" / "stations" / name
        with self.lock:
            state = json.loads((priv / "state.json").read_text())
            i_value = state["i_base"] + state["batches"]
            caterpillar = serialization.load_der_public_key((priv / "caterpillar_sign.pub").read_bytes())
            expansion = (priv / "sign_expansion.key").read_bytes()
            t0 = time.monotonic()
            cocoons = [bke_cocoon_public_key(caterpillar, expansion, i_value, j) for j in range(count)]
            issued = aa_issue_butterfly(cocoon_sign_pubs=cocoons, aa_cert=self.pki.aa.certificate,
                                        aa_priv_key=self.pki.aa.sign_priv_key, app_psids=self.psids,
                                        sign_algorithm=self.algo, validity_hours=self.validity_hours,
                                        region_ids=self.pki.region_ids, version=self.version)
            issue_ms = (time.monotonic() - t0) * 1000
            state["batches"] += 1
            state["issued"] += count
            write_private(priv / "state.json", json.dumps(state, indent=1).encode())
            tickets = [{"at": cert.encoded, "certificate": cert, "i": i_value, "j": j, "offset": offset}
                       for j, (cert, offset) in enumerate(issued)]
            self.record(name, state.get("station_id"), [t["at"] for t in tickets], state["batches"], i_value, "bke")
        return tickets, issue_ms

    def record(self, name, station_id, certs, batch, i_period, kind):
        with open(self.dir / "private" / "issued.jsonl", "a") as f:
            for c in certs:
                f.write(json.dumps({"t": round(time.time(), 3), "station": name, "station_id": station_id, "kind": kind,
                                    "batch": batch, "i_period": i_period, "hashed_id8": hashed_id8(c)}) + "\n")

    # ---------------------------------------------------------------- serving
    def serve(self, broker, port, prefix):
        import paho.mqtt.client as mqtt
        if self.pki is None:
            self.load_aa()
            done = self.dir / "private" / "provision.json"
            if done.exists():  # provisioned by a separate step: report it like "run" does
                log(f"provisioned in {json.loads(done.read_text())['provisioned_ms']} ms (separate provisioning step)")
        work = queue.Queue()
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="vnap-pki")
        if os.environ.get("CONTROL_USERNAME"):
            client.username_pw_set(os.environ["CONTROL_USERNAME"], os.environ.get("CONTROL_PASSWORD", ""))

        def on_connect(c, userdata, flags, rc, props):
            if rc == 0:
                c.subscribe(f"{prefix}/+/request", qos=1)
                log(f"connected to {broker}:{port}, listening on {prefix}/+/request")
            else:
                log(f"connection refused ({rc})")

        def on_message(c, userdata, msg):
            if msg.retain:
                return   # a stale request must not be answered again on reconnect
            work.put((time.monotonic(), msg.topic, msg.payload))

        client.on_connect = on_connect
        client.on_message = on_message
        client.reconnect_delay_set(1, 30)
        client.connect_async(broker, port, 60)
        client.loop_start()

        running = True

        def stop(*_):
            nonlocal running
            running = False
        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGINT, stop)
        log("ready")
        while running:
            try:
                received, topic, payload = work.get(timeout=0.5)
            except queue.Empty:
                continue
            self.answer(client, prefix, received, topic, payload)
        client.loop_stop()
        client.disconnect()

    def answer(self, client, prefix, received, topic, payload):
        parts = topic.split("/")
        sid = parts[-2]
        reply = f"{prefix}/{sid}/batch"
        try:
            req = json.loads(payload)
            if not isinstance(req, dict):
                raise ValueError
        except ValueError:
            client.publish(reply, json.dumps({"error": "request is not a JSON object"}), qos=1)
            return
        rid = req.get("request_id")
        name = self.station_by_id(int(sid)) if sid.isdigit() else None
        if name is None:
            log(f"request {rid} from unknown station {sid}: refused")
            client.publish(reply, json.dumps({"request_id": rid, "error": f"station {sid} is not enrolled"}), qos=1)
            return
        try:
            count = max(1, min(int(req.get("count") or 8), MAX_BATCH))
        except (TypeError, ValueError):
            count = 8
        queue_ms = (time.monotonic() - received) * 1000
        derivation = json.loads((self.dir / "private" / "stations" / name / "state.json").read_text()).get(
            "key_derivation", "pki")
        try:
            tickets, issue_ms = (self.issue_batch_public if derivation == "station" else self.issue_batch)(name, count)
        except Exception as e:  # report, keep serving
            log(f"request {rid} from station {sid}: issuance failed: {e}")
            client.publish(reply, json.dumps({"request_id": rid, "error": f"issuance failed: {e}"}), qos=1)
            return
        state = json.loads((self.dir / "private" / "stations" / name / "state.json").read_text())
        answer = {"request_id": rid, "i_period": state["i_base"] + state["batches"] - 1, "count": len(tickets),
                  "certificates": [base64.b64encode(t["at"]).decode() for t in tickets],
                  "issue_ms": round(issue_ms, 1), "queue_ms": round(queue_ms, 1), "key_derivation": derivation}
        if derivation == "station":
            answer["indices"] = [t["j"] for t in tickets]
            answer["offsets"] = [f"{t['offset']:064x}" for t in tickets]   # the private keys stay with the vehicle
        else:
            answer["keys"] = [base64.b64encode(t["priv_key_der"]).decode() for t in tickets]
        client.publish(reply, json.dumps(answer), qos=1)
        log(f"request {rid} from station {sid} ({name}, {req.get('unused', '?')} unused): issued {len(tickets)} AT(s) "
            f"in batch {state['batches']}, issue {issue_ms:.0f} ms, queue {queue_ms:.0f} ms")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("command", choices=("provision", "serve", "run"))
    ap.add_argument("--dir", default="/pki")
    ap.add_argument("--config", default=os.environ.get("PKI_CONFIG"))
    ap.add_argument("--broker", default=os.environ.get("CONTROL_BROKER", "pseudo-broker"))
    ap.add_argument("--port", type=int, default=int(os.environ.get("CONTROL_PORT", "1883")))
    ap.add_argument("--topic", default="vnap/pki")
    args = ap.parse_args()
    cfg = load_config(args.config) if args.command != "serve" else (load_config(args.config) if args.config else {})
    run = RunPKI(args.dir, cfg)
    if args.command in ("provision", "run"):
        run.provision()
    if args.command in ("serve", "run"):
        run.serve(args.broker, args.port, args.topic.rstrip("/"))


if __name__ == "__main__":
    main()
