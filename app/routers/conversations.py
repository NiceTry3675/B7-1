import asyncio
import logging
import time
from typing import Annotated

from fastapi import APIRouter, Path, Query, Request, Response

from app import schemas as s
from app.dependencies import DatabaseDep, SettingsDep, UserDep, db_call, threaded
from app.errors import COMMON_ERRORS, APIError, error_docs
from app.llm import build_context

logger = logging.getLogger("advisor")
router = APIRouter(prefix="/conversations", tags=["conversations"])

ConversationID = Annotated[int, Path(gt=0)]
Limit = Annotated[int, Query(ge=1, le=100)]
Offset = Annotated[int, Query(ge=0)]


@router.post(
    "",
    response_model=s.Conversation,
    status_code=201,
    responses=error_docs(*COMMON_ERRORS, "PERSONA_NOT_FOUND"),
)
async def new_conversation(body: s.NewConversation, user: UserDep, db: DatabaseDep):
    return await db_call(db.new_conversation, user["id"], body.persona_id)


@router.get("", response_model=s.ConversationPage, responses=error_docs(*COMMON_ERRORS))
async def conversations(user: UserDep, db: DatabaseDep, limit: Limit = 20, offset: Offset = 0):
    return await db_call(db.conversations, user["id"], limit, offset)


@router.get(
    "/{conversation_id}/turns",
    response_model=s.TurnPage,
    responses=error_docs(*COMMON_ERRORS, "CONVERSATION_NOT_FOUND"),
)
async def turns(
    conversation_id: ConversationID,
    user: UserDep,
    db: DatabaseDep,
    limit: Limit = 20,
    offset: Offset = 0,
):
    return await db_call(db.turns, user["id"], conversation_id, limit, offset)


@router.post(
    "/{conversation_id}/turns",
    response_model=s.Turn,
    status_code=201,
    responses={
        200: {"model": s.Turn, "description": "이미 완료된 동일 질문"},
        **error_docs(
            *COMMON_ERRORS,
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
    db: DatabaseDep,
    settings: SettingsDep,
):
    tid, cid, rid = str(body.client_message_id), conversation_id, request.state.request_id
    existing, system, history = await db_call(db.begin_turn, user["id"], cid, tid, body.content)
    if existing:
        response.status_code = 200
        return existing
    logger.info(
        "db_save_success request_id=%s user_id=%s conversation_id=%s turn_id=%s status=processing",
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
                build_context,
                settings,
                request.app.state.token_counter,
                system,
                history,
                body.content,
            )
            logger.info(
                "ai_call_start request_id=%s user_id=%s conversation_id=%s turn_id=%s",
                rid,
                user["id"],
                cid,
                tid,
            )
            answer = await request.app.state.llm.generate(system, messages, rid)
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
