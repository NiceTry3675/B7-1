import asyncio
from types import SimpleNamespace

import httpx
import pytest

from rpi_llm.config import RPiSettings
from rpi_llm.errors import RuntimeUncertain
from rpi_llm.lease import RuntimeLease
from rpi_llm.main import create_app


class FakeRuntime:
    def __init__(self):
        self.ready = True
        self.idle = False
        self.uncertain = False
        self.gate = None
        self.started = asyncio.Event()
        self.calls = 0

    async def probe_ready(self):
        return self.ready

    async def is_idle(self):
        return self.idle

    async def count_tokens(self, payload):
        return 10

    async def generate(self, payload):
        self.calls += 1
        self.started.set()
        if self.gate:
            await self.gate.wait()
        if self.uncertain:
            raise RuntimeUncertain()
        return SimpleNamespace(answer="답변", finish_reason="stop", usage=None)

    async def close(self):
        pass


def payload():
    return {
        "model": "local-advisor",
        "system_prompt": "답하세요.",
        "messages": [{"role": "user", "content": "질문"}],
        "max_output_tokens": 20,
        "temperature": 0.7,
    }


@pytest.fixture
async def api(tmp_path):
    config = RPiSettings(
        _env_file=None,
        llm_service_token="s" * 32,
        llm_model_path=tmp_path / "gemma.gguf",
        llm_context_tokens=100,
        llm_output_token_limit=20,
    )
    runtime = FakeRuntime()
    app = create_app(config, runtime)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://rpi.test"
        ) as client:
            yield client, runtime, app


async def test_both_internal_routes_require_token_even_for_bad_body(api):
    client, runtime, _ = api
    for path in ("/internal/v1/health", "/internal/v1/generate"):
        response = await client.request(
            "POST" if path.endswith("generate") else "GET", path, content="not-json"
        )
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "LLM_UNAUTHORIZED"
        assert response.headers["WWW-Authenticate"] == "Bearer"
        assert response.headers["X-Request-ID"] == response.json()["request_id"]
    assert runtime.calls == 0


async def test_generate_preserves_request_id_and_rejects_invalid_input(api):
    client, runtime, _ = api
    headers = {"Authorization": "Bearer " + "s" * 32, "X-Request-ID": "req_test"}
    health = await client.get("/internal/v1/health", headers=headers)
    assert health.json() == {
        "status": "ready",
        "model": "local-advisor",
        "active_requests": 0,
        "max_concurrency": 1,
    }
    response = await client.post("/internal/v1/generate", json=payload(), headers=headers)
    assert response.status_code == 200
    assert response.headers["X-Request-ID"] == "req_test"
    assert response.json()["answer"] == "답변" and response.json()["usage"] is None
    wrong = {**payload(), "model": "wrong"}
    assert (await client.post("/internal/v1/generate", json=wrong, headers=headers)).json()[
        "error"
    ]["code"] == "LLM_MODEL_NOT_FOUND"
    bad = {**payload(), "messages": [{"role": "assistant", "content": "wrong"}]}
    assert (await client.post("/internal/v1/generate", json=bad, headers=headers)).json()["error"][
        "code"
    ] == "LLM_INVALID_INPUT"
    assert runtime.calls == 1


async def test_other_conversation_is_busy_with_zero_queue(api):
    client, runtime, _ = api
    headers = {"Authorization": "Bearer " + "s" * 32}
    runtime.gate = asyncio.Event()
    first = asyncio.create_task(
        client.post("/internal/v1/generate", json=payload(), headers=headers)
    )
    try:
        await asyncio.wait_for(runtime.started.wait(), 1)
        busy = await client.post("/internal/v1/generate", json=payload(), headers=headers)
        assert busy.status_code == 503 and busy.json()["error"]["code"] == "LLM_BUSY"
        assert busy.headers["Retry-After"] == "5"
        health = await client.get("/internal/v1/health", headers=headers)
        assert health.json()["active_requests"] == 1
        assert runtime.calls == 1
    finally:
        runtime.gate.set()
        assert (await first).status_code == 200


async def test_uncertain_runtime_stays_unready_until_idle_verified(api):
    client, runtime, app = api
    headers = {"Authorization": "Bearer " + "s" * 32}
    runtime.uncertain = True
    response = await client.post("/internal/v1/generate", json=payload(), headers=headers)
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "LLM_INTERNAL_ERROR"
    assert app.state.queue.state == "quarantined"
    assert (await client.get("/internal/v1/health", headers=headers)).status_code == 503
    runtime.idle = True
    assert (await client.get("/internal/v1/health", headers=headers)).status_code == 200


def test_single_process_lease_rejects_second_wrapper():
    with RuntimeLease("http://127.0.0.1:8080"):
        with pytest.raises(RuntimeError, match="already owns"):
            with RuntimeLease("http://127.0.0.1:8080"):
                pass
    with RuntimeLease("http://127.0.0.1:8080"):
        pass
