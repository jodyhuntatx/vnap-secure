# Test summaries

Results of the privacy scenarios: what the passive eavesdropper
(`sim/images/eavesdropper/`, see [eavesdropper and scoring](../docs/functional/eavesdropper-and-scoring.md))
could link across pseudonym changes, and measurements of certificate refill. Scenario files are
in `sim/scenarios/`. Each result names the station image it was measured with
(tags: [CHANGELOG](../CHANGELOG.md#station-image-tags)).

**CAM timing leak.** The eavesdropper results below, except the vnap:r2-p17 reruns and the
refill measurements (vnap:r2-p18 and later), were measured with images up to vnap:r2-p16, where
the CAM timer kept running through each ID change. A car's position within the 1 s CAM
cycle therefore carried over to its new identity. Matching on that timing alone linked 16 of
16 changes in the random-turn mix zone, so these results hold only against an eavesdropper
that ignores timing. vnap:r2-p17 restarts the CAM timer at a random phase on every ID change
(the same matching then linked 1 of 13). See [pseudonym change](../docs/functional/pseudonym-change.md#cam-timing).

## Documents

| Document | Scenario | Result |
|---|---|---|
| [convoy-test-2026-10-04.docx](convoy-test-2026-10-04.docx) | `c-its-pki-convoy`: three OBUs driving together, synchronized full ID changes, 3–13 s silent periods, 15 s link window | of 16 links, 11 joined two different cars; recommends further scenarios |
| [mixzone-test-2026-10-04.docx](mixzone-test-2026-10-04.docx) | `c-its-pki-mixzone`: four OBUs changing identity in an intersection mix zone, 6–12 s silent periods, 15 s link window | of 17 links, 15 joined two different cars, but all follow one rotation an attacker could undo; recommends random turns next |
| [mixzone-random-test-2026-10-04.docx](mixzone-random-test-2026-10-04.docx) | `c-its-pki-mixzone-random`: the same crossing with random turns and random return roads | of 18 links, 17 joined two different cars, spread over 10 pairings; the best fixed turn rule recovers 10 of 24 crossings; recommends a turn-aware attacker next |
| [mixzone-test-2026-10-04-r2-p17.docx](mixzone-test-2026-10-04-r2-p17.docx) | `c-its-pki-mixzone` rerun on vnap:r2-p17 (CAM timer rephase), eavesdropper linking by timing and position | 18 links, none correct; timing at chance level, but 19 of 19 position links (replay) form the same one-road rotation, so a pattern-aware attacker can still undo them |
| [mixzone-random-test-2026-10-04-r2-p17.docx](mixzone-random-test-2026-10-04-r2-p17.docx) | `c-its-pki-mixzone-random` rerun on vnap:r2-p17 | 12 links, none correct; timing at chance level and position mistakes without a pattern |
| [pki-refill-test-2026-10-07.docx](pki-refill-test-2026-10-07.docx) | `c-its-pki-refill-sweep` (batch sizes 4–32, four OBUs refilling together) and `c-its-pki-mixzone-pki` (random-turn mix zone with the run's own PKI) | refresh time ≈ PKI issue time (≈50 ms per ticket) × batch size × stations asking at once; no starvation, reuse or cross-run sharing; mix-zone links: 4 of 25 correct, timing at chance |

Each document has the setup, every link scored against ground truth, limitations and
recommendations for subsequent scenarios.

## Earlier results

Shorter runs without a separate document (moved here from the earlier single README).

- **Full ID change (default):** in c-its-pki-tracking every OBU pseudonym change also
  changes its MAC, GN address and `stationId`, so the eavesdropper sees a new vehicle each
  time. It still links all of them by position continuity: the simulated OBU never moves,
  and a vehicle that keeps sending its exact position is easy to follow. Unlinkability
  needs more than synchronized identifiers, e.g. silent periods or changes in mix zones.
- **Full ID change with a silent period** (`c-its-pki-tracking-silent`, 3–13 s):
  - **Default eavesdropper:** in a 2.5-minute run with 10 changes it linked none. It saw
    the one OBU as 8 separate vehicles, because each silence exceeded its 3 s link window.
  - **Patient eavesdropper:** one with `--link-window 15`, attached to the same run, linked
    every change again (gaps of 5–12 s). With a single stationary vehicle and nobody else
    around, waiting out the silence is enough.
  - **What this means:** a silent period protects only where other vehicles could be the
    one that reappears, i.e. in dense traffic or mix zones (TR 103 415 clauses 4.1.4–4.1.6).
  - **The cost:** the vehicle is invisible while silent; the RSU received 7 instead of 10
    OBU CAMs in a 10 s window.
- **Moving vehicles** (`c-its-pki-traffic`: two OBUs on crossing roads at 40 and 50 km/h,
  full ID change and 3–13 s silent periods):
  - **Default eavesdropper:** linked none of the changes; every new identity looked like a
    new vehicle (8 vehicles plus the RSU in the first run).
  - **Patient eavesdropper (`--link-window 15`):** linked every change of both vehicles
    (3 of 3 each) without mixing them up. It predicts where a silent vehicle reappears from
    its last speed and the length of the gap, and two vehicles on different roads are easy
    to tell apart. Movement alone is no protection; it would take vehicles that could
    plausibly have swapped places during the silence (a mix zone).
- **Certificate-only changes:** with `pseudonyms.id_change = "certificate"` it links every
  change through the unchanged MAC, GN address and `stationId`.
- **Certificate refill** (`vnap:r2-p18-test`, two OBUs changing every 5 s, refill at 2,
  batches of 8, the PKI returning the private keys):
  - **Refill:** each OBU received batches as it ran low. Every new AT passed the chain check,
    and the RSU verified the CAMs signed with them.
  - **No reuse:** each OBU used 17 distinct ATs, all from its own entries in `issued.jsonl`.
  - **Refresh time:** 454–971 ms, of which the PKI issued a batch of 8 in 376–533 ms. Both
    OBUs asked at the same moment, so one waited up to 537 ms behind the other at the PKI.
  - **PKI outage:** with the PKI stopped, requests were retried after 10 s and 20 s, and four
    changes were refused while the pool was empty. The first retry after the PKI came back was
    answered (refresh 31 170 ms, counted from the first unanswered request).
  - **Fixed pools:** without `PKI_REFILL_AT` the regression scenarios pass; the pool's unit
    test confirms that fixed pools still wrap around.
  - **ThreadSanitizer:** the pool's own test passes clean, and a sanitized OBU running two
    refills reported nothing in the new code (8 reports, all inside the Zenoh library).
