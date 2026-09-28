# VNAP-Secure

Build scripts, source patches, simulation harness and certificates for running
[Vanetza-NAP](https://github.com/nap-it/vanetza-nap) (`release2-main`) `socktap` with C-ITS
security enabled (ETSI TS 103 097: `certs-v2` for V1.2.1 certificates, `certs-v3` for
V1.3.1/IEEE 1609.2 certificates).

This is **not** the Vanetza-NAP source tree. It holds the patch set and tooling around it:

- The Vanetza-NAP source is cloned separately into `~/vanetza-nap`.
- The patches are also committed there on the `jodyhuntatx` branch, on top of
  `release2-main` `241438fd`.
- Certificates are generated with Vanetza's `certify` tool or with the separate
  **C-ITS-PKI** utility (`~/COIMBRA/C-ITS-PKI`, see its `docs/README.md`).

## Contents

| Path | Purpose |
|---|---|
| `docker-build.sh` | copy `vnap-patches/` (or `vnap-origs/`) into `~/vanetza-nap` and build `vnap:latest` |
| `src-diffs.sh` | side-by-side diff of every patch against the upstream original |
| `docker-compose.yml`, `docker.env`, `start-vnap.sh`, `stop-vnap.sh` | RSU/OBU simulation with docker-compose |
| `certify-shell.sh` | shell in a `vnap:latest` container with `vnap-certs/` mounted (for `certify`) |
| `gen-certify.sh` | reference script for generating the `certify` Root/AA/AT set (run inside `certify-shell.sh`) |
| `convert-sign-key.sh` | convert a PEM private key to the PKCS#8 DER format socktap loads |
| `0-vmware-mount-dir.sh` | mount the host's shared folders at `/mnt/hgfs` in the VM |
| `1-extend-docker-fs.sh` | grow the VM's root logical volume (Docker needs ~10 GB of build cache) |
| `exec-to-vm.sh`, `run-build.sh`, `sync-vnap-dist.sh` | host-side helpers: ssh into the VM, build remotely and load the image on the host, copy `~/vanetza-nap` back |
| `vnap-patches/` | patched Vanetza-NAP files (see [Patch set](#patch-set)) |
| `vnap-origs/` | the corresponding upstream `release2-main` files |
| `vnap-docker/` | plain-docker simulation harness and the `vnap-msgcheck` message checker |
| `vnap-certs/certify/` | Root/AA/AT generated with Vanetza's `certify` (TS 103 097 V1.2.1, `certs-v2`) |
| `vnap-certs/c-its-pki/` | Root/AA/EA/TLM/AT and butterfly ATs from C-ITS-PKI (currently the v3 set, `certs-v3`) |

In `vnap-patches/` and `vnap-origs/`, each file is named after its path in vanetza-nap
with `/` replaced by `__`, e.g. `tools__socktap__time_trigger.cpp`.

## Development setup

Vanetza-NAP has file names that differ only by case, so it cannot be built on the default
(case-insensitive) macOS file system. Development uses an Ubuntu 24.04 VM in VMware on a
MacBook:

- **In the VM:** `~/vanetza-nap` is cloned here and all Docker images are built here.
  This repo can be cloned in the VM too, or shared from the host: run
  `0-vmware-mount-dir.sh`, which mounts it at `/mnt/hgfs/...`, then symlink it to
  `~/COIMBRA/vnap-secure`.
- **On the host:**
  - `exec-to-vm.sh` opens an ssh session to the VM (`VM_IP` is hardcoded as
    `172.16.93.134`).
  - `run-build.sh` runs `docker-build.sh` in the VM, then `docker save`, `scp` and
    `docker load` the image on the host.
  - `sync-vnap-dist.sh` copies `~/vanetza-nap` back to the host.
- **Git on `/mnt/hgfs`** reports "dubious ownership", because the mount shows files as
  owned by another user. Use `git -c safe.directory='*' …`, or run
  `git config --global --add safe.directory <path>` once.
- **Snap-confined Docker** in the VM can only read files under the real `$HOME`, and not
  in dot-directories. That rules out volumes and build contexts on `/mnt/hgfs` (see
  `EXEC_DIR` below and `vnap-docker/msgcheck/build-msgcheck.sh`, which stages its files
  in `$HOME`).
- **Disk:** a full image build needs about 10 GB of Docker build cache. Grow the VM disk,
  then run `1-extend-docker-fs.sh` or `growpart` + `pvresize` + `lvextend -r`.

## Build

```bash
./docker-build.sh          # apply vnap-patches/ to ~/vanetza-nap, build vnap:latest
./docker-build.sh origs    # any argument: restore vnap-origs/ (upstream) instead, then build
./src-diffs.sh             # review all patches against upstream
```

`docker-build.sh` must run in the VM. The first build takes about 20 minutes; later
builds reuse the cached dependency layers. Checking out the vanetza-nap `jodyhuntatx`
branch gives the same sources as running `docker-build.sh` without arguments.

## Run the simulation

### Option 1: docker-compose

```bash
./start-vnap.sh                           # starts rsu + obu from vnap:latest, follows the RSU log
EXEC_DIR=~/vnap-run ./start-vnap.sh       # required when this repo is on /mnt/hgfs (snap docker)
./stop-vnap.sh                            # same EXEC_DIR as above
```

- **Working directory:** `start-vnap.sh` copies `vnap-certs/` and `docker-compose.yml` to
  `EXEC_DIR` (default: the current directory) and creates the `vanetzalan0` network
  (192.168.98.0/24).
- **Certificates:** `docker-compose.yml` runs both stations with the C-ITS-PKI v3 set: the
  RSU uses `at.cert`/`at.der`, the OBU uses butterfly AT `bke_at_0`.
- **Other certificates:** edit the `AT_*`, `AA_CERT`, `ROOT_CERT` and `VANETZA_SECURITY`
  variables (section below).

### Option 2: plain docker (`vnap-docker/`)

```bash
cd vnap-docker
NATIVE=1 ./run-r2-sim.sh c-its-pki vnap:latest   # scenario, image
./check-r2-cams.sh 20                             # per station: CAMs received and their security_report
./end-r2-sim.sh
```

`run-r2-sim.sh` scenarios:

| Scenario | Certificates |
|---|---|
| `none` | no security (baseline) |
| `certify` | `certs-v2`, `vnap-certs/certify` `ticket_vnap` for both stations (expired 2026-06-07) |
| `certify-fresh` | `certs-v2`, certify Root/AA with per-station ATs `ticket_vnap_{rsu,obu}_20260924` (valid until 2026-11-23) |
| `certify-fresh-badroot` | as above, but the OBU trusts an unrelated root (negative test) |
| `naive-v3` | `certs-v3` with self-generated certificates per station (negative test: must be rejected) |
| `c-its-pki` | `certs-v3` (or `PKI_SECURITY=certs-v2`) from `vnap-certs/c-its-pki`: RSU `at`, OBU `bke_at_0` |
| `c-its-pki-badroot` | as above, but the OBU trusts `tlm.cert` instead of the root (negative test) |

Options:

- `NATIVE=1`: use the image's own entrypoint (patched images), rather than mounting
  `r2-entrypoint.sh`, which is for the unpatched `vnap:r2-stock` image.
- `CERTS_DIR=<dir>`: use another certificate set. `<dir>` must contain `c-its-pki/` and be
  under `$HOME`.
- `PKI_SECURITY=certs-v2|certs-v3`: security mode for the `c-its-pki` scenarios.

### Container environment (`entrypoint.sh`)

| Variable | Meaning |
|---|---|
| `SECURITY=certs` | run socktap with `--certificate $AT_CERT --certificate-key $AT_KEY --certificate-chain $AA_CERT --trusted-certificate $ROOT_CERT` |
| `VANETZA_SECURITY` | `certs-v2` (default when `SECURITY=certs`) or `certs-v3`; must match the certificate format |
| unset `SECURITY` | socktap runs with `config.ini` (`security=none`), or with whatever `VANETZA_SECURITY` selects |
| `SECURITY=pseudonyms` | not supported on release2 (the pseudonym-rotation patch was not ported; the container exits) |

Private keys must be PKCS#8 DER (or PEM for v3). Keys from `certify` and C-ITS-PKI already
are.

## Certificates

- **`vnap-certs/certify/`**: generated with Vanetza's `certify` tool (V1.2.1 format, `certs-v2`).
  - `gen-certify.sh` at the top level is the reference: `generate-key`, then
    `generate-root` (AIDs 36/37/141), `generate-aa`, `generate-ticket`. Run it inside
    `certify-shell.sh`.
  - `vnap-certs/gen-certify.sh` is an older variant without AID 141.
  - The original `ticket_vnap` AT expired on 2026-06-07. Use the `*_20260924` ATs, which
    are valid until 2026-11-23, or issue new ones with
    `certify generate-ticket --sign-key aa_vnap.key --sign-cert aa_vnap.cert …`.
- **`vnap-certs/c-its-pki/`**: generated by C-ITS-PKI `gen-vnap-certs.sh`. That script
  writes directly into this directory.
  - With no argument it produces the v3 format; with any argument, v2.
  - Contents: `root_ca`, `aa`, `ea`, `tlm`, the regular AT (`at.cert`/`at.der`) and eight
    butterfly ATs (`bke_at_<j>.cert` / `bke_at_<j>_sign.der`).
  - C-ITS-PKI's `docs/compliance.md` documents their conformance.
- **Keys:** `convert-sign-key.sh <key.pem>` converts a PEM key to PKCS#8 DER.

## Patch set

The patches are in `vnap-patches/` (upstream versions in `vnap-origs/`) and committed on
the vanetza-nap `jodyhuntatx` branch:

| Patch | Files | Why |
|---|---|---|
| Debian snapshot mirror | `Dockerfile` | bullseye reached end of life on 2026-08-31; `deb.debian.org` pool files return 404, so the image no longer built |
| `certify` + `SECURITY=certs` mode | `Dockerfile`, `entrypoint.sh` | ship the `certify` tool; start socktap with certificate, AA chain and trusted root from environment variables |
| Clock sync | `tools/socktap/time_trigger.{hpp,cpp}` | the runtime clock lagged wall time, so CAMs were rejected as `Invalid_Timestamp`; also fixes a concurrent `schedule()` assertion crash |
| PRNG mutex | `vanetza/security/backend_cryptopp.{hpp,cpp}` | the shared CryptoPP random pool was used from several threads (v3 verification, key-check asserts) |
| Optional Assurance_Level | `vanetza/security/v2/default_certificate_validator.cpp` | accept older v2 certificates without the attribute (TS 103 097 V1.2.1 requires it; current C-ITS-PKI and `certify` output include it) |
| v3 DER keys | `vanetza/security/v3/persistence.cpp` | load PKCS#8 DER keys; readable errors instead of `terminate … char const*` |
| v3 full-chain verification | `vanetza/security/v3/certificate_chain.{hpp,cpp}` (new), `straight_verify_service.{hpp,cpp}`, `vanetza/security/CMakeLists.txt`, `tools/socktap/security.cpp` | upstream v3 accepted any AT regardless of issuer; now AT → AA → trusted root is verified (IEEE 1609.2 signing input), and `--trusted-certificate` works for v3 |
| Startup diagnostics | `tools/socktap/security.cpp` | log `[V3-CHAIN]` / `[V2-CHAIN]` results for the configured AA and own AT |

## Validation and troubleshooting

- **Startup log** (`docker logs rsu`): `[V3-CHAIN]` / `[V2-CHAIN]` lines show whether the
  configured chain and own AT are valid. A wrong root shows up there.
- **Received messages** are not logged to stdout. socktap publishes each as JSON on MQTT
  `vanetza/out/cam` (and `…/denm` etc.) on the station's embedded broker. The
  `security_report` field holds the verification result:

  ```bash
  docker run --rm --network vanetzalan0 eclipse-mosquitto:2 \
    mosquitto_sub -h 192.168.98.10 -t vanetza/out/cam -v      # .10 = RSU, .20 = OBU
  ```

  `check-r2-cams.sh` counts and tallies these per station.
- **Message files** (signed or encrypted, e.g. from C-ITS-PKI) can be checked offline
  with Vanetza's own security code:

  ```bash
  vnap-docker/msgcheck/build-msgcheck.sh          # builds vnap:msgcheck
  docker run --rm -v DIR:/w vnap:msgcheck v3 /w/msg /w/at.cert /w/aa.cert /w/root_ca.cert
  docker run --rm -v DIR:/w vnap:msgcheck decode-v3 /w/msg.enc
  ```

- **Known upstream limitations:**
  - socktap always uses non-strict decapsulation, so messages that fail verification
    are still delivered with a failing `security_report`.
  - v2 puts `--certificate-chain` AAs into the cache without checking them against the
    trusted root.
  - The v2 verifier accepts only payload type `signed`.

## History

Before 2026-09, this repo held a patch set for Vanetza-NAP `main`:

- parser fixes;
- LRU-cache mutex;
- debug/metrics logging;
- pseudonym rotation (`pseudonym_certificate_provider`);
- a docker-compose-free harness and a kind/K8s attempt.

It was replaced by the release2 patch set above on 2026-09-28. The removed `vnap-origs/`,
`vnap-patches/` and `vnap-docker/` directories, including files that were never
committed, are archived in `~/vnap-secure-removed-dirs-20260928.tar.gz` in the
development VM. Their committed versions remain in git history, up to the commit that
introduced this README.
