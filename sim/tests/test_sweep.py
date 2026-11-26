"""vnapsim.cpu and vnapsim.sweep: CPU samples and the cost per change, on synthetic data (no docker)."""
import io
import json
import os
import sys
import tarfile
import tempfile
import unittest

HERE = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from vnapsim import cpu, overhead, sweep  # noqa: E402

T0 = 1_800_000_000.0


def cpu_tar(path, rates, seconds=60, interval=0.1):
    """A collector tar whose cpu.jsonl has containers using `rates` ms of CPU per second."""
    lines = []
    for k in range(int(seconds / interval) + 1):
        t = T0 + k * interval
        lines.append(json.dumps({"t": t, "u": {n: int(r * 1000 * k * interval) for n, r in rates.items()}}))
    data = ("\n".join(lines) + "\n").encode()
    with tarfile.open(path, "w") as tar:
        info = tarfile.TarInfo("logs/cpu.jsonl")
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))


class Cpu(unittest.TestCase):
    def test_rates(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "collector-logs.tar")
            cpu_tar(path, {"car1": 50.0, "car2": 70.0, "rsu": 40.0, "pki": 1.0})
            r = cpu.analyse(cpu.read(path), {"car1": "vehicle", "car2": "vehicle", "rsu": "rsu", "pki": "pki"})
        s = r["summary"]
        self.assertAlmostEqual(s["vehicle_ms_per_s"], 60.0, places=1)
        self.assertAlmostEqual(s["rsu_ms_per_s"], 40.0, places=1)
        self.assertAlmostEqual(s["stations_ms_per_s"], 160.0, places=1)   # the PKI is not a station
        car1 = next(st for st in r["stations"] if st["station"] == "car1")
        self.assertAlmostEqual(car1["cpu_s"], 3.0, places=2)
        self.assertEqual(len(car1["timeline"]), 60)
        self.assertAlmostEqual(car1["timeline"][10][1], 50.0, places=1)

    def test_no_samples(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "collector-logs.tar")
            with tarfile.open(path, "w"):
                pass
            self.assertIsNone(cpu.read(path))
        self.assertIsNone(cpu.analyse(None, {}))


PLAN = """
duration_s = 60
seeds = [1, 2]
set = ["image=vnap:test"]
[[group]]
name = "periodic"
scenario = "crossing:periodic"
baseline = ['control.client.mode="manual"']
rate_factor = "interval"
[group.factors.interval]
"30" = ["control.client.interval=30"]
"10" = ["control.client.interval=10"]
[group.factors.silence]
none = ["defaults.pseudonyms.silent_max_ms=0"]
long = ["defaults.pseudonyms.silent_max_ms=9000"]
"""


class Sweep(unittest.TestCase):
    def plan(self, d):
        path = os.path.join(d, "plan.toml")
        with open(path, "w") as f:
            f.write(PLAN)
        return sweep.load_plan(path)

    def test_runs(self):
        with tempfile.TemporaryDirectory() as d:
            runs = sweep.runs(self.plan(d))
        # (baseline + 2 x 2 cells) x 2 seeds, a seed's cells together
        self.assertEqual(len(runs), 10)
        self.assertEqual([r["cell"] for r in runs[:5]],
                         ["baseline", "interval-30_silence-none", "interval-30_silence-long",
                          "interval-10_silence-none", "interval-10_silence-long"])
        r = runs[1]
        self.assertEqual(r["sets"], ["image=vnap:test", "control.client.interval=30",
                                     "defaults.pseudonyms.silent_max_ms=0", "control.mobility_seed=1"])
        self.assertEqual(sweep.run_dir("/x", r), "/x/periodic/interval-30_silence-none/seed-1")

    def test_slopes(self):
        # vehicle CPU = 50 ms/s + 4 ms per change; with silence also 0.1 s stale per change
        with tempfile.TemporaryDirectory() as d:
            plan = self.plan(d)
            for r in sweep.runs(plan):
                interval = {"interval-30": 30, "interval-10": 10}.get(r["cell"].split("_")[0])
                rate = 1 / interval if interval else 0.0           # changes per second per vehicle
                silent = r["levels"] and r["levels"]["silence"] == "long"
                noise = 0.5 if r["seed"] == 1 else -0.5
                out = sweep.run_dir(d, r)
                os.makedirs(out)
                o = {"version": overhead.VERSION, "duration_s": 600.0, "outages": [],
                     "summary": {"vehicles": 4, "changes": int(round(4 * 600 * rate)),
                                 "cpu_vehicle_ms_per_s": 50 + 4 * rate + noise, "cpu_rsu_ms_per_s": 30 + 2 * 4 * rate,
                                 "stale_share": 0.1 * rate if silent else 0.0},
                     "cpu": {"stations": [{"station": "rsu", "role": "rsu", "span_s": 600.0}]},
                     "counters": {"stations": [{"station": "rsu", "verifies_per_s": 10.0}]}}
                with open(os.path.join(out, "overhead.json"), "w") as f:
                    json.dump(o, f)
                with open(os.path.join(out, "sweep-run.json"), "w") as f:
                    json.dump(r, f)
            result, text = sweep.write_analysis(d, plan)
        g, = result["groups"]
        self.assertEqual([c["cell"] for c in g["cells"]][0], "baseline")
        by = {s["series"]["silence"]: s["measures"] for s in g["slopes"]}
        self.assertEqual(set(by), {"none", "long"})
        v = by["none"]["cpu_vehicle_ms_per_s"]["slope"]
        self.assertAlmostEqual(v["mean"], 4.0, places=1)
        self.assertEqual(v["n"], 2)
        self.assertAlmostEqual(by["none"]["cpu_rsu_ms_per_s"]["slope"]["mean"], 2.0, places=1)   # per change of the run
        self.assertAlmostEqual(by["long"]["stale_share"]["slope"]["mean"], 0.1, places=2)
        self.assertIn("vehicle CPU 4.0", text)

    def test_fit_exclude(self):
        # a saturated cell far off the line changes nothing once it is excluded
        with tempfile.TemporaryDirectory() as d:
            plan = self.plan(d)
            plan["group"][0]["fit_exclude"] = ["interval-10_silence-none"]
            for r in sweep.runs(plan):
                rate = {"interval-30": 1 / 30, "interval-10": 1 / 10}.get(r["cell"].split("_")[0], 0.0)
                cpu = 50 + 4 * rate if r["cell"] != "interval-10_silence-none" else 0.0
                out = sweep.run_dir(d, r)
                os.makedirs(out)
                with open(os.path.join(out, "overhead.json"), "w") as f:
                    json.dump({"version": overhead.VERSION, "duration_s": 600.0, "summary": {"vehicles": 4, "changes": int(round(4 * 600 * rate)),
                                                                "cpu_vehicle_ms_per_s": cpu}}, f)
                with open(os.path.join(out, "sweep-run.json"), "w") as f:
                    json.dump(r, f)
            result, text = sweep.write_analysis(d, plan)
        g, = result["groups"]
        by = {s["series"]["silence"]: s["measures"] for s in g["slopes"]}
        self.assertAlmostEqual(by["none"]["cpu_vehicle_ms_per_s"]["slope"]["mean"], 4.0, places=3)
        self.assertEqual([c["cell"] for c in g["cells"] if not c["fitted"]], ["interval-10_silence-none"])
        self.assertIn("interval-10_silence-none *", text)

    def test_paused_runs_left_out(self):
        with tempfile.TemporaryDirectory() as d:
            plan = self.plan(d)
            for r in sweep.runs(plan):
                out = sweep.run_dir(d, r)
                os.makedirs(out)
                gap = 4829.0 if (r["cell"], r["seed"]) == ("baseline", 2) else 0.1
                with open(os.path.join(out, "overhead.json"), "w") as f:
                    json.dump({"version": overhead.VERSION, "duration_s": 600.0, "summary": {"vehicles": 4, "changes": 0},
                               "cpu": {"settings": {"gap_s_max": gap}, "stations": []}}, f)
                with open(os.path.join(out, "sweep-run.json"), "w") as f:
                    json.dump(r, f)
            result, text = sweep.write_analysis(d, plan)
            self.assertEqual(sweep.paused(os.path.join(d, "periodic", "baseline", "seed-2")), 4829.0)
        self.assertEqual((result["runs"], result["paused"]), (9, ["periodic/baseline/seed-2"]))
        self.assertIn("left out, the host was paused", text)

    def test_cpu_gap(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "collector-logs.tar")
            cpu_tar(path, {"car1": 50.0})
            self.assertAlmostEqual(cpu.analyse(cpu.read(path), {"car1": "vehicle"})["settings"]["gap_s_max"], 0.1, places=2)


if __name__ == "__main__":
    unittest.main()
