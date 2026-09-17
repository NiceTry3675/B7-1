import asyncio
import hashlib
import logging
import secrets
import sqlite3
import time
from contextlib import asynccontextmanager, suppress
from functools import partial
from typing import Annotated
from uuid import uuid4

import anyio
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from fastapi import Depends, FastAPI, Path, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from starlette.exceptions import HTTPException

from app import schemas as s
from app.config import Settings
from app.db import Database
from app.errors import APIError, error_docs
from app.llm import ChatTokenCounter, LLMClient, build_context

logger = logging.getLogger("advisor")
bearer = HTTPBearer(auto_error=False)
password_hasher = PasswordHasher()
DUMMY_HASH = password_hasher.hash(secrets.token_urlsafe(32))
COMMON = ("AUTH_REQUIRED", "INVALID_INPUT", "DB_ERROR", "INTERNAL_ERROR")


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


async def current_user(
    request: Request, credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)]
):
    if credentials is None:
        raise APIError("AUTH_REQUIRED")
    user = await db_call(request.app.state.db.authenticate, token_digest(credentials.credentials))
    request.state.user_id = user["id"]
    return user


UserDep = Annotated[dict, Depends(current_user)]
ConversationID = Annotated[int, Path(gt=0)]
Limit = Annotated[int, Query(ge=1, le=100)]
Offset = Annotated[int, Query(ge=0)]


def create_app(settings=None, *, llm=None, token_counter=None):
    settings = settings or Settings()
    db = Database(settings.database_path)

    async def cleanup():
        while True:
            await asyncio.sleep(10)
            try:
                count = await db_call(db.cleanup, settings.turn_stale_seconds)
                if count:
                    logger.info("stale_turns_recovered count=%s", count)
            except APIError:
                logger.error("db_save_failure event=stale_cleanup code=DB_ERROR")

    @asynccontextmanager
    async def lifespan(app):
        logging.basicConfig(level=settings.log_level.upper(), format="%(levelname)s %(message)s")
        # HTTP client info logs include internal URLs; use the safe application events instead.
        logging.getLogger("httpx").setLevel(logging.WARNING)
        logging.getLogger("httpcore").setLevel(logging.WARNING)
        await db_call(db.initialize)
        await db_call(db.cleanup, settings.turn_stale_seconds)
        app.state.llm = llm or LLMClient(settings)
        app.state.token_counter = token_counter or await threaded(
            ChatTokenCounter, settings.llm_tokenizer_path
        )
        task = asyncio.create_task(cleanup())
        try:
            yield
        finally:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
            await app.state.llm.close()

    app = FastAPI(
        title="위인 챗봇 API",
        version="0.3.0",
        lifespan=lifespan,
        description="API_SPEC.md의 서비스 API. 라즈베리파이 내부 API는 별도 서비스입니다.",
    )
    app.state.db = db
    app.state.settings = settings

    def error_response(request, exc):
        request_id = request.state.request_id
        if exc.code == "DB_ERROR":
            logger.error("db_save_failure request_id=%s code=DB_ERROR", request_id)
        return JSONResponse(
            status_code=exc.status,
            headers=exc.headers,
            content={
                "error": {"code": exc.code, "message": exc.message, "details": exc.details},
                "request_id": request_id,
                "turn_id": exc.turn_id,
            },
        )

    @app.middleware("http")
    async def request_metadata(request, call_next):
        request.state.request_id = "req_" + uuid4().hex
        request.state.started = time.monotonic()
        # Path only: never log query strings, credentials, request bodies, or remote responses.
        logger.info(
            "request_received request_id=%s path=%s", request.state.request_id, request.url.path
        )
        try:
            response = await call_next(request)
        except Exception:
            logger.error(
                "request_failure request_id=%s code=INTERNAL_ERROR", request.state.request_id
            )
            response = error_response(request, APIError("INTERNAL_ERROR"))
        response.headers["X-Request-ID"] = request.state.request_id
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @app.exception_handler(APIError)
    async def api_error(request, exc):
        return error_response(request, exc)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        # Pydantic's input/ctx may include passwords; return only safe, static descriptions.
        details = [
            {
                "field": ".".join(str(v) for v in e["loc"][1:]) or "body",
                "message": "형식, 필수 항목 또는 허용 길이를 확인해 주세요.",
            }
            for e in exc.errors()
        ]
        return error_response(request, APIError("INVALID_INPUT", details=details))

    @app.exception_handler(HTTPException)
    async def http_error(request, exc):
        code = "AUTH_REQUIRED" if exc.status_code in {401, 403} else "INVALID_INPUT"
        error = APIError(code)
        if exc.status_code in {404, 405}:
            error.status = exc.status_code
            error.message = "요청한 API 경로 또는 메서드를 확인해 주세요."
        return error_response(request, error)

    @app.post(
        "/api/v1/auth/signup",
        response_model=s.User,
        status_code=201,
        responses=error_docs("EMAIL_ALREADY_EXISTS", "INVALID_INPUT", "DB_ERROR"),
    )
    async def signup(body: s.Signup):
        hashed = await threaded(password_hasher.hash, body.password)
        return await db_call(db.signup, str(body.email), hashed)

    @app.post(
        "/api/v1/auth/login",
        response_model=s.Login,
        responses=error_docs("INVALID_CREDENTIALS", "INVALID_INPUT", "DB_ERROR"),
    )
    async def login(body: s.Credentials):
        user = await db_call(db.find_user, str(body.email))
        valid = await threaded(
            verify_password, user["password_hash"] if user else DUMMY_HASH, body.password
        )
        if not user or not valid:
            raise APIError("INVALID_CREDENTIALS")
        token = secrets.token_urlsafe(32)
        await db_call(db.new_session, user["id"], token_digest(token), settings.session_ttl_seconds)
        return {"access_token": token, "expires_in": settings.session_ttl_seconds, "user": user}

    @app.post("/api/v1/auth/logout", status_code=204, responses=error_docs(*COMMON))
    async def logout(user: UserDep):
        await db_call(db.logout, user["session_id"])
        return Response(status_code=204)

    @app.get("/api/v1/me", response_model=s.User, responses=error_docs(*COMMON))
    async def me(user: UserDep):
        return user

    @app.get("/api/v1/personas", response_model=s.Personas, responses=error_docs(*COMMON))
    async def personas(user: UserDep):
        return {"items": await db_call(db.personas)}

    @app.post(
        "/api/v1/conversations",
        response_model=s.Conversation,
        status_code=201,
        responses=error_docs(*COMMON, "PERSONA_NOT_FOUND"),
    )
    async def new_conversation(body: s.NewConversation, user: UserDep):
        return await db_call(db.new_conversation, user["id"], body.persona_id)

    @app.get(
        "/api/v1/conversations", response_model=s.ConversationPage, responses=error_docs(*COMMON)
    )
    async def conversations(user: UserDep, limit: Limit = 20, offset: Offset = 0):
        return await db_call(db.conversations, user["id"], limit, offset)

    @app.get(
        "/api/v1/conversations/{conversation_id}/turns",
        response_model=s.TurnPage,
        responses=error_docs(*COMMON, "CONVERSATION_NOT_FOUND"),
    )
    async def turns(
        conversation_id: ConversationID, user: UserDep, limit: Limit = 20, offset: Offset = 0
    ):
        return await db_call(db.turns, user["id"], conversation_id, limit, offset)

    @app.post(
        "/api/v1/conversations/{conversation_id}/turns",
        response_model=s.Turn,
        status_code=201,
        responses={
            200: {"model": s.Turn, "description": "이미 완료된 동일 질문"},
            **error_docs(
                *COMMON,
                "CONVERSATION_NOT_FOUND",
                "CONVERSATION_BUSY",
                "MESSAGE_IN_PROGRESS",
                "MESSAGE_ID_CONFLICT",
                "CONTEXT_TOO_LARGE",
                "AI_UNAVAILABLE",
                "AI_BUSY",
                "AI_NOT_READY",
                "AI_CONFIG_ERROR",
                "AI_TIMEOUT",
                "REQUEST_INTERRUPTED",
            ),
        },
    )
    async def new_turn(
        conversation_id: ConversationID,
        body: s.NewTurn,
        user: UserDep,
        request: Request,
        response: Response,
    ):
        tid, cid, rid = str(body.client_message_id), conversation_id, request.state.request_id
        existing, system, history = await db_call(db.begin_turn, user["id"], cid, tid, body.content)
        if existing:
            response.status_code = 200
            return existing
        logger.info(
            "db_save_success request_id=%s user_id=%s conversation_id=%s turn_id=%s "
            "status=processing",
            rid,
            user["id"],
            cid,
            tid,
        )
        failure = None
        answer = None
        started = time.monotonic()
        # Reserve 3 seconds for the final transaction and error serialization.
        remaining = settings.turn_timeout_seconds - (started - request.state.started) - 3
        try:
            async with asyncio.timeout(max(0, remaining)):
                messages = await threaded(
                    build_context, settings, app.state.token_counter, system, history, body.content
                )
                logger.info(
                    "ai_call_start request_id=%s user_id=%s conversation_id=%s turn_id=%s",
                    rid,
                    user["id"],
                    cid,
                    tid,
                )
                answer = await app.state.llm.generate(system, messages, rid)
                logger.info(
                    "ai_call_success request_id=%s latency_ms=%s",
                    rid,
                    int((time.monotonic() - started) * 1000),
                )
        except TimeoutError:
            failure = APIError("AI_TIMEOUT", turn_id=tid)
        except APIError as exc:
            failure = exc
            failure.turn_id = tid
        except Exception:
            failure = APIError("INTERNAL_ERROR", turn_id=tid)
        if failure:
            logger.warning(
                "ai_call_failure request_id=%s code=%s latency_ms=%s",
                rid,
                failure.code,
                int((time.monotonic() - started) * 1000),
            )
        try:
            row = await db_call(
                db.finish_turn,
                cid,
                tid,
                answer=answer if not failure else None,
                error=failure.code if failure else None,
            )
        except APIError as exc:
            exc.turn_id = tid
            raise
        logger.info(
            "db_save_success request_id=%s user_id=%s conversation_id=%s turn_id=%s status=%s",
            rid,
            user["id"],
            cid,
            tid,
            row["status"],
        )
        if row["status"] == "failed":
            raise APIError(row["error_code"], turn_id=tid)
        return row

    @app.get("/api/v1/health", response_model=s.Health)
    async def health():
        return {"status": "ok"}

    return app


app = create_app()
