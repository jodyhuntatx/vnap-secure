"""TOTP second factor (RFC 6238: HMAC-SHA1, 6 digits, 30 s steps), recovery codes, and the
encryption of TOTP secrets at rest.

The secrets must be stored reversibly (the server computes the same codes as the user's app),
so they are encrypted with AES-256-GCM under a key kept in its own file outside the data
directory: a copy of the database or a backup alone does not reveal anyone's second factor."""

import base64
import hashlib
import hmac
import os
import secrets
import struct
import time
import urllib.parse

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

DIGITS = 6
STEP = 30
WINDOW = 1            # accept the previous and the next step too (clock drift)
RECOVERY_CODES = 10


def new_secret():
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def code_at(secret, step, digits=DIGITS):
    key = base64.b32decode(secret + "=" * (-len(secret) % 8), casefold=True)
    mac = hmac.new(key, struct.pack(">Q", step), hashlib.sha1).digest()
    offset = mac[-1] & 0x0F
    value = struct.unpack(">I", mac[offset:offset + 4])[0] & 0x7FFFFFFF
    return str(value % 10 ** digits).zfill(digits)


def current_step(now=None):
    return int((time.time() if now is None else now) // STEP)


def match(secret, code, last_step=0, now=None):
    """The time step the code belongs to, or None. Steps at or before last_step are refused,
    so a code cannot be used twice."""
    code = (code or "").replace(" ", "")
    if not (code.isdigit() and len(code) == DIGITS):
        return None
    now_step = current_step(now)
    for step in range(now_step - WINDOW, now_step + WINDOW + 1):
        if step > last_step and hmac.compare_digest(code_at(secret, step), code):
            return step
    return None


def otpauth_uri(secret, username, issuer):
    label = urllib.parse.quote(f"{issuer}:{username}")
    query = urllib.parse.urlencode({"secret": secret, "issuer": issuer, "algorithm": "SHA1",
                                    "digits": DIGITS, "period": STEP})
    return f"otpauth://totp/{label}?{query}"


def qr_svg(text):
    """The URI as an SVG QR code (for authenticator apps), or None without the segno package."""
    try:
        import io
        import segno
    except ImportError:
        return None
    out = io.BytesIO()
    segno.make(text, error="m").save(out, kind="svg", scale=5, border=2, dark="#000", light="#fff")
    return out.getvalue().decode()


def new_recovery_codes():
    """One-time codes for a lost authenticator: (codes shown once, their digests to store)."""
    alphabet = "abcdefghjkmnpqrstuvwxyz23456789"
    codes = ["-".join("".join(secrets.choice(alphabet) for _ in range(5)) for _ in range(2))
             for _ in range(RECOVERY_CODES)]
    return codes, [recovery_digest(c) for c in codes]


def recovery_digest(code):
    return hashlib.sha256((code or "").strip().lower().encode()).hexdigest()


class SecretBox:
    """AES-256-GCM for TOTP secrets; the key file is created (mode 0600) on first use."""

    def __init__(self, key_file):
        self.key_file = key_file
        self._key = None

    def key(self):
        if self._key is None:
            if not os.path.exists(self.key_file):
                os.makedirs(os.path.dirname(self.key_file) or ".", exist_ok=True)
                fd = os.open(self.key_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, "wb") as f:
                    f.write(AESGCM.generate_key(bit_length=256))
            with open(self.key_file, "rb") as f:
                self._key = f.read()
            if len(self._key) != 32:
                raise ValueError(f"{self.key_file}: not a 256-bit key")
        return self._key

    def seal(self, plaintext, context):
        nonce = secrets.token_bytes(12)
        sealed = AESGCM(self.key()).encrypt(nonce, plaintext.encode(), context.encode())
        return "v1:" + base64.b64encode(nonce + sealed).decode()

    def open(self, token, context):
        """The plaintext, or None when it cannot be decrypted (other key, tampered, other user)."""
        if not token or not token.startswith("v1:"):
            return None
        raw = base64.b64decode(token[3:])
        try:
            return AESGCM(self.key()).decrypt(raw[:12], raw[12:], context.encode()).decode()
        except Exception:  # noqa: BLE001  (InvalidTag: wrong key or modified value)
            return None
