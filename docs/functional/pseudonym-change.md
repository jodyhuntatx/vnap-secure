# Pseudonym change

A station with a pseudonym pool keeps its current pseudonym (authorization ticket and signing
key) until it receives a change event. A change is a synchronized change of all of the
station's identifiers, optionally followed by a silent period, so that an eavesdropper cannot
simply follow the station across it.

## Event-driven changes

```
 message network: GeoNetworking/CAMs             control network: control only
 ┌─────┐  CAMs  ┌─────────────────────────┐ eth1   ┌───────────────┐       ┌───────────────┐
 │ rsu │◀──────▶│ obu  socktap ─ Pseudonym│───────▶│ pseudo-broker │◀──────│ pseudo-client │
 └─────┘  eth0  │      Channel (own MQTT  │        │ MQTT          │       │ events        │
                │      client)            │        └───────────────┘       └───────────────┘
                └─────────────────────────┘
```

- **Channel:** a dedicated MQTT connection from socktap (`tools/socktap/pseudonym_channel.cpp`)
  to the control broker on the control network. Events, actions and answers are specified in
  [control channels](../reference/control-channels.md#pseudonym-change).
- **Who sends events:**
  - the event client (periodically, at random intervals, once, or by hand);
  - the mobility client, when a vehicle enters a mix zone ([vehicle movement](vehicle-movement.md#mix-zones));
  - the service's control actions.
- **Thread safety:** the MQTT thread only queues the event. The change runs on socktap's
  `io_context` under the `TimeTrigger` mutex, which also guards CAM signing, so a change cannot
  fall between the certificate and private key lookups.
- **After a change:** the next signed message carries the full new certificate, so receivers
  learn it at once.
- **Pool:** a fixed pool (scenario `pseudonyms = { cert, key, count }`) wraps around after its
  last pseudonym. With [certificate refill](certificate-refill.md), the pool grows at runtime
  and no pseudonym is used twice.

## ID change notification (ETSI TS 102 723-8 / -9)

With `id_change = "full"` (`PSEUDO_ID_CHANGE=full`, the default), a pseudonym change follows
the two-phase commit of clause 6.3:

1. **Subscribers:** the network and transport layer (GeoNetworking routers) and the facilities
   layer (CAM application) subscribe to the security entity's ID change service
   (`vanetza/security/id_change_service`).
2. **PREPARE** goes to all subscribers, with the 8-octet id of the new authorization ticket (its
   HashedId8). The network layer locks every router, so nothing is sent with the old
   identifiers until the change completes.
3. **The security entity switches** to the new authorization ticket and key.
4. **COMMIT:**
   - every router gets a new GN address, whose MID is also the source MAC of its frames;
   - the CAM application uses a new `stationId`;
   - both are derived from the id with SHA-256 under separate labels, so they share no bytes
     with each other or with the certificate.

   If a subscriber refuses PREPARE, everyone prepared gets **ABORT** and nothing changes.

| Service | Clause | Here |
|---|---|---|
| IDCHANGE-SUBSCRIBE / -UNSUBSCRIBE | 5.2.5, 5.2.7 | `IdChangeService::subscribe()`; socktap's subscriptions unsubscribe on destruction |
| IDCHANGE-EVENT (PREPARE, COMMIT, ABORT, DEREG) | 5.2.6, 6.3.1 | hook functions; DEREG when the security entity is destroyed |
| IDCHANGE-TRIGGER | 5.2.8 | `trigger()`: the security entity changes to the next pseudonym; control action `trigger` |
| ID-LOCK / ID-UNLOCK | 5.2.9, 5.2.10 | `lock(seconds 0..255)` / `unlock(handle)`; changes are refused while locked; control actions `lock` / `unlock` |

- **Logging:** each change is logged as
  `[IDCHANGE] network: GN address MID / MAC <old> -> <new>` and
  `[IDCHANGE] facilities: stationId <old> -> <new>`, plus ID-LOCK and ID-UNLOCK lines.
  `vnapctl` uses these lines to keep naming a station whose IDs change: `status` shows its
  current identity, and `events --kind idchange` the changes.
- **Certificate-only changes:** `id_change = "certificate"` (`PSEUDO_ID_CHANGE=certificate`)
  changes only the certificate. This is useful as a comparison: the eavesdropper then links
  every change through the unchanged identifiers.
- **Not changed:** the configured station ID and MAC remain the station's identity on its
  local MQTT interface (e.g. `receiverID`) and its control topics. Messages injected on
  `vanetza/in/*` carry whatever `stationId` their payload has.

## Silent period

With `pseudonyms.silent_min_ms` / `silent_max_ms` (`PSEUDO_SILENT_MIN_MS` /
`PSEUDO_SILENT_MAX_MS`), the station sends nothing for a random time in that range after each
full ID change. This is the silent period strategy of ETSI TR 103 415 clause 4.1.4; clause
4.2.1 reports the SAE J2735 values of 3 to 13 s.

- **How:** the network layer starts it on COMMIT, and `DccPassthrough` drops all outgoing
  frames (CAMs, beacons, injected messages) until it ends. Reception continues.
- **Logging:** `[IDCHANGE] silent period <n> ms` at the start, and
  `silent period over, <n> frame(s) suppressed` at the end.
- **Cost** (TR 103 415): while silent, the vehicle is missing from its neighbours' view, and it
  reappears suddenly afterwards.

## CAM timing

The CAM timer restarts at a random phase on every full ID change. The first CAM after the
change is skipped, and the next comes after a random delay of up to one interval
(`[IDCHANGE] CAM timer: new phase, next CAM in <n> ms`).

Without this, each car's position within the 1 s CAM cycle carried over to its new identity
and linked the two: a timing-only linker matched 16 of 16 changes in the random-turn mix zone,
and 1 of 13 with the random phase ([eavesdropper](eavesdropper-and-scoring.md#linking-techniques)).

## Security of the channel

Whoever can publish change events controls when a station changes pseudonym. See
[control channels](../reference/control-channels.md#security).

## Related

- **Scenario keys:** `pseudonyms` per station, `[control]`, `[control.client]` and
  `[[control.mix_zones]]` ([scenario format](../reference/scenario-format.md)).
- **Patches:** [pseudonym and ID change](../patches/pseudonym-and-id-change.md),
  [mobility and CAM](../patches/mobility-and-cam.md#cam-timer-rephase).
- **Results:** the mix zone and convoy test summaries in [results](../../results/README.md).
