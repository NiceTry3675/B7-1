from uuid import uuid4

import pytest

from app.db import Database
from app.errors import APIError
from service_cts.backend import (
    AUTH_CTS,
    TURN_CTS,
    AuthAction,
    AuthState,
    TurnAction,
    TurnState,
    project_auth,
    project_turn,
)
from service_cts.core import NoEdgePolicy


def database(tmp_path):
    db = Database(tmp_path / "cts.db")
    db.initialize()
    user = db.signup("cts@example.com", "test-hash")
    return db, user["id"]


def session_row(db, token_hash):
    with db.connect() as conn:
        return conn.execute(
            "SELECT * FROM auth_sessions WHERE token_hash=?", (token_hash,)
        ).fetchone()


def turn_row(db, cid, tid):
    with db.connect() as conn:
        return conn.execute(
            "SELECT * FROM chat_turns WHERE conversation_id=? AND id=?", (cid, tid)
        ).fetchone()


def verify(machine, before, action, after):
    assert machine.step(before, action) == after


def test_auth_issue_use_revoke_and_expiry(tmp_path):
    db, uid = database(tmp_path)
    assert project_auth(None) == AuthState.ABSENT
    with pytest.raises(APIError, match="AUTH_REQUIRED"):
        db.authenticate("missing-hash")
    assert AUTH_CTS.no_edge(AuthState.ABSENT, AuthAction.USE) == NoEdgePolicy.AUTH_REQUIRED
    db.new_session(uid, "active-hash", 3600)
    row = session_row(db, "active-hash")
    state = project_auth(row)
    verify(AUTH_CTS, AuthState.ABSENT, AuthAction.ISSUE, state)
    auth = db.authenticate("active-hash")
    verify(AUTH_CTS, state, AuthAction.USE, project_auth(session_row(db, "active-hash")))
    db.logout(auth["session_id"])
    state = project_auth(session_row(db, "active-hash"))
    verify(AUTH_CTS, AuthState.ACTIVE, AuthAction.LOGOUT, state)
    with pytest.raises(APIError, match="AUTH_REQUIRED"):
        db.authenticate("active-hash")
    assert AUTH_CTS.no_edge(state, AuthAction.USE) == NoEdgePolicy.AUTH_REQUIRED

    db.new_session(uid, "expired-hash", 3600)
    with db.connect(write=True) as conn:
        conn.execute(
            "UPDATE auth_sessions SET expires_at='2000-01-01T00:00:00Z' WHERE token_hash=?",
            ("expired-hash",),
        )
    state = project_auth(session_row(db, "expired-hash"))
    verify(AUTH_CTS, AuthState.ACTIVE, AuthAction.CLOCK_EXPIRE, state)
    with pytest.raises(APIError, match="AUTH_REQUIRED"):
        db.authenticate("expired-hash")


def test_turn_create_duplicate_complete_fail_and_late_result(tmp_path):
    db, uid = database(tmp_path)
    cid = db.new_conversation(uid, "socrates")["id"]
    tid = str(uuid4())
    assert project_turn(None) == TurnState.ABSENT
    existing, system, history = db.begin_turn(uid, cid, tid, "질문")
    assert existing is None and system and history == []
    state = project_turn(turn_row(db, cid, tid))
    verify(TURN_CTS, TurnState.ABSENT, TurnAction.BEGIN_NEW, state)
    with pytest.raises(APIError, match="MESSAGE_IN_PROGRESS"):
        db.begin_turn(uid, cid, tid, "질문")
    assert TURN_CTS.no_edge(state, TurnAction.REPLAY_SAME) == NoEdgePolicy.IN_PROGRESS
    with pytest.raises(APIError, match="MESSAGE_ID_CONFLICT"):
        db.begin_turn(uid, cid, tid, "다른 질문")
    with pytest.raises(APIError, match="CONVERSATION_BUSY"):
        db.begin_turn(uid, cid, str(uuid4()), "다른 ID")
    assert TURN_CTS.no_edge(TurnState.ABSENT, TurnAction.OTHER_BUSY) == NoEdgePolicy.BUSY
    row = db.finish_turn(cid, tid, answer="답변")
    state = project_turn(row)
    verify(TURN_CTS, TurnState.PROCESSING, TurnAction.AI_SUCCESS, state)
    replay, _, _ = db.begin_turn(uid, cid, tid, "질문")
    verify(TURN_CTS, state, TurnAction.REPLAY_SAME, project_turn(replay))
    with pytest.raises(APIError, match="MESSAGE_ID_CONFLICT"):
        db.begin_turn(uid, cid, tid, "완료 후 다른 질문")
    assert TURN_CTS.no_edge(state, TurnAction.REPLAY_CHANGED) == NoEdgePolicy.CONFLICT
    late = db.finish_turn(cid, tid, error="AI_TIMEOUT")
    verify(TURN_CTS, state, TurnAction.LATE_RESULT, project_turn(late))

    failed_id = str(uuid4())
    db.begin_turn(uid, cid, failed_id, "실패 질문")
    failed = db.finish_turn(cid, failed_id, error="AI_BUSY")
    verify(TURN_CTS, TurnState.PROCESSING, TurnAction.AI_FAILURE, project_turn(failed))
    with pytest.raises(APIError, match="AI_BUSY"):
        db.begin_turn(uid, cid, failed_id, "실패 질문")
    verify(TURN_CTS, TurnState.FAILED, TurnAction.REPLAY_SAME, project_turn(failed))
    with pytest.raises(APIError, match="MESSAGE_ID_CONFLICT"):
        db.begin_turn(uid, cid, failed_id, "실패 후 다른 질문")
    late = db.finish_turn(cid, failed_id, answer="늦은 답")
    verify(TURN_CTS, TurnState.FAILED, TurnAction.LATE_RESULT, project_turn(late))
    assert late["answer"] is None

    stale_id = str(uuid4())
    db.begin_turn(uid, cid, stale_id, "중단 질문")
    with db.connect(write=True) as conn:
        conn.execute(
            "UPDATE chat_turns SET created_at='2000-01-01T00:00:00Z' WHERE id=?", (stale_id,)
        )
    assert db.cleanup(210) == 1
    cleaned = turn_row(db, cid, stale_id)
    verify(TURN_CTS, TurnState.PROCESSING, TurnAction.CLEANUP_STALE, project_turn(cleaned))
    late = db.finish_turn(cid, stale_id, answer="늦은 답")
    assert project_turn(late) == TurnState.FAILED and late["error_code"] == "REQUEST_INTERRUPTED"
