# Vehicle movement

Upstream socktap has a fixed position (`config.ini`, or `VANETZA_LATITUDE` /
`VANETZA_LONGITUDE`) or reads one from gpsd. With `--position-control-broker`
(`POSITION_CONTROL_BROKER`), the position becomes controllable at runtime, and a mobility
client drives the vehicles.

## Position control channel

- **Station side:** socktap subscribes to `vnap/position/<station id>` on the control broker,
  on its own MQTT connection. Each update replaces the position fix that feeds:
  - the CAM: reference position, heading, speed, acceleration, yaw rate;
  - the GeoNetworking position vector of every packet.

  Updates go to the configured station ID, so a station keeps its topic across
  [ID changes](pseudonym-change.md).
- **Payload and access:** see [control channels](../reference/control-channels.md#position).
  The static position provider is replaced by a thread-safe controllable one.

## Mobility client

`sim/images/mobility/`, image `vnap-mobility`, container `mobility`:

- **Routes:** each vehicle waits at its first waypoint until `start_s`, then drives the polyline
  at constant speed. It publishes at `rate_hz` (default 5) with the retain flag. With `loop` it
  drives back to the first waypoint and starts over; without, it stops at the last one.
- **Random turns:** instead of a `route`,
  `mobility = { crossing = [lat, lon], arm_m = 150, start_arm = "east", speed_kmh = 36 }`
  drives laps through a four-way crossing whose arms end on a square ring road. Each lap is a
  random turn at the crossing (no U-turns), a random way along the ring, and back in on the
  next arm.
  - Every lap is 4 × `arm_m`, so vehicles keep their relative timing.
  - The choices come from `control.mobility_seed`. `vnapctl` picks and records one if unset;
    `status` shows it, so a run can be repeated.
- **Logging:** every vehicle's position every 10 s, and each lap's choices (`docker logs
  mobility`).
- **Scenarios:** a station's `mobility` key gives its route or crossing. `vnapctl` then:
  - connects the station to the control network;
  - starts it at the first waypoint;
  - starts the mobility client.

  The scenario needs a `[control]` section ([scenario format](../reference/scenario-format.md);
  examples: `linkability`, `convoy`, `crossing`).

## Mix zones

A mix zone is a place where several vehicles change pseudonym at about the same time and
place, so that an observer cannot tell which new identity belongs to which old one.

**The problem.** A vehicle broadcasts CAMs several times a second with its position, speed and
heading. Changing the pseudonym certificate alone hides little: when one car changes on an
empty road, an eavesdropper sees one identity stop and another appear a moment later on the
same trajectory, and links them. A change protects privacy only when there are other
candidates for the new identity.

**How a mix zone creates that ambiguity:**

- **Many vehicles together:** every vehicle entering the zone changes there, so several old
  identities disappear and several new ones appear in the same small area.
- **Unpredictable movement:** the zone is where trajectories diverge, typically an
  intersection, so extrapolating a vehicle's heading does not single out one new identity.
- **Silence:** vehicles stop transmitting for a random time after the change, so the observer
  has a gap in position and in message timing instead of a continuous track.

The privacy gained is roughly the number of vehicles that could plausibly be the same one
(the anonymity set). A vehicle alone in a zone gains nothing.

**In the simulation:**

| Element | Role |
|---|---|
| `[[control.mix_zones]]` | the zone: the mobility client sends a change event to each vehicle entering it, which stands in for a vehicle knowing it is in a designated zone |
| `silent_min_ms`, `silent_max_ms` (`pseudonyms`) | the random radio silence after a full ID change ([pseudonym change](pseudonym-change.md)) |
| crossing mobility with random turns | unpredictable exits (`crossing`). With fixed turns (`crossing:blocks`) the eavesdropper's mistakes form a pattern an attacker could undo |
| `min_vehicles` | the "many vehicles together" condition: a vehicle does not spend a pseudonym on a change that would be linked anyway |
| the eavesdropper and its score | the test of the zone: it links old and new identities by position and CAM timing, and the score against ground truth says how well the zone worked ([eavesdropper and scoring](eavesdropper-and-scoring.md)) |

The mix zone is the countermeasure, the eavesdropper measures it, and the parameters above
are what to vary to see when linking fails.

**Configuration.** `[[control.mix_zones]]` (name, center, radius, stations) makes the mobility client send a
pseudonym change event to a vehicle each time it enters the zone, instead of on a clock. Set
`control.client.mode = "manual"` for that. The events carry the reason `mix-zone <name>`
and show up in `vnapctl events`.

**Threshold.** `min_vehicles = n` makes the change conditional on company in the zone
(examples: `crossing:min3`, `crossing:blocks-min3`):

- A vehicle that enters waits while fewer than `n` vehicles are inside.
- When the `n`-th vehicle is inside, every waiting vehicle gets its change event in the same
  position update, so they change together.
- While `n` or more stay inside, a later arrival changes on entry.
- A vehicle that leaves before the threshold is reached keeps its pseudonym; the mobility
  client logs `left mix zone <name> without a change`.

Only the stations the zone watches count (`stations`, by default every station with mobility
and pseudonyms), so an RSU at the crossing does not. A vehicle changes at most once per visit,
and one that starts inside the zone counts but does not change. The default `min_vehicles = 1`
changes every vehicle on entry.

## Dynamic mix zones

A dynamic mix zone is a mix zone that travels with the vehicles: instead of a fixed circle on
the map, the condition is that enough vehicles are near each other, wherever they are.

- **Rule:** a participating vehicle gets a pseudonym change event when at least `min_vehicles`
  participating vehicles, itself included, are within `radius_m` of it.
- **Cooldown:** at most one such change per vehicle every `cooldown_s`. The first one comes
  `cooldown_s` after the start at the earliest. Vehicles that stay together (a convoy) change
  again each time the cooldown ends.
- **Together:** vehicles that meet with the same threshold change in the same position
  update. With different thresholds, the one with the lower threshold may change alone.
- **Events** carry the reason `dynamic-mix-zone`. Set `control.client.mode = "manual"` so that
  no clock-driven changes mix in.

Two scopes select who takes part and with which threshold:

| Scope | Where | Effect |
|---|---|---|
| Run | `[control.dynamic_mix_zone]` with `enabled`, `min_vehicles`, `radius_m`, `cooldown_s` | every station with mobility and pseudonyms takes part (`enabled = false`: only the stations that say so) |
| Station | `dynamic_mix_zone = { enabled = …, min_vehicles = … }` in a station | this station takes part or not, with its own threshold; replaces the run scope for it |

Example: `convoy` (three cars, all three within 80 m, every 20 s).

**With fixed mix zones.** Fixed mix zones and a dynamic one can be used together, with one
change per vehicle every `cooldown_s` from either. Whichever fires first changes the vehicle.
For `cooldown_s` after that, the others send no event to it, and the mobility log notes them as
`station N held off (mix zone crossing): changed 0.8 s ago by dynamic mix zone, hold-off 30 s`.
Without this, a car reaching a 40 m crossing zone right after a 50 m dynamic zone fired would
get two events within a second or two. The station rejects the second one if it comes within
`min_interval_ms`. Otherwise it changes again inside the first change's silent period and
spends a pseudonym for nothing. With fixed mix zones only there is no hold-off.

**It is an oracle.** The mobility client knows every vehicle's true position, so vehicles
react to ground truth, not to what they heard on the radio. It shows whether proximity-based
changes help at all; a vehicle deciding from the CAMs it receives would be a change to the
station.

## Recorded positions

The mobility client writes every vehicle's true position, speed and heading at each update (5
times a second) to `/logs/positions.jsonl` in its container. The service and `vnapctl collect`
save it with the run's results, and the analysis of the run uses it for position error during
reception gaps and for the distance an eavesdropper followed a vehicle
([web UI](../operations/web-ui.md#privacy-and-cost-of-the-pseudonym-changes)). The file is in
memory (tmpfs), so a stalled disk cannot stall the vehicles; it grows by under 1 MB per hour and
vehicle. A gap between two lines longer than a second means the host stalled.

## Origin

Stations without a position, and the example layouts, are placed around the crossing of Rua
Alexandre Herculano and Rua Venâncio Rodrigues in Coimbra (40.208106, −8.4197756;
`ORIGIN` in `sim/vnapsim/common.py`). The crossings, arms and ring roads are synthetic
geometry in metres around that point. They do not follow the real streets; for realistic
paths, draw routes along streets in the web UI's builder.

## Watching movement

```bash
# positions in the CAMs the RSU receives
docker run --rm --network vanetzalan0 eclipse-mosquitto:2 mosquitto_sub -h 192.168.98.10 -t vanetza/out/cam \
  | jq -c '.fields.cam.camParameters | [.basicContainer.referencePosition.latitude,
           .basicContainer.referencePosition.longitude,
           .highFrequencyContainer.basicVehicleContainerHighFrequency.heading.headingValue,
           .highFrequencyContainer.basicVehicleContainerHighFrequency.speed.speedValue]'
```

The web UI's *Map* tab shows the layout and live positions, and moves a station on click.

## CAM kinematics

Upstream filled these fields from a static position only, and several were wrong in units
once the vehicle moves. The patch set fixes them:
- **Heading:** was degrees copied into a field in 0.1°.
- **Speed and heading confidence:** were m/s and degrees copied into fields in 0.01 m/s and
  0.1°. A confidence of 0.1 m/s became the invalid value 0, and the OBU could not encode its
  CAMs (`Can't determine size for unaligned PER encoding of type CAM because of SpeedConfidence`).
- **Longitudinal acceleration and yaw rate:** had the wrong scale. The yaw rate also had the
  wrong sign: ETSI counts it positive to the left, while the heading grows clockwise.

The JSON on `vanetza/out/cam` shows the decoded physical values (m/s, degrees).

## Related

- **Patches:** [mobility and CAM](../patches/mobility-and-cam.md).
- **Results:** the convoy and mix zone results are in [results](../../results/README.md).
