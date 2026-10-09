# Troubleshooting

## First steps

- **`./vnapctl status`:** stations, their certificates and certificate checks, warnings
  (stopped containers, failed chain checks, unconnected control channels, starved refills,
  low disk).
- **`docker logs <station>`:** socktap's own output.
- **`VNAPCTL_DEBUG=1 ./vnapctl …`:** `vnapctl`'s timings.
- **`./vnapctl check --json`:** every metric, to see which expectation failed and why.

## Startup certificate checks

Each station logs the result of checking its configured chain and own certificates
(`docker logs rsu`, or `vnapctl events --kind chain --since 5m`):

```
[V3-CHAIN] chain certificate /vnap-certs/c-its-pki/aa.cert: OK
[V3-CHAIN] own authorization ticket /vnap-certs/c-its-pki/at.cert: OK
```

A wrong root, an expired certificate or a mismatched key shows up here. Refilled ATs are
checked the same way (`batch authorization ticket …`). The checks are described in
[security and chain verification](../functional/security-and-chain-verification.md).

## Received messages

Received messages are not logged to stdout. socktap publishes them on MQTT, and
`security_report` holds the verification result ([monitoring](monitoring.md#application-messages-mqtt)).
Messages that fail verification are still delivered, with a failing `security_report`.

## Checking message files offline

Signed or encrypted message files (e.g. from C-ITS-PKI) can be checked with Vanetza's own
security code:

```bash
make msgcheck                                    # builds vnap:msgcheck (after make image)
docker run --rm -v DIR:/w vnap:msgcheck v3 /w/msg /w/at.cert /w/aa.cert /w/root_ca.cert
docker run --rm -v DIR:/w vnap:msgcheck decode-v3 /w/msg.enc
```

`DIR` must be under the real `$HOME` (snap-confined Docker).

## Common problems

| Symptom | Cause and fix |
|---|---|
| `up`: "C-ITS-PKI not found … git submodule update --init" | the submodule is not initialised: `make submodule` |
| `make image`: "does not contain the base commit" | `VANETZA_NAP_DIR` holds another vanetza-nap history: check out the base there, or use an empty directory |
| Image build stops with "no space left on device" | the first build needs about 10 GB of build cache; grow the disk ([installation](../installation.md#1-prepare-the-vm)) or remove unused images |
| Docker cannot read a build context or file on `/mnt/hgfs` | snap-confined Docker: use the provided scripts (they stream contexts on stdin) and keep mounts under `$HOME` |
| `up` refuses: "already running", "name in use" | another run uses the instance: `--instance auto`, or `down` the other run |
| `down` refuses: "started by …" | the run belongs to another account; `--force` if you are sure |
| Web UI: login succeeds, then 401 | the browser dropped the HTTPS-only session cookie ([web UI](web-ui.md#browser-notes)) |
| Web UI: grey map tiles saying "Referer is required" | an old UI version cached; reload with the cache cleared |
| A station's CAMs fail with `Invalid_Timestamp` | clock skew between containers; the patched image keeps its runtime clock synchronised. Check that the image is patched |
| `check`: `eavesdropper.*` metrics missing | the scenario has no `[eavesdropper]` section |

## Known limitations

These come from upstream Vanetza-NAP:
- socktap always uses non-strict decapsulation, so messages that fail verification are still
  delivered, with a failing `security_report`.
- v2 puts `--certificate-chain` AAs into the cache without checking them against the trusted
  root.
- The v2 verifier accepts only payload type `signed`.
- ThreadSanitizer still reports races inside the Zenoh library's own threads (14 in a
  3-minute c-its-pki run). They involve uninstrumented Rust code whose synchronization
  ThreadSanitizer cannot see, so they are most likely false positives. It reports none in
  socktap or Vanetza code.

And from the simulation itself:
- A pseudonym change (with the default full ID change) changes the certificate, GN address,
  MAC and CAM `stationId` together. It does not change the vehicle's broadcast position, so
  position continuity remains a linking clue
  ([eavesdropper](../functional/eavesdropper-and-scoring.md)).
- The control channel uses one account per run and no TLS
  ([control channels](../reference/control-channels.md#security)).
