# VNAP-Secure Simulation Service

Product requirements: multi-user web service and per-run PKI with butterfly certificate refill.

| Item | Value |
|---|---|
| Document | Product requirements document (PRD), draft 0.4 |
| Date | 2026-11-26 (draft 0.1: 2026-10-07; draft 0.2: 2026-11-24; draft 0.3: 2026-11-25) |
| Owner | Joseph Hunt |
| Status | Phases 1 to 9 delivered, except the MCP endpoint of phase 9 (see [8. Delivery plan](#8-delivery-plan)). Draft 0.2 adds the station requirements of section 5.10 (STA-1 to STA-4); draft 0.3 merges the overhead measurement design (5.11, section 10), whose phases 1 to 6 are delivered; draft 0.4 makes the conflict exposure share the primary safety measure of the joint experiment (10.9.3) |
| Repositories | vnap-secure (harness, vnapctl, service, docs), vanetza-nap patch set in `patches/vanetza-nap/` (socktap patches), C-ITS-PKI (certificate tooling) |
| Source | `Specs/VNAP-Secure-Simulation-Service-PRD.md`; the PDF is generated from it (`make docs-pdf`) |

## Revision history

| Draft | Date | Changes |
|---|---|---|
| 0.1 | 2026-10-07 | first draft; phases 1 and 2 approved to start |
| 0.2 | 2026-11-24 | source moved to Markdown in the repository; state of delivery (2.3, 8); station requirements STA-1 to STA-4 (5.10): CAM generation by EN 302 637-2, current stationId in every message after an ID change, security operation counters, full certificate in the first CAM after a silent period; risk "CAM generation rate" (9) |
| 0.3 | 2026-11-25 | the overhead measurement design (`VNAP-Secure-Pseudonym-Change-Overhead-Design.docx`, design draft 0.3) merged as section 10, brought up to date with phases 1, 2 and 4 and with CAM generation by EN 302 637-2; requirements OVH-1 to OVH-6 (5.11); overhead phases in the delivery plan (8) |
| 0.4 | 2026-11-26 | after the pilot (10.9.9): the primary safety measure of the joint experiment is the conflict exposure share instead of the stale share, with both measures defined (10.9.3); seeds, sensitivity, charts and H4 follow; the open question on it is closed (10.14) |

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
| Measurement | eavesdropper scoring; per-run privacy and cost of the pseudonym changes (`overhead.json`, `metrics.json`); verification collector and warning probe; operation counts per change and refill; CPU time per station and the cost-per-change sweep (`vnapctl sweep`); all of them in the report and the Results tab (section 10, phases 1 to 5) |

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
| Overhead measurement (draft 0.3) | phases 1 to 8 of section 10.12: disruption analysis and run metrics (1); collector, warning probe, conflict exposure (2); CPU sampler and sweep (3); station counters (4); report and Results tab (5); joint experiment (6, 7); misbehaviour detection (8, optional) | the "done when" of each phase in 10.12 | phases 1 (2026-10-19), 2 and 4 (2026-11-24), 3 and 5 (2026-11-25), 6 (2026-11-26); phase 7 next |

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
- **Runner and plan:** `vnapctl sweep` with `sim/sweeps/overhead.toml`: the periodic
  variants as above, and the mix zone (`crossing`, where the laps set the rate, about one
  change a minute per car) with the three silent periods; the baseline turns changes off
  (`control.client.mode="manual"`, no mix zone). Per series of cells that differ only in the
  change rate, each measure is fitted against the rate per seed; the slope is the cost per
  change, given as the mean and standard deviation over seeds. Cells where the effect
  saturates are listed in the plan's `fit_exclude`: they stay in the table but not in the slopes.
- **Image:** `vnap:r2-p21` or later, so that CAMs follow the vehicles' dynamics (STA-1).
  Results of earlier images were measured at a fixed 1 s CAM interval and are not compared with
  later ones.

#### 10.4.1 Results of the first sweep (phase 3)

`sim/sweeps/overhead.toml` on `vnap:r2-p21`, 3 minutes per run, seeds 101 to 103: 51 runs
(39 periodic, 12 mix zone), 2026-11-25. Mean ± standard deviation over the three seeds:

| Cost per change | No silent period | 3–13 s | 6–12 s | Against |
|---|---|---|---|---|
| vehicle CPU, ms | 36 ± 58 | −282 ± 81 | −284 ± 144 | the vehicle's own change rate |
| RSU CPU, ms | 9 ± 16 | −112 ± 22 | −115 ± 40 | all vehicles' changes |
| PKI CPU, ms | 32 ± 3 | 49 ± 1 | 48 ± 2 | all vehicles' changes |
| RSU verifications | −0.3 ± 0.4 | −19.7 ± 2.7 | −20.1 ± 0.8 | all vehicles' changes |
| stale time, s | 0.01 ± 0.00 | 6.7 ± 0.4 | 7.3 ± 0.5 | the vehicle's own change rate |
| stale time in the mix zone, s | 0.00 ± 0.00 | 6.8 ± 1.2 | 8.4 ± 0.3 | two points per seed (baseline, cell) |

- **The change itself is below what the host resolves.** Without a silent period the vehicle's
  and the RSU's CPU per change cannot be told from zero. Between runs of one cell the vehicle
  CPU varies by 2 to 8 ms/s against a base of about 80 ms/s; at up to 12 changes a minute that
  bounds a change's CPU cost below about 150 ms. The receivers do no extra verifications. The
  per-change costs of signing, chain checks and derivation are the operation counts of phase 4,
  and they are what carries over to other hardware (10.5.3). Counted, a change of every vehicle
  costs a vehicle its ID change (0.4 ms), three chain checks (7.3 ms each) and a batch check
  (4 ms), about 26 ms; the RSU one chain check, about 7 ms. Both lie within the CPU slopes.
- **The PKI is measurable:** about 32 ms of CPU per certificate issued. It comes in steps with
  the refills: at one change a minute the pools of 4 last the 3 minutes and the PKI stays at its
  idle rate. That matches the issue time of about 50 ms per ticket in the refill test of
  2026-10-07.
- **The silent period dominates.** Each change with a silent period saves the RSU about 20
  verifications. That is the CAMs not sent, at about 2.3 a second during a mean silence of 8 to
  9 s. It also saves the vehicles CPU, and it leaves each receiver with stale information for
  about the silent period less 1 s. The negative CPU slopes are this saving; they do not show
  that a change is free.
- **Saturation:** at changes every 10 or 5 s the vehicles are silent most of the time (at 5 s
  with 6 to 12 s, almost always: stale 97% of the time, tracking 0). The staleness stops growing
  with the rate, so those four cells are left out of the slopes (`fit_exclude`).
- **Stale time to the end of the recording:** since 2026-11-26 the stale share runs from a
  vehicle's first CAM to the end of the recording. Before, a vehicle kept silent to the end was
  not stale after its last CAM: the 5 s, 6–12 s cell showed 0.2%. The time before its first CAM
  is start-up, not silence: in one run (interval 30 s, 3–13 s, seed 102) the vehicles came on
  the air 6 to 14 s after the recording started.
- **A disk stall seen only in the station logs:** in the mix zone, 3–13 s, seed 103, the first
  30 s are an outage (an NVMe timeout): the stations' log lines waited for the disk, so a
  change and its silent period were stamped at the end of the stall, while the radio kept going.
  Such stretches are found since 2026-11-26 (10.12, phase 6). The figures here are recomputed
  with both (`overhead.json` version 9).
- **Mix zone:** one change a minute per car gives too little range in the rate for the CPU
  slopes (vehicle −137 ± 145 ms without silence). Its stale time and tracking (81% of changes
  tracked without silence, 17 to 19% with) are the result, and its conflict exposure: about 2 s
  per run without silence, 26 to 29 s with it, and 8 to 9 conflicts per run entirely in stale
  time.
- **Test summary:** [`results/overhead-sweep-2026-11-25.docx`](../results/overhead-sweep-2026-11-25.docx)
  (phase 5), generated by `scripts/results/overhead-sweep-2026-11-25.py` from the sweep.
- **Pauses of the host:** the VM was paused three times during the sweep, for up to 80 min. The
  sleep of a run counts monotonic time, so the runs were still 180 s long, but CPU samples
  timed by the wall clock showed gaps of up to 4829 s. One run was affected and was run again.
  The sweep now marks a run with a gap of more than 2 s between CPU samples as failed and
  repeats it (`cpu.settings.gap_s_max`); the analysis leaves such runs out.

### 10.5 Computation load

#### 10.5.1 On the OBU

| Work | When | How it is measured |
|---|---|---|
| ID change handshake and switch | every change | `[IDCHANGE] completed in <us> us` at COMMIT (station counters, phase 4) |
| signing | every message; unchanged by a change | count and total time of messages signed and of ECDSA signatures (phase 4); signature size (10.5.4) |
| key derivation and key check | every refill, per certificate | `derivation_ms_mean`; count and time per refill (phase 4); key sizes (10.5.4) |
| chain check of new certificates | every refill, per certificate | `batch_check` count and time (phase 4); certificate sizes (10.5.4) |
| all of the above, and anything missed | the whole run | CPU time of the station's container, sampled from its cgroup about 10 times a second (CPU sampler in the collector, phase 3) |

Each station runs in its own container, so its CPU time can be read from its cgroup without
touching the station code: the collector reads each station's and the PKI's `cpu.stat`, from
their cgroup directories mounted read-only, and records them in `cpu.jsonl`. The rate is
reported per station over the run (`overhead.json` `cpu`, `metrics.json` `cpu.*`).

The cost of a change comes from the sweep (10.4), not from a window around each change, as
first proposed. A change costs a few milliseconds against about 60 ms of other work per second,
and when vehicles change together their silent periods lower every station's load at the same
moment: the first test run gave negative extra CPU in the windows after changes. Against a run
without changes on the same seed, the slope of the CPU rate over the change rate gives the net
cost per change: the work of the change less the verifications its silence saves.

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
- **Conflict exposure share:** conflict exposure over the time in conflict, both summed over
  all pairs; the primary safety measure of the joint experiment since draft 0.4 (10.9.3).

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
  `counters.py`, `cpu.py`): every change with its measures, the sizes of 10.5.4, the privacy
  metrics of 10.10.1 (tracking success, time and distance followed), the operation counts and
  the stations' CPU time;
- `sweep.json`, `sweep.txt` of a sweep: per cell the measures over seeds, per series the cost
  per change (`vnapctl sweep`).

A failure in the analysis is written to an error file and never loses the raw results. The
analysis reads only the results directory, so it can be rerun on stored runs
(`vnapctl collect --analyse`, `vnapctl overhead <dir>`), for example after a change to a
definition. The batch runner of the joint experiment uses the same files.

#### 10.7.2 Check metrics and presentation

`vnapctl check` metrics, so that a scenario can state limits:

| Metric | Meaning | State |
|---|---|---|
| `<vehicle>.change.ms_mean`, `.ms_max` | time of the change handshake on the vehicle | in `metrics.json` (phase 5) |
| `cpu.*`, `<station>.cpu.ms_per_s`, `.ms_per_s_p95` | CPU time of the stations and the PKI, ms per second; the cost per change is a slope over the runs of a sweep (`sweep.json`) | in `metrics.json` (phase 3) |
| `<station>.ops.*`, `ops.*` | operation counts and times, among them chain checks and unknown signers caused by other vehicles' changes | in `metrics.json` (phase 4) |
| `<vehicle>.gap_ms_max`, `.gap_ms_mean` | reception gaps around the vehicle's changes, at the reference receiver | in `metrics.json` (phase 1) |
| `<vehicle>.stale_share` | share of time the latest CAM of the vehicle was older than the threshold | in `metrics.json` (phase 1) |
| `<vehicle>.pos_error_m_p95`, `pos_error_m_p95` | 95th percentile of the position error during gaps | in `metrics.json` (phase 1) |
| `denm.*`, `verify.*` | warning probe and verification results | in `metrics.json` (phase 2) |
| `<vehicle>.ipg_ms_p95`, `.ipg_ms_max`, `<vehicle>.delivery_ratio` | inter-packet gap and delivery ratio of the vehicle's CAMs | in `metrics.json` (phase 1) |
| `conflict.*` | conflict exposure and missed conflicts (10.6.4) | in `metrics.json` (phase 2) |
| `msg.cert_bytes`, `<vehicle>.pool_bytes`, `msg.cam.bytes_mean` | sizes and storage (10.5.4) | in `metrics.json` (phase 1) |

The reception metrics are named by the sending vehicle: the reference receiver hears every frame on
the lossless link, so what it misses of a vehicle every receiver misses.

- **Summary report:** section 4 of `report.docx`, *Cost of the pseudonym changes*, and facts
  for `recommendations.toml` (rules such as `warnings-lost`, `unexplained-gaps`,
  `silence-without-privacy`, `unknown-signers`). Section 4.6, *Computation*, has the operation
  counts and the CPU time per station (phase 5).
- **Results tab:** the per-run figures next to the privacy result, and under *Computation* the
  operation counts and the CPU time per station, with a line of it per second (phase 5).

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
| Safety | conflict exposure share: share of conflict time (true TTC below 4 s) in which a vehicle's information about the other was stale or showed no conflict | stale share; conflict exposure in seconds; conflicts missed; position error during gaps (95th percentile, extended variant); reception gap; delivery ratio; warning delay |
| Hard limit | no lost warnings outside what the silent period itself explains | warning loss rate |
| Cost | reported, not optimised | chain checks per receiver and minute; CPU per change; certificates per vehicle and hour; pool storage; security bytes per message |

**The two safety measures.** Both describe what a pseudonym change's silent period costs the
other road users. They differ in which silent time they count.

- **Stale share.** For each vehicle, the share of time its latest CAM, as the reference
  receiver heard it, is older than 1 s (the longest CAM interval of EN 302 637-2, so in normal
  operation it is never exceeded). It runs from the vehicle's first CAM to the end of the
  recording; time in outages of the host is left out. The run's value is the mean over the
  vehicles. Almost all of it is silent periods: about changes × (silent period − 1 s), the
  check of H2. It is simple, close to the policy, independent of the scenario's geometry, and
  stable (in the pilot an sd over seeds of 0.7 to 1.1 points on 6 to 22 %). It counts every
  silent second the same, wherever the vehicle is and whoever is near: a second on an empty
  road weighs as much as one while two vehicles approach the same crossing.
- **Conflict exposure** (10.6.4). From the recorded true positions (5 times a second), every ordered
  pair of vehicles is checked: a *conflict* is a moment where their true time to collision
  (both moving on at constant velocity until their centres are within 4 m) is below 4 s, the
  range in which forward-collision and intersection warnings act. A moment of conflict is
  *exposed* when the first vehicle's information about the other is stale (latest CAM older
  than 1 s) or shows no conflict (the latest CAM, extended by its speed and heading, gives no
  TTC below 4 s). The **conflict exposure share** is exposed time over conflict time, both
  summed over all pairs; *conflict exposure* is the exposed time in seconds and *conflicts
  missed* the conflicts that lay entirely in stale time. It counts only the silence that matters,
  and with it where a policy goes silent, not only how much. Its limits: it depends on the
  scenario's geometry and speeds and on the 4 s threshold; a vehicle's information is taken
  from the CAMs the reference receiver heard, which without a channel model is close to what
  that vehicle received but not the same; and it measures situational awareness, not a
  warning that came too late. Even without changes it is not 0 (1.8 % in the pilot's
  baseline): an extended CAM misses a turn.

**Decision (draft 0.4, before stage A).** The design first declared the stale share primary
and kept conflict exposure secondary until the pilot showed how often conflicts occur. The
pilot (10.9.9) had conflicts in every run (about 120 s of conflict time per run), and it showed
the stale share misjudging the kind of policy this experiment exists to compare: the mix zone
had a third of periodic-30's stale share, yet the same conflict exposure share (0.21 and 0.21),
because it goes silent a quarter as often but exactly where the vehicles meet. The primary
safety measure is therefore the conflict exposure share. The share rather than the seconds,
because it does not grow with the number of conflicts a run's random turns produce (in the
pilot its spread over seeds was 26 % of the mean against 25 to 41 % for the seconds) and it
compares across the conditions of stage B, whose vehicle counts produce very different numbers
of conflicts. The stale share stays as a secondary measure and as a sensitivity frontier, so
results can be compared with work that uses it. The change was made after the pilot and before
any stage A run, and is recorded here for that reason.

The guessing attacker links every new pseudonym to a random silent track. Replayed on the same
frames, it gives the chance level of each run empirically, instead of the formula 1 / vehicles,
which ignores how many vehicles were actually silent at the moment.

#### 10.9.4 Hypotheses

| Id | Hypothesis | What it would show |
|---|---|---|
| H1 | A silent period lowers tracking success to chance level only when several vehicles change together (mix zone with `min_vehicles` 2, dynamic mix zone); with periodic triggers it adds stale time without that effect | where silence is worth its cost |
| H2 | Stale share is close to changes per second × (mean silent period + two CAM intervals) | a check that the measurement works; a large deviation means something else delays the first message |
| H3 | Warning loss is close to the share of time spent silent | a check of the probe |
| H4 | A dynamic mix zone reaches the tracking success of a fixed mix zone at a lower conflict exposure share | whether the dynamic zone is the better policy |
| H5 | The policies on the frontier differ between 2, 4 and 8 vehicles | whether a single recommendation can hold across densities |

#### 10.9.5 Runs

- **Run length:** 5 minutes, so that a vehicle changing every 30 s makes about 10 changes and
  chains of several changes are possible.
- **Pilot:** two policies and the baseline, 10 seeds each, at 4 vehicles. It checks the
  pipeline and measures the spread of the primary measures over seeds.
- **Seeds:** chosen from the pilot so that the 95 % confidence interval of tracking success is
  at most ± 0.1 wide, for the strongest attacker and for each attacker alone, and that of the
  conflict exposure share at most ± 0.05: 12 per cell (10.9.9).
- **Stage A, screening:** all 15 policies and the baseline at 4 vehicles with spread speeds:
  192 runs at 12 seeds, about 18 hours one after another.
- **Stage B, conditions:** the policies on or near the stage A frontier (at most 5) and the
  baseline, under all 6 combinations of vehicles and speeds: up to 432 runs, about 40 hours.
- **Runner:** `vnapctl sweep` (phase 3), shared with the adversarial agents design: a plan of
  scenario variants over a seed list, one results directory per run, resumable.

#### 10.9.6 Analysis

- **Unit:** the run. Links within a run are not independent, so confidence intervals come from
  resampling runs (bootstrap over seeds), not links.
- **Frontier:** a policy is dominated if another policy is better on both primary measures and
  the difference on at least one exceeds its confidence interval. The frontier is reported per
  condition.
- **Hard limit first:** a policy that loses warnings beyond its silent time is reported, but
  excluded from the frontier.
- **Sensitivity:** the frontier is recomputed with the conflict exposure share at a TTC
  threshold of 3 s and 5 s and a staleness threshold of 0.5 s and 2 s, with the stale share in
  its place, and with each attacker. A statement that does not survive these is reported as conditional on the
  setting it depends on.
- **Hypotheses:** each is reported as supported, not supported or undecided, with the numbers.

#### 10.9.7 Result

- **Charts:** per condition, conflict exposure share across and tracking success up, one point per policy
  with its confidence intervals, the frontier drawn through the undominated points, and the
  chance level as a line.
- **Table:** the frontier policies per condition, with both primary measures and their
  intervals.
- **Statements:** of the form "with 4 vehicles at spread speeds, policy P gave tracking success
  T ± t at conflict exposure share E ± e; no tested policy was better on both", each followed by the limits
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

#### 10.9.9 Results of the pilot (phase 6)

`sim/sweeps/joint-pilot.toml` on `vnap:r2-p21`: the baseline (no changes), changes every 30 s
(`periodic-30`) and the crossing's mix zone with at least 2 vehicles (`mixzone-min2`), all with
3–13 s silent periods; 4 vehicles at 30, 37, 43 and 50 km/h, 5 minutes per run, seeds 201 to
210: 30 runs, 2026-11-25 and 26. `vnapctl frontier` on the sweep, mean [95 % interval]:

| Policy | Changes per run | Strongest attacker | Tracking success | A0 alone | Chance (guessing) | Conflict exposure share | Stale share | Unexplained warnings |
|---|---|---|---|---|---|---|---|---|
| periodic-30 | 40 | A0 | 0.51 [0.46, 0.55] | 0.51 | 0.23 [0.22, 0.23] | 20.5 [17.1, 23.2] % | 22.3 [21.7, 22.9] % | 0 |
| mixzone-min2 | 10 | guessing | 0.41 [0.40, 0.41] | 0.20 [0.10, 0.30] | 0.41 [0.40, 0.41] | 20.6 [17.6, 23.9] % | 6.3 [5.9, 6.7] % | 0 |
| baseline | 0 | – | – | – | – | 1.8 [1.6, 2.1] % | 0.0 % | 0 |

The analysis was first run with the stale share as the primary safety measure, as declared;
the figures here are with the conflict exposure share, the primary measure since draft 0.4
(10.9.3).

- **Seeds: 12 per cell.** For tracking success within ± 0.1 (95 %), the most spread measure
  sets the count: A0 in the mix zone, sd 0.17 over seeds (10 changes a run, so a run's success
  moves in steps of 0.1). The conflict exposure share (sd 0.05) needs 5 for ± 0.05, the
  strongest attacker alone 3. Stage A is then 16 × 12 = 192 runs (about 18 hours), stage B up
  to 6 × 6 × 12 = 432 (about 40 hours), before runs repeated for host pauses.
- **Frontier: both policies.** The mix zone tracks less (0.41 against 0.51, beyond chance) at
  the same conflict exposure share, but is not better on it, so neither dominates the other.
  The frontier depends on the setting:

  | Sensitivity | periodic-30 | mixzone-min2 | Frontier |
  |---|---|---|---|
  | primary (TTC 4 s, stale 1 s) | 20.5 % | 20.6 % | both |
  | TTC 3 s | 20.0 % | 24.2 % | both |
  | TTC 5 s | 21.0 % | 18.0 % | mix zone |
  | stale threshold 0.5 s | 27.0 % | 29.2 % | both |
  | stale threshold 2 s | 18.2 % | 14.1 % | mix zone |
  | stale share instead | 22.3 % | 6.3 % | mix zone |
  | A0 alone (tracking 0.51 / 0.20) | | | both |
  | guessing attacker alone (0.23 / 0.41) | | | periodic-30 |

  Close to the collision (TTC 3 s) the mix zone leaves vehicles uninformed more often than
  periodic changes: its silence falls exactly where they meet. With an earlier warning range or
  a longer tolerance for old information it does better. A statement that the mix zone is the
  better policy at 4 vehicles is therefore conditional on the TTC threshold and the staleness
  tolerance, as 10.9.6 requires it to be reported.
- **The chance level matters.** In the mix zone A0 did worse than guessing: changes come in
  pairs at the crossing, so a guess among the two silent tracks is right about half the time,
  while A0's position links went to the wrong vehicle. The strongest attacker per policy keeps
  such a policy from looking better than it is.
- **Secondary measures:** per run, warnings lost 17.6 (periodic-30) against 4.0 (mix zone) and
  delayed 110 against 30, all explained by the silent periods. Conflict exposure in seconds is
  26.1 [19.8, 31.9] against 23.2 [19.9, 26.6] (baseline 2.2), with 3.5 and 2.3 conflicts
  entirely in stale time. The mix zone silences a quarter as often, but where the vehicles
  meet, so the stale share and the conflict exposure share rank the two policies differently;
  this led to draft 0.4 (10.9.3).
- **The host:** 9 of the 30 runs had to be repeated: 8 for pauses of the VM (gaps of 15 to 32
  min between CPU samples), one for a collector crash (`sleep length must be non-negative`,
  fixed). Two host effects showed in runs that completed, and the analysis now leaves them out
  (`overhead.json` version 9; version 10 adds the conflict exposure share at the sensitivity
  thresholds):
  - a 30 s NVMe stall (mix zone, seed 209) stamped a vehicle's change, its silent period and the
    end of it at the stall's end. The warnings it sent meanwhile looked lost outside a silent
    period (20 unexplained), which would have put the mix zone off the frontier. A silent period
    logged as over sooner than its length, right after 5 s or more without a log line, is now an
    outage seen by *station log*.
  - in one baseline run (seed 205) the vehicles came on the air 6 s after the RSU; warnings
    triggered before that were 2 to 3 s late. Start-up now runs per station: a warning before
    its sender or receiver has been heard for 2 s is left out.
- **Raw results:** `~/vnap-sweeps/joint-pilot` on the host (`frontier.txt`, `frontier.json`,
  `frontier-v4-spread.svg`).

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
| Safety | primary: conflict exposure share; hard limit: lost warnings; secondary: stale share, conflict exposure in seconds, delivery ratio, gap | `overhead.json`, `metrics.json`, Results tab, report |
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
| CPU sampler | `sim/images/collector/`, `sim/vnapsim/cpu.py` | cgroup CPU time of each station container and the PKI, 10 times a second | phase 3 |
| Sweep plan | `sim/sweeps/overhead.toml` | baseline and variants of 10.4 | phase 3 |
| Metrics and report | `sim/vnapsim/check.py`, `service/vnapapi/report.py` | 10.7.2 | phases 1, 2, 5 |
| Results tab | `service/ui/js/views/overhead.js` | the run's figures, timeline, changes, vehicles, receivers and computation | phases 1, 2, 5 |
| Test summary | `results/overhead-sweep-2026-11-25.docx`, `scripts/results/` | the sweep of 10.4.1 in the form of the other summaries | phase 5 |
| Batch runner | `sim/vnapsim/sweep.py` (`vnapctl sweep`), shared with the adversarial agents design | a plan of scenario variants over a seed list, resumable; slopes per seed | phase 3 |
| Guessing attacker | `sim/vnapsim/guessing.py`, replayed on the eavesdropper's frames | links each new pseudonym to a random silent track, 200 seeded times: the run's chance level (`overhead.json` `guessing`) | phase 6 |
| Joint analysis | `sim/vnapsim/frontier.py` (`vnapctl frontier`) | bootstrap intervals, frontier per condition, sensitivity, charts and the result table; seeds needed | phase 6 |
| Pilot plan | `sim/sweeps/joint-pilot.toml` | two policies and the baseline, 10 seeds, 4 vehicles at spread speeds, 5 minutes; results in 10.9.9 | phase 6 |
| 8-vehicle scenario | `sim/scenarios/` | the crossing with 8 vehicles | phase 7, not built |
| Misbehaving vehicle and detector (optional) | `sim/images/mobility/`, `sim/vnapsim/mbd.py` | false positions for chosen vehicles; plausibility checks on `messages.jsonl` | phase 8, not built |

### 10.12 Phases

| Phase | Content | Done when | Delivered |
|---|---|---|---|
| 1 | position recorder; `metrics.json` and `vnapctl collect`; disruption analysis from `messages.jsonl` (gap, inter-packet gap, delivery ratio, position error, stale time, ghost time, history lost, sizes); privacy metrics | on a run without a silent period, every gap is at most two CAM intervals; with a silent period, gaps match the logged silent periods | 2026-10-19 |
| 2 | verification collector; warning probe; conflict exposure | probe warnings during silent periods are reported lost or delayed, the others delivered | 2026-11-24 |
| 4 | station counters and timing (new image tag) | counts per change and refill in `overhead.json` | 2026-11-24 (`vnap:r2-p20`) |
| 3 | CPU sampler; baseline and sweep on the two scenarios, on `vnap:r2-p21` | cost-per-change slopes with their spread over seeds | 2026-11-25 (10.4.1) |
| 5 | metrics, report section, Results tab for the counts and CPU; a test summary in `results/` | the sweep reported in the form of the existing summaries | 2026-11-25 |
| 6 | joint experiment: guessing attacker, joint analysis, pilot | the seed count per cell is settled | 2026-11-26: 12 seeds (10.9.9) |
| 7 | joint experiment: stages A and B, analysis, test summary | the frontier per condition and the hypotheses reported, with intervals and sensitivity | |
| 8 (optional) | misbehaving vehicle and detector; stage C: the frontier policies with one misbehaving vehicle | detection rate and time to detection reported with and without changes | |

Phase 4 was moved ahead of phase 3, so that the sweep runs on one image with the counters. The
phase 4 run then showed every CAM carrying the full certificate, which led to STA-1 and STA-4
(`vnap:r2-p21`); the sweep of phase 3 uses that image. Phases 6 and 7 can start after phase 3.

### 10.13 Limits

| Limit | Effect | How to read the results |
|---|---|---|
| Lossless link | no packet loss, congestion or range; one reference receiver hears what all hear | measured disruption is the part caused by the change, a lower bound of what a road would show |
| Shared CPUs | times depend on the host and the other containers; between runs of one cell the vehicle CPU varies by 2 to 8 ms/s (10.4.1) | compare variants on one host; carry over through the operation counts |
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
- The TTC threshold for conflict exposure (4 s). Since draft 0.4 the conflict exposure share is
  the primary safety measure (10.9.3), and the pilot's frontier changed between 3 s and 5 s
  (10.9.9), so the threshold carries weight; 3 s and 5 s are reported as sensitivity.
- The window for the change synchronization ratio: 2 s, or overlapping silent periods only.
- Whether revocation and misbehaviour detection should get their own design, and whether the
  optional stage C is worth its runs before then.
