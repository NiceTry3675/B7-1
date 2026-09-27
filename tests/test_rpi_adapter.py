import json

import httpx
import pytest

from rpi_llm.config import RPiSettings
from rpi_llm.errors import InternalError, RuntimeUncertain
from rpi_llm.llama_adapter import LlamaAdapter
from rpi_llm.schemas import GenerationRequest


def settings(tmp_path):
    model = tmp_path / "gemma-3-1b.gguf"
    model.write_bytes(b"GGUF")
    return RPiSettings(
        _env_file=None,
        llm_service_token="s" * 32,
        llm_model_path=model,
        llm_context_tokens=128,
        llm_output_token_limit=32,
    )


def request():
    return GenerationRequest.model_validate(
        {
            "model": "local-advisor",
            "system_prompt": "한글로 답해요.",
            "messages": [
                {"role": "user", "content": "첫 질문"},
                {"role": "assistant", "content": "첫 답변"},
                {"role": "user", "content": "다음 질문"},
            ],
            "max_output_tokens": 32,
            "temperature": 0.7,
        }
    )


def runtime(settings, *, slots=None, answer="  두 번째 답변  "):
    calls = []

    def handler(req):
        calls.append(req)
        assert req.headers["Authorization"] == "Bearer " + settings.llm_service_token
        if req.url.path == "/health":
            return httpx.Response(200, json={"status": "ok"})
        if req.url.path == "/props":
            return httpx.Response(
                200,
                json={
                    "model_path": str(settings.llm_model_path),
                    "total_slots": 1,
                    "default_generation_settings": {"n_ctx": 128},
                },
            )
        if req.url.path == "/v1/models":
            return httpx.Response(200, json={"data": [{"id": "local-advisor"}]})
        if req.url.path == "/slots":
            return httpx.Response(
                200, json=slots if slots is not None else [{"is_processing": False, "id_task": -1}]
            )
        if req.url.path == "/v1/chat/completions/input_tokens":
            return httpx.Response(200, json={"input_tokens": 20})
        if req.url.path == "/v1/chat/completions":
            return httpx.Response(
                200,
                json={
                    "model": "local-advisor",
                    "choices": [{"message": {"content": answer}, "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 20, "completion_tokens": 6},
                },
            )
        raise AssertionError(req.url.path)

    adapter = LlamaAdapter(settings, httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    return adapter, calls


async def test_probe_count_and_generate_use_one_chat_serialization(tmp_path):
    config = settings(tmp_path)
    adapter, calls = runtime(config)
    try:
        assert await adapter.probe_ready()
        assert await adapter.is_idle()
        assert await adapter.count_tokens(request()) == 20
        result = await adapter.generate(request())
        assert result.answer == "두 번째 답변"
        assert result.usage.input_tokens == 20 and result.usage.output_tokens == 6
        payloads = [
            json.loads(call.content)
            for call in calls
            if call.url.path.startswith("/v1/chat/completions")
        ]
        assert payloads[0] == payloads[1]
        assert payloads[0]["cache_prompt"] is False
        assert [message["role"] for message in payloads[0]["messages"]] == [
            "system",
            "user",
            "assistant",
            "user",
        ]
    finally:
        await adapter.close()


async def test_probe_rejects_busy_slot_and_wrong_model(tmp_path):
    config = settings(tmp_path)
    adapter, _ = runtime(config, slots=[{"is_processing": True, "id_task": 1}])
    assert await adapter.probe_ready()
    assert not await adapter.is_idle()
    await adapter.close()
    config.llm_model_path.unlink()
    adapter, _ = runtime(config)
    assert not await adapter.probe_ready()
    await adapter.close()


@pytest.mark.parametrize("answer", ["", "   ", None, 42])
async def test_invalid_runtime_answer_is_safe_error(tmp_path, answer):
    adapter, _ = runtime(settings(tmp_path), answer=answer)
    with pytest.raises(InternalError, match="LLM_INTERNAL_ERROR"):
        await adapter.generate(request())
    await adapter.close()


async def test_transport_failure_is_unknown_only_after_inference_starts(tmp_path):
    def disconnected(req):
        raise httpx.ConnectError("private runtime path")

    config = settings(tmp_path)
    adapter = LlamaAdapter(config, httpx.AsyncClient(transport=httpx.MockTransport(disconnected)))
    with pytest.raises(InternalError, match="LLM_NOT_READY"):
        await adapter.count_tokens(request())
    with pytest.raises(RuntimeUncertain):
        await adapter.generate(request())
    await adapter.close()
