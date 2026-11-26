"""The guessing attacker (phase 6 of the overhead design, PRD 10.9.3): the chance level of a run, measured.

It sees what the eavesdropper saw (the CAMs in messages.jsonl) and knows nothing but which identities went
silent and when. Every new pseudonym it links to a silent track chosen at random: a track whose latest
identity was last heard between 0.8 of its CAM interval and the link window ago, as for the eavesdropper's
own position and timing links. A track continues with the identity linked to it; a new pseudonym with no
silent track starts a track of its own.

Its tracking success (correct links / pseudonym changes made) is the chance level of the run: how often a
guess is right given how many vehicles were silent at each change, which 1 / vehicles ignores. The guesses
are repeated REPETITIONS times with fixed seeds, so the figure is reproducible, and the mean is reported
with its spread over the repetitions.
"""

import bisect
import json
import os
import random
import statistics
import tomllib

REPETITIONS = 200
DEFAULT_WINDOW_S = 3.0   # the eavesdropper's default --link-window


def link_window(results_dir):
    """The run's eavesdropper link window: scenario.toml with the run's variant and overrides (run.json)."""
    from .scenario import apply_override, variant_sets   # noqa: PLC0415
    try:
        with open(os.path.join(results_dir, "scenario.toml"), "rb") as f:
            data = tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError):
        return DEFAULT_WINDOW_S
    try:
        with open(os.path.join(results_dir, "run.json")) as f:
            run = json.load(f)
    except (OSError, ValueError):
        run = {}
    sets = []
    try:
        sets += variant_sets(data, run.get("variant")) if run.get("variant") else []
    except Exception:  # noqa: BLE001  (an unknown variant: the scenario as written)
        pass
    sets += [s for s in run.get("overrides") or [] if isinstance(s, str)]
    for s in sets:
        if s.split("=", 1)[0].strip().startswith("eavesdropper"):
            try:
                apply_override(data, s)
            except Exception:  # noqa: BLE001
                pass
    ev = data.get("eavesdropper")
    return float(ev.get("link_window", DEFAULT_WINDOW_S)) if isinstance(ev, dict) else DEFAULT_WINDOW_S


def _identities(frames):
    """First and last time and the median CAM interval of every certificate heard, in order of first CAM."""
    seen = {}
    for fr in frames:
        t, cert = fr[0], fr[1]
        s = seen.setdefault(cert, {"cert": cert, "first": t, "times": []})
        s["times"].append(t)
    for s in seen.values():
        gaps = sorted(b - a for a, b in zip(s["times"], s["times"][1:]))
        s["interval"] = gaps[len(gaps) // 2] if gaps else 1.0
    return seen


def guess(frames, owner, changes, window_s, rng):
    """One guessing attacker over the CAM frames. owner: certificate -> vehicle (ground truth); changes:
    the certificates vehicles changed to. Returns (correct links, candidates at each change)."""
    ids = _identities(frames)
    order = sorted(ids.values(), key=lambda s: s["first"])
    tracks = []          # each: [its latest certificate]
    correct, candidates = 0, []
    for new in order:
        t = new["first"]
        silent = []
        for i, tr in enumerate(tracks):
            cur = ids[tr[0]]
            k = bisect.bisect_left(cur["times"], t)
            if k < len(cur["times"]):
                continue   # its latest identity is still heard after this one appeared: not silent
            dt = t - cur["times"][-1]
            if 0.8 * cur["interval"] <= dt <= window_s:
                silent.append(i)
        if new["cert"] in changes:
            candidates.append(len(silent))
        if silent:
            i = rng.choice(silent)
            old = tracks[i][0]
            tracks[i][0] = new["cert"]
            if new["cert"] in changes and owner.get(old) is not None and owner.get(old) == owner.get(new["cert"]):
                correct += 1
        else:
            tracks.append([new["cert"]])
    return correct, candidates


def analyse(frames, owner, changes, window_s, repetitions=REPETITIONS):
    """The guessing attacker's tracking success over `repetitions` seeded runs; None without frames or changes."""
    if not frames or not changes:
        return None
    changes = set(changes)
    results = [guess(frames, owner, changes, window_s, random.Random(f"guess/{k}")) for k in range(repetitions)]
    success = [c / len(changes) for c, _ in results]
    cands = results[0][1]
    vehicles = len({v for v in owner.values()})
    return {"window_s": window_s, "repetitions": repetitions,
            "tracking_success": round(statistics.mean(success), 3),
            "tracking_success_sd": round(statistics.stdev(success), 3) if len(success) > 1 else None,
            "candidates_mean": round(statistics.mean(cands), 2) if cands else None,
            "no_candidate_share": round(sum(1 for n in cands if n == 0) / len(cands), 3) if cands else None,
            "formula": round(1 / vehicles, 3) if vehicles else None}
