from datetime import timedelta
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import func, select
from fastapi import APIRouter, HTTPException, Response, Request, Depends
from pydantic import BaseModel
import database

from auth import (
    create_access_token, 
    create_refresh_token,
    hash_password, 
    oauth2_scheme, 
    verify_access_token, 
    verify_refresh_token,
    verify_password,
)

from config import settings


router = APIRouter()

class UserCreate(BaseModel):
    username: str
    password: str

class UserLogin(BaseModel):
    username: str
    password: str

class Token(BaseModel):
    access_token: str
    token_type: str


@router.post("/register")
async def register(user: UserCreate, response: Response):
    """Register a new user."""

    if not user.username or not user.password:
        raise HTTPException(status_code=400, detail="Username and password required")

    if len(user.username) < 3:
        raise HTTPException(status_code=400, detail="Username must be at least 3 characters")

    if len(user.password) < 4:
        raise HTTPException(status_code=400, detail="Password must be at least 4 characters")

    # Hash password and create user
    password_hash = hash_password(user.password)
    success = database.create_user(user.username, password_hash)

    if not success:
        raise HTTPException(status_code=409, detail="Username already exists")

    # Auto-login after registration
    access_token = create_access_token(
        data={"sub": user.username},
    )

    refresh_token = create_refresh_token(
        data={"sub": user.username}
    )

    response.set_cookie(
        key="refresh_token",
        value=refresh_token,
        httponly=True,
        secure=False,  # set True in production (HTTPS)
        samesite="lax",
        path="/"
    )

    return Token(
        access_token=access_token,
        token_type="bearer"
    )

@router.post("/api/users/token", response_model=Token)
async def login(response:Response, form_data: OAuth2PasswordRequestForm = Depends()):
    username = form_data.username
    password = form_data.password
    if not username or not password:
        raise HTTPException(status_code=400, detail="Username and password required")

    db_user = database.get_user(username)

    if not db_user or not verify_password(password, db_user["password_hash"]):
        raise HTTPException(
            status_code=401, 
            detail="Invalid username or password",
            headers={"WWW-Authenticate": "Bearer"})
    
    access_token = create_access_token(
        data={"sub": username},
    )

    refresh_token = create_refresh_token(
        data={"sub": username}
    )

    response.set_cookie(
        key="refresh_token",
        value=refresh_token,
        httponly=True,
        secure=False,   # True in production (HTTPS)
        samesite="lax", # "none" in production
        path="/"
    )

    return Token(
        access_token=access_token, 
        token_type="bearer"
    )


def get_current_user(token: str = Depends(oauth2_scheme)):
    username = verify_access_token(token)

    if username is None:
        raise HTTPException(
            status_code=401,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return username

@router.get("/me")
async def get_me(username: str = Depends(get_current_user)):
    return {"username": username}


@router.post("/refresh")
async def refresh(request: Request, response: Response):
    refresh_token = request.cookies.get("refresh_token")

    if not refresh_token:
        raise HTTPException(status_code=401, detail="No refresh token")

    username = verify_refresh_token(refresh_token)

    if username is None:
        raise HTTPException(status_code=401, detail="Invalid refresh token")

    # Create new access token
    access_token = create_access_token({"sub": username})

    return {
        "access_token": access_token,
        "token_type": "bearer"
    }

@router.post("/logout")
async def logout(response: Response):
    response.delete_cookie(
        key="refresh_token",
        httponly=True,
        secure=False,  # True in production
        samesite="lax",
        path="/"
    )
    return {"message": "Logged out successfully"}