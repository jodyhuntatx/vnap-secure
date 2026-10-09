# Patches: mobility and CAM

The behaviour is described in [vehicle movement](../functional/vehicle-movement.md) and
[pseudonym change](../functional/pseudonym-change.md#cam-timing).

## Position control channel

- **Change:**
  - `tools/socktap/mobility.{hpp,cpp}` (new) subscribes to the station's position topic on the
    control broker;
  - `positioning.cpp` replaces the static position provider with a thread-safe controllable
    one, whose fix feeds the CAM and the GeoNetworking position vector.
- **Options:** `--position-control-broker`, `-port`, `-topic`, `-username`, `-password`
  (`POSITION_CONTROL_*`).
- **Logs:** `[MOBILITY]` lines, including rejected updates (the first 5).
- **Files:** `mobility.{hpp,cpp}`, `positioning.cpp`, `main.cpp`,
  `tools/socktap/CMakeLists.txt`, `entrypoint.sh`.

## CAM kinematics

- **Problem:** upstream filled the CAM's kinematic fields from a static position only, and
  several were wrong in units once the vehicle moved:
  - **Heading:** degrees went into a field in 0.1°.
  - **Speed and heading confidence:** m/s and degrees went into fields in 0.01 m/s and 0.1°;
    a confidence of 0.1 m/s became the invalid value 0, and the CAM could not be encoded.
  - **Longitudinal acceleration and yaw rate:** wrong scale; the yaw rate also had the wrong
    sign.
- **Change:** `tools/socktap/applications/cam_application.cpp` converts each field to the
  CAM's units and sign conventions.
- **Upstream:** candidate.

## CAM timer rephase

- **Problem:** the CAM timer kept running through an ID change, so each car's position within
  the 1 s CAM cycle carried over to its new identity and linked the two.
- **Change:** on COMMIT of a full ID change, `cam_application.{hpp,cpp}` skips the next CAM and
  restarts the timer after a random delay of up to one interval
  (`[IDCHANGE] CAM timer: new phase, next CAM in <n> ms`). `main.cpp` wires the CAM application
  to the ID change service.
- **Effect:** a timing-only linker went from 16 of 16 changes matched to 1 of 13 in the
  random-turn mix zone.

## Testing

- **Scenarios:** `c-its-pki-traffic` (positions in the received CAMs), `c-its-pki-convoy` and
  the mix zone scenarios.
- **Results:** the mix zone reruns on the rephased image are in
  [results](../../results/README.md).
