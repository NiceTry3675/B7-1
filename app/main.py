import asyncio
import logging
import time
from contextlib import asynccontextmanager, suppress
from uuid import uuid4

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

from app.config import Settings
from app.db import Database
from app.dependencies import db_call, threaded
from app.errors import APIError
from app.llm import ChatTokenCounter, LLMClient
from app.routers import auth, conversations, health, personas

logger = logging.getLogger("advisor")


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

    for router in (auth.router, personas.router, conversations.router, health.router):
        app.include_router(router, prefix="/api/v1")

    return app


app = create_app()
