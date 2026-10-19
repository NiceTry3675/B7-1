from uuid import uuid4

import httpx

from app.config import Settings
from app.llm import LLMClient
from app.main import create_app as create_service
from rpi_llm.config import RPiSettings
from rpi_llm.main import create_app as create_rpi
from tests.conftest import account
from tests.test_rpi_api import FakeRuntime


async def test_service_to_rpi_adapter_persists_success_and_failure(tmp_path):
    token = "s" * 32
    rpi_settings = RPiSettings(
        _env_file=None,
        llm_service_token=token,
        llm_model_path=tmp_path / "gemma.gguf",
    )
    runtime = FakeRuntime()
    rpi = create_rpi(rpi_settings, runtime)
    backend_settings = Settings(
        _env_file=None,
        database_url=f"sqlite:///{tmp_path / 'test.db'}",
        llm_base_url="http://rpi.test",
        llm_service_token=token,
    )
    llm = LLMClient(
        backend_settings,
        httpx.AsyncClient(transport=httpx.ASGITransport(app=rpi), base_url="http://rpi.test"),
    )
    service = create_service(backend_settings, llm=llm, token_counter=lambda system, messages: 10)
    async with rpi.router.lifespan_context(rpi):
        async with service.router.lifespan_context(service):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=service), base_url="http://service.test"
            ) as client:
                auth = await account(client)
                conversation = await client.post(
                    "/api/v1/conversations", headers=auth, json={"persona_id": "socrates"}
                )
                assert conversation.status_code == 201
                path = f"/api/v1/conversations/{conversation.json()['id']}/turns"
                runtime.uncertain = True
                failed = await client.post(
                    path,
                    headers=auth,
                    json={"client_message_id": str(uuid4()), "content": "첫 질문"},
                )
                assert failed.status_code == 502
                assert failed.json()["error"]["code"] == "AI_UNAVAILABLE"
                history = (await client.get(path, headers=auth)).json()["items"]
                assert history[0]["status"] == "failed" and history[0]["answer"] is None
                runtime.uncertain = False
                runtime.idle = True
                success = await client.post(
                    path,
                    headers=auth,
                    json={"client_message_id": str(uuid4()), "content": "다시 질문"},
                )
                assert success.status_code == 201
                assert success.json()["answer"] == "답변"
                history = (await client.get(path, headers=auth)).json()["items"]
                assert [row["status"] for row in history] == ["failed", "completed"]
                assert runtime.calls == 2
