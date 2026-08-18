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
#
# `certification:explore` is separated from `certificate:read` for a reason that
# is not secrecy: exploring an unknown NDC triggers a live openFDA fetch
# (COMP-2), and openFDA's budget is 1 000 requests a day **per IP, shared across
# every feed** (docs/services.md §7). Letting anyone who can see stock spend
# that budget lets one curious user starve the nightly CronJobs. Reading an
# already-computed certificate costs a SQL query, so it is shared widely;
# spending the budget is not.
PERMS: dict[str, set[str]] = {
    "pharmacist": {
        "queue:read",
        "recommendation:approve",
        "inventory:read",
        "drug:search",
        "facility:read",
        "profile:review",
        "profile:approve",
        "profile:assess",
        "profile:explain",
        "certificate:read",
        "certification:explore",
        "forecast:read",
        # Triggering a run is a write with a distinct name on purpose — but it
        # is held by the same people who read forecasts: the pharmacist at the
        # keyboard is the one who notices the data has outrun the run.
        "forecast:run",
        # PAGE_ROLES and the rbac matrix give the pharmacist the audit page;
        # without this grant GET /audit 403s a role that can already open it.
        "audit:read",
    },
    "physician": {
        "alert:read",
        "inventory:read",
        "drug:search",
        "patient:read",
        "patient:write",
        "facility:read",
        # Prescribing is the whole reason /cart-check exists, and a physician
        # who cannot ask why a line was flagged is handed a verdict without its
        # basis — which is what §6's CDS exclusion turns on.
        "profile:assess",
        "profile:explain",
        "certificate:read",
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
        "certificate:read",
        "forecast:read",
        "forecast:run",
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
        "certificate:read",
        "certification:explore",
    },
}


def require(permission: str):
    def dependency(principal: Principal = Depends(current_principal)) -> Principal:
        if permission not in PERMS.get(principal.role, set()):
            raise HTTPException(status_code=403, detail="forbidden")
        return principal

    return dependency
