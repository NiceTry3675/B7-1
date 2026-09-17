import hashlib
import secrets
import sqlite3
from functools import partial
from typing import Annotated

import anyio
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config import Settings
from app.db import Database
from app.errors import APIError

bearer = HTTPBearer(auto_error=False)
password_hasher = PasswordHasher()
DUMMY_HASH = password_hasher.hash(secrets.token_urlsafe(32))


async def threaded(func, *args, **kwargs):
    return await anyio.to_thread.run_sync(partial(func, *args, **kwargs))


async def db_call(func, *args, **kwargs):
    try:
        return await threaded(func, *args, **kwargs)
    except sqlite3.Error as exc:
        raise APIError("DB_ERROR") from exc


def token_digest(token):
    return hashlib.sha256(token.encode()).hexdigest()


def verify_password(stored, password):
    try:
        return password_hasher.verify(stored, password)
    except (VerificationError, InvalidHashError):
        return False


async def get_database(request: Request) -> Database:
    return request.app.state.db


async def get_settings(request: Request) -> Settings:
    return request.app.state.settings


DatabaseDep = Annotated[Database, Depends(get_database)]
SettingsDep = Annotated[Settings, Depends(get_settings)]


async def current_user(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    db: DatabaseDep,
):
    if credentials is None:
        raise APIError("AUTH_REQUIRED")
    user = await db_call(db.authenticate, token_digest(credentials.credentials))
    request.state.user_id = user["id"]
    return user


UserDep = Annotated[dict, Depends(current_user)]
