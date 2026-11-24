"""vnapsim.receivers: verification, warning probe and conflict exposure on synthetic records (no docker)."""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from vnapsim import receivers  # noqa: E402
from vnapsim.overhead import extend  # noqa: E402

T0 = 1_800_000_000.0


class TTC(unittest.TestCase):
    def test_head_on(self):
        # 100 m apart, closing at 20 m/s: within 4 m after 4.8 s
        self.assertAlmostEqual(receivers.ttc((0, 0), (10, 0), (100, 0), (-10, 0)), 4.8)

    def test_parallel_and_apart(self):
        self.assertIsNone(receivers.ttc((0, 0), (10, 0), (0, 10), (10, 0)))   # same speed, side by side
        self.assertIsNone(receivers.ttc((0, 0), (10, 0), (-50, 0), (-10, 0)))   # moving away

    def test_crossing_and_overlap(self):
        # both 40 m from the centre at 10 m/s, crossing at right angles: meet at 4 s, within 4 m a little before
        t = receivers.ttc((-40, 0), (10, 0), (0, -40), (0, 10))
        self.assertTrue(3.6 < t < 4.0)
        self.assertEqual(receivers.ttc((0, 0), (0, 0), (1, 1), (0, 0)), 0.0)


def probe_records(triggers, repeat=1.0, copies=5):
    out = []
    for seq, (t, station) in enumerate(triggers, 1):
        out.append({"t": t, "station": station, "seq": seq, "event": "trigger"})
        out += [{"t": t + n * repeat, "station": station, "seq": seq, "event": "send", "n": n} for n in range(copies)]
    return out


class Warnings(unittest.TestCase):
    # car1 changes at T0+10 with a 3 s silent period, then (by its log) carries stationId 900
    history = {"car1": {"changes": [{"t": T0 + 10, "silent_ms": 3000}]}, "rsu": {"changes": []}}
    configured = {"car1": 2, "rsu": 1}

    def run_probe(self, receptions, triggers):
        return receivers.warnings(probe_records(triggers), receptions, {}, self.configured, self.history, [])

    def test_on_time_delayed_and_lost(self):
        triggers = [(T0 + 5, "car1"), (T0 + 11, "car1"), (T0 + 10.5, "car1")]
        receptions = [
            {"t": T0 + 5.02, "rx": "rsu", "m": "denm", "seq": 1, "hdr": 2, "orig": 2},       # before the change: on time
            {"t": T0 + 13.03, "rx": "rsu", "m": "denm", "seq": 2, "hdr": 900, "orig": 900},  # held back by the silence
            {"t": T0 + 0.5, "rx": "rsu", "m": "cam", "from": 1},
        ]
        w = self.run_probe(receptions, triggers)
        self.assertEqual((w["triggers"], w["deliveries"], w["on_time"], w["delayed"], w["lost"]), (3, 3, 1, 1, 1))
        self.assertEqual(w["in_silence"], 2)
        self.assertEqual(w["lost_outside_silence"], 0)
        self.assertEqual(w["delay_ms_max"], 2030)
        # seq 3: 2.5 s of silence left, copies for 4 s: it should have come through
        self.assertEqual(w["unexplained"], 1)
        self.assertEqual(w["id_leaks"], 0)

    def test_trigger_just_before_the_change(self):
        # 50 ms before the change line: on time when its first copy got out, else held back by the silence
        sent = [{"t": T0 + 9.96, "rx": "rsu", "m": "denm", "seq": 1, "hdr": 2, "orig": 2}]
        w = self.run_probe(sent, [(T0 + 9.95, "car1")])
        self.assertEqual((w["on_time"], w["in_silence"], w["unexplained"]), (1, 0, 0))
        held = [{"t": T0 + 13.01, "rx": "rsu", "m": "denm", "seq": 1, "hdr": 900, "orig": 900}]
        w = self.run_probe(held, [(T0 + 9.95, "car1")])
        self.assertEqual((w["delayed"], w["in_silence"], w["unexplained"]), (1, 1, 0))

    def test_lost_outside_silence_is_unexplained(self):
        w = self.run_probe([{"t": T0, "rx": "rsu", "m": "cam", "from": 2}], [(T0 + 20, "car1")])
        self.assertEqual((w["lost"], w["lost_outside_silence"], w["unexplained"]), (1, 1, 1))

    def test_identity_leak(self):
        # after the change a DENM still carries the configured stationId 2
        w = self.run_probe([{"t": T0 + 20.01, "rx": "rsu", "m": "denm", "seq": 1, "hdr": 2, "orig": 2}], [(T0 + 20, "car1")])
        self.assertEqual(w["id_leaks"], 1)
        self.assertEqual(w["on_time"], 1)

    def test_no_probe(self):
        self.assertIsNone(receivers.warnings([], [], {}, {}, {}, []))


class Verification(unittest.TestCase):
    def test_failures_after_change(self):
        owner = {2: ("car1", None), 900: ("car1", T0 + 10), 1: ("rsu", None)}
        changes = {"car1": [(T0 + 10, 900)]}
        rows = [{"t": T0 + 1, "rx": "rsu", "m": "cam", "from": 2, "sec": True, "rep": "Success"},
                {"t": T0 + 10.2, "rx": "rsu", "m": "cam", "from": 900, "sec": True, "rep": "Incompatible_Protocol"},
                {"t": T0 + 11.0, "rx": "rsu", "m": "cam", "from": 900, "sec": True, "rep": "Success"},
                {"t": T0 + 30.0, "rx": "rsu", "m": "cam", "from": 900, "sec": True, "rep": "False_Signature"},
                {"t": T0 + 2, "rx": "car1", "m": "cam", "from": 1, "sec": False, "rep": None}]
        v = receivers.verification(rows, owner, changes)
        self.assertEqual((v["received"], v["failed"], v["failed_after_change"]), (5, 2, 1))
        rsu = next(r for r in v["receivers"] if r["receiver"] == "rsu")
        self.assertEqual(rsu["reports"], {"Incompatible_Protocol": 1, "False_Signature": 1})
        self.assertEqual(v["changes"][0]["receivers"], 1)

    def test_identities_from_logs(self):
        status = {"stations": [{"name": "car1", "station_id": 2}, {"name": "rsu", "station_id": 1}]}
        logs = {"car1": "2027-01-15T08:00:10.000000000Z [IDCHANGE] facilities: stationId 2 -> 900 (id 0011)\n"}
        owner, changes = receivers.identities(status, logs)
        self.assertEqual(owner[900][0], "car1")
        self.assertEqual(owner[2], ("car1", None))
        self.assertEqual([sid for _, sid in changes["car1"]], [900])


def track(start_xy, v, seconds, lat0=40.0, lon0=-8.0):
    """5 Hz true positions moving at constant velocity v (m/s east, north) from start_xy (m)."""
    import math
    out = []
    for k in range(int(seconds * 5) + 1):
        t = T0 + k / 5
        x, y = start_xy[0] + v[0] * k / 5, start_xy[1] + v[1] * k / 5
        speed = math.hypot(*v)
        heading = math.degrees(math.atan2(v[0], v[1])) % 360
        out.append((t, lat0 + y / 111195.0, lon0 + x / (111195.0 * math.cos(math.radians(lat0))), speed, heading))
    return out


def cams_of(tr, every=0.2, silent=None):
    """CAMs from a true track, one per `every` s, none in the silent (from, to) window."""
    out, last = [], None
    for t, lat, lon, speed, heading in tr:
        if silent and silent[0] <= t - T0 < silent[1]:
            continue
        if last is None or t - last >= every - 1e-6:
            out.append((t, "c", 100, False, lat, lon, speed, heading, 0))
            last = t
    return out


class Conflicts(unittest.TestCase):
    def setUp(self):
        # car1 from the west and car2 from the south, 10 m/s, both reaching the centre at 6 s
        self.tracks = {2: track((-60, 0), (10, 0), 10), 3: track((0, -60), (0, 10), 10)}
        self.vehicle_of = {2: "car1", 3: "car2"}

    def test_conflict_with_fresh_information(self):
        c = receivers.conflicts(self.tracks, {"car1": cams_of(self.tracks[2]), "car2": cams_of(self.tracks[3])},
                                self.vehicle_of, 1.0, [], extend)
        self.assertEqual(c["conflicts"], 2)   # each vehicle about the other
        self.assertGreater(c["conflict_s"], 2.0)
        self.assertLess(c["exposure_s"], 0.5)
        self.assertEqual(c["missed"], 0)

    def test_silent_vehicle_is_missed(self):
        # car2 silent from 0.5 s to 9 s (the conflict starts at 1.7 s): car1's information about it is stale through the whole conflict
        cams = {"car1": cams_of(self.tracks[2]), "car2": cams_of(self.tracks[3], silent=(0.5, 9))}
        c = receivers.conflicts(self.tracks, cams, self.vehicle_of, 1.0, [], extend)
        self.assertEqual(c["missed"], 1)
        pair = next(p for p in c["pairs"] if p["receiver"] == "car1")
        self.assertAlmostEqual(pair["exposure_s"], pair["conflict_s"], places=1)
        other = next(p for p in c["pairs"] if p["receiver"] == "car2")
        self.assertLess(other["exposure_s"], 0.5)

    def test_outage_ticks_left_out(self):
        cams = {"car1": cams_of(self.tracks[2]), "car2": cams_of(self.tracks[3], silent=(0.5, 9))}
        c = receivers.conflicts(self.tracks, cams, self.vehicle_of, 1.0, [(T0, T0 + 10)], extend)
        self.assertEqual(c["conflicts"], 0)

    def test_one_vehicle(self):
        self.assertIsNone(receivers.conflicts({2: self.tracks[2]}, {"car1": []}, self.vehicle_of, 1.0, [], extend))


if __name__ == "__main__":
    unittest.main()
