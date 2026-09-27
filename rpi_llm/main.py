import hmac
import logging
import re
from contextlib import ExitStack, asynccontextmanager
from uuid import uuid4

from fastapi import Depends, FastAPI, Header, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from rpi_llm.config import RPiSettings
from rpi_llm.cts import State
from rpi_llm.errors import InternalError
from rpi_llm.lease import RuntimeLease
from rpi_llm.llama_adapter import LlamaAdapter
from rpi_llm.queue import ComputeJobQueue
from rpi_llm.runner import TaskJobRunner
from rpi_llm.schemas import GenerationRequest, GenerationResponse, HealthResponse

logger = logging.getLogger("rpi_llm")
REQUEST_ID = re.compile(r"[A-Za-z0-9_-]{1,64}\Z")


def create_app(settings=None, adapter=None):
    settings = settings or RPiSettings()
    adapter = adapter or LlamaAdapter(settings)
    queue = ComputeJobQueue()
    runner = TaskJobRunner(settings, adapter, queue)

    @asynccontextmanager
    async def lifespan(app):
        with ExitStack() as leases:
            try:
                leases.enter_context(RuntimeLease(settings.llm_runtime_url))
                if await adapter.is_idle():
                    queue.model_ready()
                    logger.info("model_ready model=%s", settings.llm_model)
                yield
            finally:
                await adapter.close()

    app = FastAPI(title="Raspberry Pi internal LLM", lifespan=lifespan)
    app.state.queue = queue
    app.state.adapter = adapter

    @app.middleware("http")
    async def request_tracking(request: Request, call_next):
        supplied = request.headers.get("X-Request-ID", "")
        request.state.request_id = (
            supplied if REQUEST_ID.fullmatch(supplied) else "req_" + uuid4().hex
        )
        if request.url.path in {"/internal/v1/health", "/internal/v1/generate"}:
            token = request.headers.get("Authorization", "").removeprefix("Bearer ")
            if not token or not hmac.compare_digest(token, settings.llm_service_token):
                response = error_response(request, InternalError("LLM_UNAUTHORIZED"))
            else:
                response = await call_next(request)
        else:
            response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        return response

    def error_response(request, exc):
        headers = {}
        if exc.code == "LLM_BUSY":
            headers["Retry-After"] = "5"
        if exc.code == "LLM_UNAUTHORIZED":
            headers["WWW-Authenticate"] = "Bearer"
        return JSONResponse(
            status_code=exc.status,
            headers=headers,
            content={
                "error": {"code": exc.code, "message": exc.message, "details": []},
                "request_id": request.state.request_id,
            },
        )

    @app.exception_handler(InternalError)
    async def internal_error(request: Request, exc: InternalError):
        return error_response(request, exc)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request: Request, exc: RequestValidationError):
        return error_response(request, InternalError("LLM_INVALID_INPUT"))

    @app.exception_handler(Exception)
    async def unexpected_error(request: Request, exc: Exception):
        logger.error(
            "llm_inference_failure request_id=%s code=LLM_INTERNAL_ERROR",
            request.state.request_id,
        )
        return error_response(request, InternalError("LLM_INTERNAL_ERROR"))

    def authenticate(authorization: str | None = Header(default=None)):
        token = ""
        if authorization and authorization.startswith("Bearer "):
            token = authorization.removeprefix("Bearer ")
        if not token or not hmac.compare_digest(token, settings.llm_service_token):
            raise InternalError("LLM_UNAUTHORIZED")

    async def refresh_readiness():
        if queue.state == State.NOT_READY:
            if await adapter.is_idle():
                queue.model_ready()
        elif queue.state == State.READY:
            if not await adapter.is_idle():
                queue.model_lost()
        elif queue.state == State.QUARANTINED and queue.active_task is None:
            if await adapter.is_idle():
                queue.recover_idle()

    @app.get("/internal/v1/health", response_model=HealthResponse)
    async def health(request: Request, _: None = Depends(authenticate)):
        await refresh_readiness()
        if queue.state in {State.NOT_READY, State.QUARANTINED}:
            logger.info("model_not_ready request_id=%s", request.state.request_id)
            raise InternalError("LLM_NOT_READY")
        logger.info("model_ready request_id=%s", request.state.request_id)
        return HealthResponse(
            model=settings.llm_model,
            active_requests=queue.active_requests,
            max_concurrency=settings.llm_max_concurrency,
        )

    @app.post("/internal/v1/generate", response_model=GenerationResponse)
    async def generate(
        payload: GenerationRequest, request: Request, _: None = Depends(authenticate)
    ):
        if payload.model != settings.llm_model:
            raise InternalError("LLM_MODEL_NOT_FOUND")
        if payload.max_output_tokens > settings.llm_output_token_limit:
            raise InternalError("LLM_INVALID_INPUT")
        await refresh_readiness()
        logger.info(
            "llm_request_received request_id=%s model=%s",
            request.state.request_id,
            settings.llm_model,
        )
        try:
            return await runner.submit(payload, request.state.request_id)
        except InternalError as exc:
            if exc.code == "LLM_BUSY":
                logger.info("llm_busy request_id=%s", request.state.request_id)
            elif exc.code == "LLM_NOT_READY":
                logger.info("model_not_ready request_id=%s", request.state.request_id)
            raise

    return app
