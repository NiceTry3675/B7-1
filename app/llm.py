import asyncio
from typing import Literal
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.errors import APIError


class Usage(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)


class Generation(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    model: str
    answer: str = Field(min_length=1)
    finish_reason: Literal["stop", "length"]
    usage: Usage | None
    latency_ms: int = Field(ge=0)


INTERNAL_ERRORS = {
    (401, "LLM_UNAUTHORIZED"): "AI_CONFIG_ERROR",
    (422, "LLM_INVALID_INPUT"): "AI_CONFIG_ERROR",
    (404, "LLM_MODEL_NOT_FOUND"): "AI_CONFIG_ERROR",
    (422, "LLM_CONTEXT_TOO_LARGE"): "CONTEXT_TOO_LARGE",
    (503, "LLM_BUSY"): "AI_BUSY",
    (503, "LLM_NOT_READY"): "AI_NOT_READY",
    (504, "LLM_TIMEOUT"): "AI_TIMEOUT",
}


class ChatTokenCounter:
    """Only tokenizer/template loading; never loads model weights or performs inference."""

    def __init__(self, path):
        self.tokenizer = None
        if path:
            try:
                from transformers import AutoTokenizer

                self.tokenizer = AutoTokenizer.from_pretrained(
                    path, local_files_only=True, trust_remote_code=False
                )
            except Exception:
                # Misconfigured local files must not reveal paths or prevent account access.
                self.tokenizer = None

    def __call__(self, system, messages):
        if self.tokenizer is None:
            raise APIError("AI_CONFIG_ERROR")
        try:
            tokens = self.tokenizer.apply_chat_template(
                [{"role": "system", "content": system}, *messages],
                tokenize=True,
                add_generation_prompt=True,
            )
            return len(tokens)
        except Exception as exc:
            raise APIError("AI_CONFIG_ERROR") from exc


def build_context(settings, count_tokens, system, history, question):
    messages = []
    for turn in history[-5:]:
        messages.extend(
            [
                {"role": "user", "content": turn["question"]},
                {"role": "assistant", "content": turn["answer"]},
            ]
        )
    messages.append({"role": "user", "content": question})
    budget = settings.llm_context_tokens - settings.llm_max_output_tokens
    while count_tokens(system, messages) > budget:
        if len(messages) == 1:
            raise APIError("CONTEXT_TOO_LARGE")
        del messages[:2]
    return messages


class LLMClient:
    def __init__(self, settings, client=None):
        self.settings = settings
        self.client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(
                settings.llm_timeout_seconds, connect=settings.llm_connect_timeout_seconds
            ),
            follow_redirects=False,
            trust_env=False,
        )

    def check_configuration(self):
        config = self.settings
        try:
            url = urlsplit(config.llm_base_url)
            valid = (
                url.scheme in {"http", "https"}
                and url.hostname
                and not url.username
                and not url.password
                and not url.query
                and not url.fragment
                and url.path in {"", "/"}
            )
        except ValueError:
            valid = False
        token = config.llm_service_token
        if not valid or len(token.encode()) < 32 or any(c.isspace() for c in token):
            raise APIError("AI_CONFIG_ERROR")

    async def request(self, method, path, request_id, payload=None):
        self.check_configuration()
        try:
            async with asyncio.timeout(self.settings.llm_timeout_seconds):
                response = await self.client.request(
                    method,
                    self.settings.llm_base_url.rstrip("/") + path,
                    headers={
                        "Authorization": f"Bearer {self.settings.llm_service_token}",
                        "X-Request-ID": request_id,
                    },
                    json=payload,
                )
            if response.status_code != 200:
                try:
                    code = response.json()["error"]["code"]
                    if not isinstance(code, str):
                        code = ""
                except (ValueError, KeyError, TypeError):
                    code = ""
                raise APIError(INTERNAL_ERRORS.get((response.status_code, code), "AI_UNAVAILABLE"))
            return response.json()
        except (TimeoutError, httpx.TimeoutException) as exc:
            raise APIError("AI_TIMEOUT") from exc
        except (httpx.HTTPError, ValueError) as exc:
            raise APIError("AI_UNAVAILABLE") from exc

    async def generate(self, system, messages, request_id):
        data = await self.request(
            "POST",
            "/internal/v1/generate",
            request_id,
            {
                "model": self.settings.llm_model,
                "system_prompt": system,
                "messages": messages,
                "max_output_tokens": self.settings.llm_max_output_tokens,
                "temperature": 0.7,
            },
        )
        try:
            result = Generation.model_validate(data)
            if result.model != self.settings.llm_model or not result.answer.strip():
                raise ValueError("Invalid generation")
            return result.answer.strip()
        except (ValidationError, ValueError) as exc:
            raise APIError("AI_UNAVAILABLE") from exc

    async def health(self, request_id):
        data = await self.request("GET", "/internal/v1/health", request_id)
        if (
            not isinstance(data, dict)
            or data.get("status") != "ready"
            or data.get("model") != self.settings.llm_model
            or type(data.get("active_requests")) is not int
            or type(data.get("max_concurrency")) is not int
            or not 0 <= data["active_requests"] <= data["max_concurrency"]
            or data["max_concurrency"] < 1
        ):
            raise APIError("AI_UNAVAILABLE")
        return data

    async def close(self):
        await self.client.aclose()
