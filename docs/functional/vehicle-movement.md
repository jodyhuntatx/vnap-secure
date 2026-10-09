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
  examples: `c-its-pki-traffic`, `c-its-pki-convoy`, `c-its-pki-mixzone-random`).

## Mix zones

`[[control.mix_zones]]` (name, center, radius, stations) makes the mobility client send a
pseudonym change event to a vehicle each time it enters the zone, instead of on a clock. Set
`control.client.mode = "manual"` for that. The events carry the reason `mix-zone <name>`
and show up in `vnapctl events`.

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
