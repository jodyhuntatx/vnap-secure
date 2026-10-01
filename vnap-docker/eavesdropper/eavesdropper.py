#!/usr/bin/env python3
"""eavesdropper -- passive ITS-G5 listener playing an untrusted party that tracks vehicles.

It only sees raw frames on the V2X link (no keys, no trust store, no MQTT, no control
channel) and uses public knowledge of the ETSI/IEEE formats to decode them:

  Ethernet            source MAC
  GeoNetworking       basic + common header, source position vector (GN address, position,
                      speed, heading)                                   [EN 302 636-4-1]
  Security header     v2: ETSI TS 103 097 V1.2.1 (hand parser), v3: IEEE 1609.2 / ETSI TS
                      103 097 V1.3.1 (OER). Signer certificate digest (HashedId8) and the
                      public certificate fields (issuer, validity, permissions)
  BTP                 destination port                                   [EN 302 636-5-1]
  Facilities          ITS PDU header of any message (stationId), full CAM decode [TS 103 900]

Messages are linked into vehicle tracks by shared identifiers (MAC, GN address, stationId,
certificate digest) and, for a certificate never seen before, by spatio-temporal continuity
with a track that just went quiet. Every pseudonym (certificate) change that can be linked
is logged with its evidence.

Output (in --log-dir): messages.jsonl (every frame), events.jsonl (linked pseudonym
changes, new tracks), tracks.json (snapshot, rewritten periodically). The console shows
events and a periodic summary.

  eavesdropper.py [--iface eth0]            live capture (needs CAP_NET_RAW)
  eavesdropper.py --pcap capture.pcap       offline analysis of a pcap file
"""

import argparse
import hashlib
import json
import math
import os
import socket
import struct
import sys
import time
from datetime import datetime, timezone

import asn1tools

ETH_P_GEONET = 0x8947
ITS_EPOCH = 1072915200  # 2004-01-01T00:00:00Z; ITS times count from here (TAI, leap seconds ignored)
BTP_PORTS = {2001: "CAM", 2002: "DENM", 2003: "MAPEM", 2004: "SPATEM", 2006: "IVIM", 2007: "SREM",
             2008: "SSEM", 2009: "CPM", 2018: "VAM", 2010: "EV-RSR"}
STATION_TYPES = {0: "unknown", 1: "pedestrian", 2: "cyclist", 3: "moped", 4: "motorcycle",
                 5: "passengerCar", 6: "bus", 7: "lightTruck", 8: "heavyTruck", 9: "trailer",
                 10: "specialVehicle", 11: "tram", 12: "lightVruVehicle", 13: "animal",
                 14: "agricultural", 15: "roadSideUnit"}


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def hexs(b):
    return b.hex() if b is not None else None


# ---------------------------------------------------------------- ASN.1 codecs

class Codecs:
    def __init__(self, asn1_dir):
        f = lambda n: os.path.join(asn1_dir, n)
        self.sec = asn1tools.compile_files(
            [f("IEEE1609dot2BaseTypes.asn"), f("IEEE1609dot2.asn"), f("TS103097v131.asn")], "oer")
        self.cam = asn1tools.compile_files([f("TS102894-2v221-CDD.asn"), f("TS103900v211-CAM.asn")], "uper")


# ---------------------------------------------------------------- GeoNetworking / BTP

def parse_lpv(b):
    """Long position vector (24 bytes)."""
    gn_addr = b[0:8]
    tst, lat, lon, pai_speed, heading = struct.unpack(">IiiHH", b[8:24])
    speed = pai_speed & 0x7FFF
    if speed & 0x4000:  # 15-bit two's complement
        speed -= 0x8000
    return {
        "gn_addr": gn_addr.hex(),
        "gn_station_type": (gn_addr[0] >> 2) & 0x1F,
        "gn_mid": gn_addr[2:8].hex(":"),
        "lat": lat / 1e7, "lon": lon / 1e7,
        "speed_mps": speed / 100.0, "heading_deg": heading / 10.0,
    }


# extended header layout after the common header (EN 302 636-4-1): offset of the source
# position vector and total size, by header type (and subtype for TSB)
_EXT_LAYOUT = {
    (1, None): (0, 24),   # BEACON: SO PV
    (2, None): (4, 48),   # GUC: SN, reserved, SO PV, DE short PV
    (3, None): (4, 44),   # GAC: SN, reserved, SO PV, area (lat, long, a, b, angle, reserved)
    (4, None): (4, 44),   # GBC: same layout as GAC
    (5, 0): (0, 28),      # SHB: SO PV, media-dependent data
    (5, 1): (4, 28),      # TSB: SN, reserved, SO PV
}


def parse_common_and_upper(p):
    """GN common header, extended header (source position) and BTP header."""
    out = {}
    if len(p) < 8:
        return out, None
    nh = p[0] >> 4
    ht, hst = p[1] >> 4, p[1] & 0x0F
    out.update({"gn_ht": ht, "gn_hst": hst, "gn_nh": nh})
    layout = _EXT_LAYOUT.get((ht, hst if ht == 5 else None))
    if layout is None:
        return out, None  # location service etc.: no payload of interest
    so_offset, ext_size = layout
    ext = p[8:]
    if len(ext) >= so_offset + 24:
        out["gn_so"] = parse_lpv(ext[so_offset:so_offset + 24])
    upper = ext[ext_size:]
    if nh in (1, 2) and len(upper) >= 4:  # BTP-A / BTP-B
        dport = struct.unpack(">H", upper[:2])[0]
        out["btp"] = "A" if nh == 1 else "B"
        out["btp_port"] = dport
        out["message"] = BTP_PORTS.get(dport, f"port {dport}")
        return out, upper[4:]
    return out, None


# ---------------------------------------------------------------- facilities

def parse_its_header(b):
    """ItsPduHeader in UPER is byte aligned: protocolVersion(8) messageId(8) stationId(32)."""
    if len(b) < 6:
        return {}
    return {"its_protocol_version": b[0], "its_message_id": b[1], "station_id": struct.unpack(">I", b[2:6])[0]}


def decode_cam(codecs, b):
    m = codecs.cam.decode("CAM", b)
    params = m["cam"]["camParameters"]
    basic = params["basicContainer"]
    ref = basic["referencePosition"]
    out = {
        "cam_station_type": basic["stationType"],
        "cam_lat": ref["latitude"] / 1e7 if ref["latitude"] != 900000001 else None,
        "cam_lon": ref["longitude"] / 1e7 if ref["longitude"] != 1800000001 else None,
        "cam_generation_delta_time": m["cam"]["generationDeltaTime"],
    }
    kind, hf = params["highFrequencyContainer"]
    if kind == "basicVehicleContainerHighFrequency":
        speed = hf["speed"]["speedValue"]
        heading = hf["heading"]["headingValue"]
        out["cam_speed_mps"] = speed / 100.0 if speed != 16383 else None
        out["cam_heading_deg"] = heading / 10.0 if heading != 3601 else None
        out["cam_vehicle_length_m"] = hf["vehicleLength"]["vehicleLengthValue"] / 10.0
        out["cam_vehicle_width_m"] = hf["vehicleWidth"] / 10.0
    out["cam_container"] = kind
    if params.get("lowFrequencyContainer"):
        lf_kind, lf = params["lowFrequencyContainer"]
        out["cam_low_frequency"] = lf_kind
        if lf_kind == "basicVehicleContainerLowFrequency":
            out["cam_vehicle_role"] = lf.get("vehicleRole")
            out["cam_path_points"] = len(lf.get("pathHistory", []))
    return out


# ---------------------------------------------------------------- security: v3 (IEEE 1609.2)

def v3_cert_info(codecs, cert):
    enc = codecs.sec.encode("EtsiTs103097Certificate", cert)
    tbs = cert["toBeSigned"]
    issuer_kind, issuer = cert["issuer"]
    vp = tbs["validityPeriod"]
    dur_unit, dur_val = vp["duration"]
    info = {
        "digest": hashlib.sha256(enc).digest()[-8:].hex(),
        "id": tbs["id"][0] if tbs["id"][0] != "name" else f"name:{tbs['id'][1]}",
        "issuer": issuer.hex() if isinstance(issuer, bytes) else issuer_kind,
        "valid_from": iso(ITS_EPOCH + vp["start"]),
        "duration": f"{dur_val} {dur_unit}",
        "psids": [a["psid"] for a in tbs.get("appPermissions", [])],
        "key": tbs["verifyKeyIndicator"][0],
    }
    if "region" in tbs:
        info["region"] = tbs["region"][0]
    return info


def parse_v3(codecs, b):
    d = codecs.sec.decode("Ieee1609Dot2Data", b)
    kind, content = d["content"]
    sec = {"security": "v3 (IEEE 1609.2)", "content": kind}
    if kind == "unsecuredData":
        return sec, content
    if kind != "signedData":
        return sec, None  # encrypted: nothing readable
    header = content["tbsData"]["headerInfo"]
    sec["psid"] = header["psid"]
    if "generationTime" in header:
        sec["generation_time"] = iso(ITS_EPOCH + header["generationTime"] / 1e6)
    if header.get("inlineP2pcdRequest"):
        sec["p2pcd_requests"] = [h.hex() for h in header["inlineP2pcdRequest"]]
    signer_kind, signer = content["signer"]
    sec["signer"] = signer_kind
    if signer_kind == "digest":
        sec["cert_digest"] = signer.hex()
    elif signer_kind == "certificate":
        info = v3_cert_info(codecs, signer[0])
        sec["cert_digest"] = info["digest"]
        sec["cert"] = info
    inner = content["tbsData"]["payload"].get("data")
    if inner and inner["content"][0] == "unsecuredData":
        return sec, inner["content"][1]
    return sec, None


# ---------------------------------------------------------------- security: v2 (TS 103 097 V1.2.1)

class V2Reader:
    def __init__(self, b, pos=0):
        self.b, self.pos = b, pos

    def u8(self):
        v = self.b[self.pos]
        self.pos += 1
        return v

    def take(self, n):
        if self.pos + n > len(self.b):
            raise ValueError("truncated")
        v = self.b[self.pos:self.pos + n]
        self.pos += n
        return v

    def length(self):
        """Variable length / IntX: leading one bits give the number of extra bytes."""
        first = self.u8()
        extra = 0
        while extra < 8 and first & (0x80 >> extra):
            extra += 1
        value = first & (0xFF >> (extra + 1)) if extra < 7 else 0
        for _ in range(extra):
            value = (value << 8) | self.u8()
        return value

    def ecc_point(self):
        kind = self.u8()
        x = self.take(32)
        y = self.take(32) if kind == 4 else None
        return kind, x, y


def v2_signer(r):
    kind = r.u8()
    if kind == 0:
        return {"signer": "self"}
    if kind == 1:
        return {"signer": "digest", "cert_digest": r.take(8).hex()}
    if kind == 2:
        return {"signer": "certificate", **v2_certificate(r)}
    if kind == 3:
        end_len = r.length()
        end = r.pos + end_len
        chain = []
        while r.pos < end:
            chain.append(v2_certificate(r))
        at = next((c for c in chain if c["cert"].get("subject_type") == "authorization_ticket"), chain[0] if chain else {})
        return {"signer": "certificate_chain", **at}
    if kind == 4:
        r.take(1)
        return {"signer": "digest_other_algorithm", "cert_digest": r.take(8).hex()}
    raise ValueError(f"unknown v2 signer type {kind}")


_V2_SUBJECT = {0: "enrollment_credential", 1: "authorization_ticket", 2: "authorization_authority",
               3: "enrollment_authority", 4: "root_ca", 5: "crl_signer"}


def v2_certificate(r):
    start = r.pos
    version = r.u8()
    issuer = v2_signer(r)
    subject_type = r.u8()
    name = r.take(r.length())
    attrs = V2Reader(r.take(r.length()))
    restrictions = V2Reader(r.take(r.length()))
    sig_start = r.pos
    alg = r.u8()
    if alg != 0:
        raise ValueError(f"unsupported v2 signature algorithm {alg}")
    _, rx, _ = r.ecc_point()
    s = r.take(32)
    # canonical form for hashing: signature R point as x coordinate only (V1.2.1 clause 4.2.12)
    canonical = r.b[start:sig_start] + bytes([alg, 0]) + rx + s
    cert = {"subject_type": _V2_SUBJECT.get(subject_type, subject_type), "version": version,
            "issuer": issuer.get("cert_digest", issuer["signer"])}
    if name:
        cert["subject_name"] = name.decode("ascii", "replace")
    try:
        aids = []
        while attrs.pos < len(attrs.b):
            t = attrs.u8()
            if t in (0, 1):  # verification_key / encryption_key: PublicKey
                key_alg = attrs.u8()
                if key_alg == 1:
                    attrs.u8()  # symmetric algorithm
                attrs.ecc_point()
            elif t == 2:
                cert["assurance_level"] = attrs.u8() >> 5
            elif t == 3:
                attrs.ecc_point()
            elif t == 32:
                end = attrs.length() + attrs.pos
                while attrs.pos < end:
                    aids.append(attrs.length())
            elif t == 33:
                end = attrs.length() + attrs.pos
                while attrs.pos < end:
                    aids.append(attrs.length())
                    attrs.take(attrs.length())
            else:
                break
        cert["psids"] = aids
        while restrictions.pos < len(restrictions.b):
            t = restrictions.u8()
            if t == 0:
                cert["valid_until"] = iso(ITS_EPOCH + struct.unpack(">I", restrictions.take(4))[0])
            elif t == 1:
                a, b_ = struct.unpack(">II", restrictions.take(8))
                cert["valid_from"], cert["valid_until"] = iso(ITS_EPOCH + a), iso(ITS_EPOCH + b_)
            elif t == 2:
                cert["valid_from"] = iso(ITS_EPOCH + struct.unpack(">I", restrictions.take(4))[0])
                restrictions.take(2)
            else:
                cert["region"] = True
                break
    except (ValueError, IndexError):
        pass  # keep what was parsed; the digest is what matters for tracking
    return {"cert_digest": hashlib.sha256(canonical).digest()[-8:].hex(), "cert": cert}


def parse_v2(b):
    r = V2Reader(b)
    if r.u8() != 2:
        raise ValueError("not a TS 103 097 V1.2.1 secured message")
    sec = {"security": "v2 (TS 103 097 V1.2.1)"}
    end = r.length() + r.pos
    while r.pos < end:
        t = r.u8()
        if t == 0:
            sec["generation_time"] = iso(ITS_EPOCH + struct.unpack(">Q", r.take(8))[0] / 1e6)
        elif t == 1:
            sec["generation_time"] = iso(ITS_EPOCH + struct.unpack(">Q", r.take(8))[0] / 1e6)
            r.take(1)
        elif t == 2:
            r.take(4)
        elif t == 3:
            r.take(10)
        elif t == 4:
            n = r.length()
            sec["p2pcd_requests"] = [h.hex() for h in (r.take(3) for _ in range(n // 3))]
        elif t == 5:
            sec["psid"] = r.length()
        elif t == 128:
            signer = v2_signer(r)
            sec.update({k: v for k, v in signer.items()})
        elif t == 129:
            r.take(13)
        elif t == 130:
            r.take(r.length())
        else:
            raise ValueError(f"unknown v2 header field {t}")
    payload_type = r.u8()
    data = r.take(r.length())
    sec["content"] = {0: "unsecured", 1: "signed", 2: "encrypted", 3: "signed_external",
                      4: "signed_and_encrypted"}.get(payload_type, payload_type)
    return sec, data if payload_type in (0, 1) else None


# ---------------------------------------------------------------- frame

def parse_frame(codecs, frame, t):
    obs = {"t": round(t, 3), "time": iso(t), "length": len(frame)}
    if len(frame) < 18:
        return None
    ethertype = struct.unpack(">H", frame[12:14])[0]
    if ethertype != ETH_P_GEONET:
        return None
    obs["mac_src"] = frame[6:12].hex(":")
    obs["mac_dst"] = frame[0:6].hex(":")
    gn = frame[14:]
    obs["gn_version"], basic_nh = gn[0] >> 4, gn[0] & 0x0F
    body = gn[4:]
    payload = None
    try:
        if basic_nh == 2:  # secured packet
            if body[0] == 3:
                sec, payload = parse_v3(codecs, body)
            elif body[0] == 2:
                sec, payload = parse_v2(body)
            else:
                sec = {"security": f"unknown (first byte {body[0]})"}
            obs.update(sec)
        elif basic_nh == 1:
            obs["security"] = "none"
            payload = body
    except Exception as e:  # malformed or unsupported: keep what we have
        obs["error"] = f"security header: {e}"
    if payload is not None:
        try:
            info, upper = parse_common_and_upper(payload)
            obs.update(info)
            if upper:
                obs.update(parse_its_header(upper))
                if obs.get("btp_port") == 2001:
                    try:
                        obs.update(decode_cam(codecs, upper))
                    except Exception as e:
                        obs["error"] = f"CAM decode: {e}"
        except Exception as e:
            obs["error"] = f"GN/BTP: {e}"
    return obs


# ---------------------------------------------------------------- tracking

def distance_m(lat1, lon1, lat2, lon2):
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def position_of(obs):
    if obs.get("cam_lat") is not None and obs.get("cam_lon") is not None:
        return obs["cam_lat"], obs["cam_lon"]
    so = obs.get("gn_so")
    if so and abs(so["lat"]) <= 90 and abs(so["lon"]) <= 180:
        return so["lat"], so["lon"]
    return None


class Tracker:
    """Links observations into tracks (union-find over identifiers) and records pseudonym changes."""

    def __init__(self, link_window, link_distance, emit):
        self.parent = {}
        self.tracks = {}  # root key -> track
        self.cert_track = {}  # cert digest -> track id (after linking)
        self.link_window, self.link_distance = link_window, link_distance
        self.emit = emit
        self.next_id = 1
        # continuity links wait for confirmation: the old identity must stay silent
        self.pending = []

    def find(self, k):
        self.parent.setdefault(k, k)
        while self.parent[k] != k:
            self.parent[k] = self.parent[self.parent[k]]
            k = self.parent[k]
        return k

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return ra
        ta, tb = self.tracks.get(ra), self.tracks.get(rb)
        # keep the older track as the survivor
        if ta and tb and tb["first_seen"] < ta["first_seen"]:
            ra, rb, ta, tb = rb, ra, tb, ta
        self.parent[rb] = ra
        if ta and tb:
            self.merge(ta, tb)
            del self.tracks[rb]
        elif tb:
            self.tracks[ra] = tb
            del self.tracks[rb]
        return ra

    @staticmethod
    def merge(ta, tb):
        ta["first_seen"] = min(ta["first_seen"], tb["first_seen"])
        ta["last_seen"] = max(ta["last_seen"], tb["last_seen"])
        ta["messages"] += tb["messages"]
        for k in ("macs", "gn_addrs", "station_ids", "station_types"):
            ta[k] = sorted(set(ta[k]) | set(tb[k]), key=str)
        for digest, p in tb["pseudonyms"].items():
            ta["pseudonyms"].setdefault(digest, p)
        ta["changes"] += tb["changes"]
        ta["trail"] = sorted(ta["trail"] + tb["trail"])[-30:]

    @staticmethod
    def keys_of(obs):
        keys = []
        if obs.get("mac_src"):
            keys.append(("mac", obs["mac_src"]))
        so = obs.get("gn_so")
        if so:
            keys.append(("gn", so["gn_addr"]))
        if obs.get("station_id") is not None:
            keys.append(("station", obs["station_id"]))
        if obs.get("cert_digest"):
            keys.append(("cert", obs["cert_digest"]))
        return keys

    @staticmethod
    def expected_interval(tr):
        gaps = sorted(tr["intervals"])
        return gaps[len(gaps) // 2] if gaps else 1.0

    def continuity_candidate(self, obs, exclude_root):
        """Track that went quiet just before, close to where this new pseudonym appeared."""
        pos = position_of(obs)
        if not pos:
            return None
        best = None
        for root, tr in self.tracks.items():
            if root == exclude_root or not tr["last_position"]:
                continue
            dt = obs["t"] - tr["last_seen"]
            # a live track sends again within its usual interval: only a gap is a candidate
            if not (0.8 * self.expected_interval(tr) <= dt <= self.link_window):
                continue
            d = distance_m(pos[0], pos[1], tr["last_position"][0], tr["last_position"][1])
            speed = tr.get("last_speed") or 0.0
            if d <= self.link_distance + speed * dt and (best is None or d < best[1]):
                best = (root, d, dt)
        return best

    def observe(self, obs):
        # confirm or drop pending continuity links first: a merge changes the track roots
        self.resolve_pending(obs["t"])
        keys = self.keys_of(obs)
        if not keys:
            return None
        digest = obs.get("cert_digest")
        known_cert = digest is not None and ("cert", digest) in self.parent
        roots_before = {self.find(k) for k in keys if k in self.parent}
        tracks_before = {r: self.tracks[r] for r in roots_before if r in self.tracks}
        shared = [k[0] for k in keys if k in self.parent and k[0] != "cert"]

        root = keys[0]
        for k in keys[1:]:
            root = self.union(root, k)
        root = self.find(root)

        new_identity = digest and not known_cert and not tracks_before
        cand = self.continuity_candidate(obs, root) if new_identity else None

        tr = self.tracks.get(root)
        if tr is None:
            tr = self.tracks[root] = {
                "track": f"V{self.next_id}", "first_seen": obs["t"], "last_seen": obs["t"], "messages": 0,
                "macs": [], "gn_addrs": [], "station_ids": [], "station_types": [], "pseudonyms": {},
                "current_pseudonym": None, "changes": 0, "last_position": None, "last_speed": None, "trail": [],
                "intervals": []}
            self.next_id += 1
            self.emit({"event": "new_track", "t": obs["t"], "time": obs["time"], "track": tr["track"],
                       "identifiers": {k: v for k, v in keys}})
        tr["messages"] += 1
        if tr["messages"] > 1:
            tr["intervals"] = (tr["intervals"] + [obs["t"] - tr["last_seen"]])[-10:]
        tr["last_seen"] = obs["t"]
        for kind, value in keys:
            field = {"mac": "macs", "gn": "gn_addrs", "station": "station_ids"}.get(kind)
            if field and value not in tr[field]:
                tr[field].append(value)
        st = obs.get("cam_station_type", (obs.get("gn_so") or {}).get("gn_station_type"))
        if st is not None and STATION_TYPES.get(st, st) not in tr["station_types"]:
            tr["station_types"].append(STATION_TYPES.get(st, st))
        pos = position_of(obs)
        if pos:
            tr["last_position"] = pos
            if not tr["trail"] or tr["trail"][-1][1:] != [round(pos[0], 7), round(pos[1], 7)]:
                tr["trail"] = (tr["trail"] + [[obs["t"], round(pos[0], 7), round(pos[1], 7)]])[-30:]
        speed = obs.get("cam_speed_mps", (obs.get("gn_so") or {}).get("speed_mps"))
        if speed is not None:
            tr["last_speed"] = abs(speed)

        if digest:
            p = tr["pseudonyms"].setdefault(digest, {"first_seen": obs["t"], "last_seen": obs["t"], "messages": 0})
            p["last_seen"] = obs["t"]
            p["messages"] += 1
            if obs.get("cert"):
                p["certificate"] = obs["cert"]
            previous = tr["current_pseudonym"]
            if previous and previous != digest and digest not in self.cert_track:
                tr["changes"] += 1
                evidence = [f"same {k}" for k in ("mac", "gn", "station") if k in shared]
                prev = tr["pseudonyms"].get(previous, {})
                self.emit({"event": "pseudonym_change_linked", "t": obs["t"], "time": obs["time"],
                           "track": tr["track"], "old": previous, "new": digest,
                           "old_last_seen_s_ago": round(obs["t"] - prev.get("last_seen", obs["t"]), 2),
                           "evidence": evidence or ["none"],
                           "identifiers": {"mac": obs.get("mac_src"), "station_id": obs.get("station_id"),
                                           "gn_addr": (obs.get("gn_so") or {}).get("gn_addr")}})
            tr["current_pseudonym"] = digest
            self.cert_track[digest] = tr["track"]
        if cand:
            cand_track = self.tracks[cand[0]]
            self.pending.append({"new": ("cert", digest), "old": next(iter(self.keys_for_track(cand[0]))),
                                 "t": obs["t"], "time": obs["time"], "distance_m": round(cand[1], 1),
                                 "gap_s": round(cand[2], 2), "old_track": cand_track["track"],
                                 "confirm_after": 1.5 * self.expected_interval(cand_track)})
        return tr["track"]

    def keys_for_track(self, root):
        return [k for k in self.parent if self.find(k) == root]

    def resolve_pending(self, now):
        """Confirm continuity links whose old identity stayed silent, drop the others."""
        still = []
        for p in self.pending:
            old_root, new_root = self.find(p["old"]), self.find(p["new"])
            old, new = self.tracks.get(old_root), self.tracks.get(new_root)
            if old is None or new is None or old_root == new_root:
                continue
            if old["last_seen"] > p["t"]:
                continue  # the old identity is still transmitting: a different vehicle
            if now - p["t"] < p["confirm_after"]:
                still.append(p)
                continue
            previous, current = old["current_pseudonym"], new["current_pseudonym"]
            survivor_root = self.union(p["old"], p["new"])
            tr = self.tracks[survivor_root]
            tr["current_pseudonym"] = current
            tr["changes"] += 1
            for digest in tr["pseudonyms"]:
                self.cert_track[digest] = tr["track"]
            self.emit({"event": "pseudonym_change_linked", "t": p["t"], "time": p["time"], "track": tr["track"],
                       "old": previous, "new": current, "old_last_seen_s_ago": p["gap_s"],
                       "evidence": [f"position continuity ({p['distance_m']} m, {p['gap_s']} s gap; "
                                    f"old identity silent since)"],
                       "merged_track": new["track"], "identifiers": {}})
        self.pending = still

    def finish(self, now):
        self.resolve_pending(now + 3600)

    def snapshot(self):
        out = []
        for tr in sorted(self.tracks.values(), key=lambda x: x["first_seen"]):
            out.append({
                "track": tr["track"], "first_seen": iso(tr["first_seen"]), "last_seen": iso(tr["last_seen"]),
                "messages": tr["messages"], "station_types": tr["station_types"], "station_ids": tr["station_ids"],
                "macs": tr["macs"], "gn_addrs": tr["gn_addrs"], "linked_pseudonym_changes": tr["changes"],
                "current_pseudonym": tr["current_pseudonym"],
                "pseudonyms": [{"digest": d, "first_seen": iso(p["first_seen"]), "last_seen": iso(p["last_seen"]),
                                "messages": p["messages"], **({"certificate": p["certificate"]} if "certificate" in p else {})}
                               for d, p in sorted(tr["pseudonyms"].items(), key=lambda x: x[1]["first_seen"])],
                "last_position": tr["last_position"], "last_speed_mps": tr["last_speed"],
                "trail": [[iso(t), lat, lon] for t, lat, lon in tr["trail"]],
            })
        return out


# ---------------------------------------------------------------- I/O

class Logs:
    def __init__(self, log_dir, verbose):
        os.makedirs(log_dir, exist_ok=True)
        self.dir = log_dir
        self.messages = open(os.path.join(log_dir, "messages.jsonl"), "a", buffering=1)
        self.events = open(os.path.join(log_dir, "events.jsonl"), "a", buffering=1)
        self.verbose = verbose

    def message(self, obs, track):
        obs = dict(obs, track=track)
        self.messages.write(json.dumps(obs, separators=(",", ":")) + "\n")
        if self.verbose:
            sec = obs.get("signer", "-")
            print(f"{obs['time']} {obs.get('mac_src')} {obs.get('message', '?'):6} station={obs.get('station_id')} "
                  f"cert={obs.get('cert_digest', '-')}({sec}) track={track}", flush=True)

    def event(self, ev):
        self.events.write(json.dumps(ev, separators=(",", ":")) + "\n")
        if ev["event"] == "pseudonym_change_linked":
            print(f"{ev['time']} LINKED pseudonym change on {ev['track']}: {ev['old']} -> {ev['new']} "
                  f"(evidence: {', '.join(ev['evidence'])})", flush=True)
        elif ev["event"] == "new_track":
            print(f"{ev['time']} new track {ev['track']}: {ev['identifiers']}", flush=True)

    def tracks(self, snapshot, counts):
        tmp = os.path.join(self.dir, "tracks.json.tmp")
        with open(tmp, "w") as f:
            json.dump({"written": iso(time.time()), "counts": counts, "tracks": snapshot}, f, indent=1)
        os.replace(tmp, os.path.join(self.dir, "tracks.json"))


def summary_line(tracker, counts):
    parts = [f"{tr['track']}: {tr['messages']} msgs, {len(tr['pseudonyms'])} pseudonym(s), "
             f"{tr['changes']} linked change(s)" for tr in sorted(tracker.tracks.values(), key=lambda x: x["first_seen"])]
    return f"[summary] {counts['frames']} frames, {counts['decoded']} decoded, {counts['errors']} errors; " + "; ".join(parts)


def read_pcap(path):
    data = open(path, "rb").read()
    magic = struct.unpack("<I", data[:4])[0]
    endian, scale = {0xA1B2C3D4: ("<", 1e-6), 0xD4C3B2A1: (">", 1e-6),
                     0xA1B23C4D: ("<", 1e-9), 0x4D3CB2A1: (">", 1e-9)}[magic]
    off = 24
    while off + 16 <= len(data):
        sec, frac, incl, _ = struct.unpack(endian + "IIII", data[off:off + 16])
        yield sec + frac * scale, data[off + 16:off + 16 + incl]
        off += 16 + incl


def live(iface):
    s = socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.htons(ETH_P_GEONET))
    s.bind((iface, 0))
    while True:
        frame = s.recv(65535)
        yield time.time(), frame


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--iface", default=os.environ.get("EAVESDROP_IFACE", "eth0"))
    ap.add_argument("--pcap", help="analyse a pcap file instead of capturing live")
    ap.add_argument("--log-dir", default=os.environ.get("EAVESDROP_LOG_DIR", "/logs"))
    ap.add_argument("--asn1-dir", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "asn1"))
    ap.add_argument("--summary-interval", type=float, default=float(os.environ.get("EAVESDROP_SUMMARY", "30")))
    ap.add_argument("--link-window", type=float, default=3.0,
                    help="max seconds between a track going quiet and a new pseudonym continuing it")
    ap.add_argument("--link-distance", type=float, default=50.0,
                    help="max metres (plus speed x gap) for position continuity linking")
    ap.add_argument("--verbose", action="store_true", default=os.environ.get("EAVESDROP_VERBOSE") == "1")
    args = ap.parse_args()

    codecs = Codecs(args.asn1_dir)
    logs = Logs(args.log_dir, args.verbose)
    tracker = Tracker(args.link_window, args.link_distance, logs.event)
    counts = {"frames": 0, "decoded": 0, "errors": 0}
    source = read_pcap(args.pcap) if args.pcap else live(args.iface)
    print(f"eavesdropper: {'pcap ' + args.pcap if args.pcap else 'live on ' + args.iface}, logs in {args.log_dir}", flush=True)
    last_snapshot = last_summary = time.time()
    try:
        for t, frame in source:
            obs = parse_frame(codecs, frame, t)
            if obs is None:
                continue
            counts["frames"] += 1
            counts["errors" if "error" in obs else "decoded"] += 1
            track = tracker.observe(obs)
            logs.message(obs, track)
            now = time.time()
            if now - last_snapshot >= 5:
                logs.tracks(tracker.snapshot(), counts)
                last_snapshot = now
            if now - last_summary >= args.summary_interval:
                print(summary_line(tracker, counts), flush=True)
                last_summary = now
    except KeyboardInterrupt:
        pass
    tracker.finish(time.time() if not args.pcap else (tracker.tracks and max(t["last_seen"] for t in tracker.tracks.values()) or 0))
    logs.tracks(tracker.snapshot(), counts)
    print(summary_line(tracker, counts), flush=True)


if __name__ == "__main__":
    sys.exit(main())
