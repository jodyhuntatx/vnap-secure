"""Scoring of the eavesdropper's linkage against ground truth.

Ground truth: the certificates (HashedId8) each station used, from its own [PSEUDONYM] log
lines. The eavesdropper's view: its /logs/events.jsonl (new tracks and linked pseudonym
changes). A link is correct when both pseudonyms belong to the same station.
"""

import json
import re

from .common import docker, env_of
from .status import Simulation

RE_CERT = re.compile(r"certificate=([0-9a-f]{16})")


def ground_truth(sim):
    """HashedId8 -> station name, from the stations' logs."""
    truth = {}
    for info in sim.stations:
        name = Simulation.name(info)
        for digest in RE_CERT.findall(docker("logs", name, check=False)):
            truth[digest] = name
    return truth


def eavesdropper_events(sim):
    """The first eavesdropper's events (empty without one or before it logged anything)."""
    for info in sim.observers:
        out = docker("exec", Simulation.name(info), "cat", "/logs/events.jsonl", check=False)
        events = []
        for line in out.splitlines():
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return Simulation.name(info), events
    return None, []


def score(truth, events):
    """Links by technique (correct / wrong), tracks with the stations behind them and their purity."""
    by_kind, links, tracks = {}, [], {}
    for e in events:
        if e.get("event") == "new_track":
            cert = (e.get("identifiers") or {}).get("cert")
            tracks[e["track"]] = [cert] if cert else []
        elif e.get("event") == "pseudonym_change_linked":
            old, new = truth.get(e.get("old")), truth.get(e.get("new"))
            if old is None or new is None:
                continue  # not a station of this run (or not seen in its log)
            evidence = (e.get("evidence") or ["?"])[0]
            kind = ("identifier" if evidence.startswith("same ") else evidence.split(" (")[0].split()[0])
            ok = old == new
            k = by_kind.setdefault(kind, {"correct": 0, "wrong": 0})
            k["correct" if ok else "wrong"] += 1
            links.append({"time": e.get("time"), "track": e.get("track"), "old": e["old"], "new": e["new"],
                          "old_station": old, "new_station": new, "technique": kind, "correct": ok})
            merged = e.get("merged_track")
            if merged and merged != e.get("track"):
                tracks.setdefault(e["track"], []).extend(tracks.pop(merged, [e["new"]]))
    track_list = []
    for name, certs in tracks.items():
        stations = [truth[c] for c in certs if c in truth]
        if not stations:
            continue
        main = max(set(stations), key=stations.count)
        track_list.append({"track": name, "identities": len(stations), "stations": stations,
                           "main_station": main, "purity": round(stations.count(main) / len(stations), 3)})
    correct = sum(1 for link in links if link["correct"])
    return {"links": len(links), "correct": correct, "wrong": len(links) - correct, "by_technique": by_kind,
            "tracks": sorted(track_list, key=lambda t: int(re.sub(r"\D", "", t["track"]) or 0)), "link_list": links}


def score_run(sim):
    """Score the running simulation's eavesdropper; None without an eavesdropper."""
    name, events = eavesdropper_events(sim)
    if name is None:
        return None
    result = score(ground_truth(sim), events)
    result["eavesdropper"] = name
    result["stations"] = sorted({Simulation.name(i) for i in sim.stations if "PSEUDO_CERT_0" in env_of(i)})
    return result
