"""Explicit live generation check, run only after the offline and health gates."""

import argparse
import asyncio
from uuid import uuid4

from app.config import Settings
from app.errors import APIError
from app.llm import LLMClient


async def check():
    client = LLMClient(Settings())
    try:
        answer = await client.generate(
            "한 문장으로 한국어로 답하세요.",
            [{"role": "user", "content": "안녕하세요."}],
            "req_" + uuid4().hex,
        )
        print(answer)
    except APIError as exc:
        print(exc.code)
        raise SystemExit(1) from None
    finally:
        await client.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true", help="send one real model request")
    args = parser.parse_args()
    if not args.run:
        parser.error("live generation needs --run")
    asyncio.run(check())


if __name__ == "__main__":
    main()
