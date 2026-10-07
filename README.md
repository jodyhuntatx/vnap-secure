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
| `vnap-docker/` | plain-docker simulation harness, `vnapctl` (scenario files in `scenarios/`: up/down, status, events, checks), the pseudonym control channel (`pseudo-ctl/`), the per-run PKI service (`pki/`), the `mobility` client that moves vehicles, the passive `eavesdropper` and the `vnap-msgcheck` message checker |
| `vnap-certs/certify/` | Root/AA/AT generated with Vanetza's `certify` (TS 103 097 V1.2.1, `certs-v2`) |
| `vnap-certs/c-its-pki/` | Root/AA/EA/TLM/AT and butterfly ATs from C-ITS-PKI (currently the v3 set, `certs-v3`) |

In `vnap-patches/` and `vnap-origs/`, each file is named after its path in vanetza-nap
with `/` replaced by `__`, e.g. `tools__socktap__time_trigger.cpp`.

## Getting started

A walkthrough from a fresh VM to a running secured simulation, with pointers to the detailed
sections below. The general Vanetza-NAP documentation (configuration, MQTT/JSON message
formats, applications) is in `~/vanetza-nap/docs/` and at
<https://wiki.nap.av.it.pt/groups/nap/tutorials/vanetza-nap/>; it does not cover security.

**0. Prerequisites.** An Ubuntu VM with Docker, git and python3, and at least 10 GB of free
disk for the first build ([Development setup](#development-setup) explains why a VM).

```bash
git clone -b release2-main https://github.com/nap-it/vanetza-nap.git ~/vanetza-nap
git clone git@github.com:jodyhuntatx/vnap-secure.git ~/COIMBRA/vnap-secure
```

**1. Build the image.** This applies the patch set to `~/vanetza-nap` and builds
`vnap:latest` (about 20 minutes the first time). See [Build](#build).

```bash
cd ~/COIMBRA/vnap-secure
./docker-build.sh
docker run --rm --entrypoint /usr/local/bin/socktap vnap:latest --help | grep pseudonym-control
```

**2. Choose a scenario.** All commands from here on run in `vnap-docker/`. Each scenario
file (`scenarios/*.toml`) describes the stations, their certificates, an optional pseudonym
control channel and an optional eavesdropper.

```bash
cd vnap-docker
./vnapctl scenarios
```

| Scenario | Use it to |
|---|---|
| `none` | check the link and CAM path without security |
| `c-its-pki` | run v3 security with certificates from C-ITS-PKI (RSU regular AT, OBU butterfly AT) |
| `certify-fresh` | run v2 security with certificates from Vanetza's `certify` |
| `c-its-pki-pseudo` | let the OBU change pseudonym on control-channel events |
| `c-its-pki-tracking` | add a passive eavesdropper that tries to track the OBU |
| `c-its-pki-badroot`, `naive-v3` | negative controls: messages must be rejected |
| `stress-naive-v3` | stress test: 4 stations at 50 Hz |

**3. Start it.** `--dry-run` shows the docker commands first. `up` waits until every station
exchanges messages.

```bash
./vnapctl up c-its-pki --dry-run
./vnapctl up c-its-pki
```

**4. Check that it works.** `status` shows the stations, their certificates and the chain
checks. `check` observes for 15 s and gives a PASS/FAIL verdict: both stations exchange
CAMs, every CAM verifies, and the certificate chains are valid.

```bash
./vnapctl status
./vnapctl check
```

**5. Watch the traffic.**
- **Events:** `./vnapctl events --duration 10s` merges the received and sent messages,
  their verification results, and the pseudonym and certificate-chain log lines.
- **Raw frames and MQTT:** see
  [Monitor messages between stations](#monitor-messages-between-stations) for the decoded
  messages over MQTT and for tcpdump/Wireshark captures.

**6. Change pseudonyms.** Stop the run and start the pseudonym scenario. Its client sends a
change event every 30 s; you can also send one by hand.

```bash
./vnapctl down
./vnapctl up c-its-pki-pseudo
docker exec pseudo-client change 2          # station 2 (the OBU): next pseudonym
./vnapctl events --duration 10s --kind pseudonym,control
```

See [Pseudonym change events](#pseudonym-change-events) for the event format and modes.

**7. Play the attacker.** The tracking scenario adds an eavesdropper that sees only the raw
frames on the message network.

```bash
./vnapctl down
./vnapctl up c-its-pki-tracking
./vnapctl status                    # the eavesdropper's tracks and linked pseudonym changes
docker logs -f eavesdropper
```

See [Eavesdropper](#eavesdropper-tracking-attacker).

**8. Run a negative control.** In `c-its-pki-badroot` the OBU trusts the wrong root and must
reject the RSU's CAMs. The scenario defines its own expectations, so `check` passes when
the rejection happens.

```bash
./vnapctl down
./vnapctl up c-its-pki-badroot
./vnapctl check
```

**9. Tear down.** `down` removes the containers and networks. It refuses runs started by
another user unless given `--force`.

```bash
./vnapctl down
```

**Next steps**
- **Variations without editing files:** `--set`, e.g.
  `./vnapctl up c-its-pki --set image=vnap:r2-p10 --set defaults.security=certs-v2 --set certs_dir=/home/demo/pki-test-v2`.
- **A second, independent simulation:** `--instance N`, before or after the subcommand
  (`./vnapctl up c-its-pki --instance 1`, then `./vnapctl status --instance 1`). It does not
  touch the default run.
- **Your own scenario:** copy a file in `scenarios/` and edit it. The format is in
  `scenarios/README.md`.
- **New certificates:** generate them with C-ITS-PKI `gen-vnap-certs.sh`, which writes to
  `vnap-certs/c-its-pki/` (see [Certificates](#certificates)).
- **Older harnesses:** `run-r2-sim.sh` and docker-compose remain available; see
  [Run the simulation](#run-the-simulation).
- **When something fails:**
  - `./vnapctl status` shows the startup chain checks and errors;
  - `docker logs rsu` / `docker logs obu` show socktap's own output;
  - `VNAPCTL_DEBUG=1` prints `vnapctl`'s timings;
  - see [Validation and troubleshooting](#validation-and-troubleshooting).

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
  in dot-directories.
  - The Docker CLI cannot read build contexts or compose files on `/mnt/hgfs`. The
    scripts send build contexts on stdin instead (`tar … | docker build -`), and
    `vnap-docker/msgcheck/build-msgcheck.sh` stages its files in `$HOME`; see also
    `EXEC_DIR` below.
  - Bind mounts from `/mnt/hgfs` work: `vnapctl` mounts `vnap-certs/` from there.
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

- `NATIVE=1` (default): use the image's own patched entrypoint. `NATIVE=0` mounts
  `r2-entrypoint.sh` instead, for an unpatched release2-main image (build one with
  `./docker-build.sh origs`). The default image is `vnap:latest`.
- `CERTS_DIR=<dir>`: use another certificate set. `<dir>` must contain `c-its-pki/` and be
  under `$HOME`.
- `PKI_SECURITY=certs-v2|certs-v3`: security mode for the `c-its-pki` scenarios.
- `PSEUDO_*`: pseudonym event client settings for `c-its-pki-pseudo` (see
  [Pseudonym change events](#pseudonym-change-events)).

### Option 3: `vnapctl` with scenario files

`vnap-docker/vnapctl up` starts a scenario described in a TOML file under
`vnap-docker/scenarios/`. The format is in `scenarios/README.md`; one file exists for each
`run-r2-sim.sh` scenario except the expired `certify`, plus `stress-naive-v3`,
`c-its-pki-tracking` (with the [eavesdropper](#eavesdropper-tracking-attacker)) and
`c-its-pki-tracking-silent` (the same with a 3–13 s silent period after each ID change) and
`c-its-pki-traffic` (two [moving](#vehicle-movement) OBUs and an RSU), `c-its-pki-convoy`
(three OBUs driving together, watched by an eavesdropper with a 15 s link window) and
`c-its-pki-mixzone` (four OBUs changing identity in an intersection mix zone) and
`c-its-pki-mixzone-random` (the same with random turns), `c-its-pki-refill` (the run's own
PKI with certificate refill), `c-its-pki-refill-sweep` (four OBUs refilling together, for
batch-size sweeps) and `c-its-pki-mixzone-pki` (the random-turn mix zone with the run's own
PKI). `vnapctl down` stops it again.

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
- **Helper images:** `up` builds `vnap-pseudo-ctl`, `vnap-mobility` and `vnap-eavesdropper`
  from their directories when they are missing or their sources changed (label
  `vnap.context_hash`).
- **Readiness:** `up` returns when every station has published on its MQTT broker,
  pseudonym stations have joined the control channel and moving stations have received
  their first position (default wait 30 s, `--wait`).
  Health, such as the expected chain failures of a negative control, is reported by
  `status` and `check`.
- **Cleanup:** `down` also removes the containers' anonymous volumes (`docker rm -v`), as do
  `end-r2-sim.sh` and `stop-vnap.sh`; bind mounts such as `vnap-certs/` are not touched.
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
| `PSEUDO_ID_CHANGE` | `full` (default): a pseudonym change also changes the GN address, MAC and CAM `stationId` through the ETSI ID change notification; `certificate`: only the certificate |
| `PSEUDO_SILENT_MIN_MS` / `PSEUDO_SILENT_MAX_MS` | random radio silence after each full ID change, in ms (default 0 = off); see [Pseudonym change events](#pseudonym-change-events) |
| `PKI_REFILL_AT` | certificate refill: request a new batch from the run's PKI service when this many unused pseudonyms are left (unset or 0: fixed pool that wraps around). With refill no pseudonym is used twice; uses the pseudonym control broker; see [Certificate refill](#certificate-refill-per-run-pki) |
| `PKI_BATCH_SIZE` | certificates to request per batch (8) |
| `PKI_TOPIC` | refill topic prefix: requests on `<prefix>/request`, batches on `<prefix>/batch` (default `vnap/pki/<station id>`) |
| `POSITION_CONTROL_BROKER` | MQTT broker of the position control channel: the station's position follows updates on its position topic ([Vehicle movement](#vehicle-movement)); needs the static position provider (`VANETZA_USE_HARDCODED_GPS=true`, the default), not gpsd |
| `POSITION_CONTROL_PORT` | position broker port (1883) |
| `POSITION_CONTROL_TOPIC` | position topic (default `vnap/position/<station id>`) |
| `POSITION_CONTROL_USERNAME` / `POSITION_CONTROL_PASSWORD` | position broker account (the password is read from the environment) |
| `VANETZA_LATITUDE` / `VANETZA_LONGITUDE` | position at startup (config.ini default 40 / -8); with a position channel, until the first update |
| `VANETZA_BRIDGE_IP` | the station's V2X address; the entrypoint bridges the interface that carries it (default `eth0`). Needed when the container is on several networks, because docker does not guarantee which one becomes `eth0`; `vnapctl` and `run-r2-sim.sh` set it |

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
- **Other actions** (`"action"`, default `change`), mapping to the ETSI ID management
  services described below:
  - `{"action": "lock", "duration": 30}`: ID-LOCK; answers with a `lock_handle`.
  - `{"action": "unlock", "lock_handle": 1}`: ID-UNLOCK.
  - `{"action": "trigger"}`: IDCHANGE-TRIGGER (change to the next pseudonym).
- **Answer:** `{"event_id": "c1-7", "result": "changed", "previous": 2, "index": 3,
  "pool_size": 8, "certificate": "<HashedId8>"}`, or `"result": "rejected"` with an `error`.
- **Rejected:**
  - an empty payload or malformed JSON (`{}` is a valid event);
  - an index outside the pool, or the index already in use;
  - events closer together than `PSEUDO_MIN_INTERVAL`;
  - changes while an ID-LOCK is held (`"error": "ID locked (ID-LOCK)"`);
  - retained messages replayed by the broker when socktap (re)subscribes, so a stale event
    has no effect.
- **Thread safety:** the MQTT thread only queues the event. The change runs on socktap's
  `io_context` under the `TimeTrigger` mutex, which also guards CAM signing, so a change
  cannot fall between the certificate and private key lookups.
- **After a change:** the next signed message carries the full new certificate, so
  receivers learn it at once.

**ID change notification (ETSI TS 102 723-8 / -9).** With `PSEUDO_ID_CHANGE=full`, the
default, a pseudonym change is a synchronized change of all of the station's identifiers,
following the two-phase commit of clause 6.3:

1. **Subscribers:** the network and transport layer (GeoNetworking routers) and the
   facilities layer (CAM application) subscribe to the security entity's ID change service
   (`vanetza/security/id_change_service`).
2. **PREPARE** goes to all subscribers with the 8-octet id of the new authorization ticket
   (its HashedId8). The network layer locks every router, so nothing is sent with the old
   identifiers until the change completes.
3. **The security entity switches** to the new authorization ticket and key.
4. **COMMIT:**
   - every router gets a new GN address, whose MID is also the source MAC of its frames;
   - the CAM application uses a new `stationId`;
   - both are derived from the id with SHA-256 under separate labels, so they share no
     bytes with each other or with the certificate.

   If a subscriber refuses PREPARE, everyone prepared gets **ABORT** and nothing changes.

| Service | Clause | Here |
|---|---|---|
| IDCHANGE-SUBSCRIBE / -UNSUBSCRIBE | 5.2.5, 5.2.7 | `IdChangeService::subscribe()`; socktap's subscriptions unsubscribe on destruction |
| IDCHANGE-EVENT (PREPARE, COMMIT, ABORT, DEREG) | 5.2.6, 6.3.1 | hook functions; DEREG when the security entity is destroyed |
| IDCHANGE-TRIGGER | 5.2.8 | `trigger()`: the security entity changes to the next pseudonym; control action `trigger` |
| ID-LOCK / ID-UNLOCK | 5.2.9, 5.2.10 | `lock(seconds 0..255)` / `unlock(handle)`; changes are refused while locked; control actions `lock` / `unlock` |

- **Logging:** each change is logged as `[IDCHANGE] network: GN address MID / MAC <old> ->
  <new>` and `[IDCHANGE] facilities: stationId <old> -> <new>`, plus ID-LOCK and ID-UNLOCK
  lines. `vnapctl` uses them to keep naming a station whose IDs change (`status` shows its
  current identity, `events --kind idchange` the changes).
- **Silent period:** with `PSEUDO_SILENT_MIN_MS`/`PSEUDO_SILENT_MAX_MS` (scenario keys
  `pseudonyms.silent_min_ms`/`silent_max_ms`), the station sends nothing for a random time
  in that range after each full ID change. This is the silent period strategy of ETSI
  TR 103 415 clause 4.1.4; clause 4.2.1 reports the SAE J2735 values of 3 to 13 s.
  - **How:** the network layer starts it on COMMIT, and `DccPassthrough` drops all
    outgoing frames (CAMs, beacons, injected messages) until it ends. Reception continues.
  - **Logging:** `[IDCHANGE] silent period <n> ms` at the start, and `silent period over,
    <n> frame(s) suppressed` at the end.
- **CAM timing:** the CAM timer restarts at a random phase on every full ID change. It used
  to keep running through the change, so each car's position within the 1 s CAM cycle
  carried over to its new identity and linked the two: a timing-only linker matched 16 of
  16 changes in the random-turn mix zone. The first CAM after the change is skipped and the
  next one comes after a random delay of up to one interval (`[IDCHANGE] CAM timer: new
  phase, next CAM in <n> ms`); with the fix the same linker matched 1 of 13.
  - **The cost** (TR 103 415): while silent, the vehicle is missing from its neighbours'
    view, and it reappears suddenly afterwards.
- **Certificate-only changes:** `PSEUDO_ID_CHANGE=certificate` (scenario key
  `pseudonyms.id_change = "certificate"`) restores the old behaviour.
- **Not changed:** the configured station ID and MAC remain the station's identity on its
  local MQTT interface (e.g. `receiverID`). Messages injected on `vanetza/in/*` carry
  whatever `stationId` their payload has.

**Client container** (`vnap-docker/pseudo-ctl/`, image `vnap-pseudo-ctl`, built on demand by
`run-r2-sim.sh`). The same image runs the broker (`broker`) and the event client (`client`):

```bash
cd vnap-docker
NATIVE=1 ./run-r2-sim.sh c-its-pki-pseudo vnap:latest       # periodic events every 30 s
PSEUDO_MODE=random PSEUDO_RANDOM_MIN=5 PSEUDO_RANDOM_MAX=20 NATIVE=1 ./run-r2-sim.sh c-its-pki-pseudo vnap:latest
PSEUDO_MODE=manual NATIVE=1 ./run-r2-sim.sh c-its-pki-pseudo vnap:latest
docker exec pseudo-client change 2          # station 2: next pseudonym
docker exec pseudo-client change 2 5        # station 2: pool index 5
docker exec pseudo-client ctl lock 2 20     # station 2: ID-LOCK for 20 s (prints the handle in the answer)
docker exec pseudo-client ctl unlock 2 1    # ID-UNLOCK handle 1
docker exec pseudo-client ctl trigger 2     # IDCHANGE-TRIGGER
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

## Certificate refill (per-run PKI)

Instead of a fixed set of certificates that every run reuses (and that wraps around when used
up), each run can have its own certificate authority. Stations start with a batch of butterfly
authorization tickets (ATs) and request a new batch when only a few unused ones are left. The
design is in the product requirements document (`Specs/VNAP-Secure-Simulation-Service-PRD.pdf`,
sections 5.5 and 7).

**Status:** phases 1–5 are done. A scenario with a `[pki]` section gets its own PKI from
`vnapctl`; `c-its-pki-refill` is the example. By default the stations derive their ATs' private
keys themselves (butterfly key expansion, "option B"), so the PKI never holds them.

```bash
cd vnap-docker
./vnapctl up c-its-pki-refill        # run's CA, RSU AT, 8 butterfly ATs per OBU; refill at 2 unused
./vnapctl status                     # per OBU: unused ATs, batches, refresh time; the PKI's issue times
./vnapctl events --kind pki --since 2m
./vnapctl check --expect 'obu1.pki.refresh_ms_max<2000'
./vnapctl down                       # also deletes the run's PKI volume with all its keys
```

- **Scenario keys:**
  - `[pki]`: `initial` (8), `refill_at` (2), `batch` (8), `validity_hours` (24), `etsi_version`
    (from the stations' security), `ip` (host .5 of the control network), `key_derivation`
    (`station`, the default, or `pki`: the PKI makes and returns the private keys, as in phases
    1–4). It needs a `[control]` section.
  - Per station, `pseudonyms = { initial, refill_at, batch, ... }` overrides the defaults;
    `refill_at = 0` gives a fixed pool from the run's PKI that wraps around.
  - Stations with certificates but no `pseudonyms` get a regular AT.
  - Certificate paths (`at_cert`, `aa_cert`, `root_cert`, `pseudonyms.cert`) are not allowed;
    the fixed-file pools remain available in scenarios without `[pki]`.
- **What `up` does:** creates a volume for the run and provisions it, then starts the PKI
  container (`pki`, role `pki`) on the control network, and mounts into each station only
  `public/` and its own `stations/<name>/`, read-only. A failed start removes the volume again;
  `down` removes it with the run.
  - A station sees neither the CA keys nor the other stations' files.
  - With `key_derivation = "station"`, provisioning is a separate one-shot container. The
    serving PKI mounts only `private/` and `public/`, so it cannot read the vehicles'
    caterpillar keys or AT keys either. With `pki`, one container provisions and serves.
- **Status, events, checks:**
  - `status`: a refill line per station (unused ATs, batches, refresh mean and max, retries,
    starved changes, pending request) and a line for the run's PKI (provisioning time, batches,
    issue and queue times).
  - `events --kind pki`: requests, installed batches and retries from the stations, issued
    batches from the PKI.
  - `check` metrics: `<station>.pki.batches`, `.added`, `.unused`, `.retries`, `.refused`,
    `.starved`, `.pending`, `.refresh_ms_mean`, `.refresh_ms_max`, and `pki.running`,
    `pki.batches`, `pki.refused`, `pki.issue_ms_max`, `pki.queue_ms_max`. Default expectations
    add `<station>.pki.starved==0` and `pki.running==1`.

- **PKI service** (`vnap-docker/pki/`, image `vnap-pki`, built from this directory and
  C-ITS-PKI's `src/` by `vnapctl up` or `pki/build.sh`; `CITS_PKI_DIR` if C-ITS-PKI is not next
  to vnap-secure):
  - `provision`: the run's root CA, TLM, EA and AA; a regular AT for road-side units;
    enrolment (caterpillar keys) and an initial batch of butterfly ATs for pseudonym stations.
  - `serve`: answers batch requests on the control broker; each batch uses the station's next
    i-period. Logs issuance and queueing time per request.
  - Output:
    - `public/`: root, AA, ... certificates.
    - `stations/<name>/`: the station's own files. With station key derivation this includes
      the vehicle's caterpillar private key, expansion key and enrolment key.
    - `private/`: CA keys, and per station the caterpillar public key (or, with
      `key_derivation = "pki"`, the caterpillar keys), the expansion key, the enrolment
      certificate and the batch state. Never mounted into stations.
    - `private/issued.jsonl`: every issued AT with its HashedId8 (ground truth).
- **Key derivation on the station** (option B, `--pki-caterpillar-key`, `--pki-expansion-key`,
  `PKI_CATERPILLAR_KEY`, `PKI_EXPANSION_KEY`; `tools/socktap/bke.{hpp,cpp}`):
  - The PKI's expansion step uses only the vehicle's caterpillar public key A and expansion
    key k. For each AT (i-period i, index j) it computes the cocoon key A + f_k(i, j)·G,
    certifies cocoon key + r·G, and returns the certificates with the offsets r and indices j,
    with no keys.
  - The station computes each private key as a + f_k(i, j) + r mod n, with f_k the IEEE
    1609.2.1 expansion function as implemented in C-ITS-PKI. It checks every key against its
    certificate and rejects ATs whose key does not match.
  - The initial batch is derived once at provisioning, playing the vehicle; those keys are
    written only to the vehicle's directory.
  - The "batch installed" line adds the derivation time:
    `(PKI issue <ms> ms, key derivation <ms> ms)`; `status` and the metric
    `<station>.pki.derivation_ms_mean` report it.
- **Stations** (`PKI_REFILL_AT`, `PKI_BATCH_SIZE`, `PKI_TOPIC`; options `--pki-refill-at`,
  `--pki-batch-size`, `--pki-topic`, `--pki-retry`):
  - When a change leaves `PKI_REFILL_AT` or fewer unused ATs, the station publishes
    `vnap/pki/<station id>/request {"request_id", "unused", "count"}` on the pseudonym control
    broker and installs the answer from `vnap/pki/<station id>/batch` while running. Each AT is
    chain-checked first (`[V3-CHAIN] batch authorization ticket ...`).
  - At most one request is outstanding; unanswered requests are repeated after 10, 20, 40 ...
    seconds (up to 120).
  - With refill on, no AT is used twice. With none left, changes are refused
    (`[PSEUDONYM] change rejected: no unused pseudonym left`) until a batch arrives.
  - Logs: `[PKI] batch requested: request <id>, <u> unused, <n> wanted` and
    `[PKI] batch installed: request <id>, <n> certificate(s), refresh <ms> ms (PKI issue <ms> ms), <u> unused`.
    Refresh time runs from the first request for a batch (retries included) to installation.

Without `vnapctl` (e.g. another harness), the service can be provisioned and attached by hand:

```bash
# provision into a host directory and use it as the scenario's certs_dir
docker run --rm -v ~/vnap-pki-runs:/runs vnap-pki provision --dir /runs/run1 --config '{"etsi_version": "v3",
  "stations": [{"name": "rsu", "station_id": 1, "certificates": "regular"},
               {"name": "obu", "station_id": 2, "certificates": "bke", "initial": 8, "batch": 8}]}'
# scenario: certs_dir = "~/vnap-pki-runs/run1" (absolute), aa_cert /vnap-certs/public/aa.cert,
#   root_cert /vnap-certs/public/root_ca.cert, OBU pool /vnap-certs/stations/obu/bke_at_{i}.cert
#   with key bke_at_{i}_sign.der and count 8, env PKI_REFILL_AT = "2"
docker run -d --name pki --network vnapctl0 -v ~/vnap-pki-runs:/runs -e CONTROL_BROKER=pseudo-broker \
  vnap-pki serve --dir /runs/run1
```

**Tested** (vnap:r2-p18-test, two OBUs changing every 5 s, refill at 2, batches of 8):
- **Refill:** each OBU received batches as it ran low. Every new AT passed the chain check,
  and the RSU verified the CAMs signed with them.
- **No reuse:** each OBU used 17 distinct ATs, all from its own entries in `issued.jsonl`.
- **Refresh time:** 454–971 ms, of which the PKI issued a batch of 8 in 376–533 ms. Both OBUs
  asked at the same moment, so one waited up to 537 ms behind the other at the PKI.
- **PKI outage:** with the PKI stopped, requests were retried after 10 s and 20 s, and four
  changes were refused while the pool was empty. The first retry after the PKI came back was
  answered (refresh 31 170 ms, counted from the first unanswered request).
- **Fixed pools:** without `PKI_REFILL_AT` the regression scenarios pass; the pool's unit
  test confirms that fixed pools still wrap around.
- **ThreadSanitizer:** the pool's own test passes clean, and a sanitized OBU running two
  refills reported nothing in the new code (8 reports, all inside the Zenoh library).

## Vehicle movement

Upstream socktap has a fixed position (`config.ini` or `VANETZA_LATITUDE`/`VANETZA_LONGITUDE`)
or reads one from gpsd. With `--position-control-broker` (`POSITION_CONTROL_BROKER`) the
position becomes controllable at runtime:

- **Station side:** socktap subscribes to `vnap/position/<station id>` on the control broker,
  on its own MQTT connection. Each update replaces the position fix that feeds the CAM
  (reference position, heading, speed, acceleration, yaw rate) and the GeoNetworking
  position vector of every packet. Updates go to the configured station ID, so a station
  keeps its topic across [ID changes](#pseudonym-change-events).
- **Payload:** a JSON object `{"lat": 40.0001, "lon": -8.0, "speed": 13.9, "heading": 90}`.
  - `lat`/`lon` in degrees are required.
  - `speed` in m/s (0–163.82), `heading` in degrees clockwise from north, `alt` in m are optional.
  - Invalid updates are rejected and logged (`[MOBILITY] position update rejected: …`, the first 5).
- **Mobility client** (`vnap-docker/mobility/`, image `vnap-mobility`): drives vehicles along
  routes. Each vehicle waits at its first waypoint until `start_s`, then drives the polyline
  at constant speed, publishing at `rate_hz` (default 5) with the retain flag, so a station
  that reconnects gets its current position at once. With `loop` it drives back to the first
  waypoint and starts over; without, it stops at the last one. It logs every vehicle's
  position every 10 s (`docker logs mobility`).
- **Scenarios:** a station's `mobility` key gives its route; `vnapctl` then connects the
  station to the control network, starts it at the first waypoint and starts the mobility
  client. The scenario needs a `[control]` section. See `vnap-docker/scenarios/README.md`
  and `c-its-pki-traffic`.
- **Random turns:** instead of a `route`, `mobility = { crossing = [lat, lon], arm_m = 150,
  start_arm = "east", speed_kmh = 36 }` drives laps through a four-way crossing whose arms
  end on a ring road: a random turn at the crossing (no U-turns), a random way along the
  ring, and back in on the next arm. Every lap is 4 × `arm_m`, so vehicles keep their
  relative timing. The choices come from `control.mobility_seed` (vnapctl picks and records
  one if unset; `status` shows it), and each lap is logged (`docker logs mobility`).
- **Mix zones:** `[[control.mix_zones]]` (center, radius) makes the mobility client send a
  pseudonym change event to a vehicle each time it enters the zone, instead of on a clock
  (set `control.client.mode = "manual"`). The events carry the reason `mix-zone <name>` and
  show up in `vnapctl events`.
- **Access:** whoever can publish on a position topic moves that station. The topics share
  the control broker, and its account (`control.auth`), with the pseudonym channel; the
  mobility client and moving stations use the same credentials.

```toml
[[stations]]
name = "obu1"
# ...
mobility = { route = [[40.0, -8.003], [40.0, -7.997]], speed_kmh = 50, start_s = 0, loop = true }
```

Move a station by hand (any MQTT client on the control network):

```bash
docker run --rm --network vnapctl0 eclipse-mosquitto:2 mosquitto_pub -h pseudo-broker \
    -t vnap/position/2 -m '{"lat": 40.0005, "lon": -8.0, "speed": 10, "heading": 0}'
```

Watch the positions in the CAMs the RSU receives:

```bash
docker run --rm --network vanetzalan0 eclipse-mosquitto:2 mosquitto_sub -h 192.168.98.10 -t vanetza/out/cam \
  | jq -c '.fields.cam.camParameters | [.basicContainer.referencePosition.latitude,
           .basicContainer.referencePosition.longitude,
           .highFrequencyContainer.basicVehicleContainerHighFrequency.heading.headingValue,
           .highFrequencyContainer.basicVehicleContainerHighFrequency.speed.speedValue]'
```

**CAM kinematics fixes.** Upstream filled these fields from a static position only, and
several were wrong in units once the vehicle moves:
- **Heading:** copied degrees into a field in 0.1°.
- **Speed and heading confidence:** copied m/s and degrees into fields in 0.01 m/s and 0.1°.
  A confidence of 0.1 m/s became the invalid value 0, and the OBU could not encode its CAMs
  (`Can't determine size for unaligned PER encoding of type CAM because of SpeedConfidence`).
- **Longitudinal acceleration and yaw rate:** wrong scale. The yaw rate also had the wrong
  sign: ETSI counts it positive to the left, while the heading grows clockwise.

The JSON on `vanetza/out/cam` shows the decoded physical values (m/s, degrees).

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

**Linkage techniques**

The eavesdropper groups messages into tracks (one per vehicle, as far as it can tell) and
tries to carry a track across each pseudonym change. A new certificate digest is a new
pseudonym; it is linked to an existing track by the first technique below that applies.

| Technique | Evidence used | Option (scenario key) | Defeated by |
|---|---|---|---|
| **Shared identifiers** | the new pseudonym's frame carries a MAC, GN address or `stationId` already seen on the track | always on | full ID change: MAC, GN address and `stationId` change with the certificate (`PSEUDO_ID_CHANGE=full`, the default) |
| **Timing phase** | CAMs come at a regular interval. The track's last CAM generation times (security header, 1 ms resolution) give its interval (least-squares fit) and phase; extended over the gap, they predict when its next CAM would come. One silent track must predict the new pseudonym's first CAM within the tolerance, and no other within twice the tolerance | `--link-by timing`, `--timing-window` 20 s, `--timing-tolerance` 25 ms (`link_by`, `timing_window`, `timing_tolerance_ms`) | restarting the CAM timer at a random phase on every ID change (vnap:r2-p17 and later) |
| **Position continuity** | the new pseudonym appears where a track that went silent could have driven in the gap: within `--link-distance` plus last speed × gap, the nearest one | `--link-by position`, `--link-window` 3 s, `--link-distance` 50 m (`link_by`, `link_window`, `link_distance`) | a silent period longer than the attacker waits, with other vehicles nearby that could be the one that reappears (convoys, mix zones with random turns) |

- **Precedence:** shared identifiers link immediately. Timing beats position when it is
  unambiguous. Its log entry then also says whether position agreed (`position pointed to
  another track` otherwise).
- **Confirmation:** timing and position links are confirmed only if the old identity stays
  silent for 1.5 message intervals, so two vehicles side by side are not merged.
- **Greedy:** each new pseudonym is decided on its own, the moment it appears. A wrong link
  uses up the old track, so the car that really owned it can no longer be linked.
  - Example (vnap:r2-p16): timing could not separate two cars that happened to send in
    phase, so position linked one of them to a third car's track.
- **Chance timing matches:** once phases are random, a new pseudonym's first CAM sometimes
  lands within the tolerance of another car's prediction. In a 3-minute run with 4 cars
  that gave 1–2 timing links, about half of them wrong. An attacker sees the same
  coincidences.
- **Logging:** every linked change is logged with its evidence (`same mac`, `same gn`,
  `same station`, `timing phase (<n> ms off after <k> CAM interval(s), next <m> ms)`,
  `position continuity (<d> m, <s> s gap; ...)`). The summary line, `tracks.json` and
  `vnapctl status` count links per technique. `vnapctl check` exposes the counts as
  `eavesdropper.linked_by_identifier`, `.linked_by_timing` and `.linked_by_position`.
- **Not used (yet):**
  - heading and turn statistics;
  - multiple hypotheses or a joint assignment of all changes at a crossing;
  - vehicle attributes in CAMs (length, width, station type);
  - the timing of GeoNetworking beacons and other periodic messages;
  - RSSI or several receivers.
- **Re-analysis:** `eavesdropper.py --replay messages.jsonl --log-dir out/ --link-by position`
  reruns the linking on a saved run's frame log with other options (no `asn1tools`
  needed). Get the log with `docker cp eavesdropper:/logs - | tar -x`.

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
./vnapctl check --expect 'eavesdropper.linked_changes==0'   # a privacy goal
./eavesdropper/run.sh [network] [name] # attach to any running simulation instead
python3 eavesdropper/eavesdropper.py --pcap capture.pcap --log-dir out/   # offline (needs asn1tools)
```

- **In scenarios:** an `[eavesdropper]` section in a scenario file adds it; see
  `scenarios/README.md`.
- **Metrics:** `vnapctl check` exposes `eavesdropper.frames`, `.decode_errors`, `.tracks`,
  `.pseudonyms`, `.linked_changes` and, per technique, `.linked_by_identifier`,
  `.linked_by_timing` and `.linked_by_position`.

**Results:** what the eavesdropper achieved against each scenario, with recommendations
for further scenarios, is in [`TestSummaries/`](TestSummaries/README.md).

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
| `certify` + `SECURITY=certs` mode | `Dockerfile`, `entrypoint.sh` | ship the `certify` tool; start socktap with certificate, AA chain and trusted root from environment variables. The entrypoint also bridges the interface carrying `VANETZA_BRIDGE_IP` instead of assuming `eth0`, so stations on a second (control) network keep their V2X traffic on the right one |
| Clock sync | `tools/socktap/time_trigger.{hpp,cpp}` | the runtime clock lagged wall time, so CAMs were rejected as `Invalid_Timestamp`; also fixes a concurrent `schedule()` assertion crash |
| PRNG mutex | `vanetza/security/backend_cryptopp.{hpp,cpp}` | the shared CryptoPP random pool was used from several threads (v3 verification, key-check asserts) |
| Optional Assurance_Level | `vanetza/security/v2/default_certificate_validator.cpp` | accept older v2 certificates without the attribute (TS 103 097 V1.2.1 requires it; current C-ITS-PKI and `certify` output include it) |
| v3 DER keys | `vanetza/security/v3/persistence.cpp` | load PKCS#8 DER keys; readable errors instead of `terminate … char const*` |
| v3 full-chain verification | `vanetza/security/v3/certificate_chain.{hpp,cpp}` (new), `straight_verify_service.{hpp,cpp}`, `vanetza/security/CMakeLists.txt`, `tools/socktap/security.cpp` | upstream v3 accepted any AT regardless of issuer; now AT → AA → trusted root is verified (IEEE 1609.2 signing input), and `--trusted-certificate` works for v3 |
| Startup diagnostics | `tools/socktap/security.cpp` | log `[V3-CHAIN]` / `[V2-CHAIN]` results for the configured AA and own AT(s), one write per line (other threads log concurrently) |
| Pseudonym pool | `vanetza/security/v{2,3}/pseudonym_certificate_provider.{hpp,cpp}` (new), `vanetza/security/CMakeLists.txt`, `tools/socktap/security.{hpp,cpp}`, `entrypoint.sh` | pre-provisioned pool of ATs (e.g. a butterfly batch); after each change the full new certificate is sent in the next message so receivers learn it immediately. Options `--pseudonym-certificate`, `--pseudonym-certificate-key` (repeatable, paired in order); logs `[PSEUDONYM]` |
| Sign header policy mutex | `vanetza/security/v{2,3}/sign_header_policy.{hpp,cpp}` | socktap verifies received packets on several threads while signing on another; unsynchronized access to the policy's P2P request trackers aborted socktap (`PeerRequestTracker` assertion) under load. Reproduce with the `stress-naive-v3` scenario |
| v3 certificate cache mutex | `vanetza/security/v3/certificate_cache.{hpp,cpp}` | the reception threads store certificates while other threads look them up; the unlocked hash tables raced (ThreadSanitizer: data races and a SEGV in lookup). Also keeps pointers instead of rehash-invalidated iterators in the short-digest index |
| v2 certificate cache mutex | `vanetza/security/v2/certificate_cache.{hpp,cpp}` | the reception threads insert and look up certificates concurrently, and lookups also modify the cache (expiry heap); ThreadSanitizer showed data races and a corrupted heap (boost assertion abort) |
| Runtime clock and ASN.1 descriptor races | `vanetza/common/manual_runtime.{hpp,cpp}`, `vanetza/asn1/asn1c_wrapper.hpp`, `vanetza/asn1/cam.hpp`, `tools/socktap/applications/cam_application.cpp` | reception threads read the runtime clocks while the main thread advances them (now atomic); `asn1c_wrapper_common::swap` wrote to the global type descriptors; `CompactR2DecodeGuard` swaps a global R2 CAM member descriptor, now under a process-wide recursive mutex that all CAM encode/decode/JSON paths take. ThreadSanitizer: 69 -> 38 reports, none of these left |
| Router thread synchronization | `tools/socktap/{router_context,time_trigger,dcc_passthrough,raw_socket_link}.{hpp,cpp}`, `pubsub.cpp`, `main.cpp` | each reception router was used by its reception thread, by timers and position updates on the main thread, and by the PubSub transmission thread with the same index. Now each router is guarded by its trigger's lock (main-thread timers only try-lock), worker threads wait for `RouterContext::start()`, the thread-to-trigger map is locked (new triggers are created outside that lock, avoiding a lock-order inversion) and the link-layer callback is published safely. ThreadSanitizer: 38 -> 19 reports, none in routing |
| PubSub, MQTT and DDS synchronization | `tools/socktap/{pubsub,dds,mqtt}.cpp`, `mqtt.hpp` | MQTT subscriptions were added on the main thread while the mosquitto loop thread iterated them; callback threads inserted into the topic priority map; several threads used one UDP output socket; DDS `operator[]` lookups inserted (and dereferenced) null publishers. Maps are now locked, lookups use `find()`, UDP sends are serialized; also fixes reading MQTT payloads as NUL-terminated strings. ThreadSanitizer: 19 -> 17 reports, none in socktap's PubSub/MQTT code |
| RSSI reader synchronization | `tools/socktap/rssi_reader.cpp` | the RSSI thread (nl80211 polling) inserts into and expires the RSSI/MCS maps and writes the channel survey while the receive thread reads them for every packet; on a real radio this could crash socktap. One mutex now guards them, never held across netlink I/O. ThreadSanitizer: RSSI reports 2 -> 0 |
| ID change notification service | `vanetza/security/id_change_service.{hpp,cpp}` (new), `pseudonym_control.hpp`, `v{2,3}/pseudonym_certificate_provider.{hpp,cpp}`, `vanetza/security/CMakeLists.txt`, `tools/socktap/id_change.{hpp,cpp}` (new), `router_context.{hpp,cpp}`, `main.cpp`, `pseudonym_channel.cpp`, `applications/cam_application.cpp`, `CMakeLists.txt`, `entrypoint.sh` | ETSI TS 102 723-8/-9 ID change notification: subscribe, two-phase commit (PREPARE/COMMIT/ABORT/DEREG), trigger, ID-LOCK/UNLOCK. A pseudonym change also changes the GN address, MAC and CAM `stationId` (`--pseudonym-id-change full`, default; `certificate` for the old behaviour). Optional random silent period after each change (`--pseudonym-silent-min/-max`, TR 103 415 4.1.4; `tools/socktap/dcc_passthrough.{hpp,cpp}`) |
| Position control channel (mobility) | `tools/socktap/mobility.{hpp,cpp}` (new), `positioning.cpp`, `main.cpp`, `CMakeLists.txt`, `entrypoint.sh` | the station's position follows updates on an MQTT topic ([Vehicle movement](#vehicle-movement)). Options `--position-control-broker`, `-port`, `-topic`, `-username`, `-password`. The static position provider is replaced by a thread-safe controllable one |
| Certificate refill | `tools/socktap/bke.{hpp,cpp}` (new, station key derivation), `vanetza/security/pseudonym_pool.hpp` (new), `pseudonym_control.hpp`, `v{2,3}/pseudonym_certificate_provider.{hpp,cpp}`, `tools/socktap/pki_channel.{hpp,cpp}` (new), `security.{hpp,cpp}`, `main.cpp`, `CMakeLists.txt`, `entrypoint.sh` | the pseudonym pool grows at runtime: new ATs from the run's PKI service, requested at a threshold of unused ones, chain-checked before use, never reused ([Certificate refill](#certificate-refill-per-run-pki)) |
| CAM timer rephase | `tools/socktap/applications/cam_application.{hpp,cpp}`, `main.cpp` | with full ID change, the CAM timer restarts at a random phase on COMMIT, so CAM timing does not link old and new identities ([Pseudonym change events](#pseudonym-change-events)) |
| CAM kinematics | `tools/socktap/applications/cam_application.cpp` | heading, speed/heading confidence, longitudinal acceleration and yaw rate in the CAM's units (and yaw rate sign); see [Vehicle movement](#vehicle-movement) |
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
  - ThreadSanitizer still reports races inside the Zenoh library's own threads (14 in a
    3-minute c-its-pki run). They involve uninstrumented Rust code whose synchronization
    ThreadSanitizer cannot see, so they are most likely false positives. It reports none in
    socktap or Vanetza code.
  - A pseudonym change (with the default full ID change) changes the certificate, GN
    address, MAC and CAM `stationId` together, but not the vehicle's broadcast position;
    see [Eavesdropper](#eavesdropper-tracking-attacker).

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
