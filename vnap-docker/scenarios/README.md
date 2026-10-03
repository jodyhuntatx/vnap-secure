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
pseudonyms = { cert = "/vnap-certs/c-its-pki/bke_at_{i}.cert",   # ... or a pseudonym pool
               key = "/vnap-certs/c-its-pki/bke_at_{i}_sign.der", count = 8, min_interval_ms = 1000,
               id_change = "full" }      # "full" (ETSI ID change: GN address, MAC, stationId too) or "certificate"

[control]                         # optional pseudonym control channel
network = "vnapctl0"
subnet = "192.168.99.0/24"
broker_ip = "192.168.99.2"
client_ip = "192.168.99.3"
auth = { username_env = "CTL_USER", password_env = "CTL_PASS" }   # optional; names of env vars, never values

[control.client]
mode = "periodic"                 # periodic | random | once | manual
interval = 30                     # periodic; random uses min_interval/max_interval; once uses delay/index
stations = [2]                    # default: all stations with a pseudonym pool

[eavesdropper]                    # optional passive tracking attacker on the message network
ip = "192.168.98.99"              # default: host .99 of the network
summary_interval = 30             # seconds between console summaries
link_window = 3.0                 # position-continuity linking: max silence before a new pseudonym
link_distance = 50.0              #   and max distance (m, plus speed x gap)
verbose = false                   # log every frame to the console

[check]                           # used by "vnapctl check" while this scenario runs
defaults = true                   # include vnapctl's default expectations
expect = ["rsu.cam.rx_per_s>=0.8"]
```

Any value can be overridden at start: `vnapctl up c-its-pki --set defaults.security=certs-v2
--set certs_dir=/home/demo/pki-test-v2 --set stations.obu.root_cert=/vnap-certs/c-its-pki/tlm.cert`.
Overrides are stored in the container labels, so `vnapctl status` and `check` see them.
