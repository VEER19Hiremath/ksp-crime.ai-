"""Lightweight JWT-based auth: login issues a token carrying the user's role,
protected routes require it via `Depends(get_current_user)`, and specific
endpoints can further restrict by role via `Depends(require_role(...))`.

Not Zoho Catalyst — that needs an OAuth client created in the Catalyst console
(only the project ID was available, not client credentials). This is meant to
be swappable later: everything downstream only reads `role` off the JWT/current
user, so switching the issuer doesn't touch route-protection logic."""
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from core.config import get_settings
from core.db import run_read_only_query

ALGORITHM = "HS256"
TOKEN_TTL = timedelta(hours=12)
ROLES = ("Investigator", "SHO", "DSP", "Analyst", "Administrator")

_security = HTTPBearer(auto_error=False)


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, password_hash: str) -> bool:
    return bcrypt.checkpw(password.encode(), password_hash.encode())


def create_access_token(username: str, role: str, full_name: str) -> str:
    settings = get_settings()
    payload = {
        "sub": username,
        "role": role,
        "full_name": full_name,
        "exp": datetime.now(timezone.utc) + TOKEN_TTL,
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=ALGORITHM)


def authenticate_user(username: str, password: str) -> dict | None:
    rows = run_read_only_query(
        "SELECT username, password_hash, role, full_name FROM app_user WHERE username = %(username)s AND active",
        {"username": username},
    )
    if not rows:
        return None
    user = rows[0]
    if not verify_password(password, user["password_hash"]):
        return None
    return {"username": user["username"], "role": user["role"], "full_name": user["full_name"]}


def get_current_user(credentials: HTTPAuthorizationCredentials | None = Depends(_security)) -> dict:
    if credentials is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated")
    settings = get_settings()
    try:
        payload = jwt.decode(credentials.credentials, settings.jwt_secret, algorithms=[ALGORITHM])
    except jwt.PyJWTError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired token")
    return {"username": payload["sub"], "role": payload["role"], "full_name": payload.get("full_name", "")}


def require_role(*allowed_roles: str):
    def _dependency(user: dict = Depends(get_current_user)) -> dict:
        if user["role"] not in allowed_roles:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                f"This action requires one of: {', '.join(allowed_roles)}",
            )
        return user

    return _dependency
