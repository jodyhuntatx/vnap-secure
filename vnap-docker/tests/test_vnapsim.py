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


if __name__ == "__main__":
    unittest.main()
