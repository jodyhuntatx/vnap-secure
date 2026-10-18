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

## 2026-10-18: regenerating the test certificates

`vnapctl certs` shows the validity of every certificate in the committed sets (`certs/c-its-pki`,
v3; `certs/certify`, v2), and `vnapctl certs regenerate v2|v3 --lifetime KEY=N` makes a new set:
v3 with C-ITS-PKI in the `vnap-pki` image (`sim/images/pki/test_certs.py`), v2 with Vanetza's
`certify` in the station image. Administrators do the same in the web UI (*Administration →
Test certificates*) or with `GET /api/admin/certs` and `POST /api/admin/certs/{set}/regenerate`.
Each lifetime (root, TLM, EA, AA, AT for v3; root, AA, AT for v2) defaults to the generating
tool's value, because the ETSI documents fix none, and can be changed. A certificate may not
outlive its signer. The new set is checked before it replaces the old one, and
`generated.json` records the lifetimes. The v2 tickets are now `ticket_vnap_rsu` and
`ticket_vnap_obu`, without a date, so a regenerated set keeps the names the scenarios use.
See [certificates](docs/certificates.md#validity-and-regeneration).

Why: the committed v3 ATs were issued on 2026-10-04 with C-ITS-PKI's default of 168 hours and
expired on 2026-10-11; since then every scenario using them failed its check, with nothing in
the logs.

## 2026-10-18: five foundational scenarios with variants

The 19 scenario files and 2 templates in `sim/scenarios/` are now five foundational scenarios,
one per phenomenon: `trust` (certificate-chain verification), `linkability` (what a change
leaks when a vehicle is alone), `crossing` (privacy from other vehicles in a mix zone),
`convoy` (vehicles that change together but never separate) and `refill` (certificate supply
from the run's PKI). The settings studied so far are their **variants**, named sets of
overrides started as `vnapctl up crossing:min3`, or with the variant list in the web UI and
`variant` in the API. `controls/` keeps the pass/fail controls `none`, `naive-v3` and
`stress-naive-v3`; `c-its-pki-badroot` is now `trust:badroot`. `templates/` links the four
scenarios users may start. Every earlier name maps to a scenario and variant in
[vnapctl](docs/operations/vnapctl.md#scenario-names-before-2026-10-18).

- `--set` addresses entries of arrays of tables by name or index
  (`control.mix_zones.crossing.min_vehicles=3`) and replaces whole arrays (`control.mix_zones=[]`).
- `[defaults] pseudonyms` holds keys for every station with a pseudonym pool, so one override
  changes them all.
- `linkability`, `convoy` and the `crossing:blocks` variants now take certificates from the
  run's PKI instead of the committed pools, and `linkability`'s OBU moves; results measured
  with the earlier files are therefore not exactly reproduced by their new names.
- `make test` loads every scenario with each of its variants.

## 2026-10-17: documentation as one PDF

`docs/vnap-secure-documentation.pdf` collects all Markdown documentation, with an introduction
on the design intention of VNAP-Secure (`scripts/docs/pdf-intro.md`); the README links it at
the top. `make docs-pdf` rebuilds it (`scripts/docs/build-pdf.py`, reportlab and markdown-it in
`scripts/docs/.venv`), and `make test` now includes `make docs-pdf-check`, which fails when the
PDF no longer matches its sources. See [build](docs/build.md#documentation-pdf).

## 2026-10-13: clearer column headers on the Results tab

Two per-track columns on the *Results* tab have new headers:

- *Purity* is now *Most Frequent* and names the vehicle the share belongs to, e.g.
  `75 % (obu1)`; on a tie it names each of them.
- *Stations, in order* is now *Station predictions in actual order across identity changes*.

The values are the same, and `score.json` keeps its fields (`purity`, `main_station`,
`stations`). The track table of the summary report (`report.docx`, section 3) has the same
two columns; it had no *Most Frequent* column before. Reports already written are unchanged
until they are made again (`python3 -m vnapapi.report <results directory>`).

## 2026-10-12: run identifiers from the scenario name

A run's identifier is `<user>-<scenario name>-<n>` (e.g. `alice-mixzone-random-3`) instead of
12 hex digits. `n` starts at 1 and counts that user's runs started under that name; the
database keeps the counters (new table `run_counters`, made on the first start), so a number
is never given twice. The name is the one the run is started with; user and name are reduced
to letters, digits, `.`, `_` and `-`. Existing runs keep their identifiers.

- A scenario is validated before the run gets its number, so a refused one uses none (and no
  longer leaves its text in `data/scenarios`).
- The broker account's environment variables no longer carry the run id.
- Fixed on the way: a station `name` that is not text (`name = 1`) is reported as a scenario
  error instead of failing the request.

## 2026-10-12: suggested next runs in the summary report

The summary report ends with "6. Suggested next runs", chosen by rule from the run's
measurements. The rules are in `service/recommendations.toml`: fixed text with conditions on
the run's facts (`links.wrong>=8`, `pattern.share>=0.8`), priorities and a limit of 6. The 16
starting rules come from the recommendations of the summaries in `results/`.

- **Editable without code:** the file is read for every report.
  `python3 -m vnapapi.report --rules <results directory>` lists a run's facts and what each
  rule made of them, including rules with a mistake.
- **No judgement:** a rule only says what was written into it; each suggestion states the
  number that triggered it.
- Details: [suggested next runs](docs/functional/eavesdropper-and-scoring.md#suggested-next-runs).

## 2026-10-12: summary report at the end of a run

When a run stops, the service writes `report.docx` into its results, in the form of the test
summaries in `results/`: header table, purpose, setup, results (with every link scored
against ground truth, wrong links by vehicle pair and the content of each track),
observations, conclusion and limitations. The *Results* tab links it first.

- **Facts only** in this first version; suggested next runs were added the same day (above).
- **Built from the collected files** (`service/vnapapi/report.py`, standard library only), so
  `python3 -m vnapapi.report <results directory>` writes it again, also for earlier runs.
- **`run.json`** gains `stopped_at`, `stop_reason` and the last check; **`score.json`** gains
  each link's `evidence`.
- Details: [summary report](docs/functional/eavesdropper-and-scoring.md#summary-report).

## 2026-10-11: run name and description

A run has a name and a description of its own, which start as its scenario's. The owner (or
an admin) changes them on the web UI's Overview tab, or with `PATCH /api/runs/{id}`, in any
state of the run. The runs list shows the name; `run.json` in the results records both. The
database gains the columns on the first start (runs from before show their scenario's name
and description).

## 2026-10-11: earlier events on the Events tab

The Events tab shows the events of the last 10 minutes when it opens (it showed only those of
the last 5 s, so it stayed empty until the next event), and says when it is connected with
nothing to show. `GET /api/runs/{id}/events/stream` takes `since_s` and sends an event `ready`
after the earlier events.

## 2026-10-11: vehicle colours

A station's `color = "#rrggbb"` sets the colour of the station and its route on the web UI's
maps (the builder and the run's Map tab). The builder has a colour picker per vehicle.

## 2026-10-11: dynamic mix zones

The mobility client sends a pseudonym change event to a vehicle when at least `min_vehicles`
participating vehicles are within `radius_m` of it, at most every `cooldown_s`:
`[control.dynamic_mix_zone]` for the run, `dynamic_mix_zone = { enabled, min_vehicles }` per
station. Scenario `c-its-pki-convoy-dynamic`; the web UI's builder has "all vehicles" and
"min cars" for the run and a toggle with "min cars" per vehicle. No new station image.

## 2026-10-11: new run from a template, saved templates

- **New run:** the page starts from a template, whose text becomes the new scenario. The
  "Scenario text" and "Builder" tabs edit that same scenario: the builder reads the text
  (`POST /api/scenarios/parse`) and keeps the keys its forms do not show. An unchanged template
  is still started by its name.
- **Saved templates:** a scenario can be saved as a template of one's own, optionally shared
  with all users (`/api/templates`; a new database table).
- **Generic names:** saving can rename stations to `rsu1…`, `vehicle1…` and mix zones to
  `zone1…`.
- `POST /api/runs` takes a `name` for the run.

## 2026-10-11: line breaks in scenario text

A line break inside a `"..."` string of a scenario text is refused by TOML ("Illegal character
'\n'"). The error now says how to write it (`"""..."""`), and the web UI's text editor says
so above the text.

## 2026-10-10: mix zone threshold

`[[control.mix_zones]]` takes `min_vehicles = n`: a vehicle that entered the zone changes its
pseudonym only once at least `n` watched vehicles are inside. Those waiting change together;
one that leaves before keeps its pseudonym. Scenario `c-its-pki-mixzone-min3`; the web UI's
builder has the field per mix zone. The web UI's Control tab no longer has the "Change
pseudonym" button, which did the same as "ID change trigger".

## 2026-10-10: `cookie_secure = false` in the repository's configuration

`service/config.toml` sets `cookie_secure = false`, for testing only: login then works over
plain HTTP in every browser, including Safari and DuckDuckGo. A production deployment sets
`true` (a step of the deployment procedure); the service's built-in default stays `true`.

## 2026-10-10: installation paths in the documentation

The documentation names the clone's parent directory `INSTALL_DIR` and the repository
`VNAP_HOME` (`${INSTALL_DIR}/vnap-secure`) instead of `~/COIMBRA`; commands change directory
with `cd ${VNAP_HOME}/…`. The scripts do not read these variables.

## 2026-10-10: vnapctl progress messages

`vnapctl` reports what it is doing on stderr (`vnapctl: …`): building a helper image, starting
the run's PKI and the stations, waiting for readiness, collecting events. `-q`/`--quiet` and
`--json` turn the messages off. `up` pulls `eclipse-mosquitto:2` before the readiness wait
instead of during it.

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
