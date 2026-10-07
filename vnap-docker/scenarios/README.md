# vnapctl scenarios

One TOML file per scenario; `vnapctl up <name>` starts it, `vnapctl scenarios` lists them.
Paths under `/vnap-certs/` refer to `certs_dir` (default: the repository's `vnap-certs/`;
a relative `certs_dir` is resolved from the scenario file), which is mounted read-only into
every station. Docker from snap can only mount directories under your home directory.

```toml
name = "example"                  # default: file name
description = "one line"
image = "vnap:latest"             # must exist locally
entrypoint = "native"             # "native": the image's patched entrypoint.sh
                                  # "stock": unpatched image, mounts ../r2-entrypoint.sh
certs_dir = "/home/demo/pki-test-v2"   # optional

[network]                         # the V2X link
name = "vanetzalan0"
subnet = "192.168.98.0/24"

[defaults]                        # inherited by every station
security = "certs-v3"             # none | dummy[-v2|-v3] | certs-v2 | certs-v3
aa_cert = "/vnap-certs/c-its-pki/aa.cert"
root_cert = "/vnap-certs/c-its-pki/root_ca.cert"
env = { }                         # extra container environment

[[stations]]
name = "rsu"
ip = "192.168.98.10"
station_id = 1
station_type = 15
mac = "6e:06:e0:03:00:01"
at_cert = "/vnap-certs/c-its-pki/at.cert"     # static AT ...
at_key = "/vnap-certs/c-its-pki/at.der"

[[stations]]
name = "obu"
# ...
# ... or a pseudonym pool (TOML inline tables must stay on one line):
pseudonyms = { cert = "/vnap-certs/c-its-pki/bke_at_{i}.cert", key = "/vnap-certs/c-its-pki/bke_at_{i}_sign.der", count = 8, min_interval_ms = 1000, id_change = "full", silent_min_ms = 0, silent_max_ms = 0 }
#   id_change: "full" (ETSI ID change: GN address, MAC, stationId change too) or "certificate"
#   silent_min_ms/silent_max_ms: random radio silence after each full ID change (0 = off)
#   first: index of the first file ({i} = first .. first+count-1, default 0), so stations can
#          split one numbered set, e.g. first = 0, count = 4 and first = 4, count = 4
# optional movement (needs [control]); the station starts at the first waypoint:
mobility = { route = [[40.0, -8.003], [40.0, -7.997]], speed_kmh = 50, start_s = 0, loop = true }
#   route: two or more [lat, lon] waypoints in degrees, driven at constant speed_kmh
#   start_s: seconds to wait at the first waypoint; loop: drive back to the first waypoint and
#   repeat (otherwise stop at the last one)
# ... or laps through a four-way crossing with random turns (instead of route and loop):
mobility = { crossing = [40.0, -8.0], arm_m = 150, start_arm = "east", speed_kmh = 36, start_s = 0 }
#   arms of arm_m metres end on a square ring road; each lap: random turn at the crossing, random
#   way along the ring, back in on the next arm (4 x arm_m per lap)

[pki]                             # optional: the run's own certificate authority (needs [control])
initial = 8                       # butterfly ATs each pseudonym station starts with
refill_at = 2                     # request a new batch at this many unused ATs (0: no refill, pool wraps)
batch = 8                         # ATs per batch
validity_hours = 24
# etsi_version = "v3"             # default: from the stations' security (certs-v3 / certs-v2)
# ip = "192.168.99.5"             # PKI service on the control network
# key_derivation = "station"      # stations derive their ATs' private keys (default); "pki": the PKI returns them
# With [pki], stations take no certificate paths: pseudonym stations use
#   pseudonyms = { initial = 8, refill_at = 2, batch = 8, min_interval_ms = 1000, ... }
# (all optional), other stations with security certs-* get a regular AT.

[control]                         # optional control channel (pseudonym events, positions)
network = "vnapctl0"
subnet = "192.168.99.0/24"
broker_ip = "192.168.99.2"
client_ip = "192.168.99.3"
mobility_ip = "192.168.99.4"      # mobility client, started when a station has mobility
mobility_rate_hz = 5              # position updates per second and vehicle
mobility_seed = 12345             # optional: repeat the random turns of a run (default: a new seed)
auth = { username_env = "CTL_USER", password_env = "CTL_PASS" }   # optional; names of env vars, never values

[[control.mix_zones]]              # optional, repeatable; needs stations with mobility and pseudonyms
name = "crossing"
center = [40.0, -8.0]             # [lat, lon]
radius_m = 40                     # pseudonym change event when a vehicle enters this circle
stations = [2, 3]                 # default: every station with mobility and pseudonyms

[control.client]
mode = "periodic"                 # periodic | random | once | manual
interval = 30                     # periodic; random uses min_interval/max_interval; once uses delay/index
stations = [2]                    # default: all stations with a pseudonym pool

[eavesdropper]                    # optional passive tracking attacker on the message network
ip = "192.168.98.99"              # default: host .99 of the network
summary_interval = 30             # seconds between console summaries
link_by = ["position", "timing"]  # evidence beyond shared identifiers (default both; [] = identifiers only)
link_window = 3.0                 # position-continuity linking: max silence before a new pseudonym
link_distance = 50.0              #   and max distance (m, plus speed x gap)
timing_window = 20.0              # timing-phase linking: max silence over which CAM timing is extended
timing_tolerance_ms = 25.0        #   and max difference between predicted and actual CAM time
verbose = false                   # log every frame to the console

[check]                           # used by "vnapctl check" while this scenario runs
defaults = true                   # include vnapctl's default expectations
expect = ["rsu.cam.rx_per_s>=0.8"]
```

Any value can be overridden at start: `vnapctl up c-its-pki --set defaults.security=certs-v2
--set certs_dir=/home/demo/pki-test-v2 --set stations.obu.root_cert=/vnap-certs/c-its-pki/tlm.cert`.
Overrides are stored in the container labels, so `vnapctl status` and `check` see them.
