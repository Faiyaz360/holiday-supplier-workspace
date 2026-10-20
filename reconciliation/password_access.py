"""A password gate with durable, revocable sessions and bounded login attempts."""

import hashlib
import hmac
import json
import math
import re
import secrets
import threading
import time
from urllib.parse import urlsplit

from fastapi import HTTPException
from google.api_core.exceptions import PreconditionFailed

COOKIE = "__Host-holiday-session"
SESSION_SECONDS = 3600
_HASH = re.compile(r"scrypt-v1\$([a-f0-9]{32})\$([a-f0-9]{64})")
_TOKEN = re.compile(r"[A-Za-z0-9_-]{43}")
_HASH_SLOT = threading.BoundedSemaphore(1)


def _derive(password, salt):
    return hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=2**17,
        r=8,
        p=1,
        maxmem=256 * 1024 * 1024,
        dklen=32,
    )


def hash_password(password):
    """Use only from trusted setup, never an HTTP request."""
    if not isinstance(password, str) or not 16 <= len(password) <= 512:
        raise ValueError("Choose a password between 16 and 512 characters")
    salt = secrets.token_bytes(16)
    return f"scrypt-v1${salt.hex()}${_derive(password, salt).hex()}"


class PasswordAccess:
    """The bucket is the same private storage boundary used by saved runs."""

    def __init__(self, bucket, encoded, origin, *, clock=None, allow_local_http=False):
        match = _HASH.fullmatch(encoded)
        parsed = urlsplit(origin)
        local_http = allow_local_http and parsed.scheme == "http" and parsed.hostname == "localhost"
        if (
            not match
            or (parsed.scheme != "https" and not local_http)
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.path
            or parsed.query
            or parsed.fragment
            or origin != f"{parsed.scheme}://{parsed.netloc}"
        ):
            raise ValueError("Configure a password hash and exact HTTPS workspace origin")
        self.bucket, self.origin = bucket, origin
        self.salt, self.digest = (bytes.fromhex(part) for part in match.groups())
        self.epoch = hashlib.sha256(encoded.encode()).hexdigest()
        self.clock = clock if clock is not None else time.time

    def csrf(self, request):
        if (
            request.headers.get("origin") != self.origin
            or request.headers.get("x-workspace-request") != "1"
        ):
            raise HTTPException(403, "Workspace request not allowed")

    def _read(self, name):
        try:
            blob = self.bucket.get_blob(name, timeout=10, retry=None)
            if blob is None:
                return None, 0
            if blob.size > 4096:
                raise ValueError("Invalid authentication record")
            data = json.loads(
                blob.download_as_bytes(if_generation_match=blob.generation, timeout=10, retry=None)
            )
            if not isinstance(data, dict):
                raise ValueError("Invalid authentication record")
            return data, int(blob.generation)
        except PreconditionFailed:
            raise
        except Exception:
            raise HTTPException(503, "Sign-in storage is unavailable. Try again later") from None

    def _write(self, name, data, generation):
        try:
            self.bucket.blob(name).upload_from_string(
                json.dumps(data),
                content_type="application/json",
                if_generation_match=generation,
                timeout=10,
                retry=None,
            )
        except PreconditionFailed:
            raise
        except Exception:
            raise HTTPException(503, "Sign-in storage is unavailable. Try again later") from None

    def _attempt(self):
        now = self.clock()
        for _ in range(4):
            try:
                record, generation = self._read("auth/login-attempts.json")
                if record is None:
                    record = {"start": now, "count": 0}
                start, count = record.get("start"), record.get("count")
                if (
                    type(start) not in {float, int}
                    or not math.isfinite(start)
                    or type(count) is not int
                    or count < 0
                ):
                    raise HTTPException(503, "Sign-in protection is unavailable")
                if now >= start + 60:
                    start, count = now, 0
                if count >= 10:
                    raise HTTPException(
                        429,
                        "Too many sign-in attempts. Try again shortly",
                        headers={"Retry-After": str(max(1, math.ceil(start + 60 - now)))},
                    )
                self._write(
                    "auth/login-attempts.json", {"start": start, "count": count + 1}, generation
                )
                return
            except PreconditionFailed:
                continue
        raise HTTPException(503, "Sign-in is busy. Try again shortly")

    def login(self, password):
        self._attempt()
        if not _HASH_SLOT.acquire(blocking=False):
            raise HTTPException(
                429, "Sign-in is busy. Try again shortly", headers={"Retry-After": "2"}
            )
        try:
            valid = hmac.compare_digest(_derive(password, self.salt), self.digest)
        except UnicodeError:
            raise HTTPException(422, "Enter a valid password") from None
        finally:
            _HASH_SLOT.release()
        if not valid:
            raise HTTPException(401, "That password did not match. Try again")
        token = secrets.token_urlsafe(32)
        record = {"expires": self.clock() + SESSION_SECONDS, "epoch": self.epoch}
        try:
            self._write(self._session_name(token), record, 0)
        except PreconditionFailed:
            raise HTTPException(503, "Sign-in could not finish. Try again") from None
        return token

    @staticmethod
    def _session_name(token):
        return "auth/sessions/" + hashlib.sha256(token.encode()).hexdigest() + ".json"

    def require(self, request):
        token = request.cookies.get(COOKIE, "")
        if not _TOKEN.fullmatch(token):
            raise HTTPException(401, "Sign in to open this workspace")
        try:
            record, _ = self._read(self._session_name(token))
        except PreconditionFailed:
            raise HTTPException(401, "Your session changed. Sign in again") from None
        expires = record.get("expires") if record else None
        if (
            type(expires) not in {int, float}
            or not math.isfinite(expires)
            or expires <= self.clock()
            or record.get("epoch") != self.epoch
        ):
            raise HTTPException(401, "Your session has ended. Sign in again")
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            self.csrf(request)
        reference = record.get("statement_ref", "")
        if not isinstance(reference, str) or (
            reference and not re.fullmatch(r"[a-f0-9]{20}", reference)
        ):
            raise HTTPException(503, "The saved workspace reference is unavailable")
        request.state.statement_ref = reference
        request.state.session_expires = expires
        return "workspace"

    def remember_statement(self, request, run_id):
        if not re.fullmatch(r"[a-f0-9]{20}", run_id):
            raise ValueError("Invalid statement reference")
        name = self._session_name(request.cookies.get(COOKIE, ""))
        for _ in range(4):
            try:
                record, generation = self._read(name)
                expires = record.get("expires") if record else None
                if (
                    type(expires) not in {int, float}
                    or not math.isfinite(expires)
                    or expires <= self.clock()
                    or record.get("epoch") != self.epoch
                ):
                    raise HTTPException(401, "Your session has ended. Sign in again")
                self._write(name, record | {"statement_ref": run_id}, generation)
                return
            except PreconditionFailed:
                continue
        raise HTTPException(503, "The review was saved but its reference could not be remembered")

    def logout(self, request):
        self.csrf(request)
        token = request.cookies.get(COOKIE, "")
        if not _TOKEN.fullmatch(token):
            return
        name = self._session_name(token)
        for _ in range(4):
            try:
                record, generation = self._read(name)
                if record is None:
                    return
                self._write(name, {"expires": 0, "epoch": self.epoch}, generation)
                return
            except PreconditionFailed:
                continue
        raise HTTPException(503, "Sign out could not be confirmed. Try again")
