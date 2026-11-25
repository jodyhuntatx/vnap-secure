# VNAP-Secure Simulation Service

Product requirements: multi-user web service and per-run PKI with butterfly certificate refill.

| Item | Value |
|---|---|
| Document | Product requirements document (PRD), draft 0.3 |
| Date | 2026-11-25 (draft 0.1: 2026-10-07; draft 0.2: 2026-11-24) |
| Owner | Joseph Hunt |
| Status | Phases 1 to 9 delivered, except the MCP endpoint of phase 9 (see [8. Delivery plan](#8-delivery-plan)). Draft 0.2 adds the station requirements of section 5.10 (STA-1 to STA-4); draft 0.3 merges the overhead measurement design (5.11, section 10), whose phases 1, 2 and 4 are delivered |
| Repositories | vnap-secure (harness, vnapctl, service, docs), vanetza-nap patch set in `patches/vanetza-nap/` (socktap patches), C-ITS-PKI (certificate tooling) |
| Source | `Specs/VNAP-Secure-Simulation-Service-PRD.md`; the PDF is generated from it (`make docs-pdf`) |

## Revision history

| Draft | Date | Changes |
|---|---|---|
| 0.1 | 2026-10-07 | first draft; phases 1 and 2 approved to start |
| 0.2 | 2026-11-24 | source moved to Markdown in the repository; state of delivery (2.3, 8); station requirements STA-1 to STA-4 (5.10): CAM generation by EN 302 637-2, current stationId in every message after an ID change, security operation counters, full certificate in the first CAM after a silent period; risk "CAM generation rate" (9) |
| 0.3 | 2026-11-25 | the overhead measurement design (`VNAP-Secure-Pseudonym-Change-Overhead-Design.docx`, design draft 0.3) merged as section 10, brought up to date with phases 1, 2 and 4 and with CAM generation by EN 302 637-2; requirements OVH-1 to OVH-6 (5.11); overhead phases in the delivery plan (8) |

## 1. Summary

VNAP-Secure runs Vanetza-NAP (release2) road-side and on-board unit simulations with C-ITS
security, pseudonym changes and a passive tracking attacker (the eavesdropper), driven today by a
command-line tool, vnapctl, on one shared virtual machine. This document describes two
developments:

- **A multi-user simulation service** on a cloud-hosted VM: an API layer with built-in user
  accounts in front of Docker, used through a web UI (and later by automation such as Claude
  through the same API).
- **A per-run PKI with certificate refill:** each run gets its own certificate authority.
  Stations start with a fixed batch of butterfly-expanded authorization tickets and request a new
  batch when only a few unused ones are left, instead of recycling a fixed set. The time to
  refresh certificates becomes a measured quantity.

## 2. Background: current state

### 2.1 What exists (draft 0.1, vnap:r2-p17)

| Component | State (vnap:r2-p17, vnap-secure main) |
|---|---|
| Stations (socktap) | Vanetza-NAP release2 with patches: v2/v3 security with chain verification, thread-safety fixes, pseudonym pools changed by events on an MQTT control channel, ETSI TS 102 723-8/-9 ID change notification (certificate, MAC, GN address and stationId change together), random silent periods, position control channel (vehicle movement), CAM kinematics fixes, CAM timer restarted at a random phase on every ID change |
| vnapctl | Scenario files (TOML): validate, up, down, status, events, check; parallel instances (`--instance N`) with separate networks; ownership labels; helper images rebuilt when their sources change |
| Helper containers | control broker and pseudonym event client (vnap-pseudo-ctl), mobility client with routes, random turns and mix-zone triggers (vnap-mobility), eavesdropper (vnap-eavesdropper) |
| Eavesdropper | passive listener on the V2X network only; links pseudonyms by shared identifiers, CAM timing phase and position continuity; replay of saved captures |
| Certificates | fixed files in vnap-certs/c-its-pki generated once with C-ITS-PKI (root, EA, AA, TLM, a regular AT, 24 butterfly ATs valid 7 days). Every run reuses the same files; a pool wraps around to its first certificate when used up |
| Results | TestSummaries/: convoy and intersection mix-zone tests, scored against ground truth |

### 2.2 Findings that shape this work

- Full ID change, silent periods and mix zones with random turns defeat the eavesdropper's
  current techniques (vnap:r2-p17: 12 links, none correct, in the random-turn mix zone).
- Side channels matter: the CAM timer phase linked 16 of 16 changes until it was rephased; a
  fixed turning pattern lets an attacker undo position-linking mistakes.
- The fixed certificate set is unrealistic and itself a linkage risk: pools wrap around and
  reuse identities, and every run uses the same 24 certificates, so captures of different runs
  share identities.
- The shared VM relies on trust: anyone with Docker access is effectively root, and instance
  numbers, image tags and ownership labels are advisory.

### 2.3 State at draft 0.2 (vnap:r2-p21)

| Component | State |
|---|---|
| Stations (socktap) | as in 2.1, plus: growing pseudonym pool refilled from the run's PKI, butterfly key derivation on the station (r2-p18, r2-p19); DENMs carry the current stationId after an ID change, security operation counters (r2-p20); CAM generation by EN 302 637-2 (r2-p21) |
| Service | API service with accounts, roles, quotas, jobs, live events, control actions, results and audit log; web UI with templates, builder and map, live view, results; backups and TOTP |
| PKI | per-run PKI service (C-ITS-PKI): root, EA, AA, enrolment, initial batches, refill |
| Measurement | eavesdropper scoring; per-run privacy and cost of the pseudonym changes (`overhead.json`, `metrics.json`); verification collector and warning probe; operation counts per change and refill (section 10, phases 1, 2 and 4) |

## 3. Goals and non-goals

### 3.1 Goals

- **G1:** Users run, observe, control and evaluate simulations through a web UI, isolated from
  each other, without shell or Docker access.
- **G2:** Certificate handling approximates a real deployment: one PKI per run, butterfly key
  expansion, station-initiated batch refill, no certificate reuse.
- **G3:** Certificate refresh time, batch issuance time and certificate starvation are measured
  and reported per run.
- **G4:** Users author scenarios without knowing addresses, MACs, file paths or container
  details.
- **G5:** Existing scenarios, test results and the command-line workflow remain reproducible.

### 3.2 Non-goals (this release)

- Federated or external identity providers (built-in accounts only).
- Real radio hardware, multiple hosts or Kubernetes.
- Certificate revocation, misbehaviour reporting and linkage-value (LA) based revocation.
- Encrypted delivery of certificate batches between PKI and station (the control channel stands
  in for the backend link).

## 4. Users and roles

| Role | Can |
|---|---|
| Admin | manage accounts and quotas; reset passwords; manage the scenario catalogue, approved images and certificate policy limits; see and stop all runs; read the audit log |
| User | create, start, stop and delete own runs; author scenarios; send control actions to own runs; view and download own results |
| Viewer | view runs and results shared with them; no control actions |

## 5. Requirements

MUST requirements are needed for the release; SHOULD requirements are expected unless there is
a reason not to; MAY requirements are optional.

### 5.1 Platform and deployment

| ID | Requirement | Priority |
|---|---|---|
| PLAT-1 | Runs on a single cloud-hosted VM with expanded disk and memory, Docker CE (not the snap package), the repositories cloned locally. | MUST |
| PLAT-2 | Only HTTPS (443) is exposed, through a reverse proxy with TLS; MQTT brokers and simulation networks are not reachable from outside. | MUST |
| PLAT-3 | The API service runs as a dedicated system user and is the only component with access to the Docker socket. | MUST |
| PLAT-4 | Station and helper images are built on the VM or pulled from a private registry; station images are referenced by immutable tags (e.g. vnap:r2-p17), not latest. | MUST |
| PLAT-5 | Backups of the database and of retained results. | SHOULD |

### 5.2 Accounts and authentication

| ID | Requirement | Priority |
|---|---|---|
| AUTH-1 | Built-in username and password accounts; passwords hashed with Argon2id; no plaintext or reversible storage. | MUST |
| AUTH-2 | Accounts are created by an admin (no self-registration); password resets are admin-initiated. | MUST |
| AUTH-3 | Session cookies are HttpOnly, Secure and SameSite; state-changing requests are protected against CSRF. | MUST |
| AUTH-4 | Failed logins are rate-limited and repeated failures lock the account temporarily. | MUST |
| AUTH-5 | Every security-relevant action (login, run start/stop, control action, account change) is written to an audit log. | MUST |
| AUTH-6 | API tokens for automation (scoped to one user, revocable). | SHOULD |
| AUTH-7 | Optional TOTP second factor. | MAY |

### 5.3 Runs and multi-user operation

| ID | Requirement | Priority |
|---|---|---|
| RUN-1 | Each run is owned by one user; only the owner, viewers it is shared with and admins can see it. | MUST |
| RUN-2 | Instance numbers (networks, subnets, container names) are allocated atomically by the service; users never choose them. | MUST |
| RUN-3 | Per-user quotas: concurrent runs, maximum run duration (runs are stopped automatically at the limit), disk for retained results. | MUST |
| RUN-4 | Every container has CPU and memory limits. | MUST |
| RUN-5 | Starting and stopping runs are asynchronous jobs with visible progress; a failed start rolls back everything it created. | MUST |
| RUN-6 | A run records the exact scenario, overrides, image tags and seeds it was started with, so it can be repeated. | MUST |
| RUN-7 | Helper images are tagged by the digest of their sources, so different versions never overwrite each other. | MUST |

### 5.4 Scenario authoring

Today a scenario is a TOML file that names IP addresses, MACs, station IDs and certificate
paths. Users should not need any of these. Three authoring levels share one validator and one
scenario model:

| Level | For | How |
|---|---|---|
| Templates | most users | pick a catalogue scenario (e.g. random-turn mix zone) and set its exposed parameters: number of vehicles, speeds, silent period, certificate policy, eavesdropper techniques and windows |
| Builder | custom scenarios | add stations in forms; draw routes, crossings and mix zones on a map; the builder produces the scenario |
| Expert | power users | edit the generated scenario text directly, validated as it is typed |

| ID | Requirement | Priority |
|---|---|---|
| SCN-1 | The scenario format has a machine-readable schema from which the UI builds its forms. | MUST |
| SCN-2 | Validation returns structured errors (field path and message) and reports every problem at once. | MUST |
| SCN-3 | The service assigns addresses, MACs, station IDs and container names; users may not set them. | MUST |
| SCN-4 | Privileged fields (image, container environment, certificate directories, entrypoint) are admin-only; user scenarios may only use approved images and an allow-list of settings. | MUST |
| SCN-5 | A dry run shows what a scenario would start without starting it. | SHOULD |
| SCN-6 | The fixed-file certificate pools remain available to admins for reproducing earlier results. | SHOULD |

### 5.5 Certificates and PKI

Each run gets its own PKI, created when the run starts and destroyed, with all its keys, when
the run is removed. Certificates are authorization tickets (ATs) issued by butterfly key
expansion (BKE, IEEE 1609.2.1 / ETSI), using the C-ITS-PKI implementation.

| ID | Requirement | Priority |
|---|---|---|
| PKI-1 | Per run: a root CA, enrolment authority (EA) and authorization authority (AA) are generated at start; stations trust that root. | MUST |
| PKI-2 | Each pseudonym station is enrolled (caterpillar keys) and receives an initial batch of BKE ATs before it starts. Default: 8. | MUST |
| PKI-3 | Road-side units receive a regular AT from the run's AA. | MUST |
| PKI-4 | A station requests a new batch when its unused ATs drop to a threshold. Default threshold: 2 unused. Default batch size: 8. All three values are configurable per scenario. | MUST |
| PKI-5 | At most one request is outstanding per station; requests are retried with backoff when unanswered. | MUST |
| PKI-6 | Delivered ATs are added to the station's pool while it runs and are chain-checked like the initial ones; the pool never wraps around to a used AT. | MUST |
| PKI-7 | If the pool is empty when a change is due, the station keeps its current AT and refuses the change with a recorded reason (starvation). | MUST |
| PKI-8 | Each batch uses a new expansion period (i-value), so no two ATs of a station share derivation inputs. | MUST |
| PKI-9 | AT validity covers the run; validity periods are compressed to the run's time scale. | MUST |
| PKI-10 | Phase A: the PKI service returns the ATs and their private keys. Phase B: the station derives the private keys itself from its caterpillar key, the expansion value and per-AT offsets returned by the AA, so the PKI never knows them. | MUST (A), SHOULD (B) |
| PKI-11 | Run PKI material is stored on a per-run volume, mounted read-only into stations where possible, and deleted with the run. | MUST |

### 5.6 Certificate metrics

| ID | Requirement | Priority |
|---|---|---|
| MET-1 | Refresh time: from a station's batch request to the batch being installed in its pool, per request; reported as count, mean, max and distribution per run. | MUST |
| MET-2 | Issuance time at the PKI per batch, and batch size, so refresh time can be related to batch size. | MUST |
| MET-3 | Requests, batches, retries, failures and starvation events (refused changes) per station. | MUST |
| MET-4 | Unused ATs per station over time. | SHOULD |
| MET-5 | The metrics are available in status, events, checks (expectations such as no starvation) and the results view. | MUST |
| MET-6 | A batch-size sweep (e.g. 4, 8, 16, 32) can be run as one experiment and summarized. | SHOULD |

### 5.7 Live monitoring and control

| ID | Requirement | Priority |
|---|---|---|
| LIVE-1 | Live event stream per run (messages sent and received, verification results, pseudonym and ID changes, PKI events, control actions, errors), filterable by kind and station. | MUST |
| LIVE-2 | Map view of vehicle positions, routes and mix zones; optionally the eavesdropper's tracks. | SHOULD |
| LIVE-3 | Control actions on own runs: pseudonym change, ID lock/unlock, ID change trigger, move vehicle. | MUST |
| LIVE-4 | Each run's control broker uses credentials generated by the service; users never see them. | MUST |

### 5.8 Results and evaluation

| ID | Requirement | Priority |
|---|---|---|
| RES-1 | Check verdicts with default and scenario expectations. | MUST |
| RES-2 | Eavesdropper results per technique (identifiers, timing, position), scored automatically against ground truth (correct and wrong links, track purity). | MUST |
| RES-3 | Download of captures, logs and generated test summary documents. | SHOULD |
| RES-4 | Repeated runs of one configuration with different seeds, aggregated. | SHOULD |

### 5.9 Security

| ID | Requirement | Priority |
|---|---|---|
| SEC-1 | No user-controlled value reaches a container command line, mount or image reference without validation against an allow-list. | MUST |
| SEC-2 | Private keys never leave the server and are never shown in the UI or logs. | MUST |
| SEC-3 | Secrets reach containers only through the environment or mounted files, never as command-line arguments or labels. | MUST |
| SEC-4 | Runs are isolated by network: a run's containers cannot reach another run's networks. | MUST |

### 5.10 Station behaviour (draft 0.2)

The cost and privacy of pseudonym changes are measured on the stations' messages, so the
stations must send what a vehicle would send.

| ID | Requirement | Priority |
|---|---|---|
| STA-1 | CAMs are generated as EN 302 637-2 (clause 6.1.3) specifies: checked every 100 ms, sent when the heading changed by more than 4°, the position by more than 4 m or the speed by more than 0.5 m/s since the last CAM, otherwise at the latest after T_GenCam (at most 1 s). Generation at a fixed interval remains selectable, to reproduce earlier results. | MUST |
| STA-2 | After a full ID change, every message a station sends (CAM, DENM) carries its new stationId; the configured stationId does not appear on the V2X network. | MUST |
| STA-3 | Stations record the count and time of their security operations (signing, verification, chain checks, key derivation, ID changes), so the computation cost of a pseudonym change and of a refill can be measured. | SHOULD |
| STA-4 | After a silent period, the first CAM carries the full certificate, so neighbours can verify the new identity's first message. | MUST |

### 5.11 Overhead measurement (draft 0.3)

What a pseudonym change costs, measured next to the privacy it gives. Section 10 is the design.

| ID | Requirement | Priority |
|---|---|---|
| OVH-1 | Every run's privacy and cost measures are computed from its saved results when it ends, without user action (`metrics.json`, `overhead.json`, `score.json`), and can be recomputed on stored runs (10.7.1). | MUST |
| OVH-2 | Per change and receiver: reception gap, position error, stale time, ghost time, history lost, verification failures, and the delay or loss of warnings; conflict exposure from the vehicles' true positions (10.6). | MUST |
| OVH-3 | Per change and per refill: the operation counts and times of the vehicle and its neighbours (STA-3, 10.5); sizes of keys, certificates, signatures and messages, and pool storage (10.5.4). | MUST |
| OVH-4 | CPU time of every station container, aligned with changes and refills (10.5.1). | SHOULD |
| OVH-5 | A sweep of change rates and silent periods against a baseline without changes gives a cost per change (10.4); a joint experiment, fixed before it is run, compares policies on privacy and safety with confidence intervals (10.9). | SHOULD |
| OVH-6 | Misbehaviour detection under identity changes (10.10.3). | MAY |

## 6. Architecture

```
browser --HTTPS-- reverse proxy (TLS) -- API service ---- job worker ---- Docker
                                              |                |
                                          database      per run:  stations (socktap)
                                   (users, runs, results,         control broker
                                    audit log)                    PKI service      <- new
                                                                  mobility client
                                                                  eavesdropper
                                                                  per-run volume (PKI material)
```

- **vnapctl as a library:** scenario model and validation, up/down, status, events, check and
  the eavesdropper reports move into a Python package; the command-line tool and the API service
  both use it.
- **API service:** FastAPI (Python, like vnapctl), OpenAPI description, session and token
  authentication; SQLite at first, Postgres when needed. Long operations run in a worker.
- **PKI service (new):** a container per run wrapping C-ITS-PKI. It creates the run's CA
  hierarchy, enrols stations, issues initial batches and answers refill requests on the run's
  control broker.
- **Stations:** socktap gains a growing pseudonym pool and a PKI request/response handler on its
  existing control connection.

## 7. Certificate refill protocol

The control broker stands in for the backend link between a vehicle and its PKI (in a
deployment, a cellular or internet connection authenticated with the enrolment credential). It
is not part of the V2X network, so the eavesdropper does not see it. Topics use the station's
configured ID, which stays fixed across ID changes, like the existing pseudonym and position
topics.

| Message | Topic | Content |
|---|---|---|
| Request (station to PKI) | `vnap/pki/<station>/request` | request_id, unused count, requested batch size, time sent |
| Batch (PKI to station) | `vnap/pki/<station>/batch` | request_id, i-period, ATs (encoded certificates); phase A also the private keys, phase B the expansion offsets instead; issuance time in ms |
| Error (PKI to station) | `vnap/pki/<station>/batch` | request_id, error text; the station retries with backoff |

```
station                                       PKI service
  | unused ATs <= threshold (default 2)           |
  |-- request {request_id, unused, count} ------> |  BKE: expand caterpillar keys for i-period k,
  |                                               |  AA issues `count` ATs (default 8)
  | <-- batch {request_id, ATs, keys} ----------- |
  | add to pool, chain check, log refresh time    |
```

Station log lines (examples): `[PKI] batch requested (2 unused, 8 wanted)`,
`[PKI] batch installed: 8 certificate(s), 412 ms after request`,
`[PKI] pool empty: change refused`. vnapctl derives the metrics in section 5.6 from these lines
and the PKI service's log.

## 8. Delivery plan

| Phase | Content | Acceptance criteria | Delivered |
|---|---|---|---|
| 1. PKI service | container wrapping C-ITS-PKI: per-run root, EA, AA; enrolment; initial batches; RSU AT; refill requests over MQTT; issuance timing; any service mode needed in C-ITS-PKI | a run's stations trust only that run's root; initial batches of the configured size; a request is answered with a valid batch of new ATs; issuance time logged | 2026-10-07 |
| 2. Station refill (option A) | socktap: pool that grows at runtime, unused tracking, request at threshold, no wrap-around, starvation handling, [PKI] logs, chain check of new ATs; ThreadSanitizer run | over a run longer than one batch, no AT is used twice; refresh time logged per request; all regression scenarios pass; no new ThreadSanitizer reports | 2026-10-07 |
| 3. vnapctl | certificate policy in scenarios (initial 8, refill at 2, batch 8 by default); PKI provisioning and per-run volume; teardown deletes keys; PKI events, metrics and checks; fixed pools as expert option | mix-zone scenarios run with per-run PKI; status and check show refresh metrics; earlier scenarios still run with fixed pools | 2026-10-07 |
| 4. Measurement | mix-zone reruns with per-run PKI; batch-size sweep; test summary | summary relates refresh time to batch size | 2026-10-07 |
| 5. Station key derivation (option B) | BKE private key derivation in socktap; PKI returns offsets instead of keys | PKI holds no AT private keys; derivation time included in refresh time | 2026-10-07 |
| 6. vnapctl library | package split; schema and structured validation; service-assigned addresses; allow-lists; atomic instance allocation; content-addressed helper images | CLI behaviour unchanged; validator returns field-level errors | 2026-10-07 |
| 7. API service | accounts, roles, runs, quotas, jobs, events stream, control actions, results, audit log | requirements AUTH, RUN, LIVE, RES, SEC met for the API | 2026-10-08 |
| 8. Web UI | runs list, scenario templates and builder with map, live view, control, results | a user can author, run, observe and evaluate a scenario without the command line | 2026-10-08 |
| 9. Hardening | backups, TOTP, MCP endpoint for automation | - | 2026-10-08, except the MCP endpoint |
| Station fidelity (draft 0.2) | STA-1 to STA-4: CAM generation by EN 302 637-2, current stationId in DENMs, security operation counters, certificate after the silent period | CAM intervals follow the vehicle's dynamics (about 0.4 s at 36 km/h, 1 s when stationary); the first CAM after a silent period carries the full certificate; no configured stationId on the network after a change; operation counts per change and refill in `overhead.json` | 2026-11-24 (vnap:r2-p20, r2-p21) |
| Overhead measurement (draft 0.3) | phases 1 to 8 of section 10.12: disruption analysis and run metrics (1); collector, warning probe, conflict exposure (2); CPU sampler and sweep (3); station counters (4); report and Results tab (5); joint experiment (6, 7); misbehaviour detection (8, optional) | the "done when" of each phase in 10.12 | phases 1 (2026-10-19), 2 and 4 (2026-11-24); phase 3 next |

## 9. Risks and open questions

| Topic | Notes |
|---|---|
| Option A realism | the PKI knows the stations' private keys and derivation cost is missing from refresh time; refresh-time results should be confirmed after option B |
| Time compression | real batches cover weeks; runs last minutes. Validity, i-periods and refill timing are scaled; the scaling must be stated with every result |
| PKI load | batch issuance is CPU-bound; many stations requesting at once (synchronized changes in a mix zone) will queue. This is a finding to measure, not to hide |
| Enrolment realism | real enrolment and AT requests are encrypted and authenticated with the enrolment credential and go through a registration authority; the control channel simplifies this |
| Retention | how long runs, captures and summaries are kept per user, and what is deleted with a run (PKI keys always) |
| UI technology | to be chosen in phase 8 (server-rendered pages or a single-page application) |
| CAM generation rate (draft 0.2) | images up to vnap:r2-p20 sent CAMs at a fixed 1 s interval whatever the vehicle did. Every CAM then carried the full certificate (it is attached at most once a second), CAM timing was strictly regular, and receivers heard moving vehicles less often than real ones would. Results measured with those images must say so; from vnap:r2-p21 CAMs follow EN 302 637-2 (STA-1) |

## 10. Overhead of pseudonym changes and the privacy-safety trade-off

This section is the measurement design that was kept until draft 0.3 of this document in
`Specs/VNAP-Secure-Pseudonym-Change-Overhead-Design.docx` (design drafts 0.1 to 0.3,
2026-10-15 to 2026-10-17, the last after a review by B. Sousa). It is merged here unchanged in
substance and brought up to date with what has been delivered (10.12) and with CAM generation
by EN 302 637-2 (STA-1, `vnap:r2-p21`). The requirements it serves are OVH-1 to OVH-6
(5.11). Scope: computation load on the OBU and on its neighbours, sizes and storage, the
disruption of communication that may affect safety, a catalogue of privacy, PKI, safety,
security and cost metrics, and a joint experiment that weighs them against each other.

### 10.1 Summary

Pseudonym changes protect privacy at a price: work for the vehicle and its neighbours, and
moments in which the neighbours know less about the vehicle. The eavesdropper's scoring measures
the privacy side in detail; this design measures the cost, so that every privacy result can be
read against what it costs.

- **Computation:** the cost of one change and of one refill, on the OBU and on the vehicles that
  receive from it, as times and as counts of cryptographic operations.
- **Disruption:** what receivers lose around a change: the gap in reception, the error in where
  they believe the vehicle is, the time their information is stale, verification failures, and
  delayed or lost warnings.

The method is a comparison on the same seed: a run without changes as the baseline, and the
same scenario with changes at increasing rates and with longer silent periods. The overhead is
the difference; against the change rate it gives a cost per change.

A joint experiment (10.9) then scores the same runs for privacy and for cost. It places each
countermeasure on a chart of privacy against safety, with confidence intervals, and names the
policies that no other tested policy beats on both. Its result is a comparison within the
simulated conditions; 10.9.8 says what it cannot claim and what further evidence a
recommendation to manufacturers would need. Section 10.10 catalogues every metric, grouped as
privacy, PKI, safety, security and cost. All measures are collected automatically when a run
ends (10.7).

### 10.2 Background

#### 10.2.1 What a change does

| Step | Work | Effect on communication |
|---|---|---|
| ID change notification (PREPARE, COMMIT) | routers locked; new GN address, MAC and `stationId`, each derived with SHA-256; switch to the next certificate and key | nothing is sent with the old identifiers once PREPARE has started |
| CAM timer restart | none worth counting | the first CAM after the change is skipped, the next comes after a random delay of up to one interval: up to two intervals without a CAM. From `vnap:r2-p21` the interval is the vehicle's current T_GenCam (about 0.4 s for a car at 36 km/h, 1 s when it keeps speed and heading) |
| Silent period (optional) | none | all outgoing frames are dropped for a random time (`silent_min_ms` to `silent_max_ms`): CAMs, beacons and injected messages, warnings included |
| First signed message | the full new certificate is attached; from `vnap:r2-p21` also on the first CAM after the silent period (STA-4) | receivers must check the new certificate's chain once |
| Refill (with `[pki]`) | a request over the control channel; per certificate: private key derivation, key check, chain check | none, unless the pool runs empty: changes are then refused |

#### 10.2.2 Measured before this design

| Measure | Where | Covers |
|---|---|---|
| `<station>.pki.refresh_ms_mean`, `refresh_ms_max` | `vnapctl check`, `metrics.json` | time from a refill request to its installation |
| `<station>.pki.derivation_ms_mean` | `vnapctl check`, `metrics.json` | key derivation per batch |
| `<station>.pki.starved` | `vnapctl check`, `metrics.json` | changes refused for lack of certificates |
| `pki.issue_ms_max`, `pki.queue_ms_max` | `vnapctl check`, `metrics.json` | work and queueing at the PKI |
| `silent period over, <n> frame(s) suppressed` | station log | frames dropped per silent period |
| `silent.share` | summary report facts | share of the vehicles' time spent silent |
| `rsu.cam.rx_per_s` | `vnapctl check`, `metrics.json` | CAMs per second at the RSU |
| every frame on the link, with time, sender identifiers and position | eavesdropper `messages.jsonl` | raw material for the disruption measures (10.6) |

None of these was aligned with individual changes, none measured CPU, and none measured what
receivers believe about a vehicle during a gap. The check metrics were computed only when a
check was run, over its observation window; since phase 1 they are computed over the whole run
and written to `metrics.json` when a run ends (10.7.1).

#### 10.2.3 Trust model

Receivers do not ask the PKI to validate certificates. Each station is configured at start with
the trusted root and the AA certificate, and checks every new authorization ticket locally:
AT → AA → trusted root, and the message signature (see
[security and chain verification](../docs/functional/security-and-chain-verification.md)). The
run's PKI is reached only by vehicles that request a new batch, over the control channel. A
certificate cache miss therefore means local work: the receiver sees an AT it has not checked
before and verifies its chain once.

Certificates are generated per run and are trusted for its duration. There is no revocation
(3.2): no certificate revocation list is issued or checked, so every AT that chains to the root
is accepted. Section 10.10.3 says what measuring revocation would take.

### 10.3 Goals and non-goals

- **Goals:**
  - a cost per change and per refill, in CPU time and in cryptographic operations, for the OBU
    and for its neighbours;
  - disruption measures per change and per run, at every receiver, that describe what a safety
    application would have lost;
  - a sweep that shows how both grow with the change rate and the silent period, next to the
    privacy result of the same runs;
  - a joint experiment, defined before it is run, that compares countermeasures on privacy and
    safety at once and states the result with its uncertainty (10.9);
  - measures available in `vnapctl check`, the run's result files and the summary report, so
    that scenarios can state limits (`expect`).
- **Non-goals:**
  - absolute OBU figures: containers on a VM are not OBU hardware. Times are for comparison;
    the operation counts are what carries over (10.5.3);
  - radio effects: the link is a lossless bridge, without loss, congestion or range. The
    disruption measured is the part the change causes, a lower bound (10.13);
  - judging safety applications: the measures describe what information was missing, not
    whether a particular application would have failed.

### 10.4 Method

- **Baseline:** the scenario with pseudonym changes switched off, on the same mobility seed, so
  that vehicles drive the same paths.
- **Variants:** the same scenario with changes every 60, 30, 10 and 5 s, each with no silent
  period, 3 to 13 s (the SAE J2735 values reported in ETSI TR 103 415) and 6 to 12 s.
- **Alignment:** every measure is kept per change, with the change's time from the station log
  (`[IDCHANGE]` lines), not only as a run average. A run average hides a 10 s gap among minutes
  of normal traffic.
- **Cost per change:** the slope of a measure against changes per minute, over the variants.
- **Seeds:** 3 per variant to start. 13 variants (one baseline, 12 with changes) of 3 minutes
  each take about two hours per scenario.
- **Scenarios:** `crossing:periodic` (four vehicles changing every 20 s wherever they are) and
  `crossing` (four vehicles; changes synchronized in the mix zone, so refills arrive together).
- **Image:** `vnap:r2-p21` or later, so that CAMs follow the vehicles' dynamics (STA-1).
  Results of earlier images were measured at a fixed 1 s CAM interval and are not compared with
  later ones.

### 10.5 Computation load

#### 10.5.1 On the OBU

| Work | When | How it is measured |
|---|---|---|
| ID change handshake and switch | every change | `[IDCHANGE] completed in <us> us` at COMMIT (station counters, phase 4) |
| signing | every message; unchanged by a change | count and total time of messages signed and of ECDSA signatures (phase 4); signature size (10.5.4) |
| key derivation and key check | every refill, per certificate | `derivation_ms_mean`; count and time per refill (phase 4); key sizes (10.5.4) |
| chain check of new certificates | every refill, per certificate | `batch_check` count and time (phase 4); certificate sizes (10.5.4) |
| all of the above, and anything missed | the whole run | CPU time of the station's container, sampled from its cgroup about 10 times a second (CPU sampler, phase 3, not yet built) |

Each station runs in its own container, so its CPU time can be read from its cgroup without
touching the station code. The cgroup samples are aligned with the change and refill times. The
extra CPU in a window around each event, against the baseline, catches work the counters do not.

#### 10.5.2 On the neighbours

Every receiver of a changed vehicle sees an unknown certificate and checks its chain once. With
many vehicles changing, for example together in a mix zone, this work arrives at every receiver
at the same moment.

- counters at each receiver: unknown signers (certificate cache misses), chain checks and
  their time. The check is local, against the configured root and AA (10.2.3); no request goes
  to the PKI;
- signature verifications and their total time, at each receiver;
- the same cgroup samples at the RSU and the other OBUs, aligned with the changes of the
  vehicles they hear.

The counters are described in [operation counters](../docs/patches/operation-counters.md).

#### 10.5.3 Carrying the results over to real hardware

CPU times on shared VM cores depend on the host and on the other containers. The design
therefore reports both times and counts per change: signatures made and verified, chain checks,
keys derived. Multiplied by the per-operation times of a target OBU or its hardware security
module, the counts give an estimate for that hardware. The times serve to compare variants
within one host.

#### 10.5.4 Sizes and storage

Sizes matter twice: on the air, where every byte of a signed message adds to channel load, and
on the vehicle, where a pool of pseudonyms takes storage. Unlike times, sizes do not depend on
the host and carry over to real hardware unchanged.

| Item | Where it is measured | Notes |
|---|---|---|
| private key, public key | the station's key files; derivation output | ECDSA on NIST P-256: a 32-byte private key; the public key compressed or uncompressed, as encoded |
| authorization ticket (AT) | certificate files in the pool and in each PKI batch | the committed test ATs (`certs/c-its-pki/`) are 135 bytes; the AA certificate 182 bytes |
| signature | parsed from received frames by the eavesdropper | about 64 bytes of value for P-256, plus encoding; the same on every signed message |
| signer field | parsed from frames: `signer` is `digest` (8 bytes) or `certificate` (the full AT) | the full certificate is attached when a second has passed since the last one, on the first message after a change and on the first CAM after a silent period. At 1 s CAMs (up to `vnap:r2-p20`) that was every CAM; from `vnap:r2-p21` about one CAM in three for a moving car |
| message size | `length` of every frame in `messages.jsonl`, by message type and signer kind | security overhead = signed size − unsigned payload size; measured CAMs: 193 B with a digest, 323 B with the certificate |
| pool storage | computed: pool size × (AT size + private key size), and per refill batch | with a refill, the peak pool size is the batch size plus the low-water mark |

### 10.6 Communication disruption

Disruption is measured at the receivers, because what matters for safety is what the neighbours
know about the vehicle. Receivers are the RSU and every other OBU.

#### 10.6.1 Measures

| Measure | Definition | Why it matters |
|---|---|---|
| Reception gap | per change and receiver: time from the last message under the old identity to the first under the new one | for that long the vehicle is missing from the receiver's view |
| Position error | the receiver's belief about the vehicle's position against its true position, over time; two variants: last known position, and last position extended by speed and heading | turns a gap into metres of uncertainty, which is what a collision warning depends on |
| Time stale | share of time the receiver's latest information about the vehicle is older than a threshold (1 s, the longest CAM interval of EN 302 637-2) | one number per vehicle and run, comparable across variants |
| Verification failures | received messages whose security report is not `Success`, within 5 s after a change | a receiver that cannot yet check the new certificate may discard messages |
| Ghost time | time the old identity remains in the receiver's neighbour table while the new one is already there, under a stated table timeout (1.1 s) | the receiver briefly counts two vehicles where there is one, and loses the vehicle's history |
| Warning delay and loss | delay from triggering a DENM to its reception, and the share never received (10.6.3) | the most direct safety cost: a silent period also drops warnings |
| Inter-packet gap | per sender vehicle and receiver: time between consecutive CAMs, over the whole run (95th percentile and maximum) | the reception gap seen in context: how a change compares with the normal rhythm of CAMs |
| Delivery ratio | CAMs received / CAMs expected at the sender's CAM rate, in windows around each change and over the run; also received / sent from the stations' tx events | on the lossless link, every shortfall is caused by the change or the silent period |
| History lost | per change and receiver: one neighbour entry ended and one started; the path-history points and the time under the old identity that the receiver can no longer attach to the vehicle | the "tracking IDs lost" of a cooperative-perception or neighbour table |
| Conflict exposure | time during which two vehicles are in conflict (true time-to-collision below a threshold) while one's information about the other is stale (10.6.4) | the part of stale time that falls where a warning would be needed |

The receiver itself cannot link the old identity to the new one; that is the purpose of the
change. The measures are therefore computed afterwards, by the analysis, which knows from the
station logs which identities belong to which vehicle. A receiver experiences the reception gap
as one vehicle disappearing and an unknown one appearing, without history. The measures
quantify exactly that loss: how long the vehicle was missing under any identity, and how much
of what the receiver knew about it no longer applies.

The minimum cost of a change, without a silent period, is set by the CAM timer restart: up to
two CAM intervals without a CAM. The measured gap should never be shorter; if it is longer,
something else delays the first message. With CAM generation by EN 302 637-2 the interval
depends on the vehicle's dynamics, so the analysis takes it from the sender's CAMs before the
change.

#### 10.6.2 Data sources

- **Reception gap, position belief, ghost time:** the eavesdropper's `messages.jsonl` holds
  every frame with time, identifiers and position. On the lossless link every receiver hears
  the same frames, so the eavesdropper serves as a reference receiver. The mapping from
  identity to vehicle comes from the station logs, as for scoring.
- **True positions:** the mobility client records each vehicle's position 5 times a second
  (`positions.jsonl`, phase 1).
- **Verification results:** each receiving station publishes received messages with their
  security report on its own broker; the collector (`vnap-collector`, phase 2) records them
  during the run (`receptions.jsonl`).

#### 10.6.3 Warning probe

A probe publishes DENMs on a vehicle's `vanetza/in/` topic at random times, about once every
10 s, repeated as the DEN basic service does, and records when and whether each receiver gets
them (`[probe]`, in the collector). A warning triggered during a silent period is dropped with
the other frames, so the probe measures how often that happens and for how long warnings are
held back.

This also allows a defence to be measured: ending the silent period early, or exempting
warnings from it, when a warning has to go out. That is a change to the station code and is
left to a later decision (10.14).

#### 10.6.4 Conflict exposure instead of collision risk

Vanetza-NAP has no collision detection or warning application, and the stations do not
estimate collision risk. Time-to-collision (TTC) is therefore computed by the analysis, not by
the stations, from the recorded true positions, speeds and headings of each pair of vehicles.

- **Conflict:** a pair of vehicles whose true TTC (constant speed and heading) is below a
  threshold, 4 s, in the range used by forward-collision and intersection warnings.
- **Conflict exposure:** the time in conflict during which the receiving vehicle's latest
  information about the other is stale (older than 1 s), or during which its believed TTC, from
  that information, is above the threshold while the true TTC is below.
- **Missed conflicts:** conflicts that begin and end entirely within a stale period at the
  receiver.

These are measures of missing information at the moments it would have mattered, not a
probability of collision. On the crossing scenarios, conflicts occur mainly at the
intersection, which is also where mix zones place the silent periods; this coincidence is what
the measure is meant to expose. The mobility client does not make vehicles yield to each
other, and drives on the road's centre line, so vehicles meeting on one arm count as head-on
conflicts. That is acceptable for a measure of lost information, but the counts are not crash
rates.

### 10.7 Outputs

#### 10.7.1 Automatic collection

Every measure is collected without user action, in the same step that saves logs, `score.json`
and the summary report when a service run stops (`sim/vnapsim/collect.py`, shared by the
service and `vnapctl collect <dir>` for command-line runs):

- `metrics.json`: the `vnapctl check` metrics computed over the whole run from the saved
  station logs, instead of only over a check window, with the receivers' verification results
  and the operation counts (`ops.*`);
- `overhead.json`: the disruption analysis (`sim/vnapsim/overhead.py`, `receivers.py`,
  `counters.py`): every change with its measures, the sizes of 10.5.4, the privacy metrics of
  10.10.1 (tracking success, time and distance followed) and the operation counts; CPU samples
  once phase 3 adds them.

A failure in the analysis is written to an error file and never loses the raw results. The
analysis reads only the results directory, so it can be rerun on stored runs
(`vnapctl collect --analyse`, `vnapctl overhead <dir>`), for example after a change to a
definition. The batch runner of the joint experiment uses the same files.

#### 10.7.2 Check metrics and presentation

`vnapctl check` metrics, so that a scenario can state limits:

| Metric | Meaning | State |
|---|---|---|
| `<station>.change.ms_mean`, `.ms_max` | time of the change handshake on the station | proposed; the times are in `overhead.json` (`id_change_us`) |
| `<station>.change.cpu_ms_mean` | extra CPU per change against the baseline window | proposed (phase 3) |
| `<station>.ops.*`, `ops.*` | operation counts and times, among them chain checks and unknown signers caused by other vehicles' changes | in `metrics.json` (phase 4) |
| `<receiver>.gap_ms_max`, `.gap_ms_mean` | reception gaps around changes, over all senders | proposed; in `overhead.json` |
| `<receiver>.stale_share` | share of time information about some sender was older than the threshold | proposed; in `overhead.json` |
| `<receiver>.pos_error_m_p95` | 95th percentile of the position error during gaps | proposed; in `overhead.json` |
| `denm.*`, `verify.*` | warning probe and verification results | in `metrics.json` (phase 2) |
| `<receiver>.ipg_ms_p95`, `<receiver>.delivery_ratio` | inter-packet gap and delivery ratio of CAMs | proposed; in `overhead.json` |
| `conflict.*` | conflict exposure and missed conflicts (10.6.4) | in `metrics.json` (phase 2) |
| `<station>.cert_bytes`, `<station>.pool_bytes`, `msg.cam.bytes_mean` | sizes and storage (10.5.4) | proposed; in `overhead.json` |

- **Summary report:** section 4 of `report.docx`, *Cost of the pseudonym changes*, and facts
  for `recommendations.toml` (rules such as `warnings-lost`, `unexplained-gaps`,
  `silence-without-privacy`). The operation counts are not yet in the report (phase 5).
- **Results tab:** the per-run figures next to the privacy result; the operation counts are not
  yet shown (phase 5).

### 10.8 Relation to the adversarial agents design

`Specs/VNAP-Secure-Adversarial-Agents-Design.docx` weighs a defence's privacy against
`silent.share` only. The measures here give that loop a cost that is closer to safety: stale
time, position error and lost warnings. They would replace or join `silent.share` in the blue
agent's score, and lost warnings would become a hard constraint.

In the other direction, the joint experiment (10.9) uses the strongest attacker in that
design's archive, once there is one, so that a countermeasure is not judged only against the
attacker that happens to exist today.

### 10.9 Joint experiment: privacy against safety

The measures above describe cost; the eavesdropper's scoring describes privacy. This section
defines one experiment that produces both for the same runs, so that countermeasures can be
compared on the two at once. Everything in it (factors, measures, hypotheses, analysis) is fixed
before the runs, so that the result cannot be shaped afterwards by choosing the measure that
looks best.

#### 10.9.1 Question

For each pseudonym-change policy, under each traffic condition: how much privacy does it give,
what does it cost in safety-relevant information, and which policies are not beaten on both by
another tested policy?

#### 10.9.2 Factors

| Factor | Levels | Notes |
|---|---|---|
| Change trigger | periodic every 30 s; periodic every 10 s; mix zone, `min_vehicles` 1; mix zone, `min_vehicles` 2; dynamic mix zone | existing scenario keys |
| Silent period | none; 3 to 13 s; 6 to 12 s | `silent_min_ms`, `silent_max_ms` |
| Baseline | no pseudonym changes | the reference for every cost measure |
| Vehicles | 2, 4, 8 | 8 needs a new scenario on the crossing; to be confirmed that the host carries it |
| Speeds | all equal; spread (e.g. 30 to 50 km/h) | equal speeds favour the defender in a mix zone |
| Attacker | A0 with `link_window` 15 s, both techniques; later the strongest attacker in the adversarial archive; a guessing attacker | attackers are replayed on the same frames, so they cost no runs |

Held fixed: full ID change, the CAM timer restart, CAM generation by EN 302 637-2 and the
certificate after the silent period (station image `vnap:r2-p21` or later), random turns at the
crossing, certificate refill from the run's PKI, and the warning probe of 10.6.3. A policy is
one combination of trigger and silent period: 15 policies and the baseline.

#### 10.9.3 Primary and secondary measures

One primary measure per side is declared now. The others are reported, but a conclusion rests on
the primary ones.

| Side | Primary | Secondary |
|---|---|---|
| Privacy | tracking success: correct links / pseudonym changes made, for the strongest attacker | longest chain followed; time and distance followed; correct links relative to the guessing attacker; mistake share; change synchronization ratio |
| Safety | stale share at receivers: share of time information about a vehicle is older than 1 s | conflict exposure; position error during gaps (95th percentile, extended variant); reception gap; delivery ratio; warning delay |
| Hard limit | no lost warnings outside what the silent period itself explains | warning loss rate |
| Cost | reported, not optimised | chain checks per receiver and minute; CPU per change; certificates per vehicle and hour; pool storage; security bytes per message |

Conflict exposure was considered as the primary safety measure, because it weighs stale time by
where it falls. It stays secondary for now: it depends on a TTC threshold and on how often the
scenarios produce conflicts, which the pilot has to show first. If the pilot gives enough
conflicts per run for a stable estimate, the choice is revisited before stage A (10.14).

The guessing attacker links every new pseudonym to a random silent track. Replayed on the same
frames, it gives the chance level of each run empirically, instead of the formula 1 / vehicles,
which ignores how many vehicles were actually silent at the moment.

#### 10.9.4 Hypotheses

| Id | Hypothesis | What it would show |
|---|---|---|
| H1 | A silent period lowers tracking success to chance level only when several vehicles change together (mix zone with `min_vehicles` 2, dynamic mix zone); with periodic triggers it adds stale time without that effect | where silence is worth its cost |
| H2 | Stale share is close to changes per second × (mean silent period + two CAM intervals) | a check that the measurement works; a large deviation means something else delays the first message |
| H3 | Warning loss is close to the share of time spent silent | a check of the probe |
| H4 | A dynamic mix zone reaches the tracking success of a fixed mix zone at a lower stale share | whether the dynamic zone is the better policy |
| H5 | The policies on the frontier differ between 2, 4 and 8 vehicles | whether a single recommendation can hold across densities |

#### 10.9.5 Runs

- **Run length:** 5 minutes, so that a vehicle changing every 30 s makes about 10 changes and
  chains of several changes are possible.
- **Pilot:** two policies and the baseline, 10 seeds each, at 4 vehicles. It checks the
  pipeline and measures the spread of the primary measures over seeds.
- **Seeds:** chosen from the pilot so that the 95 % confidence interval of tracking success is
  at most ± 0.1 wide. 10 per cell is the starting estimate.
- **Stage A, screening:** all 15 policies and the baseline at 4 vehicles with spread speeds:
  160 runs, about 15 hours one after another.
- **Stage B, conditions:** the policies on or near the stage A frontier (at most 5) and the
  baseline, under all 6 combinations of vehicles and speeds: up to 360 runs, about 33 hours.
- **Runner:** the batch runner of the adversarial agents design: one scenario over a seed list
  with overrides, one results directory per run.

#### 10.9.6 Analysis

- **Unit:** the run. Links within a run are not independent, so confidence intervals come from
  resampling runs (bootstrap over seeds), not links.
- **Frontier:** a policy is dominated if another policy is better on both primary measures and
  the difference on at least one exceeds its confidence interval. The frontier is reported per
  condition.
- **Hard limit first:** a policy that loses warnings beyond its silent time is reported, but
  excluded from the frontier.
- **Sensitivity:** the frontier is recomputed with the staleness threshold at 0.5 s and 2 s, and
  with each attacker. A statement that does not survive these is reported as conditional on the
  setting it depends on.
- **Hypotheses:** each is reported as supported, not supported or undecided, with the numbers.

#### 10.9.7 Result

- **Charts:** per condition, stale share across and tracking success up, one point per policy
  with its confidence intervals, the frontier drawn through the undominated points, and the
  chance level as a line.
- **Table:** the frontier policies per condition, with both primary measures and their
  intervals.
- **Statements:** of the form "with 4 vehicles at spread speeds, policy P gave tracking success
  T ± t at stale share S ± s; no tested policy was better on both", each followed by the limits
  of 10.13.
- **Test summary:** a document in `results/` in the form of the existing summaries, and the raw
  per-run results kept for re-analysis.

#### 10.9.8 What it can and cannot claim

The result is a comparison of countermeasures within the simulated conditions. It supports
statements of the form "policy P is better than policy Q on both privacy and safety in this
setup" and "this is what silence costs per unit of privacy here". It is the basis for choosing
what to examine further.

It is not yet a recommendation to vehicle manufacturers. That needs evidence this harness
cannot produce:

| Missing | Why it matters | Further step |
|---|---|---|
| Radio channel | packet loss with distance and congestion adds to the gaps and changes the cost of silence | carry the policies into Artery (Vanetza with OMNeT++ and SUMO), which models the channel |
| Realistic traffic | privacy and receiver load depend on density, road networks and trips of hours | SUMO traffic on real road networks, at hundreds of vehicles |
| Attacker coverage | real tracking uses receivers at many places and over long periods | an attacker with receivers at a chosen share of intersections, scored over whole trips |
| Safety applications | stale information is not the same as a warning that came too late | a reference application (e.g. forward collision or intersection warning) and lost warning lead time |
| OBU hardware | CPU times on a VM do not carry over | per-operation times measured on an OBU or its security module |
| Validation | the setup has not been shown to reproduce known results | reproduce one or two published mix-zone or silent-period results; independent review |

### 10.10 Metrics catalogue

The review of design draft 0.2 suggested a catalogue of metrics in five groups. This section
analyses each against what the harness measured and what this design adds, and records the
decision. Status at the review: exists (measured), derivable (from data runs already saved,
analysis only), new (needs a component of 10.11), deferred (outside the current scope; 10.10.3).

#### 10.10.1 Analysis of the suggested metrics

| Group | Metric | Status and how it is measured | Decision |
|---|---|---|---|
| Privacy | Tracking success ratio | exists in part: `score.json` counts correct and wrong links. The ratio needs the number of changes made, from the stations' `[PSEUDONYM]` lines: correct links / changes made | adopted; the primary privacy measure of the joint experiment (10.9.3); in `overhead.json` (phase 1) |
| | Mean / maximum tracking time and distance | exists in part: `followed_changes` per track and the longest chain. Time is the span of a chain of consecutive correct identities, from frame times; distance needs the true positions | adopted: time and distance followed per track, mean and maximum per run (phase 1) |
| | Pseudonym lifetime / change frequency | derivable: times of the `[PSEUDONYM]` changes per station; with mix zones the lifetime varies and is reported as a distribution | adopted; also checks that a policy changes as often as it should |
| PKI / privacy | Pseudonym pool consumption | exists in part: `pki.batches`, `pki.added`, `pki.unused`, `pki.starved` per station. Add certificates used per hour against the batch size, and refill requests per hour | adopted; certificates per vehicle and hour join the cost measures |
| | Authority overhead | exists: provisioning latency (`refresh_ms_mean`, `refresh_ms_max`), PKI work and queueing, certificates issued (`pki-issued.jsonl`). Storage on the vehicle: from sizes (10.5.4) | adopted; storage as `pool_bytes` |
| | Revocation cost (CRL size, time until revocation takes effect) | not possible: the run's PKI issues no CRL and the stations check none (10.2.3) | deferred (10.10.3) |
| Safety | Packet loss and awareness: delivery ratio, inter-packet gap | derivable: from `messages.jsonl` and the stations' tx events. On the lossless link loss comes only from the change and the silent period | adopted (10.6.1) |
| | Silent-period duration: lost warnings, TTC violations, collision risk | silent time exists (`silent.share`); lost warnings: warning probe; TTC: computed by the analysis from true positions, since Vanetza-NAP has no collision module | adapted: lost warnings by the probe; TTC violations as conflict exposure (10.6.4); collision risk not estimated |
| | Neighbour-table / cooperative-perception disruption: identities lost per change, position-tracking error | derivable: every change ends one neighbour entry at every receiver; history lost, ghost time and position error (10.6.1). The scenarios send CAMs only, no CPMs | adopted for CAMs as "history lost"; CPM out of scope until scenarios send CPMs |
| Security | Cryptographic overhead: signing and verification latency, message size | signing and verification counts and times: station counters (10.5.1); message size: frame lengths and signer kind (10.5.4) | adopted, with key, certificate and signature sizes |
| | Misbehaviour detection rate under identity changes | not possible: no station misbehaves and none runs misbehaviour detection | optional stage (10.10.3) |
| Cost | Change synchronization ratio | derivable: from the change times of all stations, the share of changes with at least one other vehicle changing within 2 s, or with overlapping silent periods | adopted; reported under privacy (it is what a mix zone aims at) and cost (simultaneous chain checks at receivers) |

The review also asked that the measures of 10.2.2 be kept and collected automatically for every
run; 10.7.1 does so.

#### 10.10.2 Where each group is used

| Group | In the joint experiment | In every run |
|---|---|---|
| Privacy | primary: tracking success ratio; secondary: time and distance followed, synchronization ratio | `score.json`, `overhead.json`, Results tab, report |
| PKI | cost: certificates per vehicle and hour, pool storage | `metrics.json`, report |
| Safety | primary: stale share; hard limit: lost warnings; secondary: conflict exposure, delivery ratio, gap | `overhead.json`, `metrics.json`, Results tab, report |
| Security | cost: CPU per change, chain checks, security bytes per message | `overhead.json`, `metrics.json`, report |

#### 10.10.3 Deferred and optional metrics

- **Revocation cost:** needs CRL issuance in the run's PKI, distribution to the vehicles over
  the control channel, and CRL checking in the stations (a station patch). It could then measure
  CRL size against revoked vehicles, distribution time, and the time from revocation to the last
  accepted message. One design point worth measuring: whether a revoked vehicle costs one CRL
  entry per certificate or one per vehicle, which depends on how the PKI links a vehicle's
  certificates. Outside the current scope (3.2); proposed as a separate design.
- **Misbehaviour detection:** needs a vehicle that sends false information, for example a
  mobility option that reports a shifted or jumping position for chosen vehicles, and a
  detector. A detector of plausibility checks (position jumps, speed against distance travelled,
  position off the road) can run in the analysis on `messages.jsonl` as a reference receiver,
  without a station change. Measures: detection rate and time to detection, with and without
  pseudonym changes. A change starts a new history for the detector, so evidence collected under
  the old identity is lost; this is the privacy and safety conflict the metric exposes. Proposed
  as an optional stage C of the joint experiment (phase 8).
- **Cooperative perception:** disruption of CPM object tracking needs scenarios that send CPMs.
  The history-lost measure carries over unchanged once they do.

#### 10.10.4 Answers to the review of design draft 0.2

| Comment on | Answer | Where |
|---|---|---|
| Already measured: collect these automatically | the check metrics existed only when a check was run, over its window; the service kept the verdict | 10.7.1: `metrics.json` over the whole run, written when a run ends |
| Key derivation: also the size of the keys | agreed | 10.5.4 |
| Chain check: also the size of the certificates | agreed | 10.5.4 |
| Sizes of signatures, for storage | agreed; signatures are on the air, certificates and keys in storage | 10.5.4 |
| Cache misses: do vehicles ask the PKI to validate certificates? | no. Receivers check AT → AA → root locally against the configured root and AA. Certificates are per run and trusted for its duration; there is no revocation | 10.2.3 |
| CPU of each container | agreed; each station is a container and its cgroup is read | 10.5.1 |
| Reception gap: the receiver cannot relate identifiers | correct. The gap is computed afterwards by the analysis with ground truth; the receiver experiences it as a vehicle vanishing and an unknown one appearing | 10.6.1 |
| TTC violations; Vanetza has no collision module | correct. TTC is computed by the analysis from true positions | 10.6.4 |
| Misbehaviour detection needs a misbehaving vehicle | agreed; together with a detector | 10.10.3; phase 8 |
| Outputs: how can this be automated? | in the existing result collection, plus `vnapctl collect` for command-line runs and the batch runner | 10.7.1 |

### 10.11 Components

| Component | Place | What | State |
|---|---|---|---|
| Position recorder | `sim/images/mobility/` | every published position of every vehicle, with time, kept with the run's results (`positions.jsonl`) | phase 1 |
| Disruption analysis | `sim/vnapsim/overhead.py` | reads a results directory (`messages.jsonl`, positions, station logs) and writes `overhead.json` | phase 1 |
| Run metrics and collection | `sim/vnapsim/collect.py`, `sim/vnapsim/cli.py` | `metrics.json` over the whole run from saved logs; `vnapctl collect` and `vnapctl overhead` | phase 1 |
| Privacy metrics | `sim/vnapsim/overhead.py` | tracking success ratio, time and distance followed, pseudonym lifetime, synchronization ratio | phase 1 |
| Sizes | `sim/vnapsim/overhead.py` | sizes and storage (10.5.4) | phase 1 |
| Verification collector | `sim/images/collector/`, `sim/vnapsim/receivers.py` | records received CAMs and DENMs with security reports at each receiver | phase 2 |
| Warning probe | scenario format (`[probe]`), collector | DENMs at random times; delivery per receiver | phase 2 |
| Conflicts | `sim/vnapsim/receivers.py` | TTC, conflict exposure and missed conflicts (10.6.4) | phase 2 |
| Station counters and timing | `patches/vanetza-nap/`, `sim/vnapsim/counters.py` | change timing line; counts and times of signatures, verifications, chain checks, unknown signers, derivations | phase 4 (`vnap:r2-p20`) |
| CPU sampler | harness | cgroup CPU time of each station container, about 10 times a second | phase 3, not built |
| Sweep scenarios | `sim/scenarios/` | baseline and variants of 10.4 | phase 3, not built |
| Metrics and report | `sim/vnapsim/check.py`, `service/vnapapi/report.py` | 10.7.2 | phases 1, 2; the counts in phase 5 |
| Batch runner | shared with the adversarial agents design | one scenario over a seed list with overrides | not built |
| Guessing attacker | eavesdropper | links each new pseudonym to a random silent track | phase 6, not built |
| Joint analysis | `sim/vnapsim/frontier.py` | bootstrap intervals, frontier per condition, sensitivity, charts and the result table | phase 6, not built |
| 8-vehicle scenario | `sim/scenarios/` | the crossing with 8 vehicles | phase 7, not built |
| Misbehaving vehicle and detector (optional) | `sim/images/mobility/`, `sim/vnapsim/mbd.py` | false positions for chosen vehicles; plausibility checks on `messages.jsonl` | phase 8, not built |

### 10.12 Phases

| Phase | Content | Done when | Delivered |
|---|---|---|---|
| 1 | position recorder; `metrics.json` and `vnapctl collect`; disruption analysis from `messages.jsonl` (gap, inter-packet gap, delivery ratio, position error, stale time, ghost time, history lost, sizes); privacy metrics | on a run without a silent period, every gap is at most two CAM intervals; with a silent period, gaps match the logged silent periods | 2026-10-19 |
| 2 | verification collector; warning probe; conflict exposure | probe warnings during silent periods are reported lost or delayed, the others delivered | 2026-11-24 |
| 4 | station counters and timing (new image tag) | counts per change and refill in `overhead.json` | 2026-11-24 (`vnap:r2-p20`) |
| 3 | CPU sampler; baseline and sweep on the two scenarios, on `vnap:r2-p21` | cost-per-change slopes with their spread over seeds | next |
| 5 | metrics, report section, Results tab for the counts and CPU; a test summary in `results/` | the sweep reported in the form of the existing summaries | |
| 6 | joint experiment: guessing attacker, joint analysis, pilot | the seed count per cell is settled | |
| 7 | joint experiment: stages A and B, analysis, test summary | the frontier per condition and the hypotheses reported, with intervals and sensitivity | |
| 8 (optional) | misbehaving vehicle and detector; stage C: the frontier policies with one misbehaving vehicle | detection rate and time to detection reported with and without changes | |

Phase 4 was moved ahead of phase 3, so that the sweep runs on one image with the counters. The
phase 4 run then showed every CAM carrying the full certificate, which led to STA-1 and STA-4
(`vnap:r2-p21`); the sweep of phase 3 uses that image. Phases 6 and 7 can start after phase 3.

### 10.13 Limits

| Limit | Effect | How to read the results |
|---|---|---|
| Lossless link | no packet loss, congestion or range; one reference receiver hears what all hear | measured disruption is the part caused by the change, a lower bound of what a road would show |
| Shared CPUs | times depend on the host and the other containers | compare variants on one host; carry over through the operation counts |
| Few vehicles | receiver load from simultaneous changes grows with traffic density | include a run with 6 to 10 vehicles once the measures work |
| Neighbour table model | socktap keeps no table like a safety application would | ghost time is computed under a stated timeout, not observed |
| Position belief model | receivers do not estimate positions themselves | the two variants (last known, extended) bracket what an application might do |
| No safety application | no station computes TTC or issues collision warnings | conflict exposure is computed from true positions; it measures missing information, not crashes avoided |
| Centre-line driving | vehicles in opposite directions on one arm pass through each other | conflict counts include those passes |
| CAM rate of older images | up to `vnap:r2-p20` CAMs were sent every second whatever the vehicle did | results of those images are compared only with each other (9) |
| No revocation, no misbehaviour | certificates are trusted for the run; no vehicle lies | revocation cost is not measured; misbehaviour detection only in the optional stage |

### 10.14 Open questions

- The staleness threshold: 1 s, or per application (collision warnings may need much fresher
  information).
- Whether warnings should be exempt from silent periods, or end them. This is a change to the
  station behaviour and a trade between privacy and safety, to be measured before it is decided.
- Which target hardware to use for carrying the operation counts over, and where its
  per-operation times come from.
- The neighbour-table timeout to assume for ghost time.
- Whether the sweep should also vary the CAM rate. With STA-1 the rate follows the vehicles'
  dynamics; what remains is whether to vary speeds and `[cam] periodicity` (T_GenCamMax).
- The choice of primary measures for the joint experiment (10.9.3). They should be agreed before
  the pilot, because changing them afterwards weakens the result.
- Whether several run instances can run in parallel on the host; stages A and B take about two
  days one after another.
- Whether the host carries 8 vehicles with a PKI, an eavesdropper and the collectors.
- The TTC threshold for conflict exposure (4 s), and whether conflict exposure should replace
  stale share as the primary safety measure once the pilot shows how often conflicts occur.
- The window for the change synchronization ratio: 2 s, or overlapping silent periods only.
- Whether revocation and misbehaviour detection should get their own design, and whether the
  optional stage C is worth its runs before then.
