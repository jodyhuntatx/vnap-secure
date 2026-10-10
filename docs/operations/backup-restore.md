# Backup and restore

## What is backed up

An archive holds the service's data directory:
- **Database:** a consistent online snapshot of the users, runs, checks and audit log.
- **Results:** every run's collected logs, scores and issue logs.
- **User scenarios:** the users' scenario texts.

Not in a backup:
- **The TOTP key** (`[auth] totp_key_file`): see [below](#the-totp-key).
- **The runs' PKI keys:** they exist only while a run does.
- **Running simulations:** containers and networks.

An archive contains password hashes and the audit log. Copy it off the VM only encrypted (for
example with `age`, `gpg`, or an encrypted rclone remote).

## Commands

```bash
cd ${VNAP_HOME}/service
./backup.sh create               # new archive in [backup] dir; the oldest beyond [backup] keep are deleted
./backup.sh list                 # archives, newest first
./backup.sh verify <archive>     # every file against the manifest, and the database's integrity
```

- **Archive:** `backups/vnap-backup-<UTC time>.tar.gz`, mode 0600, with a manifest of SHA-256
  digests. `[backup] keep` (default 14) archives are kept.
- **Daily backups:** `deploy/vnap-backup.timer` runs `create` at 03:15 as the service account
  (and at the next boot if the VM was off then).
- **Admins** can also click *Back up now* on the Admin page, which lists the archives too.
  Archives are downloaded on the server, not through the API.

## Restore

With the service stopped:

```bash
systemctl stop vnap-api
sudo -u vnap sh -c 'cd /home/vnap/vnap-secure/service && ./backup.sh restore <archive> --force'
systemctl start vnap-api
```

`restore`:
1. verifies the archive first;
2. keeps the current data directory as `data.before-restore-<time>`;
3. follows a change of data directory (e.g. a restore on another VM), so runs still find
   their scenario texts;
4. marks runs that were active at backup time as failed, since their containers are not in a
   backup.

Without `--force` it refuses to replace a data directory that is not empty.

## The TOTP key

TOTP secrets must be stored reversibly, because the server computes the same codes as the
user's app. They are encrypted with AES-256-GCM under the key in `[auth] totp_key_file`
(default `service/secrets/totp.key`). The key is created on first use, mode 0600, outside
the data directory, so the database or a backup alone does not reveal anyone's second factor.

- **Keep a copy** of the key file in a safe place, separately from the backups (once; it
  does not change).
- **If the key is lost,** every user's TOTP must be reset: Admin page, or
  `python3 -m vnapapi.admin reset-totp <name>`. Those users log in with their password and
  set TOTP up again.
- **Recovery codes** are stored as digests and do not depend on the key.
