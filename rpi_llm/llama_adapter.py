import asyncio
from dataclasses import dataclass

import httpx

from rpi_llm.errors import InternalError, RuntimeUncertain
from rpi_llm.schemas import Usage


@dataclass(frozen=True)
class RuntimeGeneration:
    answer: str
    finish_reason: str
    usage: Usage | None


class LlamaAdapter:
    def __init__(self, settings, client=None):
        self.settings = settings
        self.client = client or httpx.AsyncClient(
            timeout=settings.llm_runtime_timeout_seconds,
            follow_redirects=False,
            trust_env=False,
        )

    async def close(self):
        await self.client.aclose()

    async def _request(self, method, path, *, json=None):
        async with asyncio.timeout(self.settings.llm_runtime_timeout_seconds):
            return await self.client.request(
                method,
                self.settings.llm_runtime_url + path,
                headers={"Authorization": f"Bearer {self.settings.llm_service_token}"},
                json=json,
            )

    async def probe_ready(self):
        if not self.settings.llm_model_path.is_file():
            return False
        try:
            async with asyncio.timeout(self.settings.llm_runtime_probe_timeout_seconds):
                health = await self._request("GET", "/health")
                props = await self._request("GET", "/props")
                models = await self._request("GET", "/v1/models")
                if any(response.status_code != 200 for response in (health, props, models)):
                    return False
                info = props.json()
                return (
                    health.json().get("status") == "ok"
                    and info.get("model_path") == str(self.settings.llm_model_path)
                    and info.get("total_slots") == 1
                    and info.get("default_generation_settings", {}).get("n_ctx")
                    == self.settings.llm_context_tokens
                    and [item.get("id") for item in models.json().get("data", [])]
                    == [self.settings.llm_model]
                )
        except (TimeoutError, httpx.HTTPError, ValueError, TypeError, AttributeError):
            return False

    async def is_idle(self):
        try:
            async with asyncio.timeout(self.settings.llm_runtime_probe_timeout_seconds):
                if not await self.probe_ready():
                    return False
                response = await self._request("GET", "/slots")
                slots = response.json()
                return (
                    response.status_code == 200
                    and isinstance(slots, list)
                    and len(slots) == 1
                    and slots[0].get("is_processing") is False
                )
        except (TimeoutError, httpx.HTTPError, ValueError, TypeError, AttributeError):
            return False

    def _payload(self, request):
        return {
            "model": self.settings.llm_model,
            "messages": [
                {"role": "system", "content": request.system_prompt},
                *[message.model_dump() for message in request.messages],
            ],
            "max_tokens": request.max_output_tokens,
            "temperature": request.temperature,
            "stream": False,
            "cache_prompt": False,
        }

    async def count_tokens(self, request):
        try:
            response = await self._request(
                "POST", "/v1/chat/completions/input_tokens", json=self._payload(request)
            )
            if response.status_code != 200:
                raise InternalError("LLM_NOT_READY")
            value = response.json().get("input_tokens")
            if type(value) is not int or value < 1:
                raise InternalError("LLM_INTERNAL_ERROR")
            return value
        except (TimeoutError, httpx.HTTPError, ValueError, TypeError, AttributeError) as exc:
            raise InternalError("LLM_NOT_READY") from exc

    async def generate(self, request):
        try:
            response = await self._request(
                "POST", "/v1/chat/completions", json=self._payload(request)
            )
        except (TimeoutError, httpx.HTTPError) as exc:
            raise RuntimeUncertain() from exc
        if response.status_code == 503:
            raise InternalError("LLM_BUSY")
        if response.status_code != 200:
            raise RuntimeUncertain()
        try:
            data = response.json()
            choices = data["choices"]
            if data.get("model") != self.settings.llm_model or len(choices) != 1:
                raise ValueError("unexpected runtime response")
            choice = choices[0]
            answer = choice["message"]["content"]
            reason = choice["finish_reason"]
            if (
                not isinstance(answer, str)
                or not answer.strip()
                or reason not in {"stop", "length"}
            ):
                raise ValueError("invalid answer")
            raw_usage = data.get("usage")
            usage = None
            if raw_usage is not None:
                usage = Usage.model_validate(
                    {
                        "input_tokens": raw_usage["prompt_tokens"],
                        "output_tokens": raw_usage["completion_tokens"],
                    }
                )
            return RuntimeGeneration(answer.strip(), reason, usage)
        except (ValueError, TypeError, KeyError, IndexError, AttributeError) as exc:
            raise InternalError("LLM_INTERNAL_ERROR") from exc
