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

## CAM generation

- **Problem:** upstream sends a CAM every `[cam] periodicity` ms (1000), whatever the vehicle
  does. EN 302 637-2 (clause 6.1.3) sends more often while a vehicle moves or turns. At 1 s
  every CAM also carried the full certificate, which is attached at most once a second, and the
  CAM timing was strictly regular.
- **Change:** `cam_application.{hpp,cpp}` check every 100 ms (T_CheckCamGen) whether a CAM is
  due:
  - **Dynamics:** at least 100 ms since the last CAM, and the heading changed by more than 4°,
    the position by more than 4 m or the speed by more than 0.5 m/s. The time since the last
    CAM becomes T_GenCam.
  - **Timeout:** T_GenCam has elapsed. After 3 CAMs sent this way, T_GenCam returns to
    T_GenCamMax, the configured periodicity.
  - DCC is not applied (T_GenCamDcc = T_GenCamMin = 100 ms).
  - After an ID change, the rephase below applies within the current T_GenCam.
- **Option:** `--cam-generation dynamic` (default) or `fixed` (the upstream behaviour, to
  reproduce results of images up to `vnap:r2-p20`); entrypoint `CAM_GENERATION`. socktap logs
  `[CAM] generation: dynamic (...)` at start.
- **Upstream:** candidate.

## CAM timer rephase

- **Problem:** the CAM timer kept running through an ID change, so each car's position within
  the 1 s CAM cycle carried over to its new identity and linked the two.
- **Change:** on COMMIT of a full ID change, `cam_application.{hpp,cpp}` skips the next CAM and
  restarts the timer after a random delay of up to one interval (T_GenCam with dynamic generation)
  (`[IDCHANGE] CAM timer: new phase, next CAM in <n> ms`). `main.cpp` wires the CAM application
  to the ID change service.
- **Effect:** a timing-only linker went from 16 of 16 changes matched to 1 of 13 in the
  random-turn mix zone.

## Testing

- **Scenarios:** `linkability` (positions in the received CAMs), `convoy` and `crossing`.
- **Results:** the mix zone reruns on the rephased image are in
  [results](../../results/README.md).
