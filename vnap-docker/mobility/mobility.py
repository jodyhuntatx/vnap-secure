#!/usr/bin/env python3
"""Mobility client: moves V2X stations along routes.

Publishes each vehicle's position on the control channel broker, topic
<topic_prefix>/<station_id> (default vnap/position/<station_id>), as JSON
{"lat": deg, "lon": deg, "speed": m/s, "heading": deg from north}. socktap started with
--position-control-broker applies it to its CAMs and GeoNetworking position vector.

Configuration: JSON in the environment variable MOBILITY_CONFIG (or a file given as the only
argument):
  {"broker": "pseudo-broker", "port": 1883, "topic_prefix": "vnap/position", "rate_hz": 5,
   "vehicles": [{"station_id": 2, "route": [[40.208106, -8.4227848], [40.208106, -8.4167664]],
                 "speed_kmh": 50, "start_s": 0, "loop": true}]}
A vehicle waits at its first waypoint until start_s, then drives the route at constant speed.
With loop it drives back to the first waypoint and starts over; without, it stops at the end.

Random turns: instead of "route", {"crossing": [lat, lon], "arm_m": 150, "start_arm": "east"}
drives laps through a four-way crossing whose four roads (arms) end on a square ring road.
Each lap: from the end of the arrival arm to the crossing, a random turn there (left,
straight or right; no U-turn), out to the end of the exit arm, a random direction along the
ring to the next arm, and back in on that arm, which is the next lap's arrival arm. Every lap
is 4 x arm_m long, so vehicles keep their relative timing. "seed" (top level) makes the
choices reproducible; each lap's choices are logged.

Mix zones (optional): "mix_zones": [{"name": "x", "center": [lat, lon], "radius_m": 40,
"stations": [2, 3]}] sends a pseudonym change event to a vehicle each time it enters a zone
(topic <pseudonym_topic>/<station_id>/change, default vnap/pseudonym), so ID changes happen
where vehicles can be confused with each other. Without "stations", every vehicle is watched.
Broker account (optional): CONTROL_USERNAME / CONTROL_PASSWORD.
"""
import json
import math
import os
import random
import signal
import sys
import time

import paho.mqtt.client as mqtt

EARTH_RADIUS = 6371008.8  # m (mean radius)
LOG_EVERY = 10.0  # s


def distance(a, b):
    """Great-circle distance in m between (lat, lon) points (haversine)."""
    la1, lo1, la2, lo2 = map(math.radians, (*a, *b))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 2 * EARTH_RADIUS * math.asin(math.sqrt(h))


def bearing(a, b):
    """Initial bearing in degrees (0 = north, clockwise) from a to b."""
    la1, lo1, la2, lo2 = map(math.radians, (*a, *b))
    y = math.sin(lo2 - lo1) * math.cos(la2)
    x = math.cos(la1) * math.sin(la2) - math.sin(la1) * math.cos(la2) * math.cos(lo2 - lo1)
    return math.degrees(math.atan2(y, x)) % 360.0


class Vehicle:
    def __init__(self, raw, prefix):
        self.station_id = int(raw["station_id"])
        points = [tuple(float(c) for c in p) for p in raw["route"]]
        if len(points) < 2:
            raise ValueError(f"vehicle {self.station_id}: a route needs at least two waypoints")
        for lat, lon in points:
            if not (-90 <= lat <= 90 and -180 <= lon <= 180):
                raise ValueError(f"vehicle {self.station_id}: waypoint ({lat}, {lon}) out of range")
        self.loop = bool(raw.get("loop", False))
        if self.loop and points[0] != points[-1]:
            points.append(points[0])  # closing segment back to the start
        self.segments = []  # (start, end, length m, bearing deg)
        for a, b in zip(points, points[1:]):
            if a != b:
                self.segments.append((a, b, distance(a, b), bearing(a, b)))
        self.length = sum(s[2] for s in self.segments)
        if self.length <= 0:
            raise ValueError(f"vehicle {self.station_id}: route has zero length")
        self.speed = float(raw.get("speed_kmh", 50)) / 3.6
        if not (0 < self.speed <= 163.82):
            raise ValueError(f"vehicle {self.station_id}: speed_kmh must be in (0, 589.7]")
        self.start = float(raw.get("start_s", 0))
        self.topic = raw.get("topic") or f"{prefix}/{self.station_id}"

    def state(self, t):
        """Position, speed and heading t seconds after the client started."""
        driven = (t - self.start) * self.speed
        if driven <= 0:
            a, b, _, head = self.segments[0]
            return a, 0.0, head
        if driven >= self.length and not self.loop:
            a, b, _, head = self.segments[-1]
            return b, 0.0, head
        driven %= self.length
        for a, b, length, head in self.segments:
            if driven <= length:
                f = driven / length
                # linear interpolation is accurate enough for segments of a few km
                return (a[0] + f * (b[0] - a[0]), a[1] + f * (b[1] - a[1])), self.speed, head
            driven -= length
        a, b, _, head = self.segments[-1]
        return b, self.speed, head


class MixZone:
    def __init__(self, raw, vehicles):
        self.name = str(raw.get("name", "zone"))
        self.center = tuple(float(c) for c in raw["center"])
        self.radius = float(raw.get("radius_m", 40))
        if self.radius <= 0:
            raise ValueError(f"mix zone {self.name}: radius_m must be > 0")
        ids = {v.station_id for v in vehicles}
        self.stations = set(int(s) for s in raw.get("stations", ids))
        if not self.stations <= ids:
            raise ValueError(f"mix zone {self.name}: stations {sorted(self.stations - ids)} have no route")
        self.inside = {}  # station id -> inside at the last tick (None: not evaluated yet)

    def entered(self, station_id, position):
        """True when the vehicle has just entered the zone (not when it starts inside)."""
        inside = distance(position, self.center) <= self.radius
        was = self.inside.get(station_id)
        self.inside[station_id] = inside
        return inside and was is False


ARMS = {"north": (1, 0), "east": (0, 1), "south": (-1, 0), "west": (0, -1)}


class CrossingVehicle:
    """Laps through a four-way crossing with random turns (see the module docstring)."""

    def __init__(self, raw, prefix, seed):
        self.station_id = int(raw["station_id"])
        self.center = tuple(float(c) for c in raw["crossing"])
        self.arm = float(raw.get("arm_m", 150))
        if self.arm <= 0:
            raise ValueError(f"vehicle {self.station_id}: arm_m must be > 0")
        self.arrival = raw.get("start_arm", "east")
        if self.arrival not in ARMS:
            raise ValueError(f"vehicle {self.station_id}: start_arm must be one of {', '.join(ARMS)}")
        self.speed = float(raw.get("speed_kmh", 50)) / 3.6
        if not (0 < self.speed <= 163.82):
            raise ValueError(f"vehicle {self.station_id}: speed_kmh must be in (0, 589.7]")
        self.start = float(raw.get("start_s", 0))
        self.topic = raw.get("topic") or f"{prefix}/{self.station_id}"
        self.loop = True
        self.rng = random.Random(f"{seed}/{self.station_id}")
        self.segments, self.length, self.laps = [], 0.0, 0
        self.pending_log = []
        self._add_lap()

    def point(self, *arms):
        """Centre plus arm_m along each given arm (an arm end, or a ring corner for two arms)."""
        north = sum(ARMS[a][0] for a in arms) * self.arm
        east = sum(ARMS[a][1] for a in arms) * self.arm
        lat = self.center[0] + north / 111195.0
        lon = self.center[1] + east / (111195.0 * math.cos(math.radians(self.center[0])))
        return (lat, lon)

    @staticmethod
    def turn_name(arrival, exit_arm):
        """Turn seen by a driver coming in on 'arrival' and leaving on 'exit_arm'."""
        hn, he = (-ARMS[arrival][0], -ARMS[arrival][1])   # heading towards the centre
        en, ee = ARMS[exit_arm]
        if (en, ee) == (hn, he):
            return "straight"
        # z of heading x exit in (east, north) coordinates: negative = clockwise = right
        return "right" if he * en - hn * ee < 0 else "left"

    def _add_lap(self):
        a = self.arrival
        exit_arm = self.rng.choice([x for x in ARMS if x != a])              # no U-turn
        nxt = self.rng.choice([x for x in ARMS if ARMS[x][0] * ARMS[exit_arm][0] + ARMS[x][1] * ARMS[exit_arm][1] == 0])
        points = [self.point(a), self.center, self.point(exit_arm), self.point(exit_arm, nxt), self.point(nxt)]
        lap_start = self.start + self.length / self.speed
        for p, q in zip(points, points[1:]):
            self.segments.append((p, q, distance(p, q), bearing(p, q)))
            self.length += self.segments[-1][2]
        self.laps += 1
        self.pending_log.append(f"station {self.station_id} lap {self.laps} (from t={lap_start:.1f} s): in from {a}, "
                                f"{self.turn_name(a, exit_arm)} turn, out {exit_arm}, ring to {nxt}")
        self.arrival = nxt

    def state(self, t):
        driven = (t - self.start) * self.speed
        if driven <= 0:
            a, b, _, head = self.segments[0]
            return a, 0.0, head
        while driven > self.length:
            self._add_lap()
        for a, b, length, head in self.segments:
            if driven <= length:
                f = driven / length
                return (a[0] + f * (b[0] - a[0]), a[1] + f * (b[1] - a[1])), self.speed, head
            driven -= length
        a, b, _, head = self.segments[-1]
        return b, self.speed, head


def load_config():
    if len(sys.argv) > 1:
        with open(sys.argv[1]) as f:
            return json.load(f)
    raw = os.environ.get("MOBILITY_CONFIG")
    if not raw:
        sys.exit("mobility: set MOBILITY_CONFIG (JSON) or pass a config file")
    return json.loads(raw)


def main():
    cfg = load_config()
    prefix = cfg.get("topic_prefix", "vnap/position").rstrip("/")
    seed = cfg.get("seed", random.randrange(1 << 32))
    try:
        vehicles = [CrossingVehicle(v, prefix, seed) if "crossing" in v else Vehicle(v, prefix)
                    for v in cfg.get("vehicles", [])]
    except (KeyError, TypeError, ValueError) as e:
        sys.exit(f"mobility: bad configuration: {e}")
    if not vehicles:
        sys.exit("mobility: no vehicles configured")
    try:
        zones = [MixZone(z, vehicles) for z in cfg.get("mix_zones", [])]
    except (KeyError, TypeError, ValueError) as e:
        sys.exit(f"mobility: bad mix zone: {e}")
    pseudonym_topic = cfg.get("pseudonym_topic", "vnap/pseudonym").rstrip("/")
    period = 1.0 / float(cfg.get("rate_hz", 5))
    broker, port = cfg.get("broker", "pseudo-broker"), int(cfg.get("port", 1883))

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="vnap-mobility")
    if os.environ.get("CONTROL_USERNAME"):
        client.username_pw_set(os.environ["CONTROL_USERNAME"], os.environ.get("CONTROL_PASSWORD", ""))
    client.on_connect = lambda c, u, f, rc, p: print(f"[MOBILITY] connected to {broker}:{port} ({rc})")
    client.on_disconnect = lambda c, u, f, rc, p: print(f"[MOBILITY] disconnected ({rc}), reconnecting")
    client.reconnect_delay_set(1, 30)
    client.connect_async(broker, port, 60)
    client.loop_start()

    running = True

    def stop(*_):
        nonlocal running
        running = False
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)

    for v in vehicles:
        if isinstance(v, CrossingVehicle):
            print(f"[MOBILITY] station {v.station_id}: random turns at {v.center[0]:.6f} {v.center[1]:.6f}, arms "
                  f"{v.arm:.0f} m, {v.speed * 3.6:.0f} km/h from t={v.start:.1f} s, topic {v.topic}")
        else:
            print(f"[MOBILITY] station {v.station_id}: {len(v.segments)} segment(s), {v.length:.0f} m"
                  f"{' loop' if v.loop else ''}, {v.speed * 3.6:.0f} km/h from t={v.start:.0f} s, topic {v.topic}")
    if any(isinstance(v, CrossingVehicle) for v in vehicles):
        print(f"[MOBILITY] random seed {seed}")
    for z in zones:
        print(f"[MOBILITY] mix zone {z.name}: {z.center[0]:.6f} {z.center[1]:.6f} radius {z.radius:.0f} m, "
              f"stations {sorted(z.stations)}: pseudonym change on entry")
    events = 0
    t0 = time.monotonic()
    next_tick, next_log = t0, t0
    while running:
        now = time.monotonic()
        t = now - t0
        log = now >= next_log
        for v in vehicles:
            (lat, lon), speed, heading = v.state(t)
            payload = {"lat": round(lat, 7), "lon": round(lon, 7), "speed": round(speed, 2),
                       "heading": round(heading, 1)}
            # retained: a station that (re)connects gets its current position at once
            client.publish(v.topic, json.dumps(payload), qos=0, retain=True)
            for line in getattr(v, "pending_log", []):
                print(f"[MOBILITY] {line}")
            if getattr(v, "pending_log", None):
                v.pending_log.clear()
            for z in zones:
                if v.station_id in z.stations and z.entered(v.station_id, (lat, lon)):
                    events += 1
                    event = {"event_id": f"mobility-{events}", "reason": f"mix-zone {z.name}"}
                    client.publish(f"{pseudonym_topic}/{v.station_id}/change", json.dumps(event), qos=1)
                    print(f"[MOBILITY] t={t:6.1f} s station {v.station_id} entered mix zone {z.name}: "
                          f"change event {event['event_id']}")
            if log:
                print(f"[MOBILITY] t={t:6.1f} s station {v.station_id}: {payload['lat']:.6f} {payload['lon']:.6f} "
                      f"{speed * 3.6:.0f} km/h heading {heading:.0f}")
        if log:
            next_log += LOG_EVERY
        next_tick += period
        time.sleep(max(0.0, next_tick - time.monotonic()))
    client.loop_stop()
    client.disconnect()


if __name__ == "__main__":
    main()
