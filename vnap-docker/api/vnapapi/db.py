"""SQLite storage: users (with TOTP), sessions, API tokens, runs, shares, checks, audit log."""

import json
import os
import sqlite3
import threading
import time

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL, password_hash TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('admin', 'user', 'viewer')), disabled INTEGER NOT NULL DEFAULT 0,
    failed_logins INTEGER NOT NULL DEFAULT 0, locked_until REAL NOT NULL DEFAULT 0,
    created_at REAL NOT NULL, created_by TEXT,
    totp_secret TEXT, totp_pending TEXT, totp_enabled INTEGER NOT NULL DEFAULT 0, totp_last_step INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS totp_recovery (
    id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    code_hash TEXT NOT NULL, used_at REAL);
CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    csrf TEXT NOT NULL, created_at REAL NOT NULL, expires_at REAL NOT NULL, address TEXT);
CREATE TABLE IF NOT EXISTS tokens (
    id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name TEXT NOT NULL, token_hash TEXT UNIQUE NOT NULL, created_at REAL NOT NULL, last_used REAL,
    revoked INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY, owner_id INTEGER NOT NULL REFERENCES users(id), scenario TEXT NOT NULL,
    scenario_file TEXT NOT NULL, overrides TEXT NOT NULL, state TEXT NOT NULL, instance INTEGER,
    lan TEXT, ctl TEXT, vnap_run_id TEXT, image TEXT, seed INTEGER, error TEXT,
    created_at REAL NOT NULL, started_at REAL, stopped_at REAL, deadline REAL, stop_reason TEXT,
    start_status TEXT);
CREATE TABLE IF NOT EXISTS run_shares (
    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE, user_id INTEGER NOT NULL REFERENCES users(id),
    PRIMARY KEY (run_id, user_id));
CREATE TABLE IF NOT EXISTS checks (
    id INTEGER PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    created_at REAL NOT NULL, verdict TEXT NOT NULL, result TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS audit (
    id INTEGER PRIMARY KEY, ts REAL NOT NULL, username TEXT, action TEXT NOT NULL, target TEXT,
    detail TEXT, address TEXT);
"""


class Database:
    def __init__(self, path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self.conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.execute("PRAGMA journal_mode = WAL")
        self.lock = threading.RLock()
        with self.lock:
            self._migrate()
            self.conn.executescript(SCHEMA)
        os.chmod(path, 0o600)

    # columns added after the first release: (table, column, definition)
    MIGRATIONS = [
        ("users", "totp_secret", "TEXT"),
        ("users", "totp_pending", "TEXT"),
        ("users", "totp_enabled", "INTEGER NOT NULL DEFAULT 0"),
        ("users", "totp_last_step", "INTEGER NOT NULL DEFAULT 0"),
    ]

    def _migrate(self):
        tables = {r[0] for r in self.conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        for table, column, definition in self.MIGRATIONS:
            if table in tables and column not in {r[1] for r in self.conn.execute(f"PRAGMA table_info({table})")}:
                self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    def query(self, sql, args=()):
        with self.lock:
            return [dict(r) for r in self.conn.execute(sql, args).fetchall()]

    def one(self, sql, args=()):
        rows = self.query(sql, args)
        return rows[0] if rows else None

    def execute(self, sql, args=()):
        with self.lock:
            return self.conn.execute(sql, args).lastrowid

    def audit(self, username, action, target=None, detail=None, address=None):
        self.execute("INSERT INTO audit (ts, username, action, target, detail, address) VALUES (?, ?, ?, ?, ?, ?)",
                     (time.time(), username, action, target, json.dumps(detail) if detail is not None else None, address))
