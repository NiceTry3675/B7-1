"""Authenticated readiness check; no inference, token, or address output."""

import asyncio
from uuid import uuid4

from app.config import Settings
from app.errors import APIError
from app.llm import LLMClient


async def main():
    llm = LLMClient(Settings())
    try:
        data = await llm.health("req_" + uuid4().hex)
        print(f"ready: active={data['active_requests']} max={data['max_concurrency']}")
    except APIError as exc:
        print(f"{exc.code}: {exc.message}")
        raise SystemExit(1) from None
    finally:
        await llm.close()


if __name__ == "__main__":
    asyncio.run(main())
