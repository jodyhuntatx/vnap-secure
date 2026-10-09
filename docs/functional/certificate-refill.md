# Certificate refill (per-run PKI)

Instead of a fixed set of certificates that every run reuses (and that wraps around when used
up), each run can have its own certificate authority. Stations start with a batch of butterfly
authorization tickets (ATs) and request a new batch when only a few unused ones are left. By
default the stations derive their ATs' private keys themselves (butterfly key expansion), so
the PKI never holds them. The design is in the product requirements document
(`Specs/VNAP-Secure-Simulation-Service-PRD.pdf`, sections 5.5 and 7).

```bash
cd sim
./vnapctl up c-its-pki-refill        # run's CA, RSU AT, 8 butterfly ATs per OBU; refill at 2 unused
./vnapctl status                     # per OBU: unused ATs, batches, refresh time; the PKI's issue times
./vnapctl events --kind pki --since 2m
./vnapctl check --expect 'obu1.pki.refresh_ms_max<2000'
./vnapctl down                       # also deletes the run's PKI volume with all its keys
```

## Scenario keys

- **`[pki]`:**
  - `initial` (8), `refill_at` (2), `batch` (8), `validity_hours` (24);
  - `etsi_version` (default: from the stations' security);
  - `ip` (default: host .5 of the control network);
  - `key_derivation`: `station`, the default, or `pki`, where the PKI makes and returns the
    private keys.

  It needs a `[control]` section.
- **Per station:** `pseudonyms = { initial, refill_at, batch, ... }` overrides the defaults.
  `refill_at = 0` gives a fixed pool from the run's PKI that wraps around.
- **Regular ATs:** stations with certificates but no `pseudonyms` get one.
- **No certificate paths:** `at_cert`, `aa_cert`, `root_cert` and `pseudonyms.cert` are not
  allowed with `[pki]`. The fixed-file pools remain available in scenarios without `[pki]`.

## What `up` does

1. Creates a Docker volume for the run and provisions it.
2. Starts the PKI container (`pki`, role `pki`) on the control network.
3. Mounts into each station only `public/` and its own `stations/<name>/`, read-only. A
   station sees neither the CA keys nor the other stations' files.

A failed start removes the volume again; `down` removes it with the run.

With `key_derivation = "station"`, provisioning is a separate one-shot container. The serving
PKI mounts only `private/` and `public/`, so it cannot read the vehicles' caterpillar keys or
AT keys either. With `pki`, one container provisions and serves.

## PKI service

`sim/images/pki/`, image `vnap-pki`, built from that directory and C-ITS-PKI's `src/`
([architecture](../architecture.md#dependency-on-c-its-pki)):

- **`provision`:** creates the run's root CA, TLM, EA and AA, and a regular AT for road-side
  units. For pseudonym stations it also does the enrolment (caterpillar keys) and issues an
  initial batch of butterfly ATs.
- **`serve`:** answers batch requests on the control broker; each batch uses the station's next
  i-period. It logs issuance and queueing time per request.
- **Volume layout:**
  - `public/`: the root, AA and other certificates.
  - `stations/<name>/`: the station's own files. With station key derivation this includes the
    vehicle's caterpillar private key, expansion key and enrolment key.
  - `private/`: never mounted into stations. It holds:
    - the CA keys;
    - per station, the caterpillar public key (or, with `key_derivation = "pki"`, the
      caterpillar keys);
    - the expansion key, the enrolment certificate and the batch state.
  - `private/issued.jsonl`: every issued AT with its HashedId8, the ground truth for scoring.
    The service keeps it (digests only) with a run's results.

## Key derivation on the station

Butterfly key expansion (IEEE 1609.2.1) splits the work so that the PKI never sees the ATs'
private keys:

- **The PKI** uses only the vehicle's caterpillar public key A and expansion key k. For each AT
  (i-period i, index j) it computes the cocoon key A + f_k(i, j)·G, certifies cocoon key + r·G,
  and returns the certificates with the offsets r and indices j, without keys.
- **The station** computes each private key as a + f_k(i, j) + r mod n, with f_k the IEEE
  1609.2.1 expansion function as implemented in C-ITS-PKI (`tools/socktap/bke.{hpp,cpp}`).
  It checks every key against its certificate and rejects ATs whose key does not match.
- **The initial batch** is derived once at provisioning, playing the vehicle; those keys are
  written only to the vehicle's directory.
- **Options:** `--pki-caterpillar-key`, `--pki-expansion-key` (`PKI_CATERPILLAR_KEY`,
  `PKI_EXPANSION_KEY`).

## Stations

Options `--pki-refill-at`, `--pki-batch-size`, `--pki-topic`, `--pki-retry`
(`PKI_REFILL_AT`, `PKI_BATCH_SIZE`, `PKI_TOPIC`):

- **Requests:** when a change leaves `PKI_REFILL_AT` or fewer unused ATs, the station requests a
  batch on the control broker and installs the answer while running
  ([control channels](../reference/control-channels.md#certificate-refill)). Each AT is
  chain-checked first (`[V3-CHAIN] batch authorization ticket ...`).
- **Retries:** at most one request is outstanding; unanswered requests are repeated after
  10, 20, 40 … seconds (up to 120).
- **No reuse:** with refill on, no AT is used twice. With none left, changes are refused
  (`[PSEUDONYM] change rejected: no unused pseudonym left`) until a batch arrives.
- **Logs:**
  - `[PKI] batch requested: request <id>, <u> unused, <n> wanted`;
  - `[PKI] batch installed: request <id>, <n> certificate(s), refresh <ms> ms (PKI issue <ms> ms, key derivation <ms> ms), <u> unused`.

  Refresh time runs from the first request for a batch (retries included) to installation.

## Status, events and checks

- **`status`:**
  - per station, a refill line: unused ATs, batches, refresh mean and max, retries, starved
    changes, pending request;
  - a line for the run's PKI: provisioning time, batches, issue and queue times, and the
    C-ITS-PKI commit.
- **`events --kind pki`:** requests, installed batches and retries from the stations, and
  issued batches from the PKI.
- **`check` metrics:**
  - per station: `<station>.pki.batches`, `.added`, `.unused`, `.retries`, `.refused`,
    `.starved`, `.pending`, `.refresh_ms_mean`, `.refresh_ms_max`, `.derivation_ms_mean`;
  - for the PKI: `pki.running`, `pki.batches`, `pki.refused`, `pki.issue_ms_max`,
    `pki.queue_ms_max`.

  The default expectations add `<station>.pki.starved==0` and `pki.running==1`.

## Sizing

Measured (`results/pki-refill-test-2026-10-07.docx`):
- The PKI issues about 50 ms per ticket.
- The refresh time is roughly the issue time × the batch size × the number of stations asking
  at once.
- A station starves when the refresh time exceeds (`refill_at` + 1) × the interval between its
  pseudonym changes. Synchronized changes, as in a mix zone, make stations ask at the same time.

## Without vnapctl

The service can be provisioned and attached by hand, e.g. for another harness:

```bash
# provision into a host directory and use it as the scenario's certs_dir
docker run --rm -v ~/vnap-pki-runs:/runs vnap-pki provision --dir /runs/run1 --config '{"etsi_version": "v3",
  "stations": [{"name": "rsu", "station_id": 1, "certificates": "regular"},
               {"name": "obu", "station_id": 2, "certificates": "bke", "initial": 8, "batch": 8}]}'
# scenario: certs_dir = "~/vnap-pki-runs/run1" (absolute), aa_cert /vnap-certs/public/aa.cert,
#   root_cert /vnap-certs/public/root_ca.cert, OBU pool /vnap-certs/stations/obu/bke_at_{i}.cert
#   with key bke_at_{i}_sign.der and count 8, env PKI_REFILL_AT = "2"
docker run -d --name pki --network vnapctl0 -v ~/vnap-pki-runs:/runs -e CONTROL_BROKER=pseudo-broker \
  vnap-pki serve --dir /runs/run1
```

## Related

- **Patches:** [certificate refill](../patches/certificate-refill.md).
- **Results:** `results/pki-refill-test-2026-10-07.docx`.
