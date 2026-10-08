"""Accounts and authentication: Argon2id passwords, sessions with CSRF tokens, API tokens,
login rate limiting and account lockout."""

import collections
import hashlib
import hmac
import secrets
import threading
import time

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

ROLES = ("admin", "user", "viewer")
SESSION_COOKIE = "vnap_session"
CSRF_HEADER = "X-CSRF-Token"
TOKEN_PREFIX = "vnap_"
MIN_PASSWORD = 12

_hasher = PasswordHasher()  # Argon2id with the library's current recommended parameters


def hash_password(password):
    check_password_policy(password)
    return _hasher.hash(password)


def check_password_policy(password):
    if not isinstance(password, str) or len(password) < MIN_PASSWORD:
        raise ValueError(f"password must have at least {MIN_PASSWORD} characters")


def verify_password(stored_hash, password):
    try:
        return _hasher.verify(stored_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def digest(token):
    """Sessions and API tokens are stored only as SHA-256 digests."""
    return hashlib.sha256(token.encode()).hexdigest()


def new_token(prefix=""):
    return prefix + secrets.token_urlsafe(32)


def constant_equal(a, b):
    return hmac.compare_digest(a.encode(), b.encode())


class LoginLimiter:
    """At most `per_minute` login attempts per client address (in memory)."""

    def __init__(self, per_minute):
        self.per_minute = per_minute
        self.attempts = collections.defaultdict(collections.deque)
        self.lock = threading.Lock()

    def allow(self, address):
        now = time.monotonic()
        with self.lock:
            q = self.attempts[address]
            while q and now - q[0] > 60:
                q.popleft()
            if len(q) >= self.per_minute:
                return False
            q.append(now)
            return True


class Accounts:
    def __init__(self, db, cfg):
        self.db, self.cfg = db, cfg
        self.limiter = LoginLimiter(cfg["auth"]["login_attempts_per_minute"])
        # a fixed hash to verify against for unknown users, so timing does not reveal who exists
        self._dummy = _hasher.hash(secrets.token_urlsafe(16))

    # ------------------------------------------------------------ users
    def create_user(self, username, password, role, created_by=None):
        if role not in ROLES:
            raise ValueError(f"role must be one of {', '.join(ROLES)}")
        if not username or not username.replace("-", "").replace("_", "").replace(".", "").isalnum() or len(username) > 64:
            raise ValueError("username: letters, digits, '.', '-', '_' (at most 64)")
        if self.db.one("SELECT id FROM users WHERE username = ?", (username,)):
            raise ValueError("username already exists")
        return self.db.execute("INSERT INTO users (username, password_hash, role, created_at, created_by) VALUES (?, ?, ?, ?, ?)",
                               (username, hash_password(password), role, time.time(), created_by))

    def set_password(self, username, password):
        self.db.execute("UPDATE users SET password_hash = ?, failed_logins = 0, locked_until = 0 WHERE username = ?",
                        (hash_password(password), username))
        self.revoke_sessions(username)

    def revoke_sessions(self, username):
        self.db.execute("DELETE FROM sessions WHERE user_id = (SELECT id FROM users WHERE username = ?)", (username,))

    # ------------------------------------------------------------ login
    def login(self, username, password, address):
        """Returns (user, None) or (None, reason). Locks the account after repeated failures."""
        user = self.db.one("SELECT * FROM users WHERE username = ?", (username,))
        if user is None:
            verify_password(self._dummy, password or "")
            return None, "invalid username or password"
        if user["locked_until"] > time.time():
            return None, "account locked after repeated failed logins; try again later"
        if not verify_password(user["password_hash"], password or ""):
            failed = user["failed_logins"] + 1
            locked = time.time() + 60 * self.cfg["auth"]["lockout_minutes"] if failed >= self.cfg["auth"]["max_failed_logins"] else 0
            self.db.execute("UPDATE users SET failed_logins = ?, locked_until = ? WHERE id = ?",
                            (0 if locked else failed, locked, user["id"]))
            return None, "invalid username or password"
        if user["disabled"]:
            return None, "account disabled"
        if _hasher.check_needs_rehash(user["password_hash"]):
            self.db.execute("UPDATE users SET password_hash = ? WHERE id = ?", (_hasher.hash(password), user["id"]))
        self.db.execute("UPDATE users SET failed_logins = 0, locked_until = 0 WHERE id = ?", (user["id"],))
        return user, None

    def new_session(self, user, address):
        token, csrf = new_token(), new_token()
        now = time.time()
        self.db.execute("INSERT INTO sessions (token_hash, user_id, csrf, created_at, expires_at, address) VALUES (?, ?, ?, ?, ?, ?)",
                        (digest(token), user["id"], csrf, now, now + 3600 * self.cfg["server"]["session_hours"], address))
        self.db.execute("DELETE FROM sessions WHERE expires_at < ?", (now,))
        return token, csrf

    def session(self, token):
        row = self.db.one("SELECT s.csrf, s.expires_at, u.* FROM sessions s JOIN users u ON u.id = s.user_id "
                          "WHERE s.token_hash = ?", (digest(token),))
        if not row or row["expires_at"] < time.time() or row["disabled"]:
            return None
        return row

    def end_session(self, token):
        self.db.execute("DELETE FROM sessions WHERE token_hash = ?", (digest(token),))

    # ------------------------------------------------------------ API tokens
    def new_api_token(self, user, name):
        token = new_token(TOKEN_PREFIX)
        tid = self.db.execute("INSERT INTO tokens (user_id, name, token_hash, created_at) VALUES (?, ?, ?, ?)",
                              (user["id"], name, digest(token), time.time()))
        return tid, token

    def api_token(self, token):
        row = self.db.one("SELECT t.id AS token_id, u.* FROM tokens t JOIN users u ON u.id = t.user_id "
                          "WHERE t.token_hash = ? AND t.revoked = 0", (digest(token),))
        if not row or row["disabled"]:
            return None
        self.db.execute("UPDATE tokens SET last_used = ? WHERE id = ?", (time.time(), row["token_id"]))
        return row
