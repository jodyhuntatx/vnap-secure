"""Backups of the service's data: the database, the collected results and the users' scenario
texts, in one compressed archive with a manifest of SHA-256 digests.

  python3 -m vnapapi.backup create            (deploy/vnap-backup.timer runs it daily)
  python3 -m vnapapi.backup list
  python3 -m vnapapi.backup verify <archive>
  python3 -m vnapapi.backup restore <archive> [--force]   (with the service stopped)

The database is copied with SQLite's online backup API, so `create` is consistent while the
service runs. Not in the archive: the TOTP key ([auth] totp_key_file, keep a copy of it
separately) and the runs' PKI keys (they live only as long as a run). The archive holds password
hashes and audit data: it is written with mode 0600; keep copies off the VM encrypted."""

import argparse
import hashlib
import io
import json
import os
import shutil
import socket
import sqlite3
import sys
import tarfile
import tempfile
import time

FORMAT = 1
PREFIX = "vnap-backup-"
SUFFIX = ".tar.gz"
DB_NAME = "vnapapi.db"
DIRS = ("results", "scenarios")   # under data_dir
ACTIVE = ("queued", "starting", "running", "stopping")


class BackupError(Exception):
    pass


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def snapshot_db(src, dest):
    """A consistent copy of a live SQLite database (WAL included)."""
    source = sqlite3.connect(src)
    target = sqlite3.connect(dest)
    try:
        source.backup(target)
    finally:
        target.close()
        source.close()


def create(cfg, dest_dir=None, keep=None):
    """Writes a new archive; returns its path. Deletes the oldest beyond `keep` (0: keep all)."""
    data = cfg["server"]["data_dir"]
    dest_dir = dest_dir or cfg["backup"]["dir"]
    keep = cfg["backup"]["keep"] if keep is None else keep
    db_path = os.path.join(data, DB_NAME)
    if not os.path.isfile(db_path):
        raise BackupError(f"no database at {db_path}")
    os.makedirs(dest_dir, mode=0o700, exist_ok=True)
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    final = os.path.join(dest_dir, f"{PREFIX}{stamp}{SUFFIX}")
    n = 1
    while os.path.exists(final):
        final = os.path.join(dest_dir, f"{PREFIX}{stamp}-{n}{SUFFIX}")
        n += 1
    with tempfile.TemporaryDirectory(dir=dest_dir) as tmp:
        snap = os.path.join(tmp, DB_NAME)
        snapshot_db(db_path, snap)
        files = {DB_NAME: snap}
        for d in DIRS:
            root = os.path.join(data, d)
            for dirpath, _, names in os.walk(root):
                for name in sorted(names):
                    full = os.path.join(dirpath, name)
                    if os.path.isfile(full) and not os.path.islink(full):
                        files[os.path.relpath(full, data)] = full
        con = sqlite3.connect(snap)
        counts = {t: con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ("users", "runs", "checks", "audit")}
        con.close()
        manifest = {"format": FORMAT, "created_at": time.time(), "host": socket.gethostname(), "counts": counts,
                    "data_dir": os.path.abspath(data),
                    "files": {rel: {"sha256": _sha256(full), "bytes": os.path.getsize(full)} for rel, full in sorted(files.items())}}
        partial = os.path.join(tmp, "partial" + SUFFIX)
        fd = os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as raw, tarfile.open(fileobj=raw, mode="w:gz") as tar:
            body = json.dumps(manifest, indent=1).encode()
            info = tarfile.TarInfo("manifest.json")
            info.size, info.mtime, info.mode = len(body), int(time.time()), 0o600
            tar.addfile(info, io.BytesIO(body))
            for rel, full in sorted(files.items()):
                tar.add(full, arcname=f"data/{rel}", recursive=False)
        os.replace(partial, final)
    prune(dest_dir, keep)
    return final


def archives(dest_dir):
    """Archives in a directory, newest first: [{name, path, bytes, created_at}]."""
    if not os.path.isdir(dest_dir):
        return []
    out = []
    for name in os.listdir(dest_dir):
        if name.startswith(PREFIX) and name.endswith(SUFFIX):
            path = os.path.join(dest_dir, name)
            out.append({"name": name, "path": path, "bytes": os.path.getsize(path), "created_at": os.path.getmtime(path)})
    return sorted(out, key=lambda a: (a["created_at"], a["name"]), reverse=True)


def prune(dest_dir, keep):
    if keep and keep > 0:
        for old in archives(dest_dir)[keep:]:
            os.remove(old["path"])


def _members(tar):
    """Regular files only, with safe relative names (no links, devices, absolute paths or '..')."""
    out = {}
    for m in tar.getmembers():
        name = m.name
        if not m.isfile() or name.startswith("/") or ".." in name.split("/"):
            raise BackupError(f"unexpected entry in archive: {name}")
        out[name] = m
    return out


def verify(path):
    """Checks every file against the manifest and the database's integrity; returns the manifest."""
    try:
        tar = tarfile.open(path, "r:gz")
    except (tarfile.TarError, OSError) as e:
        raise BackupError(f"{path}: not a readable archive ({e})")
    with tar:
        members = _members(tar)
        if "manifest.json" not in members:
            raise BackupError("no manifest.json")
        manifest = json.load(tar.extractfile(members["manifest.json"]))
        if manifest.get("format") != FORMAT:
            raise BackupError(f"unknown backup format {manifest.get('format')}")
        listed = {f"data/{rel}" for rel in manifest["files"]}
        extra = set(members) - listed - {"manifest.json"}
        if extra:
            raise BackupError(f"files not in the manifest: {', '.join(sorted(extra))}")
        for rel, meta in manifest["files"].items():
            m = members.get(f"data/{rel}")
            if m is None:
                raise BackupError(f"missing: {rel}")
            h = hashlib.sha256()
            f = tar.extractfile(m)
            for block in iter(lambda: f.read(1 << 20), b""):
                h.update(block)
            if h.hexdigest() != meta["sha256"]:
                raise BackupError(f"checksum mismatch: {rel}")
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, DB_NAME)
            with open(db, "wb") as out:
                shutil.copyfileobj(tar.extractfile(members[f"data/{DB_NAME}"]), out)
            con = sqlite3.connect(db)
            result = con.execute("PRAGMA integrity_check").fetchone()[0]
            con.close()
            if result != "ok":
                raise BackupError(f"database integrity check: {result}")
    return manifest


def restore(cfg, path, force=False):
    """Replaces data_dir with the archive's content (the service must be stopped). The previous
    data_dir is kept as <data_dir>.before-restore-<time>. Runs that were active when the backup was
    taken are marked failed: their containers are not part of a backup."""
    manifest = verify(path)
    data = cfg["server"]["data_dir"].rstrip("/")
    if os.path.exists(data) and os.listdir(data) and not force:
        raise BackupError(f"{data} is not empty: stop the service and use --force (the current data is kept aside)")
    parent = os.path.dirname(data)
    staging = tempfile.mkdtemp(prefix=".restore-", dir=parent)
    try:
        with tarfile.open(path, "r:gz") as tar:
            members = _members(tar)
            for rel in manifest["files"]:
                target = os.path.join(staging, rel)
                os.makedirs(os.path.dirname(target), mode=0o700, exist_ok=True)
                fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, "wb") as out:
                    shutil.copyfileobj(tar.extractfile(members[f"data/{rel}"]), out)
        for d in DIRS:
            os.makedirs(os.path.join(staging, d), mode=0o700, exist_ok=True)
        con = sqlite3.connect(os.path.join(staging, DB_NAME))
        # runs refer to their scenario text by absolute path: follow a move to another data_dir
        old = manifest.get("data_dir", "").rstrip("/")
        if old and old != os.path.abspath(data):
            con.execute("UPDATE runs SET scenario_file = ? || substr(scenario_file, ?) WHERE substr(scenario_file, 1, ?) = ?",
                        (os.path.abspath(data), len(old) + 1, len(old) + 1, old + "/"))
        marked = con.execute(f"UPDATE runs SET state = 'failed', error = 'restored from a backup taken while the run was "
                             f"active; its containers are not part of the backup' WHERE state IN ({','.join('?' * len(ACTIVE))})",
                             ACTIVE).rowcount
        con.commit()
        con.close()
        os.chmod(staging, 0o700)
        aside = None
        if os.path.exists(data):
            aside = f"{data}.before-restore-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}"
            os.rename(data, aside)
        os.rename(staging, data)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return {"restored": manifest["counts"], "runs_marked_failed": marked, "previous_data": aside}


def main():
    ap = argparse.ArgumentParser(prog="vnapapi.backup", description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("create", help="write a new archive (and delete the oldest beyond [backup] keep)")
    p.add_argument("--dir", help="where to write it (default [backup] dir)")
    sub.add_parser("list", help="archives in [backup] dir, newest first")
    sub.add_parser("verify", help="check an archive's checksums and database").add_argument("archive")
    p = sub.add_parser("restore", help="replace the data directory with an archive (service stopped)")
    p.add_argument("archive")
    p.add_argument("--force", action="store_true", help="replace an existing data directory (kept aside)")
    args = ap.parse_args()

    from .config import load_config
    cfg = load_config()
    try:
        if args.cmd == "create":
            path = create(cfg, args.dir)
            m = verify(path)
            print(f"{path}: {len(m['files'])} file(s), {m['counts']['users']} user(s), {m['counts']['runs']} run(s), "
                  f"{os.path.getsize(path) / 1e6:.1f} MB")
        elif args.cmd == "list":
            for a in archives(cfg["backup"]["dir"]):
                print(f"{a['name']}  {a['bytes'] / 1e6:8.1f} MB  {time.strftime('%Y-%m-%d %H:%M', time.localtime(a['created_at']))}")
        elif args.cmd == "verify":
            m = verify(args.archive)
            print(f"ok: {len(m['files'])} file(s), counts {m['counts']}, taken "
                  f"{time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(m['created_at']))} on {m['host']}")
        else:
            r = restore(cfg, args.archive, args.force)
            print(f"restored {r['restored']}; {r['runs_marked_failed']} active run(s) marked failed"
                  + (f"; previous data kept in {r['previous_data']}" if r["previous_data"] else ""))
    except BackupError as e:
        sys.exit(f"backup: {e}")


if __name__ == "__main__":
    main()
