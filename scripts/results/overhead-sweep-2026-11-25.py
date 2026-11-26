#!/usr/bin/env python3
"""Write results/overhead-sweep-2026-11-25.docx, the test summary of phase 3 of the overhead design
(PRD 10.4.1), from the sweep's runs.

  scripts/results/overhead-sweep-2026-11-25.py ~/vnap-sweeps/overhead-r2-p21 [out.docx]

The tables come from the sweep (sweep.json, from `vnapctl sweep DIR --analyse`) and from each run's
overhead.json; the text states what they showed on 2026-11-25. Rerunning it on another sweep gives that
sweep's tables under this sweep's text, so copy it for a new summary.
"""

import glob
import json
import os
import statistics
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.realpath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "service"))

from vnapapi.report import write_docx  # noqa: E402

OUT = os.path.join(ROOT, "results", "overhead-sweep-2026-11-25.docx")


def mean(values):
    vals = [v for v in values if v is not None]
    return statistics.mean(vals) if vals else None


def per_cell(sweep_dir):
    """{(group, cell): [overhead.json of each seed]}, without runs the analysis left out."""
    with open(os.path.join(sweep_dir, "sweep.json")) as f:
        skipped = set(json.load(f).get("paused") or [])
    out = {}
    for path in sorted(glob.glob(os.path.join(sweep_dir, "*", "*", "seed-*", "overhead.json"))):
        rel = os.path.relpath(os.path.dirname(path), sweep_dir)
        if rel in skipped or not os.path.isfile(os.path.join(os.path.dirname(path), "sweep-run.json")):
            continue
        group, cell, _ = rel.split(os.sep)
        with open(path) as f:
            out.setdefault((group, cell), []).append(json.load(f))
    return out


def fmt(sp, scale=1.0, digits=1, unit=""):
    """mean ± sd of a sweep.json spread."""
    if not sp:
        return "-"
    sd = "" if sp["sd"] is None else f" ± {sp['sd'] * scale:.{digits}f}"
    return f"{sp['mean'] * scale:.{digits}f}{sd}{unit}"


def fnum(x, digits=1, unit=""):
    return "-" if x is None else f"{x:,.{digits}f}{unit}"


def main(sweep_dir, out=OUT):
    with open(os.path.join(sweep_dir, "sweep.json")) as f:
        sweep = json.load(f)
    groups = {g["group"]: g for g in sweep["groups"]}
    runs = per_cell(sweep_dir)
    total = sweep["runs"]

    def extra(group, cell, key, sub="summary", scale=1.0):
        return mean([((o.get(sub) or {}) if sub != "counters" else ((o.get("counters") or {}).get("summary") or {})).get(key)
                     for o in runs.get((group, cell), [])]) if (group, cell) in runs else None

    def conflicts(group, cell):
        return mean([(o.get("conflicts") or {}).get("exposure_s") for o in runs.get((group, cell), [])]), \
            mean([(o.get("conflicts") or {}).get("missed") for o in runs.get((group, cell), [])])

    b = [("title", "Cost of Pseudonym Changes: Test Summary"),
         ("subtitle", "Phase 3 of the overhead design (PRD section 10): CPU time, operations and stale information against "
                      "the change rate and the silent period, with a run without changes as the baseline"),
         ("table", ["Item", "Value"], [
             ["Date", "2026-11-25, 51 runs of 3 minutes, one at a time"],
             ["Plan", "sim/sweeps/overhead.toml (vnapctl sweep): seeds 101, 102, 103; every cell drives the same paths per seed"],
             ["Scenarios", "crossing:periodic (four vehicles changing together every 60, 30, 10 or 5 s) and crossing (the mix "
                           "zone, about one change a minute per car); an RSU; the run's own PKI with refill"],
             ["Silent periods", "none, 3-13 s (the SAE J2735 values in ETSI TR 103 415), 6-12 s"],
             ["Image", "vnap:r2-p21: CAM generation by EN 302 637-2, full certificate after the silent period, operation "
                       "counters"],
             ["Measurement", "the collector's CPU samples of every container (10 a second, from their cgroups), the stations' "
                             "operation counters, the eavesdropper as reference receiver and the true positions"],
             ["Host", "one VM, 4 vCPUs shared by all containers; paused three times during the sweep (one run repeated)"],
             ["Design", "Specs/VNAP-Secure-Simulation-Service-PRD.md, sections 10.4, 10.4.1 and 10.5"]], [0.22, 0.78]),
         ("h1", "1. Purpose"),
         ("p", "A pseudonym change costs computation: the vehicle switches identity, every receiver checks the chain of the new "
               "certificate, and the pool needs refilling sooner. With a silent period it also costs information: the vehicle "
               "sends nothing, so its neighbours' view of it goes stale. This sweep measures both against the change rate, so "
               "that the cost of one change can be read as the slope of each measure, with its spread over seeds."),
         ("h1", "2. Setup"),
         ("bullet", "Baseline: the same scenario with pseudonym changes switched off (periodic: the control client in manual "
                    "mode; mix zone: no zone), on the same seed, so that the vehicles drive the same paths."),
         ("bullet", "Cells: periodic, every combination of 4 change intervals and 3 silent periods (12 cells); mix zone, the 3 "
                    "silent periods. 3 seeds each, 51 runs."),
         ("bullet", "Cost per change: per seed, a least-squares line of each measure against the change rate over the baseline "
                    "and the cells that differ only in the rate; the slope's mean and standard deviation over the seeds. A "
                    "vehicle's measures are fitted against its own change rate, so its slope is what one change of every "
                    "vehicle costs a vehicle, its own work and its neighbours' together; the RSU's and the PKI's against all "
                    "vehicles' changes."),
         ("bullet", "Saturated cells: at changes every 10 or 5 s, a silent period of up to 12-13 s keeps the vehicles silent "
                    "most of the time. Those four cells are shown but left out of the slopes (fit_exclude)."),
         ("bullet", "CPU time: per container from its cgroup, in ms of CPU per second (1000 is one core). Operation counts: "
                    "the stations' own counters of signing, verifying, chain checks, key derivations and ID changes."),
         ("h1", "3. Results")]

    p = groups["periodic"]
    cells = p["cells"]
    b.append(("h2", "3.1 Periodic changes"))
    rows = []
    for c in cells:
        exposure, missed = conflicts("periodic", c["cell"])
        rows.append([c["cell"].replace("_", ", ").replace("interval-", "every ").replace("silence-", "silence ")
                     + ("" if c.get("fitted", True) else " *"),
                     fmt(c["changes_per_min_vehicle"], digits=1), fmt(c["cpu_vehicle_ms_per_s"]), fmt(c["cpu_rsu_ms_per_s"]),
                     fmt(c["cpu_pki_ms_per_s"]), fmt(c["stale_share"], 100, 0, " %"),
                     fmt(c["tracking_success"], 100, 0, " %"), fnum(exposure, 1, " s")])
    b.append(("table", ["Cell", "Changes /min", "Vehicle CPU ms/s", "RSU CPU ms/s", "PKI CPU ms/s", "Stale", "Tracked",
                        "Conflict exposure"], rows, [0.22, 0.1, 0.12, 0.12, 0.11, 0.1, 0.1, 0.13]))
    b.append(("p", "Mean ± standard deviation over the three seeds. Changes per minute per vehicle. Stale: share of time a "
                   "receiver's latest CAM of a vehicle was older than 1 s. Tracked: changes the eavesdropper followed. "
                   "Conflict exposure: seconds per run in which two vehicles' true time-to-collision was below 4 s while one's "
                   "information about the other was stale or showed no conflict. * not in the slopes (saturated)."))

    b.append(("h2", "3.2 Cost per change"))
    order = ["none", "3-13", "6-12"]
    by = {s["series"].get("silence"): s["measures"] for s in p["slopes"]}
    labels = [("cpu_vehicle_ms_per_s", "Vehicle CPU (ms)", 1.0, 0), ("cpu_rsu_ms_per_s", "RSU CPU (ms)", 1.0, 0),
              ("cpu_pki_ms_per_s", "PKI CPU (ms)", 1.0, 0), ("rsu_verifies_per_s", "RSU verifications", 1.0, 1),
              ("stale_share", "Stale time (s)", 1.0, 1)]
    b.append(("table", ["Per change", "No silent period", "Silence 3-13 s", "Silence 6-12 s"],
              [[label] + [fmt((by.get(s) or {}).get(key, {}).get("slope"), scale, digits) for s in order]
               for key, label, scale, digits in labels], [0.31, 0.23, 0.23, 0.23]))
    b.append(("bullet", "Without a silent period the vehicles' and the RSU's CPU per change cannot be told from zero: between "
                        "runs of one cell the vehicle CPU varies by 2 to 8 ms/s against a base of about 80 ms/s. At up to 12 "
                        "changes a minute that bounds a change below about 150 ms of CPU on this host."))
    b.append(("bullet", "The PKI's cost is measurable, about 32 ms of CPU per certificate. It comes in steps with the refills: "
                        "at one change a minute the pools of 4 last the 3 minutes and the PKI stays at its idle rate. With a "
                        "silent period its slope is higher (48-49 ms) only because those lines are fitted over the baseline, "
                        "60 s and 30 s cells, across the step of the first refills."))
    b.append(("bullet", "With a silent period the slopes are savings: each change spares the RSU about 20 verifications (the "
                        "CAMs not sent in a mean silence of 8 to 9 s) and the vehicles about 280 ms of CPU, and leaves each "
                        "receiver with stale information for about the silent period less 1 s. They do not show that a change "
                        "is free; they show that the silence costs information, not computation."))

    b.append(("h2", "3.3 Operation counts per change"))
    none = [c["cell"] for c in cells if c["cell"].endswith("silence-none")]
    ops = lambda key: mean([extra("periodic", c, key, "counters") for c in none])   # noqa: E731
    b.append(("table", ["Operation", "Count", "Time on this host"], [
        ["ID change on the vehicle", "1 per change", f"{fnum(ops('id_change_us_mean'), 0)} µs on average"],
        ["Chain check of the new certificate", f"{fnum(ops('chain_checks_per_change'), 1)} per change "
                                               f"({fnum(ops('receivers_per_change'), 0)} receivers)",
         f"{fnum(ops('chain_check_us_mean') / 1000, 1)} ms each, {fnum(ops('chain_check_us_per_change') / 1000, 1)} ms per change"],
        ["Unknown signers", f"{fnum(ops('unknown_signer_per_change'), 2)} per change", "-"],
        ["Key derivation (refill)", "1 per certificate", f"{fnum(ops('derivation_us_mean'), 0)} µs each"],
        ["Batch check (refill)", "1 per certificate", f"{fnum(ops('batch_check_us_mean') / 1000, 1)} ms each"],
        ["Signing a message", "every message, unchanged by a change", f"{fnum(ops('sign_us_mean') / 1000, 1)} ms each"],
        ["Verifying a message", "every message received", f"{fnum(ops('verify_us_mean') / 1000, 1)} ms each"]],
        [0.34, 0.33, 0.33]))
    b.append(("p", "Means over the cells without a silent period. Every receiver checked each new certificate once; no "
                   "receiver met a signer it knew only by digest, which vnap:r2-p21's certificate after the silent period "
                   "was meant to achieve. From the counts, a change of every vehicle costs a vehicle its ID change, three "
                   "chain checks (one for each other vehicle) and a batch check: about 26 ms on this host. The RSU does one "
                   "chain check per change, about 7 ms. Both lie within the CPU slopes of 3.2 (36 ± 58 and 9 ± 16 ms), which "
                   "the counts resolve far better. With silent periods the counted chain checks per change fall below the "
                   "receivers (2.5 at every 10 s, 0 at every 5 s with 6-12 s): a certificate replaced before it is heard is "
                   "never checked."))

    m = groups["mixzone"]
    b.append(("h2", "3.4 Mix zone"))
    rows = []
    for c in m["cells"]:
        exposure, missed = conflicts("mixzone", c["cell"])
        rows.append([c["cell"].replace("silence-", "silence "), fmt(c["changes_per_min_vehicle"], digits=2),
                     fmt(c["cpu_vehicle_ms_per_s"]), fmt(c["stale_share"], 100, 1, " %"),
                     fmt(c["tracking_success"], 100, 0, " %"), fmt(c["gap_ms_mean"], digits=0, unit=" ms"),
                     fnum(exposure, 1, " s"), fnum(missed, 1)])
    b.append(("table", ["Cell", "Changes /min", "Vehicle CPU ms/s", "Stale", "Tracked", "Gap", "Conflict exposure",
                        "Conflicts missed"], rows, [0.15, 0.11, 0.14, 0.11, 0.1, 0.13, 0.13, 0.13]))
    mby = {s["series"].get("cell"): s["measures"] for s in m["slopes"]}
    b.append(("p", "The mix zone's change rate is set by the laps, so each cell is compared with the baseline alone. Stale "
                   "time per change: " + ", ".join(f"{fmt((mby.get('silence-' + s) or {}).get('stale_share', {}).get('slope'))} s "
                                                   f"with silence {s}" for s in ("3-13", "6-12"))
                   + ". The silent period brought tracking down from about 80 % of the changes to below 20 %, and conflict "
                     "exposure up from about 2 s to 29 s per run, with 9 to 10 conflicts per run lying entirely in stale "
                     "time: the vehicles go silent exactly where they meet. At one change a minute the CPU slopes are too "
                     "noisy to use (vehicle -137 ± 145 ms without silence)."))

    b.append(("h2", "3.5 Host pauses"))
    b.append(("p", "The VM was paused three times during the sweep, for up to 80 minutes. A run's 180 s count monotonic time, "
                   "so the runs stayed 3 minutes long, but the CPU samples are timed by the wall clock: one run showed gaps "
                   "of 514, 4829 and 2111 s and CPU rates far too low. It was run again. The sweep now marks a run with a gap "
                   "of more than 2 s between CPU samples as failed and repeats it, and the analysis leaves such runs out."))

    b += [("h1", "4. Conclusion"),
          ("p", "On this host, a pseudonym change costs too little computation to measure as CPU time: the bound is about "
                "150 ms per change, and the operation counts put it at about 26 ms on the vehicles and 7 ms on the RSU, "
                "almost all of it the receivers' chain checks of the new certificate. The PKI pays about 32 ms per "
                "certificate. The cost that matters is the silent period's: it stops CPU and verifications rather than "
                "adding them, but leaves the neighbours without information for about the silent period less 1 s per change, "
                "and in the mix zone it multiplied the time spent in conflicts with stale information by more than ten. "
                "That is the trade-off the joint experiment (phases 6 and 7) has to weigh against the privacy it buys."),
          ("h1", "5. Limitations"),
          ("bullet", "Four vehicles and one RSU. Receiver load from simultaneous changes grows with traffic density; three "
                     "seeds per cell."),
          ("bullet", "Shared CPUs: all containers and the PKI share one VM's 4 vCPUs. Times compare runs on this host only; "
                     "the counts carry over to other hardware."),
          ("bullet", "Lossless link: no packet loss, congestion or range, so the measured disruption is the part caused by "
                     "the change."),
          ("bullet", "No warning probe in the sweep's scenarios, so lost and delayed warnings were not measured; conflicts "
                     "are computed from true positions and count missing information, not crashes."),
          ("bullet", "Synchronized changes (periodic) are a worst case for the receivers and a best case for privacy."),
          ("bullet", "3-minute runs: at one change a minute the pools of 4 never needed refilling, so the PKI's cost shows "
                     "only from 30 s intervals on."),
          ("h1", "6. Recommendations"),
          ("number", "Carry the cost over through the counts. For a target platform, multiply the counts of 3.3 by that "
                     "platform's times for an ECDSA verification and a chain check; do not scale the CPU times measured here."),
          ("number", "Keep the silent period shorter than the change interval. Beyond that the vehicles are silent nearly "
                     "all the time, certificates go unchecked and the cells saturate (3.1)."),
          ("number", "Add the warning probe to the sweep's scenarios, so that the next sweep also measures warnings lost "
                     "and delayed in silent periods, the hard limit of the joint experiment."),
          ("number", "Rerun with 6 to 10 vehicles, where simultaneous changes load the receivers more."),
          ("number", "For a CPU slope that resolves a change, run 10 minutes per cell with 6 or more seeds, on a host with "
                     "dedicated CPUs."),
          ("number", "Start the joint experiment (phases 6 and 7): the mix zone's conflict exposure is the safety cost its "
                     "privacy has to be weighed against.")]
    write_docx(b, out, title="Cost of Pseudonym Changes: Test Summary")
    print(f"{out}: {total} runs")


if __name__ == "__main__":
    if len(sys.argv) not in (2, 3) or not os.path.isfile(os.path.join(sys.argv[1], "sweep.json")):
        sys.exit(__doc__)
    main(*sys.argv[1:])
