# Test summaries

Results of the privacy scenarios: what the passive eavesdropper
(`vnap-docker/eavesdropper/`, see the main [README](../README.md#eavesdropper-tracking-attacker))
could link across pseudonym changes. Scenario files are in `vnap-docker/scenarios/`.

## Documents

| Document | Scenario | Result |
|---|---|---|
| [convoy-test-2026-10-04.docx](convoy-test-2026-10-04.docx) | `c-its-pki-convoy`: three OBUs driving together, synchronized full ID changes, 3–13 s silent periods, 15 s link window | of 16 links, 11 joined two different cars; recommends further scenarios |
| [mixzone-test-2026-10-04.docx](mixzone-test-2026-10-04.docx) | `c-its-pki-mixzone`: four OBUs changing identity in an intersection mix zone, 6–12 s silent periods, 15 s link window | of 17 links, 15 joined two different cars, but all follow one rotation an attacker could undo; recommends random turns next |
| [mixzone-random-test-2026-10-04.docx](mixzone-random-test-2026-10-04.docx) | `c-its-pki-mixzone-random`: the same crossing with random turns and random return roads | of 18 links, 17 joined two different cars, spread over 10 pairings; the best fixed turn rule recovers 10 of 24 crossings; recommends a turn-aware attacker next |

Each document has the setup, every link scored against ground truth, limitations and
recommendations for subsequent scenarios.

## Earlier results

Shorter runs without a separate document (moved here from the main README).

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
