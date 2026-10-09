"""Accounts and authentication: Argon2id passwords, optional TOTP second factor with recovery
codes, sessions with CSRF tokens, API tokens, login rate limiting and account lockout."""

import collections
import hashlib
import hmac
import secrets
import threading
import time

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from . import totp

ROLES = ("admin", "user", "viewer")
SESSION_COOKIE = "vnap_session"
CSRF_HEADER = "X-CSRF-Token"
TOKEN_PREFIX = "vnap_"
MIN_PASSWORD = 12
TOTP_REQUIRED = "a TOTP code (or a recovery code) is required"

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
        self.box = totp.SecretBox(cfg["auth"]["totp_key_file"])
        self.issuer = cfg["auth"]["totp_issuer"]

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
    def _failed(self, user):
        failed = user["failed_logins"] + 1
        locked = time.time() + 60 * self.cfg["auth"]["lockout_minutes"] if failed >= self.cfg["auth"]["max_failed_logins"] else 0
        self.db.execute("UPDATE users SET failed_logins = ?, locked_until = ? WHERE id = ?",
                        (0 if locked else failed, locked, user["id"]))

    def login(self, username, password, address, code=None, second_factor=True):
        """Returns (user, None) or (None, reason); reason TOTP_REQUIRED means the password was right
        and a code is missing. A wrong password or a wrong code count as failed logins, and
        repeated failures lock the account. On success user["second_factor"] says what was used."""
        user = self.db.one("SELECT * FROM users WHERE username = ?", (username,))
        if user is None:
            verify_password(self._dummy, password or "")
            return None, "invalid username or password"
        if user["locked_until"] > time.time():
            return None, "account locked after repeated failed logins; try again later"
        if not verify_password(user["password_hash"], password or ""):
            self._failed(user)
            return None, "invalid username or password"
        if user["disabled"]:
            return None, "account disabled"
        user["second_factor"] = None
        if second_factor and user["totp_enabled"]:
            if not code:
                return None, TOTP_REQUIRED
            used = self.check_second_factor(user, code)
            if not used:
                self._failed(user)
                return None, "invalid TOTP or recovery code"
            user["second_factor"] = used
        if _hasher.check_needs_rehash(user["password_hash"]):
            self.db.execute("UPDATE users SET password_hash = ? WHERE id = ?", (_hasher.hash(password), user["id"]))
        self.db.execute("UPDATE users SET failed_logins = 0, locked_until = 0 WHERE id = ?", (user["id"],))
        return user, None

    # ------------------------------------------------------------ TOTP
    def _context(self, user):
        return f"totp:{user['id']}"   # a sealed secret only opens for its own user

    def check_second_factor(self, user, code):
        """'totp' or 'recovery' when the code is valid (a recovery code is then used up), else None."""
        code = (code or "").strip()
        secret = self.box.open(user["totp_secret"], self._context(user))
        if secret and code.replace(" ", "").isdigit():
            step = totp.match(secret, code, user["totp_last_step"])
            if step is None:
                return None
            # the step only moves forward: the same code cannot log in twice
            with self.db.lock:
                done = self.db.conn.execute("UPDATE users SET totp_last_step = ? WHERE id = ? AND totp_last_step < ?",
                                            (step, user["id"], step)).rowcount
            return "totp" if done else None
        with self.db.lock:
            done = self.db.conn.execute("UPDATE totp_recovery SET used_at = ? WHERE user_id = ? AND code_hash = ? AND used_at IS NULL",
                                        (time.time(), user["id"], totp.recovery_digest(code))).rowcount
        return "recovery" if done else None

    def totp_status(self, user):
        left = self.db.one("SELECT COUNT(*) AS n FROM totp_recovery WHERE user_id = ? AND used_at IS NULL", (user["id"],))["n"]
        return {"enabled": bool(user["totp_enabled"]), "recovery_codes_left": left if user["totp_enabled"] else 0}

    def totp_setup(self, user):
        """A new secret, pending until confirmed with a code from the app (the current one stays valid)."""
        secret = totp.new_secret()
        self.db.execute("UPDATE users SET totp_pending = ? WHERE id = ?", (self.box.seal(secret, self._context(user)), user["id"]))
        uri = totp.otpauth_uri(secret, user["username"], self.issuer)
        return {"secret": secret, "uri": uri, "qr_svg": totp.qr_svg(uri)}

    def totp_enable(self, user, code):
        """Confirms the pending secret with a code; returns the new recovery codes (shown once)."""
        row = self.db.one("SELECT * FROM users WHERE id = ?", (user["id"],))
        secret = self.box.open(row["totp_pending"], self._context(row))
        if not secret:
            raise ValueError("no TOTP setup in progress: start the setup first")
        step = totp.match(secret, code)
        if step is None:
            raise ValueError("the code does not match: check the time on the device and try the next code")
        codes, digests = totp.new_recovery_codes()
        with self.db.lock:
            self.db.conn.execute("UPDATE users SET totp_secret = totp_pending, totp_pending = NULL, totp_enabled = 1, "
                                 "totp_last_step = ? WHERE id = ?", (step, user["id"]))
            self.db.conn.execute("DELETE FROM totp_recovery WHERE user_id = ?", (user["id"],))
            self.db.conn.executemany("INSERT INTO totp_recovery (user_id, code_hash) VALUES (?, ?)",
                                     [(user["id"], d) for d in digests])
        return codes

    def new_recovery_codes(self, user):
        codes, digests = totp.new_recovery_codes()
        with self.db.lock:
            self.db.conn.execute("DELETE FROM totp_recovery WHERE user_id = ?", (user["id"],))
            self.db.conn.executemany("INSERT INTO totp_recovery (user_id, code_hash) VALUES (?, ?)",
                                     [(user["id"], d) for d in digests])
        return codes

    def totp_disable(self, username):
        """Turns the second factor off (the user's own choice, or an admin reset for a lost device)."""
        with self.db.lock:
            self.db.conn.execute("UPDATE users SET totp_secret = NULL, totp_pending = NULL, totp_enabled = 0, "
                                 "totp_last_step = 0 WHERE username = ?", (username,))
            self.db.conn.execute("DELETE FROM totp_recovery WHERE user_id = (SELECT id FROM users WHERE username = ?)",
                                 (username,))

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
