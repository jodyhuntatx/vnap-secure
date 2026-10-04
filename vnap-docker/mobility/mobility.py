#!/usr/bin/env python3
"""Mobility client: moves V2X stations along routes.

Publishes each vehicle's position on the control channel broker, topic
<topic_prefix>/<station_id> (default vnap/position/<station_id>), as JSON
{"lat": deg, "lon": deg, "speed": m/s, "heading": deg from north}. socktap started with
--position-control-broker applies it to its CAMs and GeoNetworking position vector.

Configuration: JSON in the environment variable MOBILITY_CONFIG (or a file given as the only
argument):
  {"broker": "pseudo-broker", "port": 1883, "topic_prefix": "vnap/position", "rate_hz": 5,
   "vehicles": [{"station_id": 2, "route": [[40.0, -8.003], [40.0, -7.997]],
                 "speed_kmh": 50, "start_s": 0, "loop": true}]}
A vehicle waits at its first waypoint until start_s, then drives the route at constant speed.
With loop it drives back to the first waypoint and starts over; without, it stops at the end.
Broker account (optional): CONTROL_USERNAME / CONTROL_PASSWORD.
"""
import json
import math
import os
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
    try:
        vehicles = [Vehicle(v, prefix) for v in cfg.get("vehicles", [])]
    except (KeyError, TypeError, ValueError) as e:
        sys.exit(f"mobility: bad configuration: {e}")
    if not vehicles:
        sys.exit("mobility: no vehicles configured")
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
        print(f"[MOBILITY] station {v.station_id}: {len(v.segments)} segment(s), {v.length:.0f} m"
              f"{' loop' if v.loop else ''}, {v.speed * 3.6:.0f} km/h from t={v.start:.0f} s, topic {v.topic}")
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
