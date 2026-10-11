"""Mix zone and dynamic mix zone logic of the mobility client (sim/images/mobility/mobility.py)."""

import os
import sys
import types
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
# the client imports paho-mqtt, which only its image has: the zone logic does not use it
paho = types.ModuleType("paho")
paho.mqtt = types.ModuleType("paho.mqtt")
paho.mqtt.client = types.ModuleType("paho.mqtt.client")
for name, module in (("paho", paho), ("paho.mqtt", paho.mqtt), ("paho.mqtt.client", paho.mqtt.client)):
    sys.modules.setdefault(name, module)
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "images", "mobility"))
from mobility import DynamicMixZone, MixZone  # noqa: E402

IN, OUT = (40.0, -8.0), (40.01, -8.0)   # the zone's centre; about 1.1 km north of it


def zone(min_vehicles=None, stations=(2, 3, 4)):
    raw = {"name": "z", "center": list(IN), "radius_m": 40}
    if min_vehicles is not None:
        raw["min_vehicles"] = min_vehicles
    return MixZone(raw, [types.SimpleNamespace(station_id=s) for s in stations])


def tick(z, *inside):
    return z.update({s: IN if s in inside else OUT for s in z.stations})


class MixZoneTest(unittest.TestCase):
    def test_default_changes_each_vehicle_on_entry(self):
        z = zone()
        self.assertEqual(tick(z), ([], 0, []))
        self.assertEqual(tick(z, 2), ([2], 1, []))
        self.assertEqual(tick(z, 2), ([], 1, []))          # once per visit
        self.assertEqual(tick(z, 2, 3), ([3], 2, []))
        self.assertEqual(tick(z), ([], 0, []))
        self.assertEqual(tick(z, 2), ([2], 1, []))          # the next visit

    def test_a_vehicle_starting_inside_does_not_change(self):
        z = zone()
        self.assertEqual(tick(z, 2), ([], 1, []))

    def test_waiting_vehicles_change_together_at_the_threshold(self):
        z = zone(3)
        tick(z)
        self.assertEqual(tick(z, 2), ([], 1, []))
        self.assertEqual(tick(z, 2, 3), ([], 2, []))
        self.assertEqual(tick(z, 2, 3, 4), ([2, 3, 4], 3, []))
        self.assertEqual(tick(z, 2, 3, 4), ([], 3, []))

    def test_later_arrival_changes_on_entry_while_the_threshold_holds(self):
        z = zone(2)
        tick(z)
        tick(z, 2)
        self.assertEqual(tick(z, 2, 3), ([2, 3], 2, []))
        self.assertEqual(tick(z, 2, 3, 4), ([4], 3, []))

    def test_a_vehicle_that_never_meets_the_threshold_keeps_its_pseudonym(self):
        z = zone(2)
        tick(z)
        self.assertEqual(tick(z, 2), ([], 1, []))
        self.assertEqual(tick(z), ([], 0, [2]))             # left without a change
        self.assertEqual(tick(z, 3), ([], 1, []))           # 2 is no longer waiting
        self.assertEqual(tick(z, 3, 4), ([3, 4], 2, []))

    def test_a_vehicle_that_started_inside_counts_but_does_not_change(self):
        z = zone(2)
        tick(z, 2)
        self.assertEqual(tick(z, 2, 3), ([3], 2, []))

    def test_min_vehicles_must_fit_the_watched_stations(self):
        for bad in (0, 4):
            with self.assertRaises(ValueError):
                zone(bad)


if __name__ == "__main__":
    unittest.main()


NEAR = (40.0002, -8.0)   # about 22 m north of IN


def dynamic(stations, radius=50, cooldown=30):
    return DynamicMixZone({"radius_m": radius, "cooldown_s": cooldown,
                           "stations": [{"station_id": s, "min_vehicles": n} for s, n in stations.items()]},
                          [types.SimpleNamespace(station_id=s) for s in (2, 3, 4, 5)])


class DynamicMixZoneTest(unittest.TestCase):
    def test_changes_together_when_enough_are_near_and_again_after_the_cooldown(self):
        d = dynamic({2: 3, 3: 3, 4: 3})
        apart = {2: IN, 3: NEAR, 4: OUT, 5: IN}             # station 5 is near but does not take part
        together = {2: IN, 3: NEAR, 4: IN, 5: OUT}
        self.assertEqual(d.update(40.0, apart), [])
        self.assertEqual(d.update(40.2, together), [(2, 3), (3, 3), (4, 3)])
        self.assertEqual(d.update(50.0, together), [])      # cooldown
        self.assertEqual(d.update(70.2, together), [(2, 3), (3, 3), (4, 3)])
        self.assertEqual(d.update(110.0, apart), [])

    def test_no_change_before_the_first_cooldown(self):
        d = dynamic({2: 2, 3: 2})
        self.assertEqual(d.update(29.8, {2: IN, 3: IN, 4: OUT, 5: OUT}), [])
        self.assertEqual(d.update(30.0, {2: IN, 3: IN, 4: OUT, 5: OUT}), [(2, 2), (3, 2)])

    def test_each_station_has_its_own_threshold(self):
        d = dynamic({2: 2, 3: 3, 4: 1}, cooldown=10)
        self.assertEqual(d.update(10.0, {2: IN, 3: NEAR, 4: OUT, 5: OUT}), [(2, 2), (4, 1)])   # 3 needs three, 4 nobody
        self.assertEqual(d.update(12.0, {2: IN, 3: NEAR, 4: IN, 5: OUT}), [(3, 3)])

    def test_configuration_is_checked(self):
        for stations, kw in (({2: 4, 3: 2}, {}), ({9: 2}, {}), ({}, {}), ({2: 2, 3: 2}, {"radius": 0}), ({2: 2, 3: 2}, {"cooldown": 0})):
            with self.assertRaises(ValueError):
                dynamic(stations, **kw)
