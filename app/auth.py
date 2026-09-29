import hashlib
import secrets
from typing import Optional

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.config import PASSWORD_SALT, SESSION_COOKIE
from app.database import get_db
from app.models import User


def hash_password(password: str) -> str:
    return hashlib.sha256((PASSWORD_SALT + password).encode("utf-8")).hexdigest()


def verify_password(password: str, password_hash: str) -> bool:
    return secrets.compare_digest(hash_password(password), password_hash)


def new_session_token() -> str:
    return secrets.token_hex(16)


async def get_current_user(
    request: Request, db: Session = Depends(get_db)
) -> Optional[User]:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return None
    return db.query(User).filter(User.session_token == token).first()


def require_role(*roles: str):
    async def dependency(
        current_user: Optional[User] = Depends(get_current_user),
    ) -> User:
        if current_user is None or current_user.role not in roles:
            raise HTTPException(status_code=403, detail="forbidden")
        return current_user

    return dependency
