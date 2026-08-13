"""Response models are the leak guard: a column added to app_user later
cannot reach the browser unless it is named here."""

from datetime import datetime

from pydantic import BaseModel


class LoginRequest(BaseModel):
    email: str
    password: str


class LoginResponse(BaseModel):
    """No token field. The token goes in the httpOnly cookie only."""

    user_id: str
    hospital_id: str
    role: str
    expires_at: datetime


class MeResponse(BaseModel):
    user_id: str
    email: str
    full_name: str | None
    role: str
    hospital_id: str
    hospital_name: str
