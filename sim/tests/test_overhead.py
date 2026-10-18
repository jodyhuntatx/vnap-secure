"""vnapsim.overhead on a small synthetic results directory (no docker)."""
import io
import json
import os
import sys
import tarfile
import tempfile
import unittest

HERE = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from vnapsim import overhead  # noqa: E402

T0 = 1_800_000_000.0


def stamp(t):
    from datetime import datetime, timezone
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f") + "000Z"


def results_dir(silent_ms=0, eavesdropper=True):
    """Two vehicles with 1 Hz CAMs for 30 s. car1 changes a1 -> a2 at 10 s (CAMs at 11 and 12 s lost
    when silent), car2 changes b1 -> b2 at 11 s. The eavesdropper linked car1's change correctly and
    car2's to car1 (wrong)."""
    d = tempfile.mkdtemp()
    stations = [{"name": n, "station_id": i, "station_type": 5, "state": "running",   # the shape of status.json
                 "security": {"entity": "certs-v3", "pseudonym_pool": 8},
                 "pseudonym": {"changes": 1, "rejected": 0, "refill": None, "id_change": {"scope": "full"}}}
                for i, n in ((2, "car1"), (3, "car2"))]
    stations.insert(0, {"name": "rsu", "station_id": 1, "station_type": 15, "state": "running",
                        "security": {"entity": "certs-v3"}, "pseudonym": None})
    with open(os.path.join(d, "status.json"), "w") as f:
        json.dump({"stations": stations}, f)
    logs = {
        "car1": [(T0 - 1, "[PSEUDONYM] v3 pool of 8 certificate(s), changed on events only, starting at index 0 (certificate=a1)"),
                 (T0 + 10, "[PSEUDONYM] changed pool index 0 -> 1 (of 8), certificate=a2")]
        + ([(T0 + 9.99999, f"[IDCHANGE] silent period {silent_ms} ms")] if silent_ms else []),   # as socktap: just before
        "car2": [(T0 - 1, "[PSEUDONYM] v3 pool of 8 certificate(s), changed on events only, starting at index 0 (certificate=b1)"),
                 (T0 + 11, "[PSEUDONYM] changed pool index 0 -> 1 (of 8), certificate=b2")],
    }
    for name, lines in logs.items():
        with open(os.path.join(d, f"{name}.log"), "w") as f:
            f.writelines(f"{stamp(t)} {text}\n" for t, text in sorted(lines))
    frames = []
    for s in range(31):
        lost = silent_ms and 10 < s <= 10 + silent_ms // 1000
        if not lost:
            frames.append({"t": T0 + s, "message": "CAM", "cert_digest": "a1" if s <= 10 else "a2", "length": 300,
                           "signer": "certificate" if s % 2 == 0 else "digest"})
        frames.append({"t": T0 + s + 0.5, "message": "CAM", "cert_digest": "b1" if s <= 10 else "b2", "length": 200, "signer": "digest"})
    if eavesdropper:
        data = "".join(json.dumps(fr) + "\n" for fr in frames).encode()
        with tarfile.open(os.path.join(d, "eavesdropper-logs.tar"), "w") as tar:
            info = tarfile.TarInfo("logs/messages.jsonl")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    links = [{"old": "a1", "new": "a2", "old_station": "car1", "new_station": "car1", "correct": True, "technique": "position",
              "track": "V1", "evidence": ["position continuity (10.0 m, 1.00 s gap)"]},
             {"old": "a2", "new": "b2", "old_station": "car1", "new_station": "car2", "correct": False, "technique": "timing",
              "track": "V1", "evidence": ["timing phase (3 ms)"]}]
    with open(os.path.join(d, "score.json"), "w") as f:   # the shape of vnapsim.scoring.score
        json.dump({"links": 2, "correct": 1, "wrong": 1, "link_list": links, "stations": ["car1", "car2"],
                   "by_technique": {"position": {"correct": 1, "wrong": 0}, "timing": {"correct": 0, "wrong": 1}},
                   "longest_followed": {"changes": 1, "station": "car1", "track": "V1"},
                   "tracks": [{"track": "V1", "identities": 3, "stations": ["car1", "car1", "car2"], "main_station": "car1",
                               "purity": 0.667, "followed_changes": 1, "followed_station": "car1"}]}, f)
    return d


class OverheadTest(unittest.TestCase):
    def test_changes_gaps_and_outcomes(self):
        r = overhead.analyse(results_dir())
        c1, c2 = sorted(r["changes"], key=lambda c: c["vehicle"])
        self.assertEqual((c1["gap_ms"], c1["cams_missing"], c1["outcome"], c1["technique"]), (1000, 0, "linked correctly", "position"))
        self.assertEqual((c2["outcome"], c2["linked_to"]), ("linked wrongly", "car1"))
        self.assertEqual((c1["synchronized_with"], c2["synchronized_with"]), (["car2"], ["car1"]))   # 1 s apart
        s = r["summary"]
        self.assertEqual((s["changes"], s["tracking_success"], s["linked_wrongly"], s["sync_ratio"]), (2, 0.5, 1, 1.0))
        self.assertEqual((s["stale_share"], s["delivery_ratio"]), (0.0, 1.0))
        car1 = next(v for v in r["vehicles"] if v["vehicle"] == "car1")
        self.assertEqual((car1["cam_interval_ms_median"], car1["full_cert_share"], car1["cam_bytes_mean"]), (1000, round(16 / 31, 3), 300))

    def test_silent_period_shows_as_gap_and_stale_time(self):
        r = overhead.analyse(results_dir(silent_ms=2000))
        c1 = next(c for c in r["changes"] if c["vehicle"] == "car1")
        self.assertEqual((c1["silent_ms"], c1["gap_ms"], c1["cams_missing"]), (2000, 3000, 2))
        car1 = next(v for v in r["vehicles"] if v["vehicle"] == "car1")
        self.assertEqual(car1["stale_share"], round(2.0 / 30, 3))       # 3 s gap: 2 s with information older than 1 s
        self.assertEqual(car1["delivery_ratio"], round(29 / 31, 3))
        lane = next(t for t in r["timeline"] if t["vehicle"] == "car1")
        self.assertEqual(lane["gaps"], [[10.0, 13.0, "silent"]])
        self.assertEqual(lane["changes"], [[10.0, "linked correctly"]])

    def test_without_an_eavesdropper_only_the_logs(self):
        r = overhead.analyse(results_dir(eavesdropper=False))
        self.assertIsNone(r["reference_receiver"])
        self.assertEqual(r["summary"]["changes"], 2)
        self.assertIsNone(r["summary"]["gap_ms_max"])

    def test_written_once_and_reused(self):
        d = results_dir()
        first = overhead.load_or_analyse(d)
        self.assertTrue(os.path.isfile(os.path.join(d, "overhead.json")))
        self.assertEqual(overhead.load_or_analyse(d), json.loads(json.dumps(first)))


if __name__ == "__main__":
    unittest.main()
