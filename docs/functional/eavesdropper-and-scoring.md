# Eavesdropper and scoring

`sim/images/eavesdropper/` is a passive listener playing an untrusted party that wants to
track vehicles. It joins only the message network with a raw socket (`NET_RAW`) and sees
nothing but the frames on the wire: no keys or trust store, no MQTT, no control channel. It
knows the public ETSI/IEEE formats.

## What it decodes

| Layer | Standard | Extracted |
|---|---|---|
| Ethernet | | source MAC |
| GeoNetworking | EN 302 636-4-1 | GN address (station type, MAC-derived ID), source position, speed, heading |
| Security header v2 | ETSI TS 103 097 V1.2.1 (own parser) | signer, generation time, ITS-AID |
| Security header v3 | IEEE 1609.2 / TS 103 097 V1.3.1 (OER, `asn1tools`) | signer, generation time, PSID |
| Signer certificate | same | HashedId8 digest, computed exactly as Vanetza does (SHA-256 over the canonical encoding); also issuer (AA), validity, permissions. Full certificates are sent about once a second |
| BTP | EN 302 636-5-1 | port, message type |
| Facilities | TS 103 900 (CAM R2) | `stationId` of any message; full CAM: `referencePosition`, speed, heading, vehicle size, station type |

The ASN.1 modules come from Vanetza-NAP (`sim/images/eavesdropper/asn1/`, with their source
note).

## Linking techniques

The eavesdropper groups messages into tracks (one per vehicle, as far as it can tell) and
tries to carry a track across each pseudonym change. A new certificate digest is a new
pseudonym; the first technique below that applies links it to an existing track.

| Technique | Evidence used | Option (scenario key) | Defeated by |
|---|---|---|---|
| **Shared identifiers** | the new pseudonym's frame carries a MAC, GN address or `stationId` already seen on the track | always on | full ID change: MAC, GN address and `stationId` change with the certificate (`id_change = "full"`, the default) |
| **Timing phase** | CAMs come at a regular interval. The track's last CAM generation times (security header, 1 ms resolution) give its interval (least-squares fit) and phase; extended over the gap, they predict when its next CAM would come. One silent track must predict the new pseudonym's first CAM within the tolerance, and no other within twice the tolerance | `--link-by timing`, `--timing-window` 20 s, `--timing-tolerance` 25 ms (`link_by`, `timing_window`, `timing_tolerance_ms`) | restarting the CAM timer at a random phase on every ID change ([pseudonym change](pseudonym-change.md#cam-timing)) |
| **Position continuity** | the new pseudonym appears where a track that went silent could have driven in the gap: within `--link-distance` plus last speed × gap; the nearest one wins | `--link-by position`, `--link-window` 3 s, `--link-distance` 50 m (`link_by`, `link_window`, `link_distance`) | a silent period longer than the attacker waits, with other vehicles nearby that could be the one that reappears (convoys, mix zones with random turns) |

- **Precedence:** shared identifiers link immediately. Timing beats position when it is
  unambiguous; its log entry then also says whether position agreed (`position pointed to
  another track` otherwise).
- **Confirmation:** timing and position links are confirmed only if the old identity stays
  silent for 1.5 message intervals, so two vehicles side by side are not merged.
- **Greedy:** each new pseudonym is decided on its own, the moment it appears. A wrong link uses
  up the old track, so the car that really owned it can no longer be linked.
- **Chance timing matches:** with random phases, a new pseudonym's first CAM sometimes lands
  within the tolerance of another car's prediction. In a 3-minute run with 4 cars that gave 1–2
  timing links, about half of them wrong. An attacker sees the same coincidences.
- **Logging:** every linked change is logged with its evidence:
  - `same mac`, `same gn`, `same station`;
  - `timing phase (<n> ms off after <k> CAM interval(s), next <m> ms)`;
  - `position continuity (<d> m, <s> s gap; ...)`.

  The summary line, `tracks.json` and `vnapctl status` count the links per technique.
- **Not used (yet):**
  - heading and turn statistics;
  - multiple hypotheses, or a joint assignment of all changes at a crossing;
  - vehicle attributes in CAMs (length, width, station type);
  - the timing of GeoNetworking beacons and other periodic messages;
  - RSSI or several receivers.

## Running it

```bash
cd ${VNAP_HOME}/sim
./vnapctl up c-its-pki-mixzone-random   # four cars, random turns, mix zone, eavesdropper
./vnapctl status                        # includes the eavesdropper's tracks and linked changes
./vnapctl check --expect 'eavesdropper.linked_changes==0'   # a privacy goal
docker logs -f eavesdropper
images/eavesdropper/run.sh [network] [name]                  # attach to any running simulation
python3 images/eavesdropper/eavesdropper.py --pcap capture.pcap --log-dir out/   # offline (needs asn1tools)
```

- **In scenarios:** an `[eavesdropper]` section adds it
  ([scenario format](../reference/scenario-format.md)).
- **Metrics:** `vnapctl check` exposes:
  - `eavesdropper.frames`, `.decode_errors`, `.tracks`, `.pseudonyms`, `.linked_changes`;
  - per technique, `.linked_by_identifier`, `.linked_by_timing` and `.linked_by_position`.
- **Re-analysis:** `eavesdropper.py --replay messages.jsonl --log-dir out/ --link-by position`
  reruns the linking on a saved run's frame log with other options (no `asn1tools` needed).
  Get the log with `docker cp eavesdropper:/logs - | tar -x`.

| Output | Content |
|---|---|
| `docker logs -f eavesdropper` | new tracks, linked pseudonym changes, periodic summary |
| `/logs/messages.jsonl` | every decoded frame |
| `/logs/events.jsonl` | track and linkage events |
| `/logs/tracks.json` | per track: identifiers, pseudonyms (first/last seen, certificate fields), last position, recent trail; rewritten every 5 s |

## Scoring against ground truth

`sim/vnapsim/scoring.py` compares the eavesdropper's conclusions with what really happened:
- **Ground truth:** which certificate each station actually used, from the stations' own logs
  (and, with a per-run PKI, its issue log).
- **When:** the service scores every run at stop (`score.json`) and shows the result live and
  on the *Results* tab. `GET /api/runs/<id>/eavesdropper` returns it.

| Measure | Meaning | How to read it |
|---|---|---|
| **Links** (correct / wrong, per technique) | each pseudonym change the eavesdropper linked; correct when both pseudonyms belong to the same vehicle | wrong links are the attacker's mistakes; correct ones are privacy failures |
| **Longest chain followed** | the most pseudonym changes through which one track followed one vehicle without a mistake (consecutive identities of the same vehicle) | the privacy result in one number: 0 means no vehicle was followed through any change |
| **Identities** (per track) | pseudonyms the eavesdropper put into the track | 1 means nothing was linked to it |
| **Purity** (per track) | share of the track's pseudonyms that belong to its most common vehicle | read with Identities: many identities at high purity means a vehicle was tracked; low purity means vehicles were mixed up (the mix zone worked); 1 identity is always 100 % |
| **Followed** (per track) | pseudonym changes through which this track stayed on one vehicle | purity can overstate tracking: a track alternating between two cars has 50 % purity but follows neither |

## Related

- **Results:** what the eavesdropper achieved against each scenario, with recommendations for
  further scenarios, is in [results](../../results/README.md).
- **Countermeasures:** the countermeasures it is measured against are described in
  [pseudonym change](pseudonym-change.md).

## Summary report

When a run stops, the service writes `report.docx` into the run's results, in the form of the
test summaries in [results](../../results/README.md). Download it on the *Results* tab or with
`GET /api/runs/<id>/results/report.docx`.

| Section | Content |
|---|---|
| Header table | date and duration, run, mobility seed, station image, security and PKI, eavesdropper, health |
| 1. Purpose | the run's description |
| 2. Setup | every station with its role, movement and pseudonym pool; what triggered ID changes; the silent period, configured and measured; the eavesdropper's settings |
| 3. Results | pseudonym changes, mix zone entries, time silent, certificate refill, links (correct and wrong, per technique), tracks; then per vehicle, every link scored against ground truth, wrong links by vehicle pair, what each track contains, observations |
| 4. Conclusion | the result in a few sentences, with the chance level for comparison |
| 5. Limitations of this run | those that follow from the run's setup |
| 6. Suggested next runs | suggestions chosen by rule, see [below](#suggested-next-runs) |

- **Facts, and rules:** sections 1 to 5 state what was measured. Section 6 is chosen by fixed
  rules from those measurements, not by judgement; an insight that nobody has written down as
  a rule stays with the reader.
- **Without an eavesdropper** the report has the setup and the counts, and no linkage.
- **Pattern of the mistakes:** from 8 wrong links on, the report says how many of them one
  fixed rule ("old vehicle X is really new vehicle Y") explains. At 80 % or more an attacker
  who knows the traffic pattern could undo most of them.
- **Source:** only the files in the run's results (`run.json`, `status.json`, `score.json`,
  `scenario.toml`, the container logs). `run.json` therefore also records when and why the
  run stopped and its last check, and each link in `score.json` its evidence.
- **Again, or for an earlier run:** `python3 -m vnapapi.report <results directory>` in
  `service/` writes the report for the results in `data/results/<run id>`. A run from before
  this feature has no stop time in `run.json`; the last log line stands in for it, and the
  links have no evidence column.
- **If it fails**, the other results are kept and `report-error.txt` holds the reason.

### Suggested next runs

Section 6 of the report comes from the catalogue `service/recommendations.toml`. Each rule is
a fixed suggestion with conditions on the run's facts; the report prints the rules whose
conditions all hold, highest priority first, up to `limit` (6). The file is read each time a
report is written, so a change needs no restart.

```toml
[[rule]]
id = "pattern-reversible"
priority = 90
when = ["links.wrong>=8", "pattern.share>=0.8"]
title = "Break the traffic pattern."
text = "{pattern.explained} of the {links.wrong} wrong links ({pattern.share:.0%}) follow one fixed rule ..."
```

| Key | Meaning |
|---|---|
| `id` | a name of your own |
| `priority` | higher is printed first |
| `when` | conditions `fact OP value`, as in a scenario's `expect`: `OP` is one of `>= <= == != > <`, `value` a number or another fact. All must hold |
| `title`, `text` | the suggestion. `{fact}` inserts a fact; `{fact:.0%}` and `{fact:.1f}` format it. State the number that triggered the rule |

The shipped rules, by priority:

| Rule | Printed when | Suggests |
|---|---|---|
| `no-changes` | no vehicle changed its pseudonym | check the trigger |
| `pattern-reversible` | 8 or more wrong links, 80 % of them explained by one fixed pairing | random turns or uneven arrivals |
| `starved` | changes refused for lack of certificates | a larger `refill_at` or `batch` |
| `timing-leak` | 5 or more timing links, half or more correct and above chance | an image that rephases the CAM timer |
| `followed` | a vehicle followed through 3 or more changes in a row | a longer silent period, or a mix zone with `min_vehicles` |
| `above-chance` | 8 or more links, correct twice as often as guessing | more vehicles changing together, longer silence |
| `zone-threshold` | more mix zone passages without a change than with | a lower `min_vehicles`, or vehicles arriving together |
| `pool-wrap` | a certificate was used again | a larger pool, or refill |
| `no-eavesdropper` | changes, and no eavesdropper | an `[eavesdropper]` section |
| `no-silent-period` | changes without a silent period | a silent period |
| `window-too-short` | the silent period can exceed the eavesdropper's link window | a longer `link_window` |
| `one-technique` | the eavesdropper links by position or timing only | both |
| `silent-cost` | vehicles silent 30 % of the run or more | a sweep of the silent period |
| `repeat` | fewer than 10 kept runs of this scenario | more runs with other seeds |
| `few-vehicles` | 2 to 5 vehicles | six to ten |
| `equal-speeds` | all vehicles at one speed | different speeds |

Facts (all numbers; 0 where the run has no eavesdropper):

| Facts | Meaning |
|---|---|
| `duration_s`, `stations`, `vehicles`, `warnings` | length of the run, stations, stations with pseudonyms, warnings at stop |
| `changes`, `changes.rejected`, `changes.wrapped`, `changes.per_vehicle_min` | pseudonym changes; `wrapped`: back to a certificate used before |
| `silent.periods`, `silent.mean_s`, `silent.max_s`, `silent.share`, `silent.configured_max_s` | measured silent periods; `share` is the part of the vehicles' time (0 to 1) |
| `zone.count`, `zone.changes`, `zone.skipped`, `zone.min_vehicles` | mix zones: entries with a change event, passages without, largest threshold |
| `dynamic`, `dynamic.changes` | dynamic mix zone on (1), and its change events |
| `pki`, `pki.batches`, `pki.starved`, `pool.min` | the run's PKI, refill batches, changes refused for lack of certificates, smallest initial pool |
| `speeds.distinct`, `random_turns` | different speeds among the vehicles; vehicles with random turns |
| `repeats`, `repeats.seeds` | kept runs of the same owner with exactly this scenario text (this one included), and their different seeds |
| `eavesdropper`, `eavesdropper.link_window_s`, `eavesdropper.techniques` | present (1), its link window, and how many of position and timing it uses |
| `chance` | 1 / vehicles: how often a guess is right |
| `links`, `links.correct`, `links.wrong`, `links.correct_share`, `links.over_chance` | the score; `over_chance` is `correct_share` / `chance` |
| `identifier.*`, `position.*`, `timing.*` | per technique: `links`, `correct`, `correct_share` |
| `pattern.explained`, `pattern.share`, `pattern.pairings` | wrong links one fixed pairing explains, as a share of the wrong links, and the number of pairings |
| `followed.longest`, `tracks`, `tracks.mixed` | longest correct chain; vehicle tracks, and those joining several vehicles |

- **Checking a rule:** `python3 -m vnapapi.report --rules data/results/<run id>` in `service/`
  lists the run's facts and, per rule, `holds`, `no` with the conditions that failed, or
  `NOT USED` with the mistake (an unknown fact, a condition that cannot be parsed). It writes
  nothing, and exits with 1 if a rule is not used.
- **A rule with a mistake is never printed;** the report is still written.
- **Without the file** the report has no section 6.
- **Thresholds are starting values,** taken from the summaries in `results/`. One run is thin
  evidence, so each suggestion states its numbers.
