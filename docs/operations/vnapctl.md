# vnapctl

`sim/vnapctl` starts, inspects and stops simulations described by scenario files. It needs
only `python3`, the Docker CLI and the `eclipse-mosquitto:2` image. All commands below run in
`${VNAP_HOME}/sim`.

## Walkthrough

**1. Choose a scenario.** Each file in `scenarios/` describes the stations, their certificates,
and optionally a control channel, movement, the run's own PKI and an eavesdropper.

```bash
cd ${VNAP_HOME}/sim
./vnapctl scenarios          # name, stations and features of every scenario
```

| Scenario | Use it to |
|---|---|
| `none` | check the link and CAM path without security |
| `c-its-pki` | run v3 security with the fixed C-ITS-PKI set (RSU regular AT, OBU butterfly AT) |
| `certify-fresh` | run v2 security with certificates from Vanetza's `certify` |
| `c-its-pki-pseudo`, `c-its-pki-pseudo-manual` | let the OBU change pseudonym on control-channel events |
| `c-its-pki-tracking`, `c-its-pki-tracking-silent` | add a passive eavesdropper (with silent periods) |
| `c-its-pki-traffic`, `c-its-pki-convoy` | moving vehicles; a convoy watched by an eavesdropper |
| `c-its-pki-mixzone`, `c-its-pki-mixzone-random` | four cars changing identity in an intersection mix zone (fixed or random turns) |
| `c-its-pki-refill`, `c-its-pki-refill-sweep`, `c-its-pki-mixzone-pki` | the run's own PKI with certificate refill |
| `c-its-pki-badroot`, `naive-v3` | negative controls: messages must be rejected |
| `stress-naive-v3` | stress test: 4 stations at 50 Hz |
| `templates/pki-refill`, `templates/mixzone-random` | the templates users of the service start |

**2. Start it.** `--dry-run` validates and prints the Docker commands. `up` returns when every
station exchanges messages.

```bash
./vnapctl up c-its-pki --dry-run
./vnapctl up c-its-pki
```

**3. Check it.** `status` shows the stations, their certificates and chain checks. `check`
observes for 15 s and gives a PASS/FAIL verdict.

```bash
./vnapctl status
./vnapctl check
```

**4. Watch it.** `./vnapctl events --duration 10s` merges received and sent messages with their
verification results, and the pseudonym, ID change, PKI and certificate-check log lines. Raw
frames and MQTT are covered in [monitoring](monitoring.md).

**5. Change pseudonyms.** In a pseudonym scenario the event client sends change events
periodically; you can also send one by hand.

```bash
./vnapctl down && ./vnapctl up c-its-pki-pseudo
docker exec pseudo-client change 2          # station 2: next pseudonym
./vnapctl events --duration 10s --kind pseudonym,control
```

The client's commands and the event format are in
[control channels](../reference/control-channels.md).

**6. Play the attacker.**

```bash
./vnapctl down && ./vnapctl up c-its-pki-mixzone-random
./vnapctl status                    # includes the eavesdropper's tracks and linked changes
docker logs -f eavesdropper
```

See [eavesdropper and scoring](../functional/eavesdropper-and-scoring.md).

**7. Run a negative control.** In `c-its-pki-badroot` the OBU trusts the wrong root and must
reject the RSU's CAMs. The scenario defines its own expectations, so `check` passes when the
rejection happens.

**8. Tear down.**

```bash
./vnapctl down          # containers, networks, and the run's PKI volume with its keys
```

## Commands

```bash
./vnapctl scenarios [--json]                         # list scenario files
./vnapctl validate my.toml [--json] [--as-user]      # field-level errors; --as-user applies the user policy
./vnapctl schema                                     # JSON Schema of the scenario format
./vnapctl up NAME|FILE [--set KEY=VALUE ...] [--dry-run] [--wait 30] [--instance N|auto] [--as-user]
./vnapctl status [--json]
./vnapctl events [--duration 10s] [--since 10m] [--kind K,...] [--station S] [--count N] [--json]
./vnapctl check [--duration 15s] [--expect EXPR ...] [--no-defaults] [--no-scenario] [--json]
./vnapctl down [--force] [--dry-run]
```

Every command finishes on its own (none waits for Ctrl-C). Text output by default; `--json`
for scripts and agents. `VNAPCTL_DEBUG=1` prints timings to stderr.

While a command works, it reports each step on stderr in lines starting with `vnapctl:` (e.g.
building a helper image on first use, starting the run's PKI, waiting for the stations). The
result stays on stdout. `-q`/`--quiet` and `--json` turn these messages off.

## `up` and `down`

- **Validation:** `up` reports every problem at once before starting anything:
  - unknown keys and invalid security modes;
  - duplicate station names, IPs, IDs or MACs;
  - addresses outside their subnet;
  - missing certificate files;
  - certificate combinations the entrypoint cannot use.
- **Overrides:** `--set` changes any value without editing the file, e.g.
  `--set control.client.interval=10 --set defaults.security=certs-v2`. Overrides are recorded
  in the container labels, so `status` and `check` see them.
- **Refusals:** `up` does not start when a simulation already runs on the network, a
  container name is in use, a network exists with another subnet, or less than 0.5 GB of disk
  is free.
- **Rollback:** if a step fails, `up` removes whatever it created.
- **Helper images:** `up` builds the images a scenario needs (control client, mobility client,
  eavesdropper, PKI) when their tag is missing ([build](../build.md#helper-images)).
- **Readiness:** `up` returns when all of these hold (default wait 30 s, `--wait`):
  - every station has published on its MQTT broker;
  - pseudonym stations have joined the control channel;
  - moving stations have received their first position.

  Health, such as the expected chain failures of a negative control, is reported by
  `status` and `check`.
- **Cleanup:** `down` removes the containers with their anonymous volumes, the networks and
  the run's PKI volume. Bind mounts such as `certs/` are not touched.
- **Ownership:** containers are labelled with the scenario file, overrides, owner, start time
  and run ID. The owner is the account running `vnapctl`. `down` refuses runs started by
  another user, and unlabelled ones, unless given `--force`.
- **Secrets:** broker credentials are named by environment variable (`control.auth`). They
  reach the containers through `docker -e NAME`, never through the command line or labels.

## Parallel instances

`--instance N` (before or after the subcommand) runs or inspects a copy on networks
`vanetzalan0-iN` / `vnapctl0-iN`, with subnets 10.N.98.0/24 / 10.N.99.0/24 and container names
`<name>-iN`. `status`, `events`, `check` and `down` take the same option.

- **`up --instance auto`** claims the lowest free N ≥ 1 by creating its simulation network.
  Docker refuses a duplicate name, so concurrent starts never share an instance.
- `up` prints the number it claimed; a failed start releases it.
- `VNAPCTL_INSTANCE` sets the default instance for every command.

## Events

Each event has a timestamp, a station and a kind:

| Kind | Source | Content |
|---|---|---|
| `rx` | `vanetza/out/<type>` | message type, sender, `secured`, `report`, size |
| `tx` | `vanetza/own/cam` | message type |
| `control` | `vnap/pseudonym/#` | change events and status answers |
| `pseudonym` | `[PSEUDONYM]` log lines | pool start, changes, rejections, control-channel connection |
| `idchange` | `[IDCHANGE]` log lines | GN address/MAC and `stationId` changes, ID-LOCK, silent periods |
| `pki` | `[PKI]` log lines | batch requests, installed batches, retries; issued batches |
| `chain` | `[V2-CHAIN]` / `[V3-CHAIN]` log lines | certificate checks: signature chain to the root CA, at startup and for refilled ATs |
| `error` | log lines | `Exit:`, failed assertions, crashes |

- **Live and history:** MQTT has no history, so live capture covers `--duration`; `--since`
  adds older events from the container logs only.
- **Start-up gap:** the first ~1 s of a live window is missed while the subscriber containers
  start.

## Checks

`check` collects events for `--duration`, computes metrics and evaluates expectations of the
form `metric OP number|metric` (`>=`, `<=`, `==`, `!=`, `>`, `<`).

```bash
./vnapctl check --duration 20s --expect 'obu.pseudonym.changed>=1' --expect 'rsu.cam.rx_per_s>=0.9'
./vnapctl check --no-defaults --expect 'obu.cam.success_rate==0'     # negative controls
```

- **Station metrics** (per message type):
  - `<station>.<type>.rx`, `.rx_from.<station>`, `.success`, `.failed`, `.failed.<reason>`,
    `.success_rate`, `.rx_per_s`;
  - `<station>.<type>.tx`;
  - `<station>.pseudonym.changed`, `.rejected`, `.control_connected`;
  - `<station>.chain.fail`, `<station>.errors.total`;
  - refill metrics: see [certificate refill](../functional/certificate-refill.md#status-events-and-checks).
- **Global metrics:** `control.event`, `control.status`, `control.changed`,
  `stations.running`, `stations.total`, `disk.free_gb`, `pki.*`, `eavesdropper.*`.
- **Metric list:** `check --json` returns every metric. A missing counter (e.g. no DENMs)
  counts as 0; a misspelled name fails and lists the metrics that exist.
- **Scenario expectations:** `check` also evaluates the running scenario's `[check] expect`
  list. With `[check] defaults = false`, as in the negative controls, the defaults are
  skipped. `--no-scenario` ignores the scenario.
- **Default expectations** (unless `--no-defaults`):
  - all stations running;
  - at least 2 GB free disk;
  - no failed chain checks and no crash lines;
  - every station receives CAMs from every other;
  - with security enabled, a CAM success rate of 1;
  - pseudonym stations connected to their control channel;
  - with refill, no starved changes and the run's PKI running.

**Exit codes** (all commands):

| Code | Meaning |
|---|---|
| 0 | ok / pass |
| 1 | check failed |
| 2 | usage error or invalid scenario |
| 3 | nothing running |
| 4 | `status` degraded, or `up` not ready |
| 5 | conflict: already running, name in use, or owned by someone else |

## Your own scenarios

Copy a file in `scenarios/` and edit it. The format, the assigned fields and the user policy
are in [scenario format](../reference/scenario-format.md):

```bash
./vnapctl validate my.toml --json            # {"valid": false, "errors": [{"path": "stations[obu1].ip", ...}]}
./vnapctl up my.toml --instance auto
```
