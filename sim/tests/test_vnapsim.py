"""Unit tests of the vnapsim package that need no docker: python3 -m unittest discover -s tests"""
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from vnapsim.common import PKI_KEYS, STATION_KEYS, ScenarioError  # noqa: E402
from vnapsim.scenario import assign_identities, error_path, load_scenario, policy_violations  # noqa: E402
from vnapsim.schema import SCENARIO_SCHEMA  # noqa: E402

POLICY = {"approved_images": ["vnap:latest"], "allowed_env": ["VANETZA_LATITUDE"]}


def scenario_file(text):
    f = tempfile.NamedTemporaryFile("w", suffix=".toml", delete=False)
    f.write(text)
    f.close()
    return f.name


class SchemaTest(unittest.TestCase):
    def test_station_keys_match_validator(self):
        self.assertEqual(set(SCENARIO_SCHEMA["properties"]["stations"]["items"]["properties"]), STATION_KEYS)

    def test_pki_keys_match_validator(self):
        self.assertEqual(set(SCENARIO_SCHEMA["properties"]["pki"]["properties"]), PKI_KEYS)

    def test_every_scenario_file_uses_known_top_level_keys(self):
        import tomllib
        known = set(SCENARIO_SCHEMA["properties"])
        for name in os.listdir(os.path.join(os.path.dirname(HERE), "scenarios")):
            if name.endswith(".toml"):
                with open(os.path.join(os.path.dirname(HERE), "scenarios", name), "rb") as f:
                    self.assertLessEqual(set(tomllib.load(f)), known, name)


class ErrorPathTest(unittest.TestCase):
    def test_paths(self):
        cases = {
            "stations[obu1]: at_cert and at_key go together": "stations[obu1].at_cert",
            "stations[obu2]: pseudonyms.first must be >= 0": "stations[obu2].pseudonyms.first",
            "stations[rsu]: ip 10.0.0.1 not in 192.168.98.0/24": "stations[rsu].ip",
            "control.client.mode 'x' not one of manual": "control.client.mode",
            "pki: etsi_version v2 does not match the security of rsu": "pki.etsi_version",
            "pki: unknown key(s) color": "pki",
            "control.mix_zones[0]: center must be [lat, lon] in degrees": "control.mix_zones[0].center",
            "eavesdropper ip 1.2.3.4 is used by a station": "eavesdropper.ip",
            "duplicate station mac: 6e": "stations",
        }
        for text, path in cases.items():
            self.assertEqual(error_path(text), path, text)

    def test_records_from_load_scenario(self):
        path = scenario_file('[[stations]]\nname = "a"\nip = "10.0.0.1"\nsecurity = "nope"\n')
        with self.assertRaises(ScenarioError) as ctx:
            load_scenario(path)
        paths = {e["path"] for e in ctx.exception.errors}
        self.assertIn("stations[a].ip", paths)
        self.assertIn("stations[a].security", paths)
        self.assertTrue(all(e["text"] in str(ctx.exception) for e in ctx.exception.errors))


class AssignmentTest(unittest.TestCase):
    def test_defaults_follow_the_example_layout(self):
        out, assigned = assign_identities([{"name": "rsu", "station_type": 15}, {"name": "a"}, {"name": "b"}], "192.168.98.0/24")
        self.assertEqual([s["ip"] for s in out], ["192.168.98.10", "192.168.98.20", "192.168.98.30"])
        self.assertEqual([s["station_id"] for s in out], [1, 2, 3])
        self.assertEqual(out[0]["mac"], "6e:06:e0:03:00:01")
        self.assertEqual(out[0]["station_type"], 15)
        self.assertEqual(out[1]["station_type"], 5)
        self.assertIn("ip", assigned[0])
        self.assertNotIn("station_type", assigned[0])

    def test_explicit_values_are_kept_and_avoided(self):
        out, _ = assign_identities([{"name": "a", "ip": "192.168.98.10", "station_id": 1}, {"name": "b"}], "192.168.98.0/24")
        self.assertEqual(out[1]["ip"], "192.168.98.20")
        self.assertEqual(out[1]["station_id"], 2)

    def test_reserved_hosts_are_skipped(self):
        out, _ = assign_identities([{"name": f"s{i}"} for i in range(40)], "192.168.98.0/24")
        hosts = {int(s["ip"].rsplit(".", 1)[1]) for s in out}
        self.assertEqual(len(hosts), 40)
        self.assertFalse(hosts & {0, 1, 99, 255})


class PolicyTest(unittest.TestCase):
    def test_user_may_not_set_admin_or_assigned_fields(self):
        data = {"image": "busybox", "certs_dir": "/etc",
                "stations": [{"name": "a", "ip": "1.2.3.4", "env": {"LD_PRELOAD": "x"}, "at_cert": "/x"},
                             {"name": "b", "env": {"VANETZA_LATITUDE": "40"}, "pseudonyms": {"cert": "c{i}"}}],
                "control": {"auth": {"username_env": "A"}, "broker_ip": "1.2.3.4", "client": {"mode": "manual"}}}
        found = {m.split(": ")[0] for m in policy_violations(data, POLICY)}
        self.assertEqual(found, {"image", "certs_dir", "stations[a].ip", "stations[a].env", "stations[a].at_cert",
                                 "stations[b].pseudonyms.cert", "control.auth", "control.broker_ip"})

    def test_approved_image_and_allowed_env_pass(self):
        data = {"image": "vnap:latest", "stations": [{"name": "a", "env": {"VANETZA_LATITUDE": "40"}}]}
        self.assertEqual(policy_violations(data, POLICY), [])

    def test_user_scenario_validates(self):
        path = scenario_file('[defaults]\nsecurity = "certs-v3"\n[pki]\n[[stations]]\nname = "rsu"\nstation_type = 15\n'
                             '[[stations]]\nname = "car"\npseudonyms = {}\n[control]\n')
        sc = load_scenario(path, role="user", policy=POLICY)
        car = sc["stations"][1]
        self.assertEqual(car["pki"]["certificates"], "bke")   # an empty pseudonyms table is a pool with the defaults
        self.assertEqual(sc["stations"][0]["pki"]["certificates"], "regular")


class DynamicMixZoneScenarioTest(unittest.TestCase):
    BASE = ('[defaults]\nsecurity = "certs-v3"\n[pki]\n[[stations]]\nname = "rsu"\nstation_type = 15\n'
            + "".join(f'[[stations]]\nname = "car{i}"\npseudonyms = {{}}\n'
                      f'mobility = {{ crossing = [40.2, -8.4], start_arm = "{arm}" }}\n%s\n' for i, arm in ((1, "east"), (2, "north"), (3, "west")))
            + "[control]\n%s\n")

    def zone(self, *parts):
        sc = load_scenario(scenario_file(self.BASE % parts), role="user", policy=POLICY)
        return sc["control"]["mobility"]["dynamic_mix_zone"], [s["station_id"] for s in sc["stations"]]

    def errors(self, *parts):
        with self.assertRaises(ScenarioError) as ctx:
            load_scenario(scenario_file(self.BASE % parts), role="user", policy=POLICY)
        return [e["text"] for e in ctx.exception.errors]

    def test_off_without_the_tables(self):
        self.assertIsNone(self.zone("", "", "", "")[0])

    def test_run_scope_takes_every_moving_pseudonym_station(self):
        zone, ids = self.zone("", "", "", "[control.dynamic_mix_zone]\nmin_vehicles = 3\nradius_m = 60")
        self.assertEqual(zone, {"radius_m": 60, "cooldown_s": 30,
                                "stations": [{"station_id": i, "min_vehicles": 3} for i in ids[1:]]})   # not the RSU

    def test_station_scope_overrides_the_run_scope(self):
        zone, ids = self.zone("dynamic_mix_zone = { enabled = false }", "dynamic_mix_zone = { min_vehicles = 1 }", "",
                              "[control.dynamic_mix_zone]")
        self.assertEqual(zone["stations"], [{"station_id": ids[2], "min_vehicles": 1}, {"station_id": ids[3], "min_vehicles": 2}])
        zone, ids = self.zone("dynamic_mix_zone = {}", "dynamic_mix_zone = { min_vehicles = 2 }", "",
                              "[control.dynamic_mix_zone]\nenabled = false\ncooldown_s = 10")
        self.assertEqual(zone, {"radius_m": 50, "cooldown_s": 10, "stations": [{"station_id": i, "min_vehicles": 2} for i in ids[1:3]]})

    def test_station_color(self):
        sc = load_scenario(scenario_file(self.BASE % ('color = "#1A2b3c"', "", "", "")), role="user", policy=POLICY)
        self.assertEqual([s["color"] for s in sc["stations"]], [None, "#1A2b3c", None, None])
        self.assertIn('stations[car1]: color must be "#rrggbb"', self.errors('color = "red"', "", "", ""))

    def test_errors(self):
        self.assertIn("stations[car1]: dynamic_mix_zone min_vehicles 4 is more than the 3 station(s) taking part",
                      self.errors("dynamic_mix_zone = { min_vehicles = 4 }", "", "", "[control.dynamic_mix_zone]"))
        self.assertIn("control.dynamic_mix_zone: cooldown_s must be >= 1",
                      self.errors("", "", "", "[control.dynamic_mix_zone]\ncooldown_s = 0"))
        self.assertIn("control.dynamic_mix_zone: unknown key(s) radius",
                      self.errors("", "", "", "[control.dynamic_mix_zone]\nradius = 5"))
        text = self.BASE.replace('station_type = 15\n', 'station_type = 15\ndynamic_mix_zone = {}\n') % ("", "", "", "")
        with self.assertRaises(ScenarioError) as ctx:
            load_scenario(scenario_file(text), role="user", policy=POLICY)
        self.assertIn("stations[rsu]: dynamic_mix_zone needs both mobility and pseudonyms", [e["text"] for e in ctx.exception.errors])


class ScoringTest(unittest.TestCase):
    def test_purity_and_longest_followed_chain(self):
        from vnapsim.scoring import score
        truth = {"a1": "obu1", "a2": "obu1", "a3": "obu1", "b1": "obu2", "b2": "obu2"}

        def link(track, old, new, evidence="position (12 m)"):
            return {"event": "pseudonym_change_linked", "track": track, "old": old, "new": new,
                    "evidence": [evidence], "merged_track": f"T-{new}"}
        events = [{"event": "new_track", "track": "V1", "identifiers": {"cert": "a1"}},
                  {"event": "new_track", "track": "T-a2", "identifiers": {"cert": "a2"}},
                  link("V1", "a1", "a2"),
                  {"event": "new_track", "track": "T-a3", "identifiers": {"cert": "a3"}},
                  link("V1", "a2", "a3"),
                  {"event": "new_track", "track": "T-b1", "identifiers": {"cert": "b1"}},
                  link("V1", "a3", "b1", "timing (0.4 s)"),          # wrong: obu1 -> obu2
                  {"event": "new_track", "track": "V2", "identifiers": {"cert": "b2"}}]
        r = score(truth, events)
        self.assertEqual((r["links"], r["correct"], r["wrong"]), (3, 2, 1))
        self.assertEqual(r["by_technique"], {"position": {"correct": 2, "wrong": 0}, "timing": {"correct": 0, "wrong": 1}})
        v1, v2 = r["tracks"]
        self.assertEqual((v1["stations"], v1["purity"], v1["followed_changes"]), (["obu1", "obu1", "obu1", "obu2"], 0.75, 2))
        self.assertEqual((v2["identities"], v2["purity"], v2["followed_changes"]), (1, 1.0, 0))
        self.assertEqual(r["longest_followed"], {"changes": 2, "station": "obu1", "track": "V1"})
        self.assertEqual(score(truth, [])["longest_followed"], {"changes": 0, "station": None, "track": None})

if __name__ == "__main__":
    unittest.main()
