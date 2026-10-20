"""Verify Google IAP identity and resolve server-owned workspace roles."""

import json
import re
import threading
import time
from dataclasses import dataclass

from google.auth.transport.requests import Request
from google.oauth2 import id_token

CERTS_URL = "https://www.gstatic.com/iap/verify/public_key"
ISSUER = "https://cloud.google.com/iap"


class IdentityDenied(ValueError):
    """An assertion is absent, invalid or outside the configured role list."""


class IdentityUnavailable(IdentityDenied):
    """The trusted public-key service could not confirm an identity."""


@dataclass(frozen=True)
class Principal:
    email: str
    role: str


class _PublicKeys:
    """Cache public certificates only; assertions never enter this transport."""

    def __init__(self, request):
        self.request = request
        self.lock = threading.Lock()
        self.response = None
        self.expires = 0.0

    def __call__(self, url, **kwargs):
        if url != CERTS_URL:
            raise ValueError("Unexpected identity certificate source")
        with self.lock:
            if self.response is not None and time.monotonic() < self.expires:
                return self.response
            try:
                response = self.request(url, timeout=5, **kwargs)
                if response.status != 200 or len(response.data) > 100000:
                    raise ValueError
                keys = json.loads(response.data)
                if (
                    not isinstance(keys, dict)
                    or not keys
                    or any(
                        not isinstance(key, str) or not isinstance(value, str)
                        for key, value in keys.items()
                    )
                ):
                    raise ValueError
            except Exception:
                raise IdentityUnavailable("Google identity verification is unavailable") from None
            self.response = response
            self.expires = time.monotonic() + 300
            return response


class IapIdentity:
    def __init__(self, audience: str, roles: dict, *, request=None):
        if (
            not isinstance(audience, str)
            or re.fullmatch(
                r"/projects/[0-9]{6,30}/locations/[a-z][a-z0-9-]{1,62}/services/[a-z][a-z0-9-]{0,62}",
                audience,
            )
            is None
        ):
            raise ValueError("Configure the exact Cloud Run IAP audience")
        if not isinstance(roles, dict) or not 1 <= len(roles) <= 1000:
            raise ValueError("Configure assigned workspace identities")
        normalised = {}
        for email, role in roles.items():
            if (
                not isinstance(email, str)
                or len(email) > 254
                or re.fullmatch(r"[^@\s*]{1,64}@[^@\s*]+\.[^@\s*]+", email) is None
                or not isinstance(role, str)
                or role not in {"read", "execute", "review"}
                or email.lower() in normalised
            ):
                raise ValueError("Invalid workspace identity or role")
            normalised[email.lower()] = role
        self.audience = audience
        self.roles = normalised
        self.request = _PublicKeys(request if request is not None else Request())

    @classmethod
    def from_environment(cls):
        import os

        audience = os.getenv("DEMO_IAP_AUDIENCE")
        raw = os.getenv("DEMO_IAP_ROLES")
        if audience is None and raw is None:
            return None
        try:
            if raw is None or len(raw) > 100000:
                raise ValueError

            def unique_roles(pairs):
                result = {}
                for key, value in pairs:
                    if key in result:
                        raise ValueError("Duplicate workspace identity")
                    result[key] = value
                return result

            roles = json.loads(raw, object_pairs_hook=unique_roles)
            return cls(audience, roles)
        except (ValueError, TypeError):
            raise ValueError(
                "Google workspace identity configuration is incomplete or invalid"
            ) from None

    def verify(self, assertion: str) -> Principal:
        if not isinstance(assertion, str) or not 1 <= len(assertion) <= 8192:
            raise IdentityDenied("Google identity is required")
        try:
            claims = id_token.verify_token(
                assertion,
                self.request,
                audience=self.audience,
                certs_url=CERTS_URL,
                clock_skew_in_seconds=30,
            )
            email = claims.get("email")
            issued, expires = claims.get("iat"), claims.get("exp")
            if (
                claims.get("iss") != ISSUER
                or claims.get("aud") != self.audience
                or not isinstance(claims.get("sub"), str)
                or not claims["sub"]
                or not isinstance(email, str)
                or email.lower() not in self.roles
                or type(issued) is not int
                or type(expires) is not int
                or not 0 < expires - issued <= 660
            ):
                raise ValueError
            return Principal(email=email.lower(), role=self.roles[email.lower()])
        except IdentityUnavailable:
            raise
        except Exception:
            raise IdentityDenied("Google identity is invalid or not assigned access") from None
