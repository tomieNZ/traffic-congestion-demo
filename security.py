"""Security primitives for the traffic-congestion demo.

The functions in this module are intentionally small and explicit.  They make
the trust boundary visible: JWT authenticates the operator, while HMAC protects
the integrity of each sensor payload.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import datetime, timedelta, timezone
from typing import Any

import jwt


class TokenError(ValueError):
    """Raised when a bearer token is missing, invalid, or expired."""


def canonical_json(payload: dict[str, Any]) -> bytes:
    """Serialize a payload deterministically before signing it.

    Sorting keys and removing insignificant whitespace is important.  Without
    canonicalization, two JSON documents containing the same data could produce
    different signatures merely because their keys were ordered differently.
    The signature field itself is excluded because it is the value being
    computed and verified.
    """

    unsigned = {key: value for key, value in payload.items() if key != "signature"}
    return json.dumps(
        unsigned,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")


def sign_payload(payload: dict[str, Any], secret: str) -> str:
    """Return a lowercase HMAC-SHA256 digest for a sensor payload."""

    return hmac.new(
        secret.encode("utf-8"),
        canonical_json(payload),
        hashlib.sha256,
    ).hexdigest()


def verify_payload_signature(payload: dict[str, Any], secret: str) -> bool:
    """Verify a payload signature using a constant-time comparison."""

    supplied = payload.get("signature")
    if not isinstance(supplied, str):
        return False
    expected = sign_payload(payload, secret)
    return hmac.compare_digest(supplied, expected)


def create_access_token(subject: str, secret: str, lifetime_minutes: int = 30) -> str:
    """Create a short-lived HS256 JWT for an authenticated operator."""

    now = datetime.now(timezone.utc)
    claims = {
        "sub": subject,
        "iat": now,
        "exp": now + timedelta(minutes=lifetime_minutes),
    }
    return jwt.encode(claims, secret, algorithm="HS256")


def decode_access_token(token: str, secret: str) -> str:
    """Decode a JWT and return its subject, translating library errors cleanly."""

    try:
        claims = jwt.decode(token, secret, algorithms=["HS256"])
    except jwt.PyJWTError as exc:
        raise TokenError("The access token is invalid or expired.") from exc

    subject = claims.get("sub")
    if not isinstance(subject, str) or not subject:
        raise TokenError("The access token does not identify an operator.")
    return subject

