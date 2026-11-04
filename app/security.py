from datetime import datetime, timedelta, timezone
from typing import Any

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from mysql.connector import MySQLConnection
from pwdlib import PasswordHash

from app import config
from app.db import get_db

password_hasher = PasswordHash.recommended()
bearer_scheme = HTTPBearer(auto_error=False)


def hash_password(password: str) -> str:
    return password_hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return password_hasher.verify(password, password_hash)
    except Exception:
        return False


def create_access_token(user_id: int, role: str) -> str:
    if not config.JWT_SECRET_KEY or config.JWT_SECRET_KEY == "replace_with_a_long_random_secret_key":
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Chưa cấu hình JWT_SECRET_KEY trong file .env.",
        )
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=config.JWT_EXPIRE_MINUTES)
    payload = {"sub": str(user_id), "role": role, "exp": expires_at}
    return jwt.encode(payload, config.JWT_SECRET_KEY, algorithm=config.JWT_ALGORITHM)


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    connection: MySQLConnection = Depends(get_db),
) -> dict[str, Any]:
    unauthorized = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Vui lòng đăng nhập để tiếp tục.",
        headers={"WWW-Authenticate": "Bearer"},
    )
    if credentials is None or not config.JWT_SECRET_KEY:
        raise unauthorized

    try:
        payload = jwt.decode(
            credentials.credentials,
            config.JWT_SECRET_KEY,
            algorithms=[config.JWT_ALGORITHM],
        )
        user_id = int(payload.get("sub", ""))
    except (jwt.InvalidTokenError, TypeError, ValueError):
        raise unauthorized

    cursor = connection.cursor(dictionary=True)
    try:
        cursor.execute(
            """SELECT id, full_name, email, role, is_active
               FROM users WHERE id = %s LIMIT 1""",
            (user_id,),
        )
        user = cursor.fetchone()
    finally:
        cursor.close()

    if not user or not user["is_active"]:
        raise unauthorized
    return user


def require_roles(*allowed_roles: str):
    def dependency(user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
        if user["role"] not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Bạn không có quyền thực hiện chức năng này.",
            )
        return user

    return dependency
