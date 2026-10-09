#!/usr/bin/env python3
"""Rewrite one station's MAC, GN address and CAM stationId at every pseudonym (certificate)
change in a capture, simulating a vehicle that rotates all identifiers with its pseudonym.
The eavesdropper does not verify signatures, so the edited frames are valid input for it.

  rotate_identities.py in.pcap out.pcap [station MAC, default 6e:06:e0:03:00:02]
  ../eavesdropper.py --pcap out.pcap --log-dir /tmp/rot
"""
import importlib.util
import os
import struct
import sys

here = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("eavesdropper", os.path.join(here, "..", "eavesdropper.py"))
e = importlib.util.module_from_spec(spec)
spec.loader.exec_module(e)

src, dst = sys.argv[1], sys.argv[2]
mac = bytes.fromhex((sys.argv[3] if len(sys.argv) > 3 else "6e:06:e0:03:00:02").replace(":", ""))
codecs = e.Codecs(os.path.join(here, "..", "asn1"))
data = open(src, "rb").read()
out = bytearray(data[:24])
off, epoch, current, station = 24, -1, None, None
while off + 16 <= len(data):
    hdr = data[off:off + 16]
    incl = struct.unpack("<I", hdr[8:12])[0]
    frame = bytearray(data[off + 16:off + 16 + incl])
    off += 16 + incl
    if bytes(frame[6:12]) == mac:
        obs = e.parse_frame(codecs, bytes(frame), 0)
        if station is None and obs.get("station_id") is not None:
            station = obs["station_id"]
        digest = obs.get("cert_digest")
        if digest and digest != current:
            current, epoch = digest, epoch + 1
        if epoch > 0:
            new_mac = bytes([0x02, 0, 0, 0, 0x10, epoch & 0xFF])
            frame[6:12] = new_mac
            pos = bytes(frame).find(mac, 14)  # GN address MID in the source position vector
            if pos > 0:
                frame[pos:pos + 6] = new_mac
            if station is not None and obs.get("station_id") == station:
                hdr_pos = bytes(frame).find(bytes([2, obs["its_message_id"]]) + struct.pack(">I", station))
                if hdr_pos > 0:
                    frame[hdr_pos + 2:hdr_pos + 6] = struct.pack(">I", 1000 + epoch)
    out += hdr + frame
open(dst, "wb").write(out)
print(f"{epoch + 1} pseudonym epoch(s) of {mac.hex(':')}; identities rotated in {max(epoch, 0)} of them")
