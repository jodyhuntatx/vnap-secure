# vnapctl

`sim/vnapctl` starts, inspects and stops simulations described by scenario files. It needs
only `python3`, the Docker CLI and the `eclipse-mosquitto:2` image. All commands below run in
`${VNAP_HOME}/sim`.

## Walkthrough

**1. Choose a scenario.** Each file in `scenarios/` describes the stations, their certificates,
and optionally a control channel, movement, the run's own PKI and an eavesdropper. There are
five foundational scenarios, one per phenomenon. Each has named **variants**: sets of overrides
that switch one thing on or off, started as `<scenario>:<variant>`.

```bash
cd ${VNAP_HOME}/sim
./vnapctl scenarios          # name, stations, features and variants of every scenario
```

| Scenario | Phenomenon | Measures | Variants |
|---|---|---|---|
| `trust` | signed CAMs accepted only when the signer's certificate chains AT → AA → trusted root (committed test certificates) | `chain.fail`, `cam.success_rate` | `badroot` (negative control), `v2` |
| `linkability` | what a pseudonym change leaks when the vehicle is alone: identifiers, CAM timing, position | links by technique, tracks | `certificate-only`, `silent`, `silent-patient`, `identifiers-only`, `manual` |
| `crossing` | privacy from other vehicles: four cars change together in a mix zone and leave in different directions | correct and wrong links, tracks | `min2`, `min3`, `blocks`, `blocks-min3`, `periodic`, `no-silence` |
| `convoy` | cars that change together but never separate: order and spacing survive the change | correct and wrong links, followed changes | `periodic`, `min2`, `no-silence` |
| `refill` | certificate supply from the run's PKI: refill latency, queueing, starvation | `pki.refresh_ms_*`, `pki.queue_ms_max`, `pki.starved` | `slow`, `batch4`, `batch16`, `batch32`, `pki-derives`, `no-refill` |

The `controls/` folder holds pass/fail checks that measure nothing: `controls/none` (the link
and CAM path without security), `controls/naive-v3` (self-signed certificates: every CAM
rejected) and `controls/stress-naive-v3` (4 stations at 50 Hz, a regression test of the
thread-safety fixes). `templates/` links the four foundational scenarios users of the service
may start (all but `trust`, which uses certificate files). Where the scenarios before
2026-10-18 went is listed in [scenario names](#scenario-names-before-2026-10-18).

**2. Start it.** `--dry-run` validates and prints the Docker commands. `up` returns when every
station exchanges messages.

```bash
./vnapctl up trust --dry-run
./vnapctl up trust
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
./vnapctl down && ./vnapctl up linkability
docker exec pseudo-client change 2          # station 2: next pseudonym
./vnapctl events --duration 10s --kind pseudonym,control
```

The client's commands and the event format are in
[control channels](../reference/control-channels.md).

**6. Play the attacker.**

```bash
./vnapctl down && ./vnapctl up crossing
./vnapctl status                    # includes the eavesdropper's tracks and linked changes
docker logs -f eavesdropper
```

See [eavesdropper and scoring](../functional/eavesdropper-and-scoring.md).

**7. Run a negative control.** In `trust:badroot` the OBU trusts the wrong root and must
reject the RSU's CAMs. The variant sets its own expectations, so `check` passes when the
rejection happens.

```bash
./vnapctl down && ./vnapctl up trust:badroot && ./vnapctl check
```

**8. Tear down.**

```bash
./vnapctl down          # containers, networks, and the run's PKI volume with its keys
```

## Commands

```bash
./vnapctl scenarios [--json]                         # list scenario files
./vnapctl validate my.toml [--json] [--as-user]      # field-level errors; --as-user applies the user policy
./vnapctl schema                                     # JSON Schema of the scenario format
./vnapctl up NAME[:VARIANT]|FILE [--set KEY=VALUE ...] [--dry-run] [--wait 30] [--instance N|auto] [--as-user]
./vnapctl status [--json]
./vnapctl events [--duration 10s] [--since 10m] [--kind K,...] [--station S] [--count N] [--json]
./vnapctl check [--duration 15s] [--expect EXPR ...] [--no-defaults] [--no-scenario] [--json]
./vnapctl down [--force] [--dry-run]
./vnapctl collect DIR [--json]                       # save the running simulation's results; before down
./vnapctl collect DIR --analyse                      # analyse saved results again (overhead.json, metrics.json)
./vnapctl overhead RESULTS_DIR [--json] [--write]    # privacy and cost of a stopped run's pseudonym changes
./vnapctl certs [status] [v2|v3] [--json]           # the committed test certificates' validity
./vnapctl certs regenerate v2|v3 [--lifetime KEY=N ...]   # a new set; see docs/certificates.md
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
  `--set control.client.interval=10 --set defaults.security=certs-v2`:
  - an entry of an array of tables by its name or its index from 0:
    `--set stations.obu1.color="#ff0000"`, `--set control.mix_zones.crossing.min_vehicles=3`;
  - a whole value, arrays included, as TOML: `--set control.mix_zones=[]`;
  - every pseudonym station at once through `defaults.pseudonyms`:
    `--set defaults.pseudonyms.silent_max_ms=0` (a station's own key wins).

  Overrides are recorded in the container labels, so `status` and `check` see them.
- **Variants:** `up crossing:min3` applies the overrides of the scenario's `[variants.min3]`
  first, then any `--set`. The run is named `crossing:min3`, and the variant is recorded in
  the labels.
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

## Saving a run's results

The service saves every run's results when it stops. For a command-line run, `collect` writes
the same files; run it before `down`, which removes the containers and their logs:

```bash
./vnapctl --instance 7 collect results/crossing-1
./vnapctl --instance 7 down
./vnapctl overhead results/crossing-1        # privacy and cost of the pseudonym changes
```

| File | Content |
|---|---|
| `<container>.log` | every container's log, with docker timestamps |
| `eavesdropper-logs.tar` | the eavesdropper's `messages.jsonl` (every frame), `events.jsonl`, `tracks.json` |
| `positions.jsonl` | the vehicles' true positions, 5 times a second (the mobility client's recorder) |
| `pki-issued.jsonl` | the run's PKI issue log (digests, never keys) |
| `status.json`, `score.json` | the state of the run at the end, and the eavesdropper's score |
| `run.json`, `scenario.toml` | run id, scenario, variant, overrides, who started it, the mobility seed |
| `overhead.json` | privacy and cost of every pseudonym change ([web UI](web-ui.md#privacy-and-cost-of-the-pseudonym-changes)) |
| `metrics.json` | the `check` metrics over the whole run, from the saved logs |

`metrics.json` has the station and global metrics of [checks](#checks) computed over the whole
run instead of a check window. It adds:
- `<station>.cam.tx`: CAMs as the reference receiver heard them;
- per vehicle, from `overhead.json`: `.gap_ms_max`, `.gap_ms_mean` (reception gaps around
  changes), `.stale_share`, `.delivery_ratio`, `.ipg_ms_p95`, `.ipg_ms_max` (inter-CAM gaps),
  `.pos_error_m_p95`, `.pseudonym.lifetime_s_mean`, `.pool_bytes`, `.followed_s_max`;
- per run: `privacy.tracking_success`, `.sync_ratio`, `.followed_s_mean`, `.followed_s_max`,
  `.followed_m_max`, `changes.total`, `changes.unexplained`, `changes.during_outage`,
  `msg.cam.bytes_mean`, `msg.cert_bytes`, `pos_error_m_p95`, `outages`, `outages.s`.

What a check records live is not in it: received messages and their verification
(`.rx`, `.success_rate`, …) and the control channel (`control.*`). The cost names are per
sending vehicle, as the reference receiver heard it.

The analyses read only the directory: `collect DIR --analyse` makes them again, for example
after a change to a definition, also on the service's `data/results/<run id>`. A failing
analysis writes `overhead-error.txt` or `metrics-error.txt` and leaves the raw files.

## Your own scenarios

Copy a file in `scenarios/` and edit it. The format, the assigned fields and the user policy
are in [scenario format](../reference/scenario-format.md):

```bash
./vnapctl validate my.toml --json            # {"valid": false, "errors": [{"path": "stations[obu1].ip", ...}]}
./vnapctl up my.toml --instance auto
```

## Scenario names before 2026-10-18

The catalogue was reduced to five foundational scenarios with variants. The earlier files are
in the git history; each maps to a scenario and variant as follows.

| Before | Now | Difference |
|---|---|---|
| `c-its-pki` | `trust` | none |
| `c-its-pki-badroot` | `trust:badroot` | none |
| `certify-fresh` | `trust:v2` | none |
| `c-its-pki-pseudo`, `c-its-pki-pseudo-manual` | `linkability`, `linkability:manual` | the OBU moves; certificates from the run's PKI |
| `c-its-pki-tracking`, `c-its-pki-tracking-silent` | `linkability`, `linkability:silent` | the OBU moves; certificates from the run's PKI |
| `c-its-pki-traffic` | `crossing:periodic` | four cars on the crossing instead of two on straight roads |
| `c-its-pki-mixzone`, `c-its-pki-mixzone-min3` | `crossing:blocks`, `crossing:blocks-min3` | certificates from the run's PKI |
| `c-its-pki-mixzone-random`, `c-its-pki-mixzone-pki`, `templates/mixzone-random` | `crossing` | `mixzone-random` used committed certificates |
| `c-its-pki-convoy`, `c-its-pki-convoy-dynamic` | `convoy:periodic`, `convoy` | certificates from the run's PKI, so no pool is reused |
| `c-its-pki-refill-sweep` | `refill` (`batch4` … `batch32`) | none |
| `c-its-pki-refill`, `templates/pki-refill` | `refill:slow` | four OBUs instead of two |
| `none`, `naive-v3`, `stress-naive-v3` | `controls/none`, `controls/naive-v3`, `controls/stress-naive-v3` | none |
