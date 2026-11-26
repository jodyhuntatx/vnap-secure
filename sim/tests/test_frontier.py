"""vnapsim.frontier: privacy against safety over a sweep's runs, on synthetic overhead.json files (no docker)."""
import argparse
import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from vnapsim import frontier, overhead  # noqa: E402

# policy: (A0 tracking success, guessing attacker, stale share, unexplained warnings)
POLICIES = {
    "quiet": (0.20, 0.25, 0.10, 0),      # low tracking, more stale: on the frontier
    "chatty": (0.70, 0.30, 0.01, 0),     # high tracking, hardly stale: on the frontier
    "worse": (0.75, 0.30, 0.12, 0),      # beaten on both by quiet
    "lossy": (0.10, 0.20, 0.005, 3),     # best on both, but loses warnings: off the frontier
}
SEEDS = [1, 2, 3, 4, 5]


def sweep_dir(d):
    for cell, (a0, guess, stale, unexplained) in [("baseline", (None, None, 0.0, 0))] + list(POLICIES.items()):
        for i, seed in enumerate(SEEDS):
            out = os.path.join(d, "v4", cell, f"seed-{seed}")
            os.makedirs(out)
            noise = (i - 2) * 0.01
            summary = {"changes": 0 if cell == "baseline" else 20,
                       "tracking_success": None if a0 is None else a0 + noise,
                       "guess_tracking_success": None if guess is None else guess,
                       "stale_share": stale + noise / 10, "stale_share_0_5": stale * 1.3, "stale_share_2_0": stale * 0.7,
                       "denm_unexplained": unexplained if i == 0 else 0, "conflict_exposure_s": 10 * stale}
            with open(os.path.join(out, "overhead.json"), "w") as f:
                json.dump({"version": overhead.VERSION, "summary": summary}, f)
            with open(os.path.join(out, "sweep-run.json"), "w") as f:
                json.dump({"group": "v4", "cell": cell, "seed": seed}, f)
    return d


class Frontier(unittest.TestCase):
    def test_frontier_and_hard_limit(self):
        with tempfile.TemporaryDirectory() as d:
            result, text = frontier.write_analysis(sweep_dir(d))
            self.assertTrue(os.path.isfile(os.path.join(d, "frontier-v4.svg")))
        c, = result["conditions"]
        self.assertEqual(sorted(c["frontier"]["primary"]), ["chatty", "quiet"])
        by = {p["policy"]: p for p in c["policies"]}
        self.assertFalse(by["lossy"]["within_limit"])
        self.assertEqual(by["lossy"]["warnings_unexplained"], 3)
        self.assertEqual(c["baseline"]["runs"], 5)
        self.assertIn("off the frontier", text)

    def test_strongest_attacker_and_intervals(self):
        with tempfile.TemporaryDirectory() as d:
            result, _ = frontier.write_analysis(sweep_dir(d))
        by = {p["policy"]: p for p in result["conditions"][0]["policies"]}
        self.assertEqual(by["quiet"]["strongest"], "guess")      # 0.25 by guessing beats A0's 0.20
        self.assertEqual(by["chatty"]["strongest"], "A0")
        t = by["chatty"]["tracking_success"]
        self.assertAlmostEqual(t["mean"], 0.70)
        self.assertTrue(t["lo"] < 0.70 < t["hi"])
        self.assertEqual(by["quiet"]["tracking_success"]["sd"], 0.0)   # the guessing attacker's level is the same per seed

    def test_seeds_needed(self):
        self.assertEqual(frontier.seeds_needed(0.25), 25)        # (1.96 * 0.25 / 0.1)^2 = 24.01
        self.assertEqual(frontier.seeds_needed(0.0), 2)
        self.assertIsNone(frontier.seeds_needed(None))

    def test_seeds_by_the_most_spread_attacker(self):
        with tempfile.TemporaryDirectory() as d:
            result, text = frontier.write_analysis(sweep_dir(d))
        by = {p["policy"]: p for p in result["conditions"][0]["policies"]}
        # quiet: the guessing attacker is strongest and never varies, A0 does (sd 0.016): A0 sets the count
        self.assertEqual((by["quiet"]["seeds_driven_by"], by["quiet"]["seeds_needed"]), ("A0", 2))
        self.assertIn("quiet 2 (A0, sd 0.016)", text)

    def test_main(self):
        with tempfile.TemporaryDirectory() as d:
            sweep_dir(d)
            lines = []
            self.assertEqual(frontier.main(argparse.Namespace(dir=d, json=False), log=lines.append), 0)
        self.assertIn("frontier, primary:", lines[0])


if __name__ == "__main__":
    unittest.main()
