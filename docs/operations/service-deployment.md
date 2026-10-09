# Service deployment

`service/` is the multi-user simulation service: an HTTP API (`vnapapi/`, FastAPI and SQLite)
and a web UI (`ui/`) served by the same process. It drives simulations through the same
`vnapsim` package as `vnapctl`. Users reach it only over HTTPS through a reverse proxy; the
service account is the only one with access to Docker.

## Quick start (development)

```bash
cd service
python3 -m vnapapi.admin create-user root --role admin   # first admin (password asked, or VNAP_NEW_PASSWORD)
./run.sh                                                 # 127.0.0.1:8080: web UI at /ui/, OpenAPI at /api/docs
```

- **Dependencies:** `run.sh` installs `requirements.txt` into `service/.venv` (or `.deps`
  without python3-venv) and installs again whenever `requirements.txt` changes.
- **Listen address:** `VNAP_API_HOST` and `VNAP_API_PORT` change it.
- **Plain HTTP:** with the default `cookie_secure = true`, the browser keeps the session
  cookie only over HTTPS or on `http://localhost`. Safari and other WebKit browsers drop it
  even on localhost. Login over plain HTTP from another host is refused with an explanation.
  For a test on a trusted network, set `cookie_secure = false`.

## Production

On the VM, as root:

```bash
useradd --system --create-home --groups docker vnap
sudo -u vnap git clone --recurse-submodules <vnap-secure URL> /home/vnap/vnap-secure
cp /home/vnap/vnap-secure/service/deploy/vnap-api.service /etc/systemd/system/
cp /home/vnap/vnap-secure/service/deploy/vnap-backup.{service,timer} /etc/systemd/system/
systemctl enable --now vnap-api vnap-backup.timer
sudo -u vnap sh -c 'cd /home/vnap/vnap-secure/service && .venv/bin/python -m vnapapi.admin create-user root --role admin'
```

- **Service account:** `vnap-api.service` runs `service/run.sh` as `vnap`, the only member of
  the docker group, with `NoNewPrivileges`, `ProtectSystem=full` and `UMask=0077`.
- **TLS:** `deploy/Caddyfile` terminates TLS (automatic certificates) and forwards to
  `127.0.0.1:8080`. Open only 443, and 80 for the certificate challenge.
- **Station images:** the station image must exist on the VM (`make image` as `vnap`, or
  `docker load`). Users may choose only the images listed in `sim/policy.toml`.
- **Upgrade:**

  ```bash
  sudo -u vnap sh -c 'cd /home/vnap/vnap-secure && git pull && git submodule update --init'
  systemctl restart vnap-api
  ```

  Dependencies reinstall on start when `requirements.txt` changed, and the database schema
  is upgraded in place.
- **Moving from the old layout** (before 2026-10-09 the service was in
  `vnap-docker/api/`): stop the service, pull, then copy the unit files again, since their
  paths changed to `service/`. Move `vnap-docker/api/{data,secrets,backups}` to `service/` if
  they did not move with the checkout, then start.

## Configuration

`service/config.toml` (or the file named by `VNAP_API_CONFIG`). Every key is described in
[configuration](../reference/configuration.md):
- the data directory and session lifetime;
- lockout and the login rate limit;
- TOTP;
- backups;
- per-role limits;
- container CPU and memory;
- map tiles;
- worker threads.

## Accounts and authentication

- **Accounts:** built-in usernames and passwords, hashed with Argon2id (at least 12
  characters). Roles: `admin`, `user`, `viewer`. Admins create accounts and reset passwords
  (web UI, or `python3 -m vnapapi.admin` on the server); there is no self-registration.
- **Sessions:** HttpOnly, Secure, SameSite=Strict cookies. Every state-changing request needs
  the session's `X-CSRF-Token` (returned by login and `/api/auth/me`).
- **API tokens** (`Authorization: Bearer vnap_...`): for automation, scoped to one user,
  revocable. Sessions and tokens are stored only as SHA-256 digests.
- **Lockout:** repeated failed logins lock the account for a while; login attempts are
  rate-limited per client address.
- **TOTP second factor** (optional, per user; RFC 6238: 6 digits, 30 s, SHA-1, as
  authenticator apps expect):
  - **Turning it on:** a user does this on the Account page: password, then scan the QR code
    (or type the key) and confirm with a code. Ten one-time recovery codes are shown once; new
    ones can be made later.
  - **Logging in** then needs the password and a code. A wrong code counts as a failed login,
    and a code is accepted only once.
  - **Turning it off** needs the password and a code. For a lost phone without recovery codes,
    an admin resets it (Admin page, or `python3 -m vnapapi.admin reset-totp <name>`); the
    user's sessions end and the password alone works again.
  - **Where it can be managed:** only from a login session. API tokens are not affected by
    TOTP.
  - **Storage:** the secrets are encrypted with AES-256-GCM under `[auth] totp_key_file`,
    which is outside the data directory. See
    [backup and restore](backup-restore.md#the-totp-key).
- **Audit:** security-relevant actions go to the audit log (`/api/audit`, Admin page):
  - logins, failures and second factors;
  - run start and stop, control actions;
  - account, token and TOTP changes;
  - backups.

`python3 -m vnapapi.admin` on the server: `create-user`, `reset-password`, `unlock`,
`reset-totp`, `list-users`. Passwords come from the terminal or `VNAP_NEW_PASSWORD`, never
from the command line.

## Runs

- **What users start:** templates (`sim/scenarios/templates/`) or their own scenario text,
  with overrides. All are checked against the user policy, with field-level errors. Admins can
  also start the catalogue (`sim/scenarios/`).
- **Jobs:** starting and stopping are jobs. Each run gets:
  - a free instance;
  - CPU and memory limits on every container;
  - broker credentials generated by the service, which users never see.
- **Limits:** per role, a number of concurrent runs, a longest duration (runs stop
  automatically at their deadline) and disk space for kept results.
- **Record:** a run keeps its scenario, overrides, image, mobility seed and the C-ITS-PKI
  commit of its PKI.
- **Privacy:** runs are private to their owner (and admins) unless shared with other users,
  read-only.
- **Results:** at stop, the service keeps:
  - the containers' logs and the eavesdropper's logs;
  - the status;
  - the scoring (`score.json`; see [eavesdropper and scoring](../functional/eavesdropper-and-scoring.md));
  - the PKI's issue log (digests only, never keys);
  - `run.json` and the scenario.

## Tests

```bash
make test-service      # 29 tests, no Docker needed
```

The endpoints are listed in [API](../reference/api.md); the browser UI is described in
[web UI](web-ui.md).
