# Changelog

Features by date, station image tags, the mapping to the product requirements (PRD) phases,
and the history of this repository. The documentation in `docs/` describes only the current
behaviour; how it got there is recorded here.

## Station image tags

`vnap:latest` is the newest build. Tags mark images used for measurements, so results in
`results/` can name the image they were measured with.

| Tag | Built | Adds |
|---|---|---|
| `vnap:r2-p21` (= `latest`) | 2026-11-24 | CAM generation by EN 302 637-2 (heading, position, speed; at most 1 s); full certificate on the first CAM after a silent period |
| `vnap:r2-p20` | 2026-11-24 | DENMs carry the current stationId; security operation counters (`[COUNTERS]`, `[IDCHANGE] completed`) |
| `vnap:r2-p19` | 2026-10-07 | butterfly key derivation on the station (PKI returns offsets, not keys) |
| `vnap:r2-p18` | 2026-10-07 | certificate refill: pseudonym pool growing at runtime from the run's PKI |
| `vnap:r2-p17` | 2026-10-04 | CAM timer restarts at a random phase on every full ID change |
| `vnap:r2-p16` | 2026-10-04 | position control channel and CAM kinematics fixes (vehicle movement) |
| earlier `r2-p*` | 2026-09-28 – 10-03 | release2 patch set, pseudonym pool and events, thread-safety patches, ID change notification, silent periods |

Results measured with images up to `r2-p16` carry the CAM timing caveat described in
`results/README.md`: the CAM timer then kept its phase across ID changes. Images up to
`r2-p20` sent a CAM every second whatever the vehicle did, with the full certificate on every
CAM; use `CAM_GENERATION=fixed` on a newer image to reproduce their results.

## 2026-11-26: overhead design phase 6: guessing attacker, joint analysis, pilot

- **Guessing attacker** (`sim/vnapsim/guessing.py`): links each new pseudonym to a random silent
  track, 200 seeded times; its tracking success is the run's chance level (`overhead.json`
  `guessing`, summary `guess_tracking_success`). In the report's section 4.4 and the Results
  tab as *Chance level*; facts `cost.guess_*`
  ([chance level](docs/functional/eavesdropper-and-scoring.md#chance-level-the-guessing-attacker)).
- **Joint analysis:** `vnapctl frontier DIR`: per condition of a sweep, bootstrap intervals over
  seeds, the strongest attacker per policy, the warning hard limit, the frontier and its
  sensitivity (stale threshold 0.5 s and 2 s, each attacker alone), the secondary measures, a
  chart, and the seeds needed: by the most spread of the strongest attacker and each attacker
  alone ([vnapctl](docs/operations/vnapctl.md#joint-analysis)). The stale share at 0.5 s and 2 s
  is in the summary (`stale_share_0_5`, `stale_share_2_0`).
- **Pilot** (`sim/sweeps/joint-pilot.toml`, PRD 10.9.9): 30 runs. The seed count is settled at
  12 per cell. The mix zone (2 vehicles) is the frontier against periodic changes every 30 s:
  tracking 0.41 against 0.51, stale share 6% against 22%, but about the same conflict exposure.
- **Analysis changes** (`overhead.json` version 9; `vnapctl sweep --analyse` now recomputes older
  files, as `vnapctl frontier` does):
  - stale time runs from a vehicle's first CAM to the end of the recording: a vehicle kept
    silent to the end is stale after its last CAM, and the time before its first CAM is
    start-up;
  - a station log stamped late by a disk stall (a silent period logged as over sooner than its
    length, right after 5 s without a log line) is an outage, seen by *station log*;
  - warnings triggered before their sender or receiver had been heard for 2 s are left out as
    start-up (`during_startup`).

  The phase 3 figures change with these (PRD 10.4.1): stale time per change with 3–13 s 6.7 s
  (was 7.1), in the mix zone 6.8 s (was 7.1); mix zone conflict exposure 26 to 29 s per run
  (was 29). The [test summary](results/overhead-sweep-2026-11-25.docx) is regenerated.
- **Collector:** the probe loop crashed with `sleep length must be non-negative` when the clock
  passed the next send time between two reads; it reads the clock once now.

## 2026-11-25: overhead design phase 5: report, Results tab, test summary

- **Report** (`report.docx`): section 4.6, *Computation*: the ID change time, chain checks and
  unknown signers per change, signing and verifying, refills, and the CPU time per station.
  New facts `cost.ops_*`, `cost.cpu_*` and the rule `unknown-signers`
  ([facts](docs/functional/eavesdropper-and-scoring.md#suggested-next-runs)).
- **Results tab:** *Computation*, the same figures with a per-station table and a line of CPU
  time per second ([web UI](docs/operations/web-ui.md#privacy-and-cost-of-the-pseudonym-changes)).
- **Metrics:** `<vehicle>.change.ms_mean`, `.ms_max` (`metrics.json` version 5); the PRD's
  metrics table (10.7.2) now says where each is.
- **Test summary:** [results/overhead-sweep-2026-11-25.docx](results/overhead-sweep-2026-11-25.docx),
  generated from the phase 3 sweep by `scripts/results/overhead-sweep-2026-11-25.py`. Counted,
  a change costs a vehicle about 26 ms and the RSU about 7 ms on this host, within the CPU
  slopes. In the mix zone a silent period brought tracking from 81% to below 20% and conflict
  exposure from about 2 s to 29 s per run.
- **Host pauses:** `overhead.json` `cpu.settings.gap_s_max`; `vnapctl sweep` repeats a run whose CPU
  samples have a gap of more than 2 s, and its analysis leaves such runs out. A run counts as
  done once it has `sweep-run.json`.

## 2026-11-25: overhead design phase 3: CPU time and the cost-per-change sweep

- **CPU sampler:** the collector reads each station's and the PKI's cgroup `cpu.stat` 10 times a
  second (`cpu.jsonl`); `overhead.json` `cpu` and `metrics.json` `cpu.*` give ms of CPU per
  second per station, its 95th percentile over 1 s and a timeline (`sim/vnapsim/cpu.py`).
  `overhead.json` version 5, `metrics.json` version 4.
- **Sweeps:** `vnapctl sweep DIR --plan PLAN` runs scenario variants over seeds, resumable, and
  fits each measure against the change rate per seed (`sweep.json`, `sweep.txt`);
  `sim/sweeps/overhead.toml` is the plan of PRD 10.4 ([vnapctl](docs/operations/vnapctl.md#sweeps)).
- **Result** (51 runs on `vnap:r2-p21`, PRD 10.4.1): without a silent period a change's CPU cost
  on the vehicles and the RSU is below what this host resolves (vehicle 36 ± 58 ms, RSU
  9 ± 16 ms per change); the PKI's is about 32 ms per certificate. With a silent period of 3–13
  or 6–12 s, each change saves the RSU about 20 verifications and leaves receivers with stale
  information for 6.6 to 8.4 s. Cells where the silence saturates (changes every 10 or 5 s) are
  left out of the slopes.
- **Window per change dropped:** the CPU around each change, as first designed, came out
  negative, because synchronized silent periods lower everyone's load at once; the cost comes
  from the slope against a baseline without changes instead (PRD 10.5.1).

## 2026-11-25: overhead design merged into the PRD (draft 0.3)

`Specs/VNAP-Secure-Pseudonym-Change-Overhead-Design.docx` is now section 10 of
`Specs/VNAP-Secure-Simulation-Service-PRD.md`, and the `.docx` is removed (it is in the git
history; the reviewed copy `...-Overhead-Design-BS.docx` stays as the record of the review).
- **Brought up to date:** phases 1, 2 and 4 marked delivered, phase 4 recorded as moved ahead
  of phase 3; the components table has a state column; CAM generation by EN 302 637-2 and the
  certificate after the silent period in the description of a change, the sizes, the method
  and the joint experiment's fixed factors (`vnap:r2-p21` or later); the centre-line and CAM-rate
  limits; the CAM rate open question narrowed.
- **New requirements** OVH-1 to OVH-6 (PRD 5.11), and the overhead phases in the delivery plan.
- **PDF:** in the PRD's own PDF, links to other documents go to GitHub
  (`build-pdf.py --prd`).

## 2026-11-24: CAM generation by EN 302 637-2 (`vnap:r2-p21`)

The phase 4 run showed every CAM carrying the full certificate. The cause was the CAM rate:
socktap sent a CAM every `[cam] periodicity` ms (1000), and the certificate, attached at most
once a second, then went on every CAM.
- **CAM generation** ([patch](docs/patches/mobility-and-cam.md#cam-generation),
  [behaviour](docs/functional/vehicle-movement.md#cam-generation)): checked every 100 ms; a CAM
  when the heading changed by more than 4°, the position by more than 4 m or the speed by more
  than 0.5 m/s, otherwise after T_GenCam (at most 1 s). `--cam-generation dynamic` (default) or
  `fixed` (`CAM_GENERATION`, administrators only).
- **Certificate after the silent period**
  ([patch](docs/patches/pseudonym-and-id-change.md#silent-period)): the first run with dynamic
  generation showed the certificate requested at a change going with a CAM the silent period
  dropped. The first CAM of 20 of 32 new identities carried only a digest; receivers counted
  2.2 unknown signers per change and 82 failed verifications. The first CAM after a silent
  period now requests the certificate again.
- **Live check** (`crossing:periodic`, 4 vehicles, pools of 4 refilled by 4, about 3 min, 36
  changes each), against the same run on `vnap:r2-p20`:

  | | r2-p20 (1 s) | r2-p21 |
  |---|---|---|
  | CAM interval of the moving cars (median) | 1000 ms | 402 ms |
  | CAMs with the full certificate | 100 % | 36–37 % |
  | CAM size, mean (digest / certificate) | 322 B (– / 322) | 240 B (193 / 323) |
  | first CAM of a new identity with the certificate | 36 of 36 | 32 of 32 |
  | unknown signers per change, failed verifications | 0, 0 | 0, 0 |
  | messages verified per second and station | about 2.9 | about 5.8 |

  The RSU still sends every second. Tracking success, 61 % and 50 %, is one run each and not
  a comparison.
- **PRD draft 0.2:** the PRD now has a Markdown source,
  `Specs/VNAP-Secure-Simulation-Service-PRD.md`, from which `make docs-pdf` generates the PDF
  (`build-pdf.py --prd`; `make test` checks it is current); it is also in the documentation
  PDF. Draft 0.2 records the state of delivery and adds the station requirements STA-1 to STA-4
  and the CAM rate risk.
- **Results:** every result in `results/` was measured at 1 s CAMs; `results/README.md` says
  so.

## 2026-11-24: phase 4 of the overhead design: station operation counters (`vnap:r2-p20`)

Phase 4 of the overhead design (now section 10 of the PRD), moved ahead of phase 3 so
that one image (`vnap:r2-p20`, with the DENM stationId fix) serves every phase 3 measurement:
- **Station counters** ([operation counters](docs/patches/operation-counters.md)): count and
  total time of messages signed and verified, verification failures and unknown signers, ECDSA
  operations (backend-independent), chain checks of received ATs, batch checks and key
  derivations of refills, and ID changes. socktap logs them every second (`[COUNTERS]`,
  `--op-counters-interval`) and each committed change as `[IDCHANGE] completed in <us> us`.
- **Analysis** (`sim/vnapsim/counters.py`, `overhead.json` version 4): per change, the ID
  change time and the neighbours' chain checks, unknown signers and failures from the change to
  3 s after its silent period (shared equally where windows overlap); per refill, the
  derivations and batch checks; per station, totals and mean times. `metrics.json` (version 3)
  adds `ops.*` and `<station>.ops.*`. Not yet in the report or the Results tab (phase 5).
- **Live check** (`crossing:periodic`, 4 vehicles, pools of 4 refilled by 4, about 3 min,
  36 changes): 4.0 chain checks per change at 4 receivers, as expected (one per neighbour);
  0 unknown signers; 12 refills with 48 derivations and 48 batch checks. Times on this VM
  (aarch64, Crypto++): ID change 76 us mean (313 us max), signing 1.9 ms per message,
  verification 8.9 ms, chain check 8.4 ms, batch check 4.9 ms, key derivation 0.1 ms. A change
  thus costs its neighbours about 36 ms of chain checks in all, the vehicle well under 1 ms.
- **Caveat:** the `sign` count includes refused requests (e.g. before the station has a
  position); `ecdsa_sign` counts signatures made. v2 counts no chain checks.
- **Observation:** every CAM in these runs carries the full certificate (full-certificate
  share 100 %, also on the phase 2 runs). Explained and changed in `vnap:r2-p21` (above).

## 2026-11-24: phase 2 of the overhead design: what the receivers knew

Phase 2 of the overhead design (now section 10 of the PRD):
- **Collector** (`vnap-collector`, `sim/images/collector/`): with an eavesdropper or a
  `[probe]`, a helper on the message network subscribes to every station's embedded broker and
  records each received CAM and DENM with the station's security report
  (`collector-logs.tar`: `receptions.jsonl`). It starts before the control client, so the
  first change is covered. About 1 % CPU and 12 MB per hour for five stations.
- **Warning probe** (`[probe]`, in the collector): DENMs on the vehicles at random times (about
  one per `interval_s`), each repeated every `repeat_interval_s` for `repeat_duration_s` as the
  DEN basic service does (`probe.jsonl`). The station fills in its own position.
- **Analysis** (`sim/vnapsim/receivers.py`, `overhead.json` version 3): verification failures
  at the receivers, in the run and within 5 s after a receiver first hears a new identity; per
  DENM and receiver the delay of the first copy or its loss, whether the sender's silent period
  explains it, and DENMs that still carry the configured stationId after a change (identity
  leaks); conflict exposure from the recorded positions (true TTC below 4 s, centres within
  4 m) with missed conflicts.
- **`metrics.json` version 2:** the receivers' `<station>.<message>.rx`, `.success_rate`,
  `.failed` from the collector, as a live check has them, and `verify.*`, `denm.*`,
  `conflict.*`.
- **Report** section 4.5, **Results tab** figures and tables, rules `denm-id-leak`,
  `warnings-lost`, `warnings-delayed`, `warnings-unexplained`, `verify-failures`,
  `conflicts-missed`.
- **Done-when check** of the design, on `linkability:silent` (3–13 s silence, image
  `vnap:dev-denm`): of 42 probe DENMs, the 15 triggered in a silent period were delayed (by the
  silence left, rounded up to the next repetition), the 27 others arrived within 30 ms; none
  unexplained. On `crossing` (4 OBUs, 6–12 s silence in the mix zone): 572 deliveries, 476 on
  time, 84 delayed by up to 9 s, 12 lost where the silence outlasted the 10 s of repetitions;
  10 948 messages verified, none failed; 42 conflicts, 73 of their 126 s with stale or
  misleading information, 16 missed entirely.
- **Found on the way:** vanetza's JSON takes `TimestampIts` in seconds since 2004 (it scales by
  1000); milliseconds overflow the field and the station refuses the DENM.
- **Caveat:** the mobility client drives on the road's centre line, so opposite directions on
  one arm meet head-on in the TTC; conflict counts include those passes.

## 2026-11-24: DENMs carry the current stationId

Injected DENMs (`vanetza/in/denm`) were sent with the station's configured stationId in the
ITS PDU header, which stays the same across full ID changes. The eavesdropper links frames by
a shared stationId, so one DENM per pseudonym would have linked all of a vehicle's pseudonyms.
The DENM application now writes the current stationId into the header, and into
`management.actionId.originatingStationId` when the payload gives the configured one there
(`applications/denm_application.cpp`, a new file in the patch set). A payload with a top-level
`stationId` keeps upstream's behaviour.
- **Found** while preparing the warning probe of the overhead design (phase 2), which sends DENMs.
- **Checked** on `linkability` with 30 DENMs over 6 ID changes: before, every DENM carried the
  configured stationId 2; now every DENM carries the stationId of its GN address and
  certificate, and the RSU verifies all 30 (`Success`).
- **Earlier results are not affected:** no scenario or harness part sent DENMs, and none of the
  stored runs has a DENM frame.
- **Not yet fixed:** other injected messages (CAM, CPM, IVIM, VAM, …) still fall back to the
  configured stationId when their payload names none.
- **Image:** built as `vnap:dev-denm` for development; the next measurement tag (`vnap:r2-p20`)
  will also carry the station counters of phase 4.

## 2026-10-19: phase 1 of the overhead design complete

Phase 1 of the overhead design (now section 10 of the PRD):
- **Position recorder:** the mobility client writes every vehicle's true position 5 times a
  second to `positions.jsonl`, saved with the results.
- **`overhead.json` version 2** (`sim/vnapsim/overhead.py`), recomputed for stored runs on
  first use:
  - position error during reception gaps, last known and extended by speed and heading;
  - ghost time under a 1.1 s neighbour-table timeout;
  - history lost with the old identity;
  - CAM sizes with a digest and with the certificate, the certificate's size and pool storage;
  - time and distance the eavesdropper followed each vehicle;
  - per change, whether the silent period and the CAM timer explain its gap.
- **Outages:** stretches in which the receiver heard no station that should have sent, or the
  recorder stalled. They are listed, their changes marked, and their time left out of gaps,
  stale share and delivery. The 23 s unexplained gap in a stored run of 2026-10-12 was one: a
  restart of the host's systemd-networkd took the Docker bridges down. A run of 2026-10-19 had
  16 of about 30 s: the VM's virtual NVMe disk timed out (`nvme … timeout, completion polled`)
  and the eavesdropper, blocked writing its log, dropped frames.
- **Riding out stalls:** the eavesdropper and the mobility client keep `/logs` in memory
  (tmpfs), and the eavesdropper stamps frames with the kernel's receive time instead of the
  time it reads them.
- **`metrics.json`:** the `vnapctl check` metrics over the whole run from the saved logs, with
  CAMs sent and the per-vehicle cost figures.
- **`vnapctl collect DIR`** saves a command-line run's results like the service does, and
  `--analyse` makes the analyses again on saved results. The service and `vnapctl` share the
  collection (`sim/vnapsim/collect.py`).
- **Report:** section 4 shows the new figures and the outages; new rule `outages`.
- **Results tab:** the same figures, the outages, and new columns in the changes and vehicles
  tables.

## 2026-10-19: one change per vehicle from fixed and dynamic mix zones together

A run with both a fixed mix zone and a dynamic mix zone sent two change events to a vehicle
when both fired within a few seconds, typically a 50 m dynamic zone just before the car entered a 40 m
crossing zone. In a 30-minute `crossing` run with the dynamic zone added, 55 second events
were rejected by the station's `min_interval_ms`, and 50 of the 220 changes came within the
previous change's silent period. The mobility client now holds a vehicle off for the dynamic
zone's `cooldown_s` after a change event from either trigger, and logs the held-off one
instead of sending it (`station N held off (...)`).

## 2026-10-19: privacy and cost of the pseudonym changes, per run

This is the first iteration of the overhead design (now section 10 of the PRD). The
*Results* tab of a stopped run shows every pseudonym change with what the eavesdropper made of
it and what the receivers missed meanwhile:
- headline figures: tracking success, wrong links, synchronized changes, reception gap, stale
  share, delivery ratio, certificate overhead;
- a timeline per vehicle;
- sortable tables of changes and vehicles.

`sim/vnapsim/overhead.py` computes the figures from what every run already keeps (station logs,
the eavesdropper's frames, `score.json`, `status.json`) and writes `overhead.json`. It runs when
a run stops; for older runs it runs on the first request of `GET /api/runs/{id}/overhead`.
`vnapctl overhead <dir>` does the same for any results directory. See
[web UI](docs/operations/web-ui.md#privacy-and-cost-of-the-pseudonym-changes).

**Summary report:** `report.docx` has a new section 4, *Cost of the pseudonym changes*, with
the same figures, per vehicle and per outcome. Its list of the longest gaps marks each gap the
silent period and the CAM timer do not explain. Conclusion, limitations and suggested next runs
are now sections 5 to 7. The facts gain `cost.*`, and there are two new rules:
- `unexplained-gaps`: look into gaps that nothing explains;
- `silence-without-privacy`: silent periods that cost without protecting.

**Fix:** the scenario builder wrote `silent_min_ms = 0` and `silent_max_ms = 0` into every
vehicle. That overrode `[defaults] pseudonyms`, so scenarios edited in the builder lost their
shared silent period, and variants that set it had no effect. The builder now shows the
silent period for all vehicles and, per vehicle, writes only a value that is set there.
`make test-ui` runs unit tests of the builder's TOML.

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

`Specs/VNAP-Secure-Simulation-Service-PRD.pdf` (source: the `.md` next to it) plans the service in nine phases; draft 0.2 adds the station requirements, draft 0.3 the overhead measurement design (section 10):

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
| draft 0.2 | Station fidelity (STA-1 to STA-4): CAM generation, stationId in DENMs, operation counters, certificate after the silent period | 2026-11-24 |
| draft 0.3 | Overhead measurement (OVH-1 to OVH-6, section 10): phases 1 to 6 | 2026-10-19, 2026-11-24, 2026-11-25, 2026-11-26; phase 7 next |

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
