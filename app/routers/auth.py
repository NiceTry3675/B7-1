import secrets

from fastapi import APIRouter, Response

from app import schemas as s
from app.dependencies import (
    DUMMY_HASH,
    DatabaseDep,
    SettingsDep,
    UserDep,
    db_call,
    password_hasher,
    threaded,
    token_digest,
    verify_password,
)
from app.errors import COMMON_ERRORS, APIError, error_docs

router = APIRouter(tags=["auth"])


@router.post(
    "/auth/signup",
    response_model=s.User,
    status_code=201,
    responses=error_docs("EMAIL_ALREADY_EXISTS", "INVALID_INPUT", "DB_ERROR"),
)
async def signup(body: s.Signup, db: DatabaseDep):
    hashed = await threaded(password_hasher.hash, body.password)
    return await db_call(db.signup, str(body.email), hashed)


@router.post(
    "/auth/login",
    response_model=s.Login,
    responses=error_docs("INVALID_CREDENTIALS", "INVALID_INPUT", "DB_ERROR"),
)
async def login(body: s.Credentials, db: DatabaseDep, settings: SettingsDep):
    user = await db_call(db.find_user, str(body.email))
    valid = await threaded(
        verify_password, user["password_hash"] if user else DUMMY_HASH, body.password
    )
    if not user or not valid:
        raise APIError("INVALID_CREDENTIALS")
    token = secrets.token_urlsafe(32)
    await db_call(db.new_session, user["id"], token_digest(token), settings.session_ttl_seconds)
    return {"access_token": token, "expires_in": settings.session_ttl_seconds, "user": user}


@router.post("/auth/logout", status_code=204, responses=error_docs(*COMMON_ERRORS))
async def logout(user: UserDep, db: DatabaseDep):
    await db_call(db.logout, user["session_id"])
    return Response(status_code=204)


@router.get("/me", response_model=s.User, responses=error_docs(*COMMON_ERRORS))
async def me(user: UserDep):
    return user
