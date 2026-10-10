# Configuration

## Paths in this documentation

| Variable | Meaning |
|---|---|
| `INSTALL_DIR` | the directory the repository is cloned into |
| `VNAP_HOME` | the repository: `${INSTALL_DIR}/vnap-secure` |

Both are set during [installation](../installation.md#3-get-the-sources), and the commands in
this documentation use them to change directory. No script or program reads them: each finds
the repository from its own location.

## Service: `service/config.toml`

Read at start (`VNAP_API_CONFIG` names another file). Relative paths are relative to
`service/`. Missing keys take the defaults shown.

| Section | Key | Default | Meaning |
|---|---|---|---|
| `[server]` | `data_dir` | `"data"` | database, users' scenario texts, collected results (backed up) |
| | `cookie_secure` | `true` | session cookie only over HTTPS. The repository's `config.toml` sets `false`, **for testing only** (login over plain HTTP in every browser); production must set `true` |
| | `session_hours` | `12` | session lifetime |
| `[auth]` | `max_failed_logins` | `5` | failed logins (password or TOTP) before the account is locked |
| | `lockout_minutes` | `15` | lock duration |
| | `login_attempts_per_minute` | `10` | per client address |
| | `totp_issuer` | `"vnap-secure"` | name authenticator apps show |
| | `totp_key_file` | `"secrets/totp.key"` | AES-256 key for TOTP secrets; outside `data_dir`, keep a copy ([backup and restore](../operations/backup-restore.md#the-totp-key)) |
| `[backup]` | `dir` | `"backups"` | where archives are written |
| | `keep` | `14` | archives kept (0: all) |
| `[limits.user]`, `[limits.admin]` | `concurrent_runs` | `2` / `10` | runs at the same time (viewers start none) |
| | `max_duration_minutes` | `60` / `480` | longest run; stopped automatically at the deadline |
| | `results_mb` | `500` / `5000` | disk for kept results |
| `[containers.station]`, `[containers.pki]`, `[containers.default]` | `cpus`, `memory` | `1.0`/`512m`, `1.0`/`512m`, `0.5`/`256m` | limits per container role |
| `[ui]` | `tile_url` | OpenStreetMap | map tile URL template; `""` for no background map |
| | `tile_attribution` | `© OpenStreetMap contributors` | shown on the map |
| `[worker]` | `threads` | `2` | runs starting or stopping at the same time |

Environment:
- **`VNAP_API_HOST`, `VNAP_API_PORT`:** the listen address for `run.sh` (default
  `0.0.0.0:8080`, all interfaces). All interfaces are required for forwarding port 8080 from
  the VM to the host. `127.0.0.1` listens on the VM only, e.g. behind Caddy in production
  ([service deployment](../operations/service-deployment.md#production)).
- **`VNAP_API_CONFIG`:** another configuration file.
- **`VNAP_SIM_DIR`:** where `vnapsim` is (default `../sim`).
- **`CITS_PKI_DIR`:** another C-ITS-PKI ([architecture](../architecture.md#dependency-on-c-its-pki)).

## User policy: `sim/policy.toml`

What a scenario from a user may set ([scenario format](scenario-format.md#user-policy)):

| Key | Meaning |
|---|---|
| `approved_images` | station images users may choose (`image`) |
| `allowed_env` | container environment variables users may set per station (`env`) |

## vnapctl

`vnapctl` has no configuration file; scenario files and options select everything. Its
environment:
- **`VNAPCTL_INSTANCE`:** the default instance.
- **`VNAPCTL_DEBUG=1`:** timings on stderr.
- **`CITS_PKI_DIR`:** another C-ITS-PKI.

## Build

`make image` and the scripts take:

| Variable | Default | Used by |
|---|---|---|
| `IMAGE` | `vnap:latest` | `scripts/build/docker-build.sh` |
| `VANETZA_NAP_DIR` | `~/vanetza-nap` | build, `make msgcheck` |
| `NO_BUILD` | unset | `1`: prepare the vanetza-nap sources only |
| `OUTPUT_DIR` | `/vnap-certs/certify` | `scripts/certs/gen-certify.sh` |
