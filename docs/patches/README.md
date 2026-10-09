# Patch documentation

The patch set changes Vanetza-NAP's `socktap` tool, parts of the Vanetza library and the image
build. It applies to upstream vanetza-nap `release2-main` at the commit in
`patches/vanetza-nap/BASE` (`241438fd`).

- **`patches/vanetza-nap/modified/`:** the patched files, each named after its path with `/`
  written as `__`.
- **`patches/vanetza-nap/upstream/`:** the upstream originals of the files that existed
  before.
- **`patches/vanetza-nap/diffs/`:** generated unified diffs (`make diffs`).
- **Building:** `make image` lays the patched files over the base and builds `vnap:latest`
  ([build](../build.md)).
- **Commit series:** the same changes exist as commits on the local vanetza-nap branch
  `jodyhuntatx`, which is not published.

## Patches

| Group | Patch | Why | Upstream status |
|---|---|---|---|
| [Build and container](build-and-container.md) | Debian snapshot mirror | bullseye reached end of life; its pool files return 404, so the image no longer built | candidate |
| | `certify` and certificate mode in the entrypoint | ship `certify`; start socktap with certificate, chain and trusted root from the environment | candidate |
| | Bridge interface selection | bridge the interface carrying `VANETZA_BRIDGE_IP`, not whatever became `eth0` | candidate |
| [Certificate verification](certificate-verification.md) | v3 full-chain verification | upstream v3 accepted any AT regardless of issuer | candidate |
| | Startup certificate checks | log chain results for the configured AA and own ATs | candidate |
| | Optional Assurance_Level (v2) | accept older v2 certificates without the attribute | simulation convenience |
| | v3 DER keys | load PKCS#8 DER keys; readable errors | candidate |
| [Thread safety](thread-safety.md) | Clock sync, PRNG, sign header policy, v2/v3 certificate caches, runtime clock and ASN.1 descriptors, routers, PubSub/MQTT/DDS, RSSI reader | data races found with ThreadSanitizer; several crashed socktap under load | candidate |
| [Pseudonym and ID change](pseudonym-and-id-change.md) | Pseudonym pool | a station holds a pool of ATs (e.g. a butterfly batch) | simulation-specific |
| | Event-driven pseudonym change | changes on events from a separate MQTT control channel, not on a timer | simulation-specific |
| | ID change notification service | ETSI TS 102 723-8/-9: GN address, MAC and `stationId` change with the certificate; ID-LOCK | standards-based; candidate for discussion |
| | Silent period | random radio silence after each full ID change (TR 103 415) | simulation-specific |
| [Certificate refill](certificate-refill.md) | Growing pool and PKI channel | new ATs from the run's PKI at a threshold; never reused | simulation-specific |
| | Station key derivation | butterfly key expansion on the station (IEEE 1609.2.1) | simulation-specific |
| [Mobility and CAM](mobility-and-cam.md) | Position control channel | position from an MQTT topic at runtime | simulation-specific |
| | CAM kinematics | heading, speed and heading confidence, acceleration, yaw rate in the CAM's units | candidate |
| | CAM timer rephase | the CAM timer restarts at a random phase on every full ID change | simulation-specific |

"Candidate" means the change fixes upstream behaviour and could be proposed upstream as is.
"Simulation-specific" means it exists for this study (control channels, PKI protocol).

## Files

Every patched file, and the groups that change it. `make diffs-check` fails if a patched file
is missing here.

| File | New | Groups |
|---|---|---|
| `Dockerfile` | | build and container |
| `entrypoint.sh` | | build and container; pseudonym and ID change; certificate refill; mobility and CAM |
| `tools/socktap/CMakeLists.txt` | | pseudonym and ID change; certificate refill; mobility and CAM |
| `tools/socktap/main.cpp` | | thread safety; pseudonym and ID change; certificate refill; mobility and CAM |
| `tools/socktap/security.cpp` | | certificate verification; pseudonym and ID change; certificate refill |
| `tools/socktap/security.hpp` | | pseudonym and ID change; certificate refill |
| `tools/socktap/applications/cam_application.cpp` | | thread safety; pseudonym and ID change; mobility and CAM |
| `tools/socktap/applications/cam_application.hpp` | | mobility and CAM |
| `tools/socktap/time_trigger.cpp` | | thread safety; pseudonym and ID change |
| `tools/socktap/time_trigger.hpp` | | thread safety; pseudonym and ID change |
| `tools/socktap/router_context.cpp` | | thread safety; pseudonym and ID change |
| `tools/socktap/router_context.hpp` | | thread safety; pseudonym and ID change |
| `tools/socktap/dcc_passthrough.cpp` | | thread safety; pseudonym and ID change |
| `tools/socktap/dcc_passthrough.hpp` | | thread safety; pseudonym and ID change |
| `tools/socktap/raw_socket_link.cpp` | | thread safety |
| `tools/socktap/raw_socket_link.hpp` | | thread safety |
| `tools/socktap/pubsub.cpp` | | thread safety |
| `tools/socktap/dds.cpp` | | thread safety |
| `tools/socktap/mqtt.cpp` | | thread safety |
| `tools/socktap/mqtt.hpp` | | thread safety |
| `tools/socktap/rssi_reader.cpp` | | thread safety |
| `tools/socktap/positioning.cpp` | | mobility and CAM |
| `tools/socktap/mobility.cpp` | new | mobility and CAM |
| `tools/socktap/mobility.hpp` | new | mobility and CAM |
| `tools/socktap/pseudonym_channel.cpp` | new | pseudonym and ID change |
| `tools/socktap/pseudonym_channel.hpp` | new | pseudonym and ID change |
| `tools/socktap/id_change.cpp` | new | pseudonym and ID change |
| `tools/socktap/id_change.hpp` | new | pseudonym and ID change |
| `tools/socktap/pki_channel.cpp` | new | certificate refill |
| `tools/socktap/pki_channel.hpp` | new | certificate refill |
| `tools/socktap/bke.cpp` | new | certificate refill |
| `tools/socktap/bke.hpp` | new | certificate refill |
| `vanetza/asn1/asn1c_wrapper.hpp` | | thread safety |
| `vanetza/asn1/cam.hpp` | | thread safety |
| `vanetza/common/manual_runtime.cpp` | | thread safety |
| `vanetza/common/manual_runtime.hpp` | | thread safety |
| `vanetza/security/CMakeLists.txt` | | certificate verification; pseudonym and ID change; certificate refill |
| `vanetza/security/backend_cryptopp.cpp` | | thread safety |
| `vanetza/security/backend_cryptopp.hpp` | | thread safety |
| `vanetza/security/straight_verify_service.cpp` | | certificate verification |
| `vanetza/security/straight_verify_service.hpp` | | certificate verification |
| `vanetza/security/id_change_service.cpp` | new | pseudonym and ID change |
| `vanetza/security/id_change_service.hpp` | new | pseudonym and ID change |
| `vanetza/security/pseudonym_control.hpp` | new | pseudonym and ID change; certificate refill |
| `vanetza/security/pseudonym_pool.hpp` | new | certificate refill |
| `vanetza/security/v2/certificate_cache.cpp` | | thread safety |
| `vanetza/security/v2/certificate_cache.hpp` | | thread safety |
| `vanetza/security/v2/default_certificate_validator.cpp` | | certificate verification |
| `vanetza/security/v2/sign_header_policy.cpp` | | thread safety |
| `vanetza/security/v2/sign_header_policy.hpp` | | thread safety |
| `vanetza/security/v2/pseudonym_certificate_provider.cpp` | new | pseudonym and ID change; certificate refill |
| `vanetza/security/v2/pseudonym_certificate_provider.hpp` | new | pseudonym and ID change; certificate refill |
| `vanetza/security/v3/certificate_cache.cpp` | | thread safety |
| `vanetza/security/v3/certificate_cache.hpp` | | thread safety |
| `vanetza/security/v3/certificate_chain.cpp` | new | certificate verification |
| `vanetza/security/v3/certificate_chain.hpp` | new | certificate verification |
| `vanetza/security/v3/persistence.cpp` | | certificate verification |
| `vanetza/security/v3/sign_header_policy.cpp` | | thread safety |
| `vanetza/security/v3/sign_header_policy.hpp` | | thread safety |
| `vanetza/security/v3/pseudonym_certificate_provider.cpp` | new | pseudonym and ID change; certificate refill |
| `vanetza/security/v3/pseudonym_certificate_provider.hpp` | new | pseudonym and ID change; certificate refill |

## Working on the patch set

1. Edit in a vanetza-nap tree (`~/vanetza-nap`), build with `make image`, test with scenarios.
2. Copy the changed files into `patches/vanetza-nap/modified/` (path with `/` as `__`); for a
   file that existed upstream, its original goes into `upstream/`.
3. `make diffs`, then add the file to the table above and describe the change in its group
   document.
4. `make test`, then a live run of the scenarios the change affects.
