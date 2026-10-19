"""Compare local chat-template tokens with llama-server without generating text."""

import argparse
import asyncio

from pydantic import ValidationError

from app.config import Settings
from app.errors import APIError
from app.llm import ChatTokenCounter
from rpi_llm.config import RPiSettings
from rpi_llm.errors import InternalError
from rpi_llm.llama_adapter import LlamaAdapter
from rpi_llm.schemas import GenerationRequest

SAMPLES = (
    ("한국어로 짧게 답하세요.", [{"role": "user", "content": "안녕하세요."}]),
    (
        "위인의 관점에서 답하세요.",
        [
            {"role": "user", "content": "첫 질문입니다."},
            {"role": "assistant", "content": "첫 답변입니다."},
            {"role": "user", "content": "두 번째 질문입니다."},
        ],
    ),
)


async def check():
    try:
        service = Settings()
        rpi = RPiSettings()
    except ValidationError:
        print("AI_CONFIG_ERROR")
        raise SystemExit(1) from None
    if service.llm_model != rpi.llm_model or service.llm_context_tokens != rpi.llm_context_tokens:
        print("LLM_MODEL and LLM_CONTEXT_TOKENS must match on both sides")
        raise SystemExit(1)
    counter = ChatTokenCounter(service.llm_tokenizer_path)
    adapter = LlamaAdapter(rpi)
    try:
        if not await adapter.probe_ready():
            print("LLM_NOT_READY")
            raise SystemExit(1)
        for index, (system, messages) in enumerate(SAMPLES, start=1):
            request = GenerationRequest.model_validate(
                {
                    "model": rpi.llm_model,
                    "system_prompt": system,
                    "messages": messages,
                    "max_output_tokens": service.llm_max_output_tokens,
                    "temperature": 0.7,
                }
            )
            local = counter(system, messages)
            runtime = await adapter.count_tokens(request)
            print(f"sample={index} local={local} llama={runtime}")
            if local != runtime:
                raise SystemExit(1)
    except (APIError, InternalError) as exc:
        print(exc.code)
        raise SystemExit(1) from None
    finally:
        await adapter.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true", help="query a ready local llama-server")
    args = parser.parse_args()
    if not args.run:
        parser.error("tokenizer comparison needs --run")
    asyncio.run(check())


if __name__ == "__main__":
    main()
