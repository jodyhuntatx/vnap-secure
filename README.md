# VNAP-Secure

Build scripts, source patches, simulation harness and certificates for running
[Vanetza-NAP](https://github.com/nap-it/vanetza-nap) (`release2-main`) `socktap` with C-ITS
security enabled (ETSI TS 103 097: `certs-v2` for V1.2.1 certificates, `certs-v3` for
V1.3.1/IEEE 1609.2 certificates).

This is **not** the Vanetza-NAP source tree. It holds the patch set and tooling around it:

- The Vanetza-NAP source is cloned separately into `~/vanetza-nap`.
- The patches are also committed there on the `jodyhuntatx` branch, on top of
  `release2-main` `241438fd`.
- Certificates are generated with Vanetza's `certify` tool or with the separate
  **C-ITS-PKI** utility (`~/COIMBRA/C-ITS-PKI`, see its `docs/README.md`).

## Contents

| Path | Purpose |
|---|---|
| `docker-build.sh` | copy `vnap-patches/` (or `vnap-origs/`) into `~/vanetza-nap` and build `vnap:latest` |
| `src-diffs.sh` | side-by-side diff of every patch against the upstream original |
| `docker-compose.yml`, `docker.env`, `start-vnap.sh`, `stop-vnap.sh` | RSU/OBU simulation with docker-compose |
| `certify-shell.sh` | shell in a `vnap:latest` container with `vnap-certs/` mounted (for `certify`) |
| `gen-certify.sh` | reference script for generating the `certify` Root/AA/AT set (run inside `certify-shell.sh`) |
| `convert-sign-key.sh` | convert a PEM private key to the PKCS#8 DER format socktap loads |
| `0-vmware-mount-dir.sh` | mount the host's shared folders at `/mnt/hgfs` in the VM |
| `1-extend-docker-fs.sh` | grow the VM's root logical volume (Docker needs ~10 GB of build cache) |
| `exec-to-vm.sh`, `run-build.sh`, `sync-vnap-dist.sh` | host-side helpers: ssh into the VM, build remotely and load the image on the host, copy `~/vanetza-nap` back |
| `vnap-patches/` | patched Vanetza-NAP files (see [Patch set](#patch-set)) |
| `vnap-origs/` | the corresponding upstream `release2-main` files |
| `vnap-docker/` | plain-docker simulation harness, `vnapctl` (scenario files in `scenarios/`: up/down, status, events, checks), the passive `eavesdropper` and the `vnap-msgcheck` message checker |
| `vnap-certs/certify/` | Root/AA/AT generated with Vanetza's `certify` (TS 103 097 V1.2.1, `certs-v2`) |
| `vnap-certs/c-its-pki/` | Root/AA/EA/TLM/AT and butterfly ATs from C-ITS-PKI (currently the v3 set, `certs-v3`) |

In `vnap-patches/` and `vnap-origs/`, each file is named after its path in vanetza-nap
with `/` replaced by `__`, e.g. `tools__socktap__time_trigger.cpp`.

## Development setup

Vanetza-NAP has file names that differ only by case, so it cannot be built on the default
(case-insensitive) macOS file system. Development uses an Ubuntu 24.04 VM in VMware on a
MacBook:

- **In the VM:** `~/vanetza-nap` is cloned here and all Docker images are built here.
  This repo can be cloned in the VM too, or shared from the host: run
  `0-vmware-mount-dir.sh`, which mounts it at `/mnt/hgfs/...`, then symlink it to
  `~/COIMBRA/vnap-secure`.
- **On the host:**
  - `exec-to-vm.sh` opens an ssh session to the VM (`VM_IP` is hardcoded as
    `172.16.93.134`).
  - `run-build.sh` runs `docker-build.sh` in the VM, then `docker save`, `scp` and
    `docker load` the image on the host.
  - `sync-vnap-dist.sh` copies `~/vanetza-nap` back to the host.
- **Git on `/mnt/hgfs`** reports "dubious ownership", because the mount shows files as
  owned by another user. Use `git -c safe.directory='*' …`, or run
  `git config --global --add safe.directory <path>` once.
- **Snap-confined Docker** in the VM can only read files under the real `$HOME`, and not
  in dot-directories. That rules out volumes and build contexts on `/mnt/hgfs` (see
  `EXEC_DIR` below and `vnap-docker/msgcheck/build-msgcheck.sh`, which stages its files
  in `$HOME`).
- **Disk:** a full image build needs about 10 GB of Docker build cache. Grow the VM disk,
  then run `1-extend-docker-fs.sh` or `growpart` + `pvresize` + `lvextend -r`.

## Build

```bash
./docker-build.sh          # apply vnap-patches/ to ~/vanetza-nap, build vnap:latest
./docker-build.sh origs    # any argument: restore vnap-origs/ (upstream) instead, then build
./src-diffs.sh             # review all patches against upstream
```

`docker-build.sh` must run in the VM. The first build takes about 20 minutes; later
builds reuse the cached dependency layers. Checking out the vanetza-nap `jodyhuntatx`
branch gives the same sources as running `docker-build.sh` without arguments.

## Run the simulation

### Option 1: docker-compose

```bash
./start-vnap.sh                           # starts rsu + obu from vnap:latest, follows the RSU log
EXEC_DIR=~/vnap-run ./start-vnap.sh       # required when this repo is on /mnt/hgfs (snap docker)
./stop-vnap.sh                            # same EXEC_DIR as above
```

- **Working directory:** `start-vnap.sh` copies `vnap-certs/` and `docker-compose.yml` to
  `EXEC_DIR` (default: the current directory) and creates the `vanetzalan0` network
  (192.168.98.0/24).
- **Certificates:** `docker-compose.yml` runs both stations with the C-ITS-PKI v3 set: the
  RSU uses `at.cert`/`at.der`, the OBU uses butterfly AT `bke_at_0`.
- **Other certificates:** edit the `AT_*`, `AA_CERT`, `ROOT_CERT` and `VANETZA_SECURITY`
  variables (section below).

### Option 2: plain docker (`vnap-docker/`)

```bash
cd vnap-docker
NATIVE=1 ./run-r2-sim.sh c-its-pki vnap:latest   # scenario, image
./check-r2-cams.sh 20                             # per station: CAMs received and their security_report
./end-r2-sim.sh
```

`run-r2-sim.sh` scenarios:

| Scenario | Certificates |
|---|---|
| `none` | no security (baseline) |
| `certify` | `certs-v2`, `vnap-certs/certify` `ticket_vnap` for both stations (expired 2026-06-07) |
| `certify-fresh` | `certs-v2`, certify Root/AA with per-station ATs `ticket_vnap_{rsu,obu}_20260924` (valid until 2026-11-23) |
| `certify-fresh-badroot` | as above, but the OBU trusts an unrelated root (negative test) |
| `naive-v3` | `certs-v3` with self-generated certificates per station (negative test: must be rejected) |
| `c-its-pki` | `certs-v3` (or `PKI_SECURITY=certs-v2`) from `vnap-certs/c-its-pki`: RSU `at`, OBU `bke_at_0` |
| `c-its-pki-badroot` | as above, but the OBU trusts `tlm.cert` instead of the root (negative test) |
| `c-its-pki-pseudo` | as `c-its-pki`, but the OBU holds butterfly ATs `bke_at_0`–`bke_at_7` and changes pseudonym on events from the control channel (see [Pseudonym change events](#pseudonym-change-events)); needs `NATIVE=1` |

Options:

- `NATIVE=1`: use the image's own entrypoint (patched images), rather than mounting
  `r2-entrypoint.sh`, which is for the unpatched `vnap:r2-stock` image.
- `CERTS_DIR=<dir>`: use another certificate set. `<dir>` must contain `c-its-pki/` and be
  under `$HOME`.
- `PKI_SECURITY=certs-v2|certs-v3`: security mode for the `c-its-pki` scenarios.
- `PSEUDO_*`: pseudonym event client settings for `c-its-pki-pseudo` (see
  [Pseudonym change events](#pseudonym-change-events)).

### Option 3: `vnapctl` with scenario files

`vnap-docker/vnapctl up` starts a scenario described in a TOML file under
`vnap-docker/scenarios/`. The format is in `scenarios/README.md`; one file exists for each
`run-r2-sim.sh` scenario except the expired `certify`, plus `stress-naive-v3` and
`c-its-pki-tracking` (with the [eavesdropper](#eavesdropper-tracking-attacker)). `vnapctl down` stops it again.

```bash
cd vnap-docker
./vnapctl scenarios                                   # list scenario files
./vnapctl up c-its-pki                                # validate, start, wait until the stations exchange messages
./vnapctl check                                       # uses the scenario's [check] expectations
./vnapctl down                                        # stop, remove the networks
./vnapctl up c-its-pki-pseudo --set control.client.interval=10 --set defaults.security=certs-v2 \
    --set certs_dir=/home/demo/pki-test-v2            # overrides, recorded in the container labels
./vnapctl up c-its-pki --dry-run                      # validate and print the docker commands
./vnapctl --instance 1 up c-its-pki-badroot           # a second, independent simulation
```

- **Validation:** `up` reports every problem at once before starting anything:
  - unknown keys and invalid security modes;
  - duplicate station names, IPs, IDs or MACs;
  - addresses outside their subnet;
  - missing certificate files;
  - certificate combinations the entrypoint cannot use.
- **Refusals:** `up` does not start when a simulation is already running on the network,
  when a container name is in use, when a network exists with another subnet, or when
  less than 0.5 GB of disk is free.
- **Rollback:** if a step fails, `up` removes whatever it created.
- **Readiness:** `up` returns when every station has published on its MQTT broker and
  pseudonym stations have joined the control channel (default wait 30 s, `--wait`).
  Health, such as the expected chain failures of a negative control, is reported by
  `status` and `check`.
- **Ownership:** containers are labelled with the scenario file, overrides, user, start
  time and run ID. `down` refuses runs started by another user, and unlabelled runs,
  unless given `--force`.
- **Parallel instances:** `--instance N` runs a copy on networks `vanetzalan0-iN` /
  `vnapctl0-iN` with subnets `10.N.98.0/24` / `10.N.99.0/24` and container names `<name>-iN`.
  This lets a test run (for example by Claude) next to someone's simulation.
  `status`, `events`, `check` and `down` take the same option.
- **Secrets:** broker credentials are given as names of environment variables
  (`control.auth`). They reach the containers through `docker -e NAME`, never through the
  command line or labels.

### Container environment (`entrypoint.sh`)

| Variable | Meaning |
|---|---|
| `SECURITY=certs` | run socktap with `--certificate $AT_CERT --certificate-key $AT_KEY --certificate-chain $AA_CERT --trusted-certificate $ROOT_CERT` |
| `VANETZA_SECURITY` | `certs-v2` (default when `SECURITY=certs`) or `certs-v3`; must match the certificate format |
| unset `SECURITY` | socktap runs with `config.ini` (`security=none`), or with whatever `VANETZA_SECURITY` selects |
| `SECURITY=pseudonyms` | pool of ATs `PSEUDO_CERT_0`/`PSEUDO_KEY_0`, `PSEUDO_CERT_1`/`PSEUDO_KEY_1`, … (consecutive pairs from 0), plus `AA_CERT`/`ROOT_CERT`; starts with index 0 and changes only on control channel events; works with `certs-v2` and `certs-v3` |
| `PSEUDO_CONTROL_BROKER` | MQTT broker of the pseudonym control channel; without it the pseudonym never changes |
| `PSEUDO_CONTROL_PORT` | control broker port (1883) |
| `PSEUDO_CONTROL_TOPIC` | control topic prefix (default `vnap/pseudonym/<station id>`) |
| `PSEUDO_CONTROL_USERNAME` / `PSEUDO_CONTROL_PASSWORD` | control broker account (the password is read from the environment, not passed on the command line) |
| `PSEUDO_MIN_INTERVAL` | minimum milliseconds between two pseudonym changes (1000); earlier events are rejected |

Private keys must be PKCS#8 DER (or PEM for v3). Keys from `certify` and C-ITS-PKI already
are.

## Monitor messages between stations

The stations exchange messages over the `vanetzalan0` docker network: RSU 192.168.98.10,
OBU 192.168.98.20, in both run options. The container commands below use the
plain-docker names `rsu` and `obu`; with docker-compose, run `docker-compose exec rsu …`
from `EXEC_DIR` instead.

| What | Where to look | Shows |
|---|---|---|
| Received messages | MQTT `vanetza/out/<type>` on the receiving station's broker | decoded message as JSON, plus its verification result |
| Sent CAMs | MQTT `vanetza/own/cam` on the sending station's broker | the station's own CAMs as sent (`own_topic_out` in `config.ini`) |
| Frames on the link | `tcpdump` on `br0` inside a station | raw GeoNetworking frames (ethertype 0x8947), including the security header |
| Pseudonym changes | MQTT `vnap/pseudonym/#` on `pseudo-broker` (network `vnapctl0`) | change events and the stations' answers |

### vnapctl (prototype)

`vnap-docker/vnapctl` merges these sources into one tool. It needs only `python3`, the
docker CLI and the `eclipse-mosquitto:2` image.

- **Read-only:** `status`, `events` and `check` never start, stop or change anything
  (`up`/`down`: see [Option 3](#option-3-vnapctl-with-scenario-files)).
- **Bounded time:** every command finishes on its own; none waits for Ctrl-C.
- **Output:** text by default, `--json` for scripts and agents.
- **Discovery:** it finds stations on the `vanetzalan0` network, so docker-compose runs
  work too (`--lan` and `--ctl` select other networks).

```bash
cd vnap-docker
./vnapctl status          # stations, image, certificates, chain checks, pseudonym index, control channel, disk
./vnapctl events --duration 10s                      # merged, time-ordered stream
./vnapctl events --since 10m --kind pseudonym,chain  # history from container logs only
./vnapctl events --kind rx --station rsu --count 5 --json
./vnapctl check --duration 15s                       # default expectations -> PASS/FAIL
./vnapctl check --duration 20s --expect 'obu.pseudonym.changed>=1' --expect 'rsu.cam.rx_per_s>=0.9'
./vnapctl check --no-defaults --expect 'obu.cam.success_rate==0'  # negative controls (e.g. *-badroot)
```

**Events.** Each event has a timestamp, a station and a kind:

| Kind | Source | Content |
|---|---|---|
| `rx` | `vanetza/out/<type>` | message type, sender, `secured`, `report`, size |
| `tx` | `vanetza/own/cam` | message type |
| `control` | `vnap/pseudonym/#` | change events and status answers |
| `pseudonym` | `[PSEUDONYM]` log lines | pool start, changes, rejections, control-channel connection |
| `chain` | `[V2-CHAIN]` / `[V3-CHAIN]` log lines | startup chain checks |
| `error` | log lines | `Exit:`, failed assertions, crashes |

- **Live and history:** MQTT has no history, so live capture covers `--duration`, and
  `--since` adds older events from the container logs only.
- **Start-up gap:** the first ~1 s of a live window is missed while the subscriber
  containers start.

**Checks.** `check` collects events for `--duration`, computes metrics and evaluates
expectations of the form `metric OP number|metric` (`>=`, `<=`, `==`, `!=`, `>`, `<`).

- **Metrics per station and message type:**
  - `<station>.<type>.rx`, `.rx_from.<station>`, `.success`, `.failed`,
    `.failed.<reason>`, `.success_rate`, `.rx_per_s`;
  - `<station>.<type>.tx`;
  - `<station>.pseudonym.changed`, `.rejected`, `.control_connected`;
  - `<station>.chain.fail`, `<station>.errors.total`.
- **Global metrics:** `control.event`, `control.status`, `control.changed`,
  `stations.running`, `stations.total`, `disk.free_gb`.
- **Metric list:** `check --json` returns every metric.
- **Missing counters** (e.g. no DENMs) count as 0. A misspelled name fails and lists the
  metrics that do exist.
- **Scenario expectations:** when the simulation was started with `vnapctl up`, `check`
  also evaluates the scenario's `[check] expect` list. With `[check] defaults = false`, as
  in the negative controls, the defaults below are skipped (`--no-scenario` ignores the
  scenario).
- **Default expectations**, unless `--no-defaults`:
  - all stations running;
  - at least 2 GB free disk;
  - no failed chain checks and no crash lines;
  - every station receives CAMs from every other;
  - with security enabled, a CAM success rate of 1;
  - pseudonym stations connected to their control channel.

**Exit codes:** 0 ok/pass, 1 check failed, 2 usage error or invalid scenario, 3 nothing
running, 4 `status` degraded or `up` not ready, 5 conflict (already running, name in use,
owned by someone else).

- **Run labels:** `run-r2-sim.sh` labels its containers with the scenario, the user and the
  start time; `status` shows them, so it is clear who started a running simulation.
- **Debugging:** `VNAPCTL_DEBUG=1` prints timings to stderr.

### Application messages (MQTT)

Each station runs an embedded MQTT broker on port 1883. socktap publishes every message
it receives on `vanetza/out/<type>` (`cam`, `denm`, `cpm`, …):

```bash
# everything the RSU receives, with the topic (use .20 for the OBU)
docker run --rm --network vanetzalan0 eclipse-mosquitto:2 \
  mosquitto_sub -h 192.168.98.10 -t 'vanetza/out/#' -v

# one line per received CAM: sender, receiver, verification result, payload size
docker run --rm --network vanetzalan0 eclipse-mosquitto:2 \
  mosquitto_sub -h 192.168.98.10 -t vanetza/out/cam |
  jq -c '{from: .stationID, to: .receiverID, secured, report: .security_report.description, size: .packet_size}'
#  {"from":2,"to":1,"secured":true,"report":"Success","size":82}

# only messages that failed verification
docker run --rm --network vanetzalan0 eclipse-mosquitto:2 \
  mosquitto_sub -h 192.168.98.10 -t 'vanetza/out/#' |
  jq -c 'select(.security_report.description != "Success")'

# CAMs the OBU sends
docker run --rm --network vanetzalan0 eclipse-mosquitto:2 \
  mosquitto_sub -h 192.168.98.20 -t vanetza/own/cam
```

Useful JSON fields:
- `stationID` / `stationAddr`: the sender's station ID and MAC address.
- `receiverID` / `receiverType`: the receiving station.
- `secured`, `security_report.description`: the verification result, `Success` or a
  failure reason such as `Invalid_Certificate`.
- `packet_size`: size of the message payload in bytes.
- `fields`: the decoded message.

socktap always delivers received messages, even those that fail verification (non-strict
decapsulation). Judge a message by `security_report`, not by whether it arrived.
`vnap-docker/check-r2-cams.sh [seconds]` counts CAMs and tallies `security_report` per
station. `jq` is installed in the VM.

### Frames on the simulated link (tcpdump)

The image includes `tcpdump`. With `SUPPORT_MAC_BLOCKING=true` (set by `run-r2-sim.sh`)
the station's link interface is the bridge `br0`:

```bash
# live, GeoNetworking frames only (both directions, as seen by the RSU)
docker exec rsu tcpdump -i br0 -e -nn ether proto 0x8947

# frames sent by the OBU only
docker exec rsu tcpdump -i br0 -e -nn ether src 6e:06:e0:03:00:02 and ether proto 0x8947

# save a capture for Wireshark (Ctrl-C to stop); under /mnt/hgfs it is visible on the host
docker exec rsu tcpdump -i br0 -U -w - ether proto 0x8947 > /mnt/hgfs/COIMBRA/vnap.pcap
```

- **tcpdump output:** it decodes the GeoNetworking and BTP headers. It does not parse the
  secured header, so for signed packets the values printed after it are wrong (e.g.
  `Payload:`, `lat`/`lon`).
- **Wireshark:** its GeoNetworking, BTP, ITS (CAM/DENM) and IEEE 1609.2 dissectors decode
  the full frame, including the signer certificate. Wireshark is not installed in the VM;
  open the capture on the host.
- **Frame size:** with v3 and 1 Hz CAMs, every CAM carries the full signer certificate,
  so frame size does not reveal a pseudonym change. Use the control channel below or
  compare the certificate in Wireshark.

### Pseudonym control channel

```bash
# change events and answers (add -u USER -P PASSWORD if the broker requires them)
docker run --rm --network vnapctl0 eclipse-mosquitto:2 \
  mosquitto_sub -h pseudo-broker -t 'vnap/pseudonym/#' -v
#  vnap/pseudonym/2/change {"event_id":"1ac5c8f170da-4","reason":"manual"}
#  vnap/pseudonym/2/status {"event_id":"1ac5c8f170da-4","result":"changed","previous":6,"index":7,...}

docker logs -f obu 2>&1 | grep PSEUDONYM       # the OBU's side
docker logs -f pseudo-client                   # events sent by the client and the answers
```

## Pseudonym change events

A station with `SECURITY=pseudonyms` keeps its current pseudonym (AT and signing key) until
it receives a change event. Events travel on a control channel that is separate from the
V2X messages:

```
 vanetzalan0 (192.168.98.0/24): GeoNetworking/CAMs    vnapctl0 (192.168.99.0/24): control only
 ┌─────┐  CAMs  ┌─────────────────────────┐ eth1   ┌───────────────┐       ┌───────────────┐
 │ rsu │◀──────▶│ obu  socktap ─ Pseudonym│───────▶│ pseudo-broker │◀──────│ pseudo-client │
 └─────┘  eth0  │      Channel (own MQTT  │ .20    │ MQTT  .2      │       │ events  .3    │
                │      client)            │        └───────────────┘       └───────────────┘
                └─────────────────────────┘
```

- **Channel:** a dedicated MQTT connection from socktap (`tools/socktap/pseudonym_channel.cpp`)
  to its own broker on its own network. It is not the station's embedded broker, which
  carries `vanetza/in|out/*` application messages, and the RSU is not on that network.
- **Topics:** events on `<prefix>/change`, answers on `<prefix>/status`; prefix
  `vnap/pseudonym/<station id>` by default.
- **Event** (JSON, all members optional):
  `{"event_id": "c1-7", "index": 3, "reason": "periodic"}`. Without `index`, the next
  pseudonym of the pool is used (wrapping around).
- **Answer:** `{"event_id": "c1-7", "result": "changed", "previous": 2, "index": 3,
  "pool_size": 8, "certificate": "<HashedId8>"}`, or `"result": "rejected"` with an `error`.
- **Rejected:**
  - an empty payload or malformed JSON (`{}` is a valid event);
  - an index outside the pool, or the index already in use;
  - events closer together than `PSEUDO_MIN_INTERVAL`;
  - retained messages replayed by the broker when socktap (re)subscribes, so a stale event
    has no effect.
- **Thread safety:** the MQTT thread only queues the event. The change runs on socktap's
  `io_context` under the `TimeTrigger` mutex, which also guards CAM signing, so a change
  cannot fall between the certificate and private key lookups.
- **After a change:** the next signed message carries the full new certificate, so
  receivers learn it at once.

**Client container** (`vnap-docker/pseudo-ctl/`, image `vnap-pseudo-ctl`, built on demand by
`run-r2-sim.sh`). The same image runs the broker (`broker`) and the event client (`client`):

```bash
cd vnap-docker
NATIVE=1 ./run-r2-sim.sh c-its-pki-pseudo vnap:latest       # periodic events every 30 s
PSEUDO_MODE=random PSEUDO_RANDOM_MIN=5 PSEUDO_RANDOM_MAX=20 NATIVE=1 ./run-r2-sim.sh c-its-pki-pseudo vnap:latest
PSEUDO_MODE=manual NATIVE=1 ./run-r2-sim.sh c-its-pki-pseudo vnap:latest
docker exec pseudo-client change 2          # station 2: next pseudonym
docker exec pseudo-client change 2 5        # station 2: pool index 5
docker logs -f pseudo-client                # events sent and the stations' answers
docker logs obu 2>&1 | grep PSEUDONYM       # the OBU's side
```

| `run-r2-sim.sh` variable | Client setting |
|---|---|
| `PSEUDO_MODE` | `periodic` (default), `random`, `once` (one event after 5 s; `PSEUDO_INDEX` picks the index) or `manual` |
| `PSEUDO_INTERVAL` | seconds between events in `periodic` mode (30) |
| `PSEUDO_RANDOM_MIN` / `PSEUDO_RANDOM_MAX` | interval range in `random` mode (10 / 60 s) |
| `PSEUDO_COUNT` | stop after this many events (0 = no limit) |
| `PSEUDO_MIN_CHANGE_MS` | the OBU's `PSEUDO_MIN_INTERVAL` (1000) |
| `PSEUDO_CONTROL_USERNAME` / `PSEUDO_CONTROL_PASSWORD` | require this account on the broker; used by the OBU and the client |

**Security of the channel.** Whoever can publish on `<prefix>/change` controls when the
station changes pseudonym. That is useful to an attacker who wants to correlate a change
with a location, or to exhaust a small pool.
- The simulation limits access by network: only the OBU and the client are on `vnapctl0`.
  It also offers broker authentication.
- There is no TLS, and one account is shared by all participants.
- Use TLS and per-client ACLs before anything outside the simulation.
- socktap logs a warning when it connects without credentials.

## Eavesdropper (tracking attacker)

`vnap-docker/eavesdropper/` is a passive listener playing an untrusted party that wants to
track vehicles. It joins only the message network (`vanetzalan0`) with a raw socket
(`NET_RAW`) and sees nothing but the frames on the wire: no keys or trust store, no MQTT,
no control channel. It knows the public ETSI/IEEE formats.

**What it decodes from each frame**

| Layer | Standard | Extracted |
|---|---|---|
| Ethernet | | source MAC |
| GeoNetworking | EN 302 636-4-1 | GN address (station type, MAC-derived ID), source position, speed, heading |
| Security header v2 | ETSI TS 103 097 V1.2.1 (own parser) | signer, generation time, ITS-AID |
| Security header v3 | IEEE 1609.2 / TS 103 097 V1.3.1 (OER, `asn1tools`) | signer, generation time, PSID |
| Signer certificate | same | HashedId8 digest, computed exactly as Vanetza does: SHA-256 over the canonical encoding. Also issuer (AA), validity, permissions; full certificates are sent about once a second |
| BTP | EN 302 636-5-1 | port, message type |
| Facilities | TS 103 900 (CAM R2) | `stationId` of any message; full CAM: `referencePosition`, speed, heading, vehicle size, station type |

The ASN.1 modules come from Vanetza-NAP (`eavesdropper/asn1/`, with their source note).

**Tracking**
- **Shared identifiers:** messages are linked into tracks when they share a MAC, GN
  address, `stationId` or certificate digest.
- **Position continuity:** a message with a certificate never seen before, and no known
  identifier, can continue a track that went silent shortly before (`--link-window`,
  default 3 s) at the same place (`--link-distance`, default 50 m, plus speed × gap).
  - The link is confirmed only if the old identity stays silent for 1.5 message
    intervals, so two vehicles parked side by side are not merged.
- **Logging:** every linked pseudonym change is logged with its evidence (`same mac`,
  `same gn`, `same station`, or `position continuity`).

**Output**

| Where | Content |
|---|---|
| `docker logs -f eavesdropper` | new tracks, linked pseudonym changes, periodic summary |
| `/logs/messages.jsonl` | every decoded frame |
| `/logs/events.jsonl` | track and linkage events |
| `/logs/tracks.json` | per track: identifiers, pseudonyms (first/last seen, certificate fields), last position, recent trail. Rewritten every 5 s |

Copy the logs out with `docker cp eavesdropper:/logs ./eavesdropper-logs`.

**Running it**

```bash
cd vnap-docker
./vnapctl up c-its-pki-tracking        # c-its-pki-pseudo (changes every 10 s) + eavesdropper
./vnapctl status                       # includes the eavesdropper's tracks and linked changes
./vnapctl check --expect 'eavesdropper.linked_changes==0'   # a privacy goal (fails today)
./eavesdropper/run.sh [network] [name] # attach to any running simulation instead
python3 eavesdropper/eavesdropper.py --pcap capture.pcap --log-dir out/   # offline (needs asn1tools)
```

- **In scenarios:** an `[eavesdropper]` section in a scenario file adds it; see
  `scenarios/README.md`.
- **Metrics:** `vnapctl check` exposes `eavesdropper.frames`, `.decode_errors`, `.tracks`,
  `.pseudonyms` and `.linked_changes`.

**What it shows today**
- In c-its-pki-tracking it links every OBU pseudonym change through the unchanged MAC,
  GN address and `stationId` (pseudonyms rotate only the certificate and key; see
  [Pseudonym change events](#pseudonym-change-events)).
- In an offline test where the OBU's MAC, GN address and `stationId` were rewritten at
  every change, it still linked all of them by position continuity. Rotating identifiers
  is not enough while the vehicle keeps sending its exact position.

## Certificates

- **`vnap-certs/certify/`**: generated with Vanetza's `certify` tool (V1.2.1 format, `certs-v2`).
  - `gen-certify.sh` at the top level is the reference: `generate-key`, then
    `generate-root` (AIDs 36/37/141), `generate-aa`, `generate-ticket`. Run it inside
    `certify-shell.sh`.
  - `vnap-certs/gen-certify.sh` is an older variant without AID 141.
  - The original `ticket_vnap` AT expired on 2026-06-07. Use the `*_20260924` ATs, which
    are valid until 2026-11-23, or issue new ones with
    `certify generate-ticket --sign-key aa_vnap.key --sign-cert aa_vnap.cert …`.
- **`vnap-certs/c-its-pki/`**: generated by C-ITS-PKI `gen-vnap-certs.sh`. That script
  writes directly into this directory.
  - With no argument it produces the v3 format; with any argument, v2.
  - Contents: `root_ca`, `aa`, `ea`, `tlm`, the regular AT (`at.cert`/`at.der`) and eight
    butterfly ATs (`bke_at_<j>.cert` / `bke_at_<j>_sign.der`).
  - C-ITS-PKI's `docs/compliance.md` documents their conformance.
- **Keys:** `convert-sign-key.sh <key.pem>` converts a PEM key to PKCS#8 DER.

## Patch set

The patches are in `vnap-patches/` (upstream versions in `vnap-origs/`) and committed on
the vanetza-nap `jodyhuntatx` branch:

| Patch | Files | Why |
|---|---|---|
| Debian snapshot mirror | `Dockerfile` | bullseye reached end of life on 2026-08-31; `deb.debian.org` pool files return 404, so the image no longer built |
| `certify` + `SECURITY=certs` mode | `Dockerfile`, `entrypoint.sh` | ship the `certify` tool; start socktap with certificate, AA chain and trusted root from environment variables |
| Clock sync | `tools/socktap/time_trigger.{hpp,cpp}` | the runtime clock lagged wall time, so CAMs were rejected as `Invalid_Timestamp`; also fixes a concurrent `schedule()` assertion crash |
| PRNG mutex | `vanetza/security/backend_cryptopp.{hpp,cpp}` | the shared CryptoPP random pool was used from several threads (v3 verification, key-check asserts) |
| Optional Assurance_Level | `vanetza/security/v2/default_certificate_validator.cpp` | accept older v2 certificates without the attribute (TS 103 097 V1.2.1 requires it; current C-ITS-PKI and `certify` output include it) |
| v3 DER keys | `vanetza/security/v3/persistence.cpp` | load PKCS#8 DER keys; readable errors instead of `terminate … char const*` |
| v3 full-chain verification | `vanetza/security/v3/certificate_chain.{hpp,cpp}` (new), `straight_verify_service.{hpp,cpp}`, `vanetza/security/CMakeLists.txt`, `tools/socktap/security.cpp` | upstream v3 accepted any AT regardless of issuer; now AT → AA → trusted root is verified (IEEE 1609.2 signing input), and `--trusted-certificate` works for v3 |
| Startup diagnostics | `tools/socktap/security.cpp` | log `[V3-CHAIN]` / `[V2-CHAIN]` results for the configured AA and own AT(s) |
| Pseudonym pool | `vanetza/security/v{2,3}/pseudonym_certificate_provider.{hpp,cpp}` (new), `vanetza/security/CMakeLists.txt`, `tools/socktap/security.{hpp,cpp}`, `entrypoint.sh` | pre-provisioned pool of ATs (e.g. a butterfly batch); after each change the full new certificate is sent in the next message so receivers learn it immediately. Options `--pseudonym-certificate`, `--pseudonym-certificate-key` (repeatable, paired in order); logs `[PSEUDONYM]` |
| Sign header policy mutex | `vanetza/security/v{2,3}/sign_header_policy.{hpp,cpp}` | socktap verifies received packets on several threads while signing on another; unsynchronized access to the policy's P2P request trackers aborted socktap (`PeerRequestTracker` assertion) under load. Reproduce with the `stress-naive-v3` scenario |
| v3 certificate cache mutex | `vanetza/security/v3/certificate_cache.{hpp,cpp}` | the reception threads store certificates while other threads look them up; the unlocked hash tables raced (ThreadSanitizer: data races and a SEGV in lookup). Also keeps pointers instead of rehash-invalidated iterators in the short-digest index |
| v2 certificate cache mutex | `vanetza/security/v2/certificate_cache.{hpp,cpp}` | the reception threads insert and look up certificates concurrently, and lookups also modify the cache (expiry heap); ThreadSanitizer showed data races and a corrupted heap (boost assertion abort) |
| Runtime clock and ASN.1 descriptor races | `vanetza/common/manual_runtime.{hpp,cpp}`, `vanetza/asn1/asn1c_wrapper.hpp`, `vanetza/asn1/cam.hpp`, `tools/socktap/applications/cam_application.cpp` | reception threads read the runtime clocks while the main thread advances them (now atomic); `asn1c_wrapper_common::swap` wrote to the global type descriptors; `CompactR2DecodeGuard` swaps a global R2 CAM member descriptor, now under a process-wide recursive mutex that all CAM encode/decode/JSON paths take. ThreadSanitizer: 69 -> 38 reports, none of these left |
| Router thread synchronization | `tools/socktap/{router_context,time_trigger,dcc_passthrough,raw_socket_link}.{hpp,cpp}`, `pubsub.cpp`, `main.cpp` | each reception router was used by its reception thread, by timers and position updates on the main thread, and by the PubSub transmission thread with the same index. Now each router is guarded by its trigger's lock (main-thread timers only try-lock), worker threads wait for `RouterContext::start()`, the thread-to-trigger map is locked and the link-layer callback is published safely. ThreadSanitizer: 38 -> 19 reports, none in routing |
| PubSub, MQTT and DDS synchronization | `tools/socktap/{pubsub,dds,mqtt}.cpp`, `mqtt.hpp` | MQTT subscriptions were added on the main thread while the mosquitto loop thread iterated them; callback threads inserted into the topic priority map; several threads used one UDP output socket; DDS `operator[]` lookups inserted (and dereferenced) null publishers. Maps are now locked, lookups use `find()`, UDP sends are serialized; also fixes reading MQTT payloads as NUL-terminated strings. ThreadSanitizer: 19 -> 17 reports, none in socktap's PubSub/MQTT code |
| Event-driven pseudonym change | `vanetza/security/pseudonym_control.hpp` (new), `tools/socktap/pseudonym_channel.{hpp,cpp}` (new), `tools/socktap/{main.cpp,CMakeLists.txt}`, `tools/socktap/time_trigger.{hpp,cpp}` (`post()`), `entrypoint.sh` | the pseudonym changes only on events from a separate MQTT control channel, not on a timer ([Pseudonym change events](#pseudonym-change-events)). Options `--pseudonym-control-broker`, `-port`, `-topic`, `-username`, `-password` and `--pseudonym-min-interval` |

## Validation and troubleshooting

- **Startup log** (`docker logs rsu`): `[V3-CHAIN]` / `[V2-CHAIN]` lines show whether the
  configured chain and own AT are valid. A wrong root shows up there.
- **Received messages** are not logged to stdout. socktap publishes them on MQTT;
  `security_report` holds the verification result. See
  [Monitor messages between stations](#monitor-messages-between-stations).
- **Message files** (signed or encrypted, e.g. from C-ITS-PKI) can be checked offline
  with Vanetza's own security code:

  ```bash
  vnap-docker/msgcheck/build-msgcheck.sh          # builds vnap:msgcheck
  docker run --rm -v DIR:/w vnap:msgcheck v3 /w/msg /w/at.cert /w/aa.cert /w/root_ca.cert
  docker run --rm -v DIR:/w vnap:msgcheck decode-v3 /w/msg.enc
  ```

- **Known upstream limitations:**
  - socktap always uses non-strict decapsulation, so messages that fail verification
    are still delivered with a failing `security_report`.
  - v2 puts `--certificate-chain` AAs into the cache without checking them against the
    trusted root.
  - The v2 verifier accepts only payload type `signed`.
  - ThreadSanitizer still reports races in the RSSI reader (`rssi_reader.cpp`, only with
    RSSI enabled) and inside the Zenoh library's own threads. The Zenoh reports involve
    uninstrumented Rust code whose synchronization ThreadSanitizer cannot see, so they are
    most likely false positives.
  - A pseudonym change replaces only the certificate and signing key. The MAC and
    GeoNetworking addresses stay the same, so consecutive pseudonyms remain linkable at
    lower layers.

## History

Before 2026-09, this repo held a patch set for Vanetza-NAP `main`:

- parser fixes;
- LRU-cache mutex;
- debug/metrics logging;
- pseudonym rotation (`pseudonym_certificate_provider`);
- a docker-compose-free harness and a kind/K8s attempt.

It was replaced by the release2 patch set above on 2026-09-28, and the pseudonym rotation
was ported to release2 the same day (for both v2 and v3). Its fixed countdown was then
replaced by change events on a separate control channel. The removed `vnap-origs/`,
`vnap-patches/` and `vnap-docker/` directories, including files that were never
committed, are archived in `~/vnap-secure-removed-dirs-20260928.tar.gz` in the
development VM. Their committed versions remain in git history, up to the commit that
introduced this README.
