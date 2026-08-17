"""Local JWT verification. Auth issues tokens; the other seven never call it.

hospital_id and role are claims. A service that had to ask Auth on every request
would make Auth a single point of failure for the whole system.
"""

from dataclasses import dataclass

import jwt
from fastapi import Depends, HTTPException, Request

from .config import settings

# The one place the cookie name is written. auth sets it; all seven read it.
COOKIE_NAME = "medstock_token"


@dataclass(frozen=True)
class Principal:
    user_id: str
    hospital_id: str
    role: str


def credentials_token(request: Request) -> str | None:
    header = request.headers.get("authorization", "")
    # Cookie is how the browser authenticates (httpOnly, docs/services.md §2).
    # The Bearer header stays for curl and for local dev token minting — one
    # `or`, and both paths land on the same verification below.
    token = header[7:] if header.startswith("Bearer ") else request.cookies.get(COOKIE_NAME)
    return token or None


def current_principal(request: Request) -> Principal:
    token = credentials_token(request)
    if not token:
        raise HTTPException(status_code=401, detail="missing credentials")
    try:
        claims = jwt.decode(
            token,
            settings.jwt_public_key,
            algorithms=[settings.jwt_algorithm],
            audience="medstock",
        )
    except jwt.PyJWTError as exc:
        raise HTTPException(status_code=401, detail="invalid token") from exc
    return Principal(claims["sub"], claims["hospital_id"], claims["role"])


# `profile:approve` is granted to the pharmacist and to nobody else, admin
# included. It rules on a model's reading of a drug label — extracted risk
# factors that decide what colours a screen — and that is a clinical judgement
# (docs/prognosis-and-procurement.md §1.3, gate 3). An admin can grant itself
# every operational permission in this table and still must not be able to sign
# off clinical content; a gate a non-clinician can pass is not the gate the
# design claims. `profile:review` is the read side and is safe to share.
PERMS: dict[str, set[str]] = {
    "pharmacist": {
        "queue:read",
        "recommendation:approve",
        "inventory:read",
        "drug:search",
        "facility:read",
        "profile:review",
        "profile:approve",
    },
    "physician": {
        "alert:read",
        "inventory:read",
        "drug:search",
        "patient:read",
        "patient:write",
        "facility:read",
    },
    "director": {
        "dashboard:read",
        "audit:read",
        "inventory:read",
        "drug:search",
        "facility:read",
        # So the accept rate in docs/prognosis-and-procurement.md §5.4 is
        # readable by the person who has to decide whether to trust extraction.
        "profile:review",
    },
    "admin": {
        "mapping:approve",
        "formulary:write",
        "audit:read",
        "inventory:read",
        "drug:search",
        "patient:read",
        "patient:write",
        "facility:read",
        "profile:review",
    },
}


def require(permission: str):
    def dependency(principal: Principal = Depends(current_principal)) -> Principal:
        if permission not in PERMS.get(principal.role, set()):
            raise HTTPException(status_code=403, detail="forbidden")
        return principal

    return dependency
