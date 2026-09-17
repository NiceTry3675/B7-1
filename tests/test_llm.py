import asyncio
import json

import httpx
import pytest

from app.errors import APIError
from app.llm import ChatTokenCounter, LLMClient, build_context


def configured(settings):
    return settings.model_copy(
        update={
            "llm_base_url": "http://llm.test",
            "llm_service_token": "test-service-token-" + "x" * 32,
        }
    )


def result(**updates):
    return {
        "model": "local-advisor",
        "answer": "답변",
        "finish_reason": "stop",
        "usage": None,
        "latency_ms": 12,
        **updates,
    }


async def test_internal_request_contract(settings):
    async def handler(request):
        assert request.url.path == "/internal/v1/generate"
        assert request.headers["Authorization"] == "Bearer " + config.llm_service_token
        assert request.headers["X-Request-ID"] == "req_test"
        assert json.loads(request.content) == {
            "model": "local-advisor",
            "system_prompt": "system",
            "messages": [{"role": "user", "content": "hello"}],
            "max_output_tokens": 256,
            "temperature": 0.7,
        }
        return httpx.Response(200, json=result(finish_reason="length"))

    config = configured(settings)
    llm = LLMClient(config, httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    assert (
        await llm.generate("system", [{"role": "user", "content": "hello"}], "req_test") == "답변"
    )
    await llm.close()


@pytest.mark.parametrize(
    "status,code,expected",
    [
        (401, "LLM_UNAUTHORIZED", "AI_CONFIG_ERROR"),
        (422, "LLM_INVALID_INPUT", "AI_CONFIG_ERROR"),
        (404, "LLM_MODEL_NOT_FOUND", "AI_CONFIG_ERROR"),
        (422, "LLM_CONTEXT_TOO_LARGE", "CONTEXT_TOO_LARGE"),
        (503, "LLM_BUSY", "AI_BUSY"),
        (503, "LLM_NOT_READY", "AI_NOT_READY"),
        (504, "LLM_TIMEOUT", "AI_TIMEOUT"),
        (500, "LLM_INTERNAL_ERROR", "AI_UNAVAILABLE"),
        (502, "secret-internal-detail", "AI_UNAVAILABLE"),
        (2000, "LLM_BUSY", "AI_UNAVAILABLE"),
    ],
)
async def test_error_mapping(settings, status, code, expected):
    count = 0

    async def handler(request):
        nonlocal count
        count += 1
        return httpx.Response(status, json={"error": {"code": code, "message": "PRIVATE"}})

    llm = LLMClient(configured(settings), httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    with pytest.raises(APIError) as caught:
        await llm.generate("s", [], "req_test")
    assert caught.value.code == expected and "PRIVATE" not in str(caught.value)
    assert count == 1
    await llm.close()


@pytest.mark.parametrize(
    "data",
    [
        result(answer="  "),
        result(answer=23),
        result(model="other"),
        result(finish_reason="error"),
        result(latency_ms=-1),
        result(usage={"input_tokens": "3", "output_tokens": 3}),
        {"answer": "text"},
        [],
        None,
    ],
)
async def test_invalid_response(settings, data):
    llm = LLMClient(
        configured(settings),
        httpx.AsyncClient(
            transport=httpx.MockTransport(lambda request: httpx.Response(200, json=data))
        ),
    )
    with pytest.raises(APIError, match="AI_UNAVAILABLE"):
        await llm.generate("s", [], "req_test")
    await llm.close()


async def test_total_http_timeout_and_network_failure(settings):
    async def slow(request):
        await asyncio.sleep(0.2)
        return httpx.Response(200, json=result())

    config = configured(settings).model_copy(update={"llm_timeout_seconds": 0.01})
    llm = LLMClient(config, httpx.AsyncClient(transport=httpx.MockTransport(slow)))
    with pytest.raises(APIError, match="AI_TIMEOUT"):
        await llm.generate("s", [], "req_test")
    await llm.close()

    def disconnected(request):
        raise httpx.ConnectError("secret host")

    llm = LLMClient(config, httpx.AsyncClient(transport=httpx.MockTransport(disconnected)))
    with pytest.raises(APIError, match="AI_UNAVAILABLE"):
        await llm.generate("s", [], "req_test")
    await llm.close()


async def test_missing_configuration(settings):
    llm = LLMClient(settings)
    with pytest.raises(APIError, match="AI_CONFIG_ERROR"):
        await llm.health("req_test")
    await llm.close()
    with pytest.raises(APIError, match="AI_CONFIG_ERROR"):
        ChatTokenCounter("")("system", [])


def test_context_pair_trimming(settings):
    settings = settings.model_copy(update={"llm_context_tokens": 290})
    history = [{"question": str(n), "answer": "answer"} for n in range(8)]
    messages = build_context(settings, lambda s, m: 10 * len(m), "system", history, "current")
    assert messages == [
        {"role": "user", "content": "7"},
        {"role": "assistant", "content": "answer"},
        {"role": "user", "content": "current"},
    ]
    with pytest.raises(APIError, match="CONTEXT_TOO_LARGE"):
        build_context(settings, lambda s, m: 35, "system", history, "current")


def test_setting_timeout_validation(settings):
    values = settings.model_dump()
    values["llm_timeout_seconds"] = 150
    with pytest.raises(ValueError):
        type(settings)(_env_file=None, **values)
