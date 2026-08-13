"""Password hashing and token minting. The only module in the system that
touches the private key."""

from datetime import UTC, datetime, timedelta

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import Argon2Error, InvalidHashError
from medstock_shared import settings

# Library defaults, not hand-tuned parameters: they track the RFC 9106
# recommendation and are maintained by people who follow it. A time_cost
# picked by us is a number nobody would ever revisit.
_ph = PasswordHasher()

# Verified against when the email is unknown, so an unknown email and a wrong
# password take the same time and /login is not an account-existence oracle.
DUMMY_HASH = _ph.hash("not-a-real-password")

MAX_FAILED_ATTEMPTS = 10
LOCKOUT = timedelta(minutes=15)


def hash_password(password: str) -> str:
    return _ph.hash(password)


def verify_password(stored_hash: str, password: str) -> bool:
    try:
        _ph.verify(stored_hash, password)
        return True
    except (Argon2Error, InvalidHashError):
        # Argon2Error covers a wrong password; InvalidHashError (a ValueError,
        # not an Argon2Error) covers a stored hash we cannot parse. Both are a
        # failed login, never a 500 — a corrupt hash must not become an outage.
        return False


def needs_rehash(stored_hash: str) -> bool:
    """True when the library's defaults have moved since this hash was made.
    The only migration path a password store gets."""
    return _ph.check_needs_rehash(stored_hash)


def mint_token(user_id: str, hospital_id: str, role: str) -> tuple[str, datetime]:
    """The claim set is exactly what current_principal reads, plus iat/exp.
    Nothing else — an email in a token is a claim that goes stale."""
    now = datetime.now(UTC)
    expires_at = now + timedelta(hours=settings.jwt_ttl_hours)
    token = jwt.encode(
        {
            "sub": user_id,
            "hospital_id": hospital_id,
            "role": role,
            "aud": "medstock",
            "iat": now,
            "exp": expires_at,
        },
        settings.jwt_private_key,
        algorithm=settings.jwt_algorithm,
    )
    return token, expires_at
