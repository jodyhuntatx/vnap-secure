# Control channels

Stations with pseudonyms, movement or refill have a second network interface on the run's
control network (`vnapctl0`, or `vnapctl0-iN`). On it, socktap keeps its own MQTT connection
to the control broker (`pseudo-broker`), separate from the station's embedded broker that
carries application messages. RSUs and the eavesdropper are not on the control network.

| Channel | Topics (default prefix) | Publisher → subscriber |
|---|---|---|
| Pseudonym change | `vnap/pseudonym/<station id>/change`, `…/status` | event client, mobility client (mix zones), service → station; answers back |
| Position | `vnap/position/<station id>` (retained) | mobility client, service → station |
| Certificate refill | `vnap/pki/<station id>/request`, `…/batch` | station → per-run PKI; batches back |

`<station id>` is the configured station ID, which stays the same across
[ID changes](../functional/pseudonym-change.md), so a station keeps its topics.

## Pseudonym change

**Event** on `<prefix>/change` (JSON, all members optional):

```json
{"event_id": "c1-7", "index": 3, "reason": "periodic"}
```

- Without `index`, the station moves to the next pseudonym (the pool wraps around unless refill
  is on).
- **`action`** (default `change`), mapping to the ETSI ID management services:
  - `{"action": "lock", "duration": 30}`: ID-LOCK (0–255 s); the answer carries a
    `lock_handle`.
  - `{"action": "unlock", "lock_handle": 1}`: ID-UNLOCK.
  - `{"action": "trigger"}`: IDCHANGE-TRIGGER (change to the next pseudonym).

**Answer** on `<prefix>/status`:

```json
{"event_id": "c1-7", "result": "changed", "previous": 2, "index": 3, "pool_size": 8, "certificate": "<HashedId8>"}
```

Otherwise `"result": "rejected"` with an `error`, or `locked` / `unlocked` for the lock
actions. Rejected events:
- an empty payload or malformed JSON (`{}` is a valid event);
- an index outside the pool, or the index already in use;
- events closer together than `PSEUDO_MIN_INTERVAL` (`pseudonyms.min_interval_ms`);
- changes while an ID-LOCK is held (`"error": "ID locked (ID-LOCK)"`);
- with refill, changes when no unused pseudonym is left;
- retained messages replayed by the broker when socktap (re)subscribes, so a stale event has
  no effect.

**Event client** (`vnap-pseudo-ctl` image, container `pseudo-client`). Its scenario settings are
under `[control.client]`: `mode` = `periodic` (`interval`), `random` (`min_interval`,
`max_interval`), `once` (`delay`, `index`) or `manual`; `stations`. By hand:

```bash
docker exec pseudo-client change 2          # station 2: next pseudonym
docker exec pseudo-client change 2 5        # station 2: pool index 5
docker exec pseudo-client ctl lock 2 20     # station 2: ID-LOCK for 20 s (the answer has the handle)
docker exec pseudo-client ctl unlock 2 1    # ID-UNLOCK handle 1
docker exec pseudo-client ctl trigger 2     # IDCHANGE-TRIGGER
docker logs -f pseudo-client                # events sent and the stations' answers
```

The service's *Control* tab and `POST /api/runs/<id>/control` send the same events and
return the station's answer.

## Position

Payload on `vnap/position/<station id>`:

```json
{"lat": 40.208206, "lon": -8.4197756, "speed": 13.9, "heading": 90}
```

- **Fields:** `lat`/`lon` (degrees) are required. Optional: `speed` (m/s, 0–163.82),
  `heading` (degrees clockwise from north) and `alt` (m).
- **Rejected updates** are logged (`[MOBILITY] position update rejected: …`, the first 5).
- **Retained:** the mobility client publishes with the retain flag, so a reconnecting station
  gets its current position at once.

Move a station by hand:

```bash
docker run --rm --network vnapctl0 eclipse-mosquitto:2 mosquitto_pub -h pseudo-broker \
    -t vnap/position/2 -m '{"lat": 40.208606, "lon": -8.4197756, "speed": 10, "heading": 0}'
```

## Certificate refill

- **Request** (station → PKI) on `vnap/pki/<station id>/request`:
  `{"request_id": "2-3", "unused": 2, "count": 8}`. The request ID is the station ID followed
  by the request number.
- **Batch** (PKI → station) on `vnap/pki/<station id>/batch`, answering one request:
  - the request ID and the station's i-period;
  - the certificates;
  - with station key derivation, the offsets and indices from which the station derives the
    private keys; otherwise the keys.
- **One request at a time:** a station has at most one request outstanding and repeats it
  after 10, 20, 40 … s (up to 120 s).

Details are in [certificate refill](../functional/certificate-refill.md).

## Security

Whoever can publish on these topics controls the station:
- when it changes pseudonym, which helps an attacker correlate a change with a location, or
  exhaust a small pool;
- where it is;
- which certificates it receives (they are chain-checked before use).

- **Network:** only the run's own containers are on its control network.
- **Broker authentication:** `[control] auth` names environment variables holding a username and
  password. The service generates per-run credentials that users never see.
- **No TLS**, and one account shared by all participants of a run. Use TLS and per-client ACLs
  before anything outside the simulation.
- socktap logs a warning when it connects without credentials.
