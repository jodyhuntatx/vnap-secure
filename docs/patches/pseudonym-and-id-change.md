# Patches: pseudonym and ID change

The behaviour is described in [pseudonym change](../functional/pseudonym-change.md), and the
channel in [control channels](../reference/control-channels.md#pseudonym-change).

## Pseudonym pool

- **Change:** new `vanetza/security/v{2,3}/pseudonym_certificate_provider.{hpp,cpp}` hold a
  pre-provisioned pool of ATs (e.g. a butterfly batch) for v2 and v3. After each change, the
  full new certificate is sent in the next message, so receivers learn it at once.
- **Options:** `--pseudonym-certificate` and `--pseudonym-certificate-key`, repeatable and paired
  in order; `SECURITY=pseudonyms` with `PSEUDO_CERT_<i>` / `PSEUDO_KEY_<i>` in the entrypoint.
- **Logs:** `[PSEUDONYM]` lines (start, changes, rejections).
- **Files:** the providers, `vanetza/security/CMakeLists.txt`,
  `tools/socktap/security.{hpp,cpp}`, `entrypoint.sh`.

## Event-driven pseudonym change

- **Change:** the pseudonym changes only on events from a separate MQTT control channel, not on
  a timer.
  - `vanetza/security/pseudonym_control.hpp` (new) is the interface the providers implement.
  - `tools/socktap/pseudonym_channel.{hpp,cpp}` (new) is socktap's own MQTT client.
  - `time_trigger.{hpp,cpp}` gains `post()`, so the change runs on the `io_context` under the
    lock that also guards signing.
- **Options:** `--pseudonym-control-broker`, `-port`, `-topic`, `-username`, `-password`, and
  `--pseudonym-min-interval` (`PSEUDO_CONTROL_*`, `PSEUDO_MIN_INTERVAL`).
- **Files:** `pseudonym_control.hpp`, `pseudonym_channel.{hpp,cpp}`, `tools/socktap/main.cpp`,
  `tools/socktap/CMakeLists.txt`, `time_trigger.{hpp,cpp}`, `entrypoint.sh`.

## ID change notification service

- **Change:** ETSI TS 102 723-8/-9 ID change notification. The parts:
  - `vanetza/security/id_change_service.{hpp,cpp}` (new): subscribe, two-phase commit
    (PREPARE/COMMIT/ABORT/DEREG), trigger, ID-LOCK/UNLOCK;
  - `tools/socktap/id_change.{hpp,cpp}` (new): socktap's subscribers, for the network layer
    (routers: GN address and MAC) and the facilities layer (CAM `stationId`);
  - the providers and `pseudonym_control.hpp`: they run the change through the service;
  - `router_context.{hpp,cpp}`: locking routers during PREPARE and setting the new address on
    COMMIT;
  - `pseudonym_channel.cpp`: the `lock`, `unlock` and `trigger` actions.
- **Options:** `--pseudonym-id-change full|certificate` (`PSEUDO_ID_CHANGE`, default `full`).
- **Logs:** `[IDCHANGE]` lines (network, facilities, lock, unlock).
- **Files:** `id_change_service.{hpp,cpp}`, `pseudonym_control.hpp`, the providers,
  `vanetza/security/CMakeLists.txt`, `id_change.{hpp,cpp}`, `router_context.{hpp,cpp}`,
  `main.cpp`, `pseudonym_channel.cpp`, `applications/cam_application.cpp`,
  `tools/socktap/CMakeLists.txt`, `entrypoint.sh`.
- **Upstream:** standards-based; candidate for discussion upstream.

## Silent period

- **Change:** after each full ID change, `DccPassthrough` (`tools/socktap/dcc_passthrough.{hpp,cpp}`)
  drops all outgoing frames for a random time (ETSI TR 103 415 clause 4.1.4), started by the
  network layer on COMMIT.
- **Options:** `--pseudonym-silent-min` / `--pseudonym-silent-max` (`PSEUDO_SILENT_MIN_MS` /
  `PSEUDO_SILENT_MAX_MS`).
- **Logs:** `[IDCHANGE] silent period <n> ms`, then `silent period over, <n> frame(s)
  suppressed`.

## Testing

- **Scenarios:**
  - `c-its-pki-pseudo` and `c-its-pki-pseudo-manual`: changes, rejections, answers;
  - `c-its-pki-tracking` and `c-its-pki-tracking-silent`: an eavesdropper against full ID
    changes, with and without silence;
  - the mix zone and convoy scenarios ([results](../../results/README.md)).
- **Checks:** `<station>.pseudonym.changed`, `.rejected`, `control.changed`, and
  `vnapctl events --kind idchange`.
