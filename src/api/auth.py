from __future__ import annotations

import logging
from typing import Any

import firebase_admin
import firebase_admin.credentials
from firebase_admin import auth as fb_auth
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

logger = logging.getLogger(__name__)

_bearer_scheme = HTTPBearer(auto_error=False)
_initialized = False


def _ensure_firebase_initialized() -> None:
    global _initialized
    if _initialized:
        return
    if not firebase_admin._apps:
        import json
        from src.config import get_settings

        settings = get_settings()
        service_account_json = getattr(settings, "firebase_service_account_json", "")

        if service_account_json:
            # Parse JSON string
            service_account_info = json.loads(service_account_json)
            cred = firebase_admin.credentials.Certificate(service_account_info)
            firebase_admin.initialize_app(cred)
            logger.info("Firebase Admin initialized with service account JSON")
        else:
            # 回退到默认凭证 (适合 GCP 环境)
            firebase_admin.initialize_app()
            logger.info("Firebase Admin initialized with default credentials")
    _initialized = True


def verify_firebase_token(credentials: HTTPAuthorizationCredentials | None) -> dict[str, Any]:
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing authorization token",
        )
    _ensure_firebase_initialized()
    try:
        decoded = fb_auth.verify_id_token(credentials.credentials)
        return decoded
    except fb_auth.InvalidIdTokenError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Firebase ID token",
        )
    except fb_auth.ExpiredIdTokenError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Expired Firebase ID token",
        )
    except Exception:
        logger.exception("Firebase token verification failed")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not verify token",
        )


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
) -> dict[str, Any]:
    return verify_firebase_token(credentials)


async def require_admin(
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    uid = user.get("uid", "")
    from src.db.session import session_scope
    from sqlalchemy import text

    async with session_scope() as session:
        result = await session.execute(
            text("SELECT role FROM users WHERE uid = :uid"), {"uid": uid}
        )
        row = result.scalar_one_or_none()
    if row != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required",
        )
    return user
