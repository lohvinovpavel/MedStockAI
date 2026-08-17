import os
import uuid
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as pkg_version

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from medstock_shared import COOKIE_NAME, Principal, credentials_token, current_principal, engine, settings
from medstock_shared.db import SessionLocal
from medstock_shared.models import AppUser, Hospital, Membership
from sqlalchemy import select, text

from .schemas import LoginRequest, LoginResponse, MeResponse
from .security import (
    DUMMY_HASH,
    LOCKOUT,
    MAX_FAILED_ATTEMPTS,
    hash_password,
    mint_token,
    needs_rehash,
    verify_password,
)

app = FastAPI(title="auth")

INVALID = HTTPException(status_code=401, detail="invalid credentials")
"""One error for unknown email, wrong password, inactive, and locked. Telling
them apart turns /login into an account-existence oracle."""


@app.get("/healthz")
def healthz() -> dict[str, str]:
    """Liveness: the process is up. No dependencies checked on purpose —
    a database blip must not get every pod restarted."""
    return {"status": "ok"}


@app.get("/readyz")
def readyz() -> dict[str, str]:
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))
    return {"status": "ready"}


@app.get("/version")
def version() -> dict[str, str]:
    """GIT_SHA is baked in at image build time (Dockerfile) — unset outside
    a built container, e.g. running locally from source. semver comes from
    the installed medstock-auth package (pyproject.toml), not the image."""
    try:
        semver = pkg_version("medstock-auth")
    except PackageNotFoundError:
        semver = "unknown"
    return {"service": "auth", "version": os.environ.get("GIT_SHA", "unknown"), "semver": semver}


@app.post("/login", response_model=LoginResponse)
def login(body: LoginRequest, response: Response) -> LoginResponse:
    """Uses SessionLocal directly, not session_scope: this runs before there
    is a hospital_id to set, and the identity tables carry no RLS policies.
    docs/auth-spec.md §1 is the only place that exception is granted.
    """
    now = datetime.now(UTC)

    with SessionLocal() as s:
        row = s.execute(
            select(AppUser, Membership)
            .join(Membership, Membership.user_id == AppUser.id)
            .where(AppUser.email == body.email.strip())
        ).one_or_none()

        if row is None:
            verify_password(DUMMY_HASH, body.password)
            raise INVALID

        user, membership = row

        if not user.is_active or (user.locked_until is not None and user.locked_until > now):
            verify_password(DUMMY_HASH, body.password)
            raise INVALID

        if not verify_password(user.password_hash, body.password):
            user.failed_attempts += 1
            if user.failed_attempts >= MAX_FAILED_ATTEMPTS:
                user.locked_until = now + LOCKOUT
                user.failed_attempts = 0
            s.commit()
            raise INVALID

        user.failed_attempts = 0
        user.locked_until = None
        if needs_rehash(user.password_hash):
            user.password_hash = hash_password(body.password)
        s.commit()

        # Read out inside the session; the response is built after it closes.
        user_id, hospital_id, role = str(user.id), str(membership.hospital_id), membership.role

    token, expires_at = mint_token(user_id, hospital_id, role)
    _set_session_cookie(response, token)
    return LoginResponse(
        user_id=user_id, hospital_id=hospital_id, role=role, expires_at=expires_at
    )


@app.post("/logout", status_code=204)
def logout(response: Response) -> None:
    """Takes no token on purpose — logging out with an expired one must still
    clear the cookie. With no revocation list this clears the browser's copy
    and nothing more; docs/auth-spec.md §2 says so out loud."""
    response.delete_cookie(COOKIE_NAME, path="/")


def _set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        COOKIE_NAME,
        token,
        max_age=settings.jwt_ttl_hours * 3600,
        httponly=True,   # not readable by any script on the page
        secure=True,     # Chrome exempts http://localhost, so dev still works
        samesite="lax",
        path="/",
    )


@app.get("/me", response_model=MeResponse)
def me(
    request: Request,
    response: Response,
    principal: Principal = Depends(current_principal),
) -> MeResponse:
    """Reads live rather than echoing the claims, so a deactivated user stops
    working on the next request instead of at token expiry."""
    # Re-assert Path=/ on every /me. A cookie that landed on /api/auth
    # (browser default when Path is dropped in a proxy) authenticates here
    # but is not sent to /api/analogue — Analogues search then 401s
    # "missing credentials" while the physician session still looks valid.
    token = credentials_token(request)
    if token:
        _set_session_cookie(response, token)
    try:
        user_id = uuid.UUID(principal.user_id)
    except ValueError as exc:
        raise INVALID from exc

    with SessionLocal() as s:
        row = s.execute(
            select(AppUser, Membership, Hospital)
            .join(Membership, Membership.user_id == AppUser.id)
            .join(Hospital, Hospital.id == Membership.hospital_id)
            .where(AppUser.id == user_id, AppUser.is_active.is_(True))
        ).one_or_none()

        if row is None:
            raise INVALID

        user, membership, hospital = row
        return MeResponse(
            user_id=str(user.id),
            email=user.email,
            full_name=user.full_name,
            role=membership.role,
            hospital_id=str(hospital.id),
            hospital_name=hospital.name,
        )
