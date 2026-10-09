# Container environment

The patched image's entrypoint (`entrypoint.sh`, part of the patch set) starts socktap from
these environment variables. `vnapctl` sets them from the scenario. They matter when starting
a station by hand or writing another harness.

| Variable | Meaning |
|---|---|
| `SECURITY=certs` | run socktap with `--certificate $AT_CERT --certificate-key $AT_KEY --certificate-chain $AA_CERT --trusted-certificate $ROOT_CERT` |
| `VANETZA_SECURITY` | `certs-v2` (default when `SECURITY=certs`) or `certs-v3`; must match the certificate format |
| unset `SECURITY` | socktap runs with `config.ini` (`security=none`), or with whatever `VANETZA_SECURITY` selects |
| `SECURITY=pseudonyms` | pool of ATs `PSEUDO_CERT_0`/`PSEUDO_KEY_0`, `PSEUDO_CERT_1`/`PSEUDO_KEY_1`, … (consecutive pairs from 0), plus `AA_CERT`/`ROOT_CERT`; starts with index 0 and changes only on control channel events; works with `certs-v2` and `certs-v3` |
| `PSEUDO_CONTROL_BROKER` | MQTT broker of the pseudonym control channel; without it the pseudonym never changes |
| `PSEUDO_CONTROL_PORT` | control broker port (1883) |
| `PSEUDO_CONTROL_TOPIC` | control topic prefix (default `vnap/pseudonym/<station id>`) |
| `PSEUDO_CONTROL_USERNAME` / `PSEUDO_CONTROL_PASSWORD` | control broker account (the password is read from the environment, not passed on the command line) |
| `PSEUDO_MIN_INTERVAL` | minimum milliseconds between two pseudonym changes (1000); earlier events are rejected |
| `PSEUDO_ID_CHANGE` | `full` (default): a pseudonym change also changes the GN address, MAC and CAM `stationId` through the ETSI ID change notification; `certificate`: only the certificate |
| `PSEUDO_SILENT_MIN_MS` / `PSEUDO_SILENT_MAX_MS` | random radio silence after each full ID change, in ms (default 0 = off); see [pseudonym change](../functional/pseudonym-change.md#silent-period) |
| `PKI_REFILL_AT` | certificate refill: request a new batch from the run's PKI when this many unused pseudonyms are left (unset or 0: fixed pool that wraps around). With refill no pseudonym is used twice; uses the pseudonym control broker; see [certificate refill](../functional/certificate-refill.md) |
| `PKI_BATCH_SIZE` | certificates to request per batch (8) |
| `PKI_TOPIC` | refill topic prefix: requests on `<prefix>/request`, batches on `<prefix>/batch` (default `vnap/pki/<station id>`) |
| `PKI_CATERPILLAR_KEY` / `PKI_EXPANSION_KEY` | the vehicle's butterfly secrets: with them the station derives its ATs' private keys and the PKI sends offsets instead of keys |
| `POSITION_CONTROL_BROKER` | MQTT broker of the position control channel: the station's position follows updates on its position topic ([vehicle movement](../functional/vehicle-movement.md)); needs the static position provider (`VANETZA_USE_HARDCODED_GPS=true`, the default), not gpsd |
| `POSITION_CONTROL_PORT` | position broker port (1883) |
| `POSITION_CONTROL_TOPIC` | position topic (default `vnap/position/<station id>`) |
| `POSITION_CONTROL_USERNAME` / `POSITION_CONTROL_PASSWORD` | position broker account (the password is read from the environment) |
| `VANETZA_LATITUDE` / `VANETZA_LONGITUDE` | position at startup; with a position channel, until the first update. `vnapctl` sets the scenario's position, the first waypoint, or the default origin in Coimbra (the image's own `config.ini` default is 40 / −8) |
| `VANETZA_BRIDGE_IP` | the station's V2X address; the entrypoint bridges the interface that carries it (default `eth0`). Needed when the container is on several networks, because Docker does not guarantee which one becomes `eth0`; `vnapctl` sets it |
| `SUPPORT_MAC_BLOCKING=true` | bridge the link interface as `br0` (`vnapctl` sets it) |

Private keys must be PKCS#8 DER (or PEM for v3); see [certificates](../certificates.md#key-formats).

**Stock images.** For an unpatched release2-main image (`make origs`), the scenario key
`entrypoint = "stock"` mounts `sim/stock-entrypoint.sh` instead. It runs the stock entrypoint's
setup, then starts socktap with the certificate options the stock entrypoint cannot pass.
