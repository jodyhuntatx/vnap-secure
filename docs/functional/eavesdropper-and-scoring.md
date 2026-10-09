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
cd sim
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
