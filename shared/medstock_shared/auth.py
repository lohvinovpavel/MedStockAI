"""Local JWT verification. Auth issues tokens; the other seven never call it.

hospital_id and role are claims. A service that had to ask Auth on every request
would make Auth a single point of failure for the whole system.
"""

from dataclasses import dataclass

import jwt
from fastapi import Depends, HTTPException, Request

from .config import settings


@dataclass(frozen=True)
class Principal:
    user_id: str
    hospital_id: str
    role: str


def current_principal(request: Request) -> Principal:
    header = request.headers.get("authorization", "")
    if not header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="missing bearer token")
    try:
        claims = jwt.decode(
            header[7:],
            settings.jwt_public_key,
            algorithms=[settings.jwt_algorithm],
            audience="medstock",
        )
    except jwt.PyJWTError as exc:
        raise HTTPException(status_code=401, detail="invalid token") from exc
    return Principal(claims["sub"], claims["hospital_id"], claims["role"])


PERMS: dict[str, set[str]] = {
    "pharmacist": {"queue:read", "recommendation:approve", "inventory:read", "drug:search"},
    "physician": {"alert:read", "inventory:read", "drug:search"},
    "director": {"dashboard:read", "audit:read", "inventory:read", "drug:search"},
    "admin": {"mapping:approve", "formulary:write", "audit:read", "inventory:read", "drug:search"},
}


def require(permission: str):
    def dependency(principal: Principal = Depends(current_principal)) -> Principal:
        if permission not in PERMS.get(principal.role, set()):
            raise HTTPException(status_code=403, detail="forbidden")
        return principal

    return dependency
