from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import text

from src.api.auth import get_current_user
from src.db.session import session_scope

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["Auth"])


class UserProfile(BaseModel):
    uid: str
    email: str = ""
    display_name: str = ""
    photo_url: str = ""
    role: str = "user"


class RegisterRequest(BaseModel):
    uid: str


@router.get(
    "/me",
    response_model=UserProfile,
    summary="Get current user profile",
    description="Returns the authenticated user's profile from the users table.",
)
async def get_me(user: dict[str, Any] = Depends(get_current_user)) -> UserProfile:
    uid = user.get("uid", "")
    if not uid:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")

    async with session_scope() as session:
        result = await session.execute(
            text("SELECT uid, email, display_name, photo_url, role FROM users WHERE uid = :uid"),
            {"uid": uid},
        )
        row = result.mappings().first()

    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    return UserProfile(
        uid=str(row["uid"]),
        email=str(row["email"] or ""),
        display_name=str(row["display_name"] or ""),
        photo_url=str(row["photo_url"] or ""),
        role=str(row["role"] or "user"),
    )


@router.post(
    "/register",
    response_model=UserProfile,
    status_code=status.HTTP_201_CREATED,
    summary="Register a new user",
    description="Creates a new user record from the Firebase token. Default role is 'user'.",
)
async def register(
    body: RegisterRequest,
    user: dict[str, Any] = Depends(get_current_user),
) -> UserProfile:
    uid = user.get("uid", "")
    email = user.get("email", "")
    display_name = user.get("name", user.get("firebase", {}).get("sign_in_provider", ""))
    photo_url = user.get("picture", "")

    if not uid:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")

    async with session_scope() as session:
        existing = await session.execute(
            text("SELECT uid FROM users WHERE uid = :uid"), {"uid": uid}
        )
        if existing.scalar_one_or_none() is not None:
            result = await session.execute(
                text("SELECT uid, email, display_name, photo_url, role FROM users WHERE uid = :uid"),
                {"uid": uid},
            )
            row = result.mappings().first()
            return UserProfile(
                uid=str(row["uid"]),
                email=str(row["email"] or ""),
                display_name=str(row["display_name"] or ""),
                photo_url=str(row["photo_url"] or ""),
                role=str(row["role"] or "user"),
            )

        await session.execute(
            text(
                "INSERT INTO users (uid, email, display_name, photo_url, role) "
                "VALUES (:uid, :email, :display_name, :photo_url, 'user')"
            ),
            {"uid": uid, "email": email, "display_name": display_name, "photo_url": photo_url},
        )
        await session.commit()

    return UserProfile(uid=uid, email=email, display_name=display_name, photo_url=photo_url, role="user")
