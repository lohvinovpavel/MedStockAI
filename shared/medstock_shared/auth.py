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


def current_principal(request: Request) -> Principal:
    header = request.headers.get("authorization", "")
    # Cookie is how the browser authenticates (httpOnly, docs/services.md §2).
    # The Bearer header stays for curl and for local dev token minting — one
    # `or`, and both paths land on the same verification below.
    token = header[7:] if header.startswith("Bearer ") else request.cookies.get(COOKIE_NAME)
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


PERMS: dict[str, set[str]] = {
    "pharmacist": {"queue:read", "recommendation:approve", "inventory:read"},
    "physician": {"alert:read", "inventory:read"},
    "director": {"dashboard:read", "audit:read", "inventory:read"},
    "admin": {"mapping:approve", "formulary:write", "audit:read", "inventory:read"},
}


def require(permission: str):
    def dependency(principal: Principal = Depends(current_principal)) -> Principal:
        if permission not in PERMS.get(principal.role, set()):
            raise HTTPException(status_code=403, detail="forbidden")
        return principal

    return dependency
