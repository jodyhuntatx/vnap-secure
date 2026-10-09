"""JSON Schema of the scenario format (draft 2020-12), for UIs that build forms from it.

"x-vnap" marks fields with a role: {"assigned": true} the service fills them in when they are
left out (addresses, identifiers); {"admin": true} only administrators may set them (images,
container environment, certificate files, credentials, networks). load_scenario() is the
validator; this schema describes the same keys (tests/test_schema.py keeps them in step).
"""

ADMIN = {"x-vnap": {"admin": True}}
ASSIGNED = {"x-vnap": {"assigned": True}}


def _num(desc, minimum=None, maximum=None, default=None, integer=False, **extra):
    s = {"type": "integer" if integer else "number", "description": desc, **extra}
    if minimum is not None:
        s["minimum"] = minimum
    if maximum is not None:
        s["maximum"] = maximum
    if default is not None:
        s["default"] = default
    return s


def _str(desc, default=None, enum=None, **extra):
    s = {"type": "string", "description": desc, **extra}
    if default is not None:
        s["default"] = default
    if enum:
        s["enum"] = enum
    return s


LATLON = {"type": "array", "items": {"type": "number"}, "minItems": 2, "maxItems": 2, "description": "[lat, lon] in degrees"}
SECURITY = ["none", "dummy", "dummy-v2", "dummy-v3", "certs", "certs-v2", "certs-v3"]

PSEUDONYMS = {
    "type": "object", "additionalProperties": False,
    "description": "Pseudonym pool. With [pki]: initial/refill_at/batch; without: cert/key file patterns.",
    "properties": {
        "initial": _num("ATs the station starts with (with [pki])", 1, 256, 8, True),
        "refill_at": _num("request a batch at this many unused ATs; 0: no refill (with [pki])", 0, 255, 2, True),
        "batch": _num("ATs per batch (with [pki])", 1, 256, 8, True),
        "cert": _str("AT file pattern with {i}, under /vnap-certs/ (without [pki])", **ADMIN),
        "key": _str("key file pattern with {i} (without [pki])", **ADMIN),
        "count": _num("number of files (without [pki])", 1, None, None, True, **ADMIN),
        "first": _num("index of the first file (without [pki])", 0, None, 0, True, **ADMIN),
        "min_interval_ms": _num("minimum time between two changes", 0, None, 1000, True),
        "id_change": _str("what a change changes", "full", ["full", "certificate"]),
        "silent_min_ms": _num("radio silence after a full ID change, minimum", 0, None, 0, True),
        "silent_max_ms": _num("radio silence after a full ID change, maximum", 0, None, 0, True),
    },
}

MOBILITY = {
    "description": "Movement: a fixed route, or laps through a crossing with random turns.",
    "oneOf": [
        {"type": "object", "additionalProperties": False, "required": ["route"], "properties": {
            "route": {"type": "array", "items": LATLON, "minItems": 2, "description": "waypoints"},
            "speed_kmh": _num("constant speed", 0, 589.7, 50), "start_s": _num("wait at the first waypoint", 0, None, 0),
            "loop": {"type": "boolean", "default": False, "description": "drive back to the first waypoint and repeat"}}},
        {"type": "object", "additionalProperties": False, "required": ["crossing"], "properties": {
            "crossing": LATLON, "arm_m": _num("length of the roads from the crossing", 10, 5000, 150),
            "start_arm": _str("road the vehicle starts on", "east", ["north", "east", "south", "west"]),
            "speed_kmh": _num("constant speed", 0, 589.7, 50), "start_s": _num("start delay", 0, None, 0)}},
    ],
}

STATION = {
    "type": "object", "additionalProperties": False, "required": ["name"],
    "properties": {
        "name": _str("station name (container <name>[-iN])", pattern="^[a-z0-9][a-z0-9_-]*$"),
        "ip": _str("address on the simulation network", **ASSIGNED),
        "station_id": _num("ITS station ID", 1, 4294967295, None, True, **ASSIGNED),
        "station_type": _num("ETSI station type: 5 passenger car, 15 road-side unit", 0, 255, 5, True),
        "mac": _str("MAC address", **ASSIGNED),
        "security": _str("security entity", "none", SECURITY),
        "at_cert": _str("authorization ticket file under /vnap-certs/ (without [pki])", **ADMIN),
        "at_key": _str("its private key file", **ADMIN),
        "aa_cert": _str("AA certificate file (without [pki])", **ADMIN),
        "root_cert": _str("trusted root certificate file (without [pki])", **ADMIN),
        "pseudonyms": PSEUDONYMS,
        "mobility": MOBILITY,
        "env": {"type": "object", "additionalProperties": {"type": "string"},
                "description": "extra container environment", **ADMIN},
    },
}

SCENARIO_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "https://github.com/jodyhuntatx/vnap-secure/sim/scenario.schema.json",
    "title": "vnap-secure simulation scenario",
    "type": "object", "additionalProperties": False, "required": ["stations"],
    "properties": {
        "name": _str("scenario name (default: file name)"),
        "description": _str("one line"),
        "image": _str("station image", "vnap:latest", **ADMIN),
        "entrypoint": _str("native: the image's entrypoint; stock: unpatched image", "native", ["native", "stock"], **ADMIN),
        "certs_dir": _str("directory mounted at /vnap-certs (without [pki])", **ADMIN),
        "network": {"type": "object", "additionalProperties": False, "description": "the V2X network", **ADMIN,
                    "properties": {"name": _str("docker network", "vanetzalan0"), "subnet": _str("subnet", "192.168.98.0/24")}},
        "defaults": {"type": "object", "additionalProperties": False, "description": "inherited by every station",
                     "properties": {"security": _str("security entity", "none", SECURITY),
                                    "aa_cert": _str("AA certificate file", **ADMIN), "root_cert": _str("root certificate file", **ADMIN),
                                    "env": {"type": "object", "additionalProperties": {"type": "string"}, **ADMIN}}},
        "stations": {"type": "array", "items": STATION, "minItems": 1},
        "pki": {"type": "object", "additionalProperties": False, "description": "the run's own certificate authority",
                "properties": {
                    "enabled": {"type": "boolean", "default": True},
                    "etsi_version": _str("certificate format (default: from the stations' security)", None, ["v2", "v3"]),
                    "validity_hours": _num("AT validity", 1, 8760, 24, True),
                    "initial": _num("ATs per pseudonym station at start", 1, 256, 8, True),
                    "refill_at": _num("request a batch at this many unused ATs", 0, 255, 2, True),
                    "batch": _num("ATs per batch", 1, 256, 8, True),
                    "ip": _str("PKI service address on the control network", **ASSIGNED),
                    "key_derivation": _str("who derives the AT private keys", "station", ["station", "pki"])}},
        "control": {"type": "object", "additionalProperties": False,
                    "description": "control channel: pseudonym events, positions, PKI",
                    "properties": {
                        "network": _str("docker network", "vnapctl0", **ADMIN),
                        "subnet": _str("subnet", "192.168.99.0/24", **ADMIN),
                        "broker_ip": _str("broker address", **ASSIGNED), "client_ip": _str("event client address", **ASSIGNED),
                        "mobility_ip": _str("mobility client address", **ASSIGNED),
                        "mobility_rate_hz": _num("position updates per second", 0, 50, 5),
                        "mobility_seed": _num("random turns seed (default: new)", None, None, None, True),
                        "auth": {"type": "object", "description": "broker account: names of environment variables", **ADMIN,
                                 "properties": {"username_env": _str("variable"), "password_env": _str("variable")}},
                        "client": {"type": "object", "additionalProperties": False, "properties": {
                            "mode": _str("event schedule", "periodic", ["periodic", "random", "once", "manual"]),
                            "interval": _num("seconds between events (periodic)", 1, None, 30),
                            "min_interval": _num("random mode minimum", 1, None, 10),
                            "max_interval": _num("random mode maximum", 1, None, 60),
                            "count": _num("stop after this many events (0: no limit)", 0, None, 0, True),
                            "delay": _num("once mode delay", 0, None, 5), "index": _num("once mode pool index", 0, None, None, True),
                            "stations": {"type": "array", "items": {"type": "integer"},
                                         "description": "station IDs (default: all with pseudonyms)"}}},
                        "mix_zones": {"type": "array", "items": {"type": "object", "additionalProperties": False, "required": ["center"],
                                      "properties": {"name": _str("zone name"), "center": LATLON,
                                                     "radius_m": _num("radius", 0, None, 40),
                                                     "stations": {"type": "array", "items": {"type": "integer"}}}}}}},
        "eavesdropper": {"type": "object", "additionalProperties": False, "description": "passive tracking attacker",
                         "properties": {
                             "enabled": {"type": "boolean", "default": True}, "name": _str("container name", "eavesdropper"),
                             "ip": _str("address on the simulation network", **ASSIGNED),
                             "verbose": {"type": "boolean", "default": False},
                             "summary_interval": _num("seconds between summaries", 1, None, 30),
                             "link_window": _num("position linking: max silence (s)", 0, None, 3.0),
                             "link_distance": _num("position linking: max distance (m)", 0, None, 50.0),
                             "link_by": {"type": "array", "items": {"enum": ["position", "timing"]}, "default": ["position", "timing"]},
                             "timing_window": _num("timing linking: max silence (s)", 0, None, 20.0),
                             "timing_tolerance_ms": _num("timing linking tolerance", 0, None, 25.0)}},
        "check": {"type": "object", "additionalProperties": False, "properties": {
            "defaults": {"type": "boolean", "default": True, "description": "include vnapctl's default expectations"},
            "expect": {"type": "array", "items": {"type": "string"}, "description": "e.g. obu.pki.starved==0"}}},
    },
}
