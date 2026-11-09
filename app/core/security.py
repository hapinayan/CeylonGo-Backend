import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

import jwt
from dotenv import load_dotenv
from pwdlib import PasswordHash

load_dotenv()

password_hash = PasswordHash.recommended()


def hash_password(password: str) -> str:
    return password_hash.hash(password)


def verify_password(password: str, hashed_password: str) -> bool:
    return password_hash.verify(password, hashed_password)


def get_jwt_secret_key() -> str:
    secret_key = os.getenv("JWT_SECRET_KEY")
    if not secret_key:
        raise RuntimeError(
            "Configuration error: JWT_SECRET_KEY environment variable is not set."
        )
    return secret_key


def get_jwt_algorithm() -> str:
    return os.getenv("JWT_ALGORITHM", "HS256")


def get_jwt_access_token_expire_minutes() -> int:
    expire_minutes_raw = os.getenv("JWT_ACCESS_TOKEN_EXPIRE_MINUTES", "30")
    try:
        return int(expire_minutes_raw)
    except ValueError:
        raise RuntimeError(
            "Configuration error: JWT_ACCESS_TOKEN_EXPIRE_MINUTES must be an integer."
        )


def create_access_token(
    data: Dict[str, Any], expires_delta: Optional[timedelta] = None
) -> str:
    to_encode = data.copy()
    if expires_delta is not None:
        expire = datetime.now(timezone.utc) + expires_delta
    else:
        expire = datetime.now(timezone.utc) + timedelta(
            minutes=get_jwt_access_token_expire_minutes()
        )

    to_encode.update({"exp": expire})
    secret_key = get_jwt_secret_key()
    algorithm = get_jwt_algorithm()

    return jwt.encode(to_encode, secret_key, algorithm=algorithm)


def decode_access_token(token: str) -> Dict[str, Any]:
    secret_key = get_jwt_secret_key()
    algorithm = get_jwt_algorithm()
    return jwt.decode(token, secret_key, algorithms=[algorithm])
