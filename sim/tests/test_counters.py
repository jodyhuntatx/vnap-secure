"""vnapsim.counters: the stations' operation counters, per change and refill, on synthetic logs (no docker)."""
import json
import os
import sys
import unittest
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from vnapsim import counters  # noqa: E402

T0 = 1_800_000_000.0


def stamp(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f") + "000Z"


def log(lines):
    """Station log text from (seconds after T0, line) pairs."""
    return "".join(f"{stamp(T0 + t)} {text}\n" for t, text in lines)


def snapshots(seconds, events):
    """[COUNTERS] lines once a second; `events`: {second: {name: [count, us]}} added at that second."""
    total, out = {}, []
    for t in range(1, seconds + 1):
        for k, v in events.get(t, {}).items():
            c = total.setdefault(k, [0, 0])
            c[0], c[1] = c[0] + v[0], c[1] + v[1]
        base = {"sign": [10 * t, 300 * t], "verify": [20 * t, 1000 * t]}
        out.append((t + 0.001, "[COUNTERS] " + json.dumps({**base, **total})))
    return out


class Counters(unittest.TestCase):
    def setUp(self):
        # car1 changes at 10.2 s with 2 s of silence; both neighbours check its new AT when it is heard again
        self.logs = {
            "car1": log(snapshots(20, {15: {"derivation": [4, 8000], "batch_check": [4, 2000]}})
                        + [(10.2, "[IDCHANGE] completed in 850 us"),
                           (15.3, "[PKI] batch installed: request r1, 4 certificate(s), refresh 900 ms (PKI issue 40 ms), 6 unused")]),
            "car2": log(snapshots(20, {13: {"chain_check": [1, 600]}, 5: {"chain_check": [1, 500]}})),
            "rsu": log(snapshots(20, {12: {"chain_check": [1, 700], "unknown_signer": [1, 0]}})),
        }
        self.changes = [{"vehicle": "car1", "t": T0 + 10.2, "silent_ms": 2000}]

    def test_per_change(self):
        r = counters.analyse(self.logs, self.changes)
        c, = r["changes"]
        self.assertEqual(c["id_change_us"], 850)
        self.assertEqual(c["receivers"], 2)
        # the check at 5 s (a first AT at start) is outside the window
        self.assertEqual(c["chain_check"], 2)
        self.assertEqual(c["chain_check_us"], 1300)
        self.assertEqual(c["unknown_signer"], 1)
        s = r["summary"]
        self.assertEqual(s["chain_checks_per_change"], 2)
        self.assertEqual(s["id_change_us_mean"], 850)
        self.assertEqual(s["chain_check_us_mean"], 600.0)   # 1800 us over three checks
        self.assertEqual(s["sign_us_mean"], 30.0)

    def test_per_refill(self):
        r = counters.analyse(self.logs, self.changes)
        refill, = r["refills"]
        self.assertEqual((refill["station"], refill["certificates"]), ("car1", 4))
        self.assertEqual(refill["derivation"], [4, 8000])
        self.assertEqual(refill["batch_check"], [4, 2000])
        self.assertEqual(r["summary"]["derivation_us_mean"], 2000.0)

    def test_shared_window(self):
        # two vehicles change together: a neighbour's two checks are shared between the changes
        logs = {"car1": log(snapshots(20, {})), "car2": log(snapshots(20, {})),
                "rsu": log(snapshots(20, {11: {"chain_check": [2, 1000]}}))}
        changes = [{"vehicle": "car1", "t": T0 + 10.0, "silent_ms": 0},
                   {"vehicle": "car2", "t": T0 + 10.1, "silent_ms": 0}]
        r = counters.analyse(logs, changes)
        # rsu's two checks shared, and each car the other's window: none from them
        self.assertEqual([c["chain_check"] for c in r["changes"]], [1.0, 1.0])

    def test_old_image(self):
        self.assertIsNone(counters.analyse({"car1": log([(1, "[PSEUDONYM] changed pool index 0 -> 1")])}, []))


if __name__ == "__main__":
    unittest.main()
