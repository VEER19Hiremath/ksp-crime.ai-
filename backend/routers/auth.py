from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from core.auth import authenticate_user, create_access_token, get_current_user

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginRequest(BaseModel):
    username: str
    password: str


class LoginResponse(BaseModel):
    access_token: str
    role: str
    full_name: str
    username: str


@router.post("/login", response_model=LoginResponse)
def login(req: LoginRequest) -> LoginResponse:
    user = authenticate_user(req.username.strip(), req.password)
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid username or password")
    token = create_access_token(user["username"], user["role"], user["full_name"])
    return LoginResponse(
        access_token=token,
        role=user["role"],
        full_name=user["full_name"],
        username=user["username"],
    )


@router.get("/me")
def me(user: dict = Depends(get_current_user)) -> dict:
    return user
