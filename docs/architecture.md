# Architecture

## A simulation run

A run is a set of containers on two Docker networks of its own:

```
 message network vanetzalan0[-iN]  (V2X link: GeoNetworking frames)
   ┌─────┐   ┌──────┐ ┌──────┐   ┌──────────────┐
   │ RSU │   │ OBU1 │ │ OBU2 │…  │ eavesdropper │  raw socket, sees only frames
   └─────┘   └──┬───┘ └──┬───┘   └──────────────┘
                │        │       second interface of moving or pseudonym stations
 control network vnapctl0[-iN]
   ┌───────────────┐  ┌──────────────┐  ┌─────────────────┐  ┌──────────────┐
   │ control broker│  │ event client │  │ mobility client │  │ per-run PKI  │
   │ (MQTT)        │  │ (pseudonyms) │  │ (positions,     │  │ (batches of  │
   └───────────────┘  └──────────────┘  │  mix zones)     │  │  ATs)        │
                                        └─────────────────┘  └──────────────┘
```

- **Stations** run the patched `socktap` (`vnap:latest`), each with its embedded MQTT broker
  for application messages (`vanetza/in|out|own/*`). Stations with pseudonyms, movement or
  refill also join the control network, on a separate MQTT connection.
- **Control network:** carries pseudonym change events and answers, position updates and
  certificate batch requests ([control channels](reference/control-channels.md)). RSUs and
  the eavesdropper are not on it.
- **Instances:** instance 0 uses `vanetzalan0` / `vnapctl0` (192.168.98.0/24, 192.168.99.0/24).
  Instance N uses `-iN` networks with 10.N.98.0/24 and 10.N.99.0/24 and container names
  `<name>-iN`, so runs never share a network.
- **Per-run PKI:** a Docker volume per run holds the CA, the stations' certificates and,
  with station key derivation, the vehicles' secrets. Each station mounts only its own
  directory and the public certificates. The volume is deleted with the run.

## Trust boundaries

| Party | Can see | Cannot see |
|---|---|---|
| Eavesdropper | frames on the message network | MQTT, control network, keys, ground truth |
| Station | its own keys and certificates, the public certificates | CA keys, other stations' files |
| Per-run PKI (serving) | CA keys, public material, batch state | vehicles' caterpillar and AT private keys (station key derivation) |
| Control broker clients | the topics their account allows | (one shared account per run; no TLS) |
| Service users | their own runs and runs shared with them | broker credentials, container settings outside the policy |

Ground truth for scoring comes from the stations' own logs and the PKI's issue log, never
from the eavesdropper's view.

## Software

| Part | Where | Role |
|---|---|---|
| Patch set | `patches/vanetza-nap/` | changes to Vanetza-NAP ([patch documentation](patches/README.md)) |
| `vnapctl` | `sim/vnapctl` | command line; thin launcher for `vnapsim` |
| `vnapsim` | `sim/vnapsim/` | the simulation library used by `vnapctl` and the service |
| Helper images | `sim/images/` | PKI, mobility client, eavesdropper, control client, message checker |
| Service | `service/vnapapi/`, `service/ui/` | multi-user HTTP API and web UI |

`vnapsim` modules:

| Module | Contents |
|---|---|
| `scenario.py` | loading, `--set` overrides, validation, identity assignment, the user policy |
| `schema.py` | JSON Schema of the scenario format (`vnapctl schema`) |
| `lifecycle.py` | `up` / `down`, instance allocation, helper images, the run's PKI |
| `status.py`, `logs.py` | discovery, status, log parsing |
| `events.py`, `check.py` | event collection, metrics and expectations |
| `scoring.py` | eavesdropper links and tracks against ground truth |
| `cli.py` | the command line |

The service imports `vnapsim` from `sim/` (`VNAP_SIM_DIR` overrides the location). It runs
runs as jobs, gives each run a free instance, CPU and memory limits and its own broker
credentials, and keeps results after a run stops.

## Dependency on C-ITS-PKI

vnap-secure uses [C-ITS-PKI](https://github.com/jodyhuntatx/C-ITS-PKI) in two ways:

| Use | What | When C-ITS-PKI is needed |
|---|---|---|
| **Per-run PKI** (`[pki]` in a scenario) | The `vnap-pki` image is built from `sim/images/pki/` plus C-ITS-PKI's `src/` package. `pki_service.py` imports from it the CA hierarchy (`src.pki`), certificate issuing (`src.certificates`), butterfly key expansion and key handling (`src.crypto`), and the certificate types (`src.types`). C-ITS-PKI generates every certificate of such a run, including the butterfly ATs | when `vnap-pki` is built: on the first `vnapctl up` of a `[pki]` scenario, after any change to C-ITS-PKI's `src/`, or with `make pki-image` |
| **Fixed certificate set** (`certs/c-its-pki/`) | root, TLM, EA, AA, a regular AT and 24 butterfly ATs, made by C-ITS-PKI's `gen-vnap-certs.sh` and committed here | only to regenerate the set ([certificates](certificates.md)) |

- **Submodule:** C-ITS-PKI is the git submodule `external/C-ITS-PKI` (URL `../C-ITS-PKI.git`,
  relative to this repository's), pinned to a tested commit.
- **Lookup order** in `vnapctl`, the service and `make pki-image`:
  1. `CITS_PKI_DIR`, e.g. a working copy you are changing;
  2. the submodule;
  3. a checkout next to vnap-secure (`../C-ITS-PKI`), for the transition, with a note on
     stderr.
- **Provenance:** the `vnap-pki` image, the run's PKI container and the service's `run.json`
  carry the C-ITS-PKI commit (label `vnap.cits_pki`, `-modified` when `src/` has uncommitted
  changes). `vnapctl status` shows it on the run PKI line.
- **Versions:** the `vnap-pki` image tag includes a digest of C-ITS-PKI's `src/`, so a change
  builds a new image on the next `[pki]` run, and two versions never overwrite each other.
- **Without C-ITS-PKI:** `vnapctl` uses the newest `vnap-pki` image already built. With no
  image either, `[pki]` scenarios (including the service's templates) fail validation with
  "C-ITS-PKI not found … run 'git submodule update --init'". Scenarios with fixed certificate
  pools need only `certs/`.
- **The contract:** C-ITS-PKI's `tests/test_vnap_secure_interface.py` checks the names and
  keyword arguments `pki_service.py` uses, and that butterfly private and public key expansion
  agree. Its Operations Guide §8 documents the same contract from its side.

**Updating C-ITS-PKI:**

```bash
git -C external/C-ITS-PKI fetch && git -C external/C-ITS-PKI checkout origin/main
cd sim && ./vnapctl up --instance auto scenarios/templates/pki-refill.toml && ./vnapctl --instance <n> check
./vnapctl --instance <n> down && cd ..
git add external/C-ITS-PKI && git commit -m "Update C-ITS-PKI to $(git -C external/C-ITS-PKI rev-parse --short HEAD)"
```

**Developing both at once:** change C-ITS-PKI in its own checkout and point `CITS_PKI_DIR`
at it while testing here. Push C-ITS-PKI first, then move the submodule pointer. Commits made
inside `external/C-ITS-PKI` on a detached HEAD are easily lost: create a branch there first.

## Dependency on vanetza-nap

The patch set applies to upstream vanetza-nap `release2-main` at the commit in
`patches/vanetza-nap/BASE`. vanetza-nap is not a submodule:
- the patched commits are not published;
- its case-only file name differences rule out a checkout on a shared host folder;
- a recursive clone would carry about 140 MB of history that the service does not need.

`make image` fetches the base when needed ([build](build.md)).
