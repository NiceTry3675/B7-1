import asyncio
import sqlite3
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import pytest

from app.errors import APIError
from app.main import create_app
from tests.conftest import account


def question(content="진로가 고민이에요", tid=None):
    return {"client_message_id": tid or str(uuid4()), "content": content}


async def test_auth_security(client, app, auth):
    response = await client.get("/api/v1/me", headers=auth)
    assert set(response.json()) == {"id", "email", "created_at"}
    response = await client.post(
        "/api/v1/auth/signup", json={"email": " STUDENT@EXAMPLE.COM ", "password": "long-password"}
    )
    assert response.status_code == 409
    bad = {"email": "student@example.com", "password": "Test-password-123"}
    response = await client.post("/api/v1/auth/login", json=bad)
    assert response.json()["error"]["code"] == "INVALID_CREDENTIALS"
    assert response.headers["WWW-Authenticate"] == "Bearer"
    bad["email"] = "absent@example.com"
    assert (await client.post("/api/v1/auth/login", json=bad)).json()["error"][
        "code"
    ] == "INVALID_CREDENTIALS"
    with app.state.db.connect() as conn:
        assert (
            conn.execute("SELECT password_hash FROM users").fetchone()[0].startswith("$argon2id$")
        )
        digest = conn.execute("SELECT token_hash FROM auth_sessions").fetchone()[0]
        assert auth["Authorization"].split()[1] != digest
    response = await client.post("/api/v1/auth/logout", headers=auth)
    assert response.status_code == 204 and response.content == b""
    assert (await client.get("/api/v1/me", headers=auth)).status_code == 401


async def test_session_expiry_and_independent_logout(client, app, auth):
    other = await client.post(
        "/api/v1/auth/login",
        json={"email": "student@example.com", "password": "  Test-password-123  "},
    )
    assert other.headers["Cache-Control"] == "no-store"
    other_auth = {"Authorization": "Bearer " + other.json()["access_token"]}
    await client.post("/api/v1/auth/logout", headers=auth)
    assert (await client.get("/api/v1/me", headers=other_auth)).status_code == 200
    with app.state.db.connect(write=True) as conn:
        conn.execute("UPDATE auth_sessions SET expires_at='2000-01-01T00:00:00Z'")
    assert (await client.get("/api/v1/me", headers=other_auth)).status_code == 401


@pytest.mark.parametrize("path", ["/me", "/personas", "/conversations", "/conversations/1/turns"])
async def test_auth_required(client, path):
    response = await client.get("/api/v1" + path)
    assert response.status_code == 401
    assert response.json()["turn_id"] is None
    assert response.json()["request_id"] == response.headers["X-Request-ID"]


async def test_ownership_and_no_prompt_exposure(client, auth, cid, fake_llm):
    other = await account(client, "other@example.com")
    for method in ("get", "post"):
        kwargs = {"json": question()} if method == "post" else {}
        response = await getattr(client, method)(
            f"/api/v1/conversations/{cid}/turns", headers=other, **kwargs
        )
        assert response.status_code == 404 and response.json()["turn_id"] is None
    assert not fake_llm.calls
    assert (await client.get("/api/v1/conversations", headers=other)).json()["items"] == []
    personas = (await client.get("/api/v1/personas", headers=auth)).json()["items"]
    assert all(set(p) == {"id", "name", "description"} for p in personas)


@pytest.mark.parametrize(
    "body",
    [
        question(""),
        question("   "),
        question("x" * 2001),
        {"content": "hi", "client_message_id": "not-uuid"},
        {**question(), "user_id": 1},
        {**question(), "content": 123},
    ],
)
async def test_invalid_turn(client, auth, cid, body, fake_llm):
    response = await client.post(f"/api/v1/conversations/{cid}/turns", headers=auth, json=body)
    assert response.status_code == 422
    assert response.json()["error"]["details"]
    assert not fake_llm.calls


async def test_validation_does_not_echo_secrets(client, auth):
    response = await client.post(
        "/api/v1/auth/signup", json={"email": "bad", "password": "SECRET", "unexpected": "SECRET"}
    )
    assert "SECRET" not in response.text
    for query in ("limit=0", "limit=101", "offset=-1", "limit=1.5"):
        assert (await client.get("/api/v1/conversations?" + query, headers=auth)).status_code == 422
    assert (await client.get("/api/v1/conversations/0/turns", headers=auth)).status_code == 422


async def test_turn_idempotency_context_and_pagination(client, auth, cid, fake_llm):
    path = f"/api/v1/conversations/{cid}/turns"
    body = question("  첫 질문  ")
    response = await client.post(path, headers=auth, json=body)
    assert response.status_code == 201
    assert response.json()["question"] == "첫 질문"
    assert "sequence" not in response.json()
    retry = await client.post(path, headers=auth, json={**body, "content": "첫 질문"})
    assert retry.status_code == 200 and retry.json() == response.json()
    assert len(fake_llm.calls) == 1
    assert response.headers["X-Request-ID"] != retry.headers["X-Request-ID"]
    conflict = await client.post(path, headers=auth, json={**body, "content": "다른 질문"})
    assert conflict.json()["error"]["code"] == "MESSAGE_ID_CONFLICT"
    for index in range(6):
        assert (
            await client.post(path, headers=auth, json=question(f"질문 {index}"))
        ).status_code == 201
    messages = fake_llm.calls[-1][1]
    assert len(messages) == 11 and messages[0]["content"] == "질문 0"
    assert [m["role"] for m in messages] == ["user", "assistant"] * 5 + ["user"]
    first = (await client.get(path + "?limit=3", headers=auth)).json()
    second = (await client.get(path + "?limit=3&offset=3", headers=auth)).json()
    assert first["has_more"] and second["items"][0]["question"] == "질문 2"
    assert not (await client.get(path + "?offset=7", headers=auth)).json()["items"]


async def test_conversation_boundaries_and_order(client, auth, cid, fake_llm):
    await client.post(f"/api/v1/conversations/{cid}/turns", headers=auth, json=question("PRIVATE"))
    new = (
        await client.post("/api/v1/conversations", headers=auth, json={"persona_id": "sejong"})
    ).json()
    await client.post(
        f"/api/v1/conversations/{new['id']}/turns", headers=auth, json=question("NEW")
    )
    assert fake_llm.calls[-1][1] == [{"role": "user", "content": "NEW"}]
    await client.post(f"/api/v1/conversations/{cid}/turns", headers=auth, json=question("OLDER"))
    page = (await client.get("/api/v1/conversations?limit=1", headers=auth)).json()
    assert page["items"][0]["id"] == new["id"] and page["has_more"]


async def test_concurrent_turns(client, auth, cid, fake_llm):
    fake_llm.gate = asyncio.Event()
    body = question()
    path = f"/api/v1/conversations/{cid}/turns"
    task = asyncio.create_task(client.post(path, headers=auth, json=body))
    try:
        for _ in range(100):
            if fake_llm.calls:
                break
            await asyncio.sleep(0.01)
        duplicate = await client.post(path, headers=auth, json=body)
        assert duplicate.json()["error"]["code"] == "MESSAGE_IN_PROGRESS"
        assert duplicate.headers["Retry-After"] == "2"
        other = await client.post(path, headers=auth, json=question())
        assert other.json()["error"]["code"] == "CONVERSATION_BUSY"
        pending = (await client.get(path, headers=auth)).json()["items"][0]
        assert pending["status"] == "processing"
        assert all(pending[k] is None for k in ("answer", "error_code", "completed_at"))
        assert len(fake_llm.calls) == 1
    finally:
        fake_llm.gate.set()
        await task


@pytest.mark.parametrize(
    "code,status",
    [
        ("AI_BUSY", 503),
        ("AI_NOT_READY", 503),
        ("AI_CONFIG_ERROR", 503),
        ("AI_TIMEOUT", 504),
        ("AI_UNAVAILABLE", 502),
        ("CONTEXT_TOO_LARGE", 422),
    ],
)
async def test_failed_turn_replay(client, auth, cid, fake_llm, code, status):
    fake_llm.error = APIError(code)
    path = f"/api/v1/conversations/{cid}/turns"
    body = question()
    for _ in range(2):
        response = await client.post(path, headers=auth, json=body)
        assert (
            response.status_code == status
            and response.json()["turn_id"] == body["client_message_id"]
        )
    assert len(fake_llm.calls) == 1
    history = (await client.get(path, headers=auth)).json()["items"]
    assert history[0]["status"] == "failed" and history[0]["answer"] is None
    fake_llm.error = None
    assert (await client.post(path, headers=auth, json=question("retry"))).status_code == 201
    assert fake_llm.calls[-1][1] == [{"role": "user", "content": "retry"}]


async def test_db_failure_prevents_generation_and_success(
    client, app, auth, cid, fake_llm, monkeypatch
):
    def fail(*args, **kwargs):
        raise sqlite3.OperationalError("sensitive SQL and secrets")

    path = f"/api/v1/conversations/{cid}/turns"
    with monkeypatch.context() as patch:
        patch.setattr(app.state.db, "begin_turn", fail)
        response = await client.post(path, headers=auth, json=question())
        assert response.json()["error"]["code"] == "DB_ERROR"
        assert not fake_llm.calls
        assert "sensitive" not in response.text
    monkeypatch.setattr(app.state.db, "finish_turn", fail)
    fake_llm.error = APIError("AI_BUSY")
    response = await client.post(path, headers=auth, json=question())
    assert response.status_code == 500 and response.json()["error"]["code"] == "DB_ERROR"


async def test_stale_recovery_and_late_response(app, client, auth, cid):
    db = app.state.db
    tid = str(uuid4())
    db.begin_turn(1, cid, tid, "interrupted")
    assert db.cleanup(210) == 0
    with db.connect(write=True) as conn:
        conn.execute(
            "UPDATE chat_turns SET created_at=?",
            ((datetime.now(UTC) - timedelta(seconds=211)).isoformat().replace("+00:00", "Z"),),
        )
    assert db.cleanup(210) == 1
    row = db.finish_turn(cid, tid, answer="late")
    assert row["status"] == "failed" and row["error_code"] == "REQUEST_INTERRUPTED"
    response = await client.post(
        f"/api/v1/conversations/{cid}/turns", headers=auth, json=question()
    )
    assert response.status_code == 201


async def test_restart_keeps_records(settings, app, client, auth, cid, fake_llm):
    await client.post(f"/api/v1/conversations/{cid}/turns", headers=auth, json=question())
    new_app = create_app(settings, llm=fake_llm, token_counter=lambda s, m: 1)
    async with new_app.router.lifespan_context(new_app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=new_app), base_url="http://test"
        ) as c:
            response = await c.get(f"/api/v1/conversations/{cid}/turns", headers=auth)
            assert response.json()["items"][0]["status"] == "completed"


async def test_health_and_openapi(client):
    assert (await client.get("/api/v1/health")).json() == {"status": "ok"}
    spec = (await client.get("/openapi.json")).json()
    assert sum(len(v) for v in spec["paths"].values()) == 10
    assert spec["components"]["securitySchemes"]["HTTPBearer"]["scheme"] == "bearer"
    post = spec["paths"]["/api/v1/conversations/{conversation_id}/turns"]["post"]
    assert "200" in post["responses"] and "201" in post["responses"]
    assert post["responses"]["422"]["content"]["application/json"]["schema"]["$ref"].endswith(
        "ErrorResponse"
    )


async def test_whole_turn_deadline(settings, fake_llm):
    config = settings.model_copy(update={"turn_timeout_seconds": 3.05})
    fake_llm.gate = asyncio.Event()
    application = create_app(config, llm=fake_llm, token_counter=lambda s, m: 1)
    async with application.router.lifespan_context(application):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=application), base_url="http://test"
        ) as client:
            headers = await account(client)
            response = await client.post(
                "/api/v1/conversations", headers=headers, json={"persona_id": "socrates"}
            )
            path = f"/api/v1/conversations/{response.json()['id']}/turns"
            response = await client.post(path, headers=headers, json=question())
            assert response.status_code == 504
            turn = (await client.get(path, headers=headers)).json()["items"][0]
            assert turn["status"] == "failed" and turn["error_code"] == "AI_TIMEOUT"
            assert (await client.get("/api/v1/health")).status_code == 200


async def test_successful_ai_but_failed_save_is_not_success(
    client, app, auth, cid, fake_llm, monkeypatch
):
    def fail(*args, **kwargs):
        raise sqlite3.OperationalError("disk full")

    monkeypatch.setattr(app.state.db, "finish_turn", fail)
    response = await client.post(
        f"/api/v1/conversations/{cid}/turns", headers=auth, json=question()
    )
    assert len(fake_llm.calls) == 1
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "DB_ERROR"


async def test_inactive_persona_and_database_constraint(client, app, auth, cid):
    with app.state.db.connect(write=True) as conn:
        conn.execute("UPDATE personas SET is_active=0 WHERE id='socrates'")
    response = await client.post(
        "/api/v1/conversations", headers=auth, json={"persona_id": "socrates"}
    )
    assert response.status_code == 404
    assert (await client.get(f"/api/v1/conversations/{cid}/turns", headers=auth)).status_code == 200
    app.state.db.begin_turn(1, cid, str(uuid4()), "one")
    with pytest.raises(sqlite3.IntegrityError), app.state.db.connect(write=True) as conn:
        conn.execute(
            "INSERT INTO chat_turns(conversation_id,id,sequence,status,question,created_at) "
            "VALUES(?,?,2,'processing','two','2026-01-01T00:00:00Z')",
            (cid, str(uuid4())),
        )
