# Changelog

Features by date, station image tags, the mapping to the product requirements (PRD) phases,
and the history of this repository. The documentation in `docs/` describes only the current
behaviour; how it got there is recorded here.

## Station image tags

`vnap:latest` is the newest build. Tags mark images used for measurements, so results in
`results/` can name the image they were measured with.

| Tag | Built | Adds |
|---|---|---|
| `vnap:r2-p19` (= `latest`) | 2026-10-07 | butterfly key derivation on the station (PKI returns offsets, not keys) |
| `vnap:r2-p18` | 2026-10-07 | certificate refill: pseudonym pool growing at runtime from the run's PKI |
| `vnap:r2-p17` | 2026-10-04 | CAM timer restarts at a random phase on every full ID change |
| `vnap:r2-p16` | 2026-10-04 | position control channel and CAM kinematics fixes (vehicle movement) |
| earlier `r2-p*` | 2026-09-28 – 10-03 | release2 patch set, pseudonym pool and events, thread-safety patches, ID change notification, silent periods |

Results measured with images up to `r2-p16` carry the CAM timing caveat described in
`results/README.md`: the CAM timer then kept its phase across ID changes.

## 2026-10-09: OpenAPI description in the repository

`docs/reference/openapi.json`: the API's OpenAPI 3.1 description, as the service serves it at
`/api/openapi.json`. `make openapi` regenerates it (`python3 -m vnapapi.openapi`), and
`make test` checks that it is current.

## 2026-10-09: repository reorganization

Implements `Specs/VNAP-Secure-Repository-Reorganization-Plan.docx` (decisions D1–D9).

- **Layout:**
  - `vnap-patches/` and `vnap-origs/` became `patches/vanetza-nap/modified/` and `upstream/`;
  - `vnap-certs/` became `certs/`;
  - `vnap-docker/` became `sim/`, with the helper images in `sim/images/`;
  - the service moved from `vnap-docker/api/` to `service/`;
  - `TestSummaries/` became `results/`;
  - the top-level scripts moved to `scripts/{build,vm,certs}/`;
  - `r2-entrypoint.sh` became `sim/stock-entrypoint.sh`.
- **Removed:**
  - the docker-compose harness (`docker-compose.yml`, `docker.env`, `start-vnap.sh`, `stop-vnap.sh`);
  - the `run-r2-sim.sh` harness and its helpers (`end-r2-sim.sh`, `check-r2-cams.sh`,
    `rsu-log.sh`, `obu-log.sh`), all superseded by `vnapctl`;
  - the duplicate `vnap-certs/gen-certify.sh`;
  - the expired certify AT `ticket_vnap`.

  Everything removed remains at the tag `pre-reorg-2026-10`.
- **C-ITS-PKI** is a git submodule (`external/C-ITS-PKI`, pinned). `CITS_PKI_DIR` still
  overrides it, and a checkout next to vnap-secure is used for one transition release. The
  per-run PKI image, the PKI container and `run.json` record the C-ITS-PKI commit
  (`vnap.cits_pki`), and `vnapctl status` shows it.
- **vanetza-nap** stays outside the repository: `patches/vanetza-nap/BASE` records the upstream
  commit, and `make image` clones it when `VANETZA_NAP_DIR` (default `~/vanetza-nap`) is
  missing.
- **New:**
  - a `Makefile` (image, msgcheck, test, diffs, docs-check);
  - generated unified diffs of the patch set;
  - a documentation link checker;
  - `docs/` with installation, build, operations, reference, functional and patch documentation.
- **Paths** are no longer hard-coded in the scripts: `VM_IP`, `VM_USER`, `SSH_KEY`,
  `VANETZA_NAP_DIR`, `OUTPUT_DIR` and `VNAP_SIM_DIR` select them.

## 2026-10-08

- **Hardening (PRD phase 9, without the MCP endpoint):**
  - an optional TOTP second factor with recovery codes; the secrets are encrypted at rest;
  - backups of the database, results and user scenarios, with verify, restore and a daily
    timer.
- **Web UI (phase 8):**
  - the runs list, and new runs from templates, scenario text or a map-based builder;
  - a run view with overview, live map, events, control and results;
  - account and admin pages.

  Later fixes: plain-HTTP login detection, the map tile Referer, clearer certificate-check
  events, the scenario description on the overview, and the eavesdropper's "longest chain
  followed" score with explanations.
- **API service (phase 7):**
  - accounts, roles, sessions with CSRF protection, API tokens, lockout, audit log;
  - runs as jobs with per-role limits;
  - live events, control actions, checks and results.
- **Coimbra origin:** the example layouts moved from Vanetza-NAP's built-in position (40, −8)
  to the crossing of Rua Alexandre Herculano and Rua Venâncio Rodrigues, Coimbra; stations
  without a position default to it.

## 2026-10-07

- **`vnapsim` (phase 6):**
  - `vnapctl` became a launcher for the `vnapsim` package;
  - the JSON Schema of the scenario format and field-level validation errors;
  - service-assigned identities, the user policy, and atomic instance allocation
    (`--instance auto`);
  - content-addressed helper images.
- **Station key derivation (phase 5):** the station derives its butterfly ATs' private keys;
  the PKI holds none (`vnap:r2-p19`).
- **Refill measurements (phase 4):** batch-size sweep and mix zone with the run's own PKI
  (`results/pki-refill-test-2026-10-07.docx`).
- **Per-run PKI and certificate refill (phases 1–3):** the `vnap-pki` service and the
  growing pseudonym pool (`vnap:r2-p18`), driven by `vnapctl`.

## 2026-10-03 – 10-04

- Vehicle movement: the position control channel, the mobility client, traffic and convoy
  scenarios (`vnap:r2-p16`).
- Intersection mix zones, with fixed and random turns; test summaries in `results/`.
- The CAM timer rephase on ID change (`vnap:r2-p17`), and timing-phase linking in the
  eavesdropper.
- The ETSI ID change notification service (TS 102 723-8/-9): GN address, MAC and `stationId`
  change with the certificate; silent periods after ID changes.

## 2026-09-28 – 10-01

- The release2 patch set, replacing the earlier patch set for Vanetza-NAP `main`.
- Pseudonym pool and event-driven pseudonym changes on a separate control channel.
- `vnapctl`: status, events and checks, then up/down with TOML scenario files and parallel
  instances.
- Thread-safety patches, all found with ThreadSanitizer: sign header policy, v2/v3 certificate
  caches, runtime clock and ASN.1 descriptors, router threads, PubSub/MQTT/DDS, RSSI reader.
- The passive eavesdropper (tracking attacker).
- `vnap-msgcheck`: verify message files with Vanetza's own security code.

## Before 2026-09

Until 2026-09-28 this repository held a patch set for Vanetza-NAP `main`:
- parser fixes;
- an LRU-cache mutex;
- debug and metrics logging;
- pseudonym rotation (`pseudonym_certificate_provider`);
- a docker-compose-free harness;
- a kind/K8s attempt.

It was replaced by the release2 patch set on 2026-09-28. The pseudonym rotation was ported
to release2 the same day, and its fixed countdown was later replaced by change events on a
control channel. The committed versions of that work remain in git history (from the first
commit, 2026-05-21, to `6beadfb`). Files that were never committed are archived in
`~/vnap-secure-removed-dirs-20260928.tar.gz` in the development VM.

## PRD phases

`Specs/VNAP-Secure-Simulation-Service-PRD.pdf` plans the service in nine phases:

| Phase | Content | Done |
|---|---|---|
| 1–2 | PKI service mode; station refill (option A) | 2026-10-07 |
| 3 | `vnapctl`: certificate policy, per-run PKI | 2026-10-07 |
| 4 | Measurements | 2026-10-07 |
| 5 | Station key derivation (option B) | 2026-10-07 |
| 6 | `vnapctl` library | 2026-10-07 |
| 7 | API service | 2026-10-08 |
| 8 | Web UI | 2026-10-08 |
| 9 | Hardening: backups, TOTP | 2026-10-08; MCP endpoint not yet |

## Documentation moves

The README used to hold all documentation. Its sections are now here:

| Former README section | Now |
|---|---|
| Contents | [README](README.md#repository-layout) |
| Getting started | [README](README.md), [vnapctl](docs/operations/vnapctl.md) |
| Development setup | [installation](docs/installation.md) |
| Dependency on C-ITS-PKI | [architecture](docs/architecture.md#dependency-on-c-its-pki) |
| Build | [build](docs/build.md) |
| Run the simulation: options 1 and 2 | removed (harnesses retired) |
| Option 3: vnapctl with scenario files; vnapctl (prototype) | [vnapctl](docs/operations/vnapctl.md) |
| The vnapsim package, validation and the user policy | [scenario format](docs/reference/scenario-format.md), [architecture](docs/architecture.md) |
| Container environment | [container environment](docs/reference/container-environment.md) |
| Simulation service (API) | [service deployment](docs/operations/service-deployment.md), [API](docs/reference/api.md) |
| Web UI | [web UI](docs/operations/web-ui.md) |
| Hardening (phase 9) | [backup and restore](docs/operations/backup-restore.md), [service deployment](docs/operations/service-deployment.md#accounts-and-authentication) |
| Monitor messages, application messages (MQTT), tcpdump | [monitoring](docs/operations/monitoring.md) |
| Pseudonym control channel | [control channels](docs/reference/control-channels.md) |
| Pseudonym change events | [pseudonym change](docs/functional/pseudonym-change.md) |
| Certificate refill (per-run PKI) | [certificate refill](docs/functional/certificate-refill.md) |
| Vehicle movement | [vehicle movement](docs/functional/vehicle-movement.md) |
| Eavesdropper (tracking attacker) | [eavesdropper and scoring](docs/functional/eavesdropper-and-scoring.md) |
| Certificates | [certificates](docs/certificates.md) |
| Patch set | [patch documentation](docs/patches/README.md) |
| Validation and troubleshooting | [troubleshooting](docs/operations/troubleshooting.md) |
| History | this file |
