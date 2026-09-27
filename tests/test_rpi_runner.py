import asyncio
from types import SimpleNamespace

import pytest

from rpi_llm.cts import State
from rpi_llm.errors import InternalError, RuntimeUncertain
from rpi_llm.queue import ComputeJobQueue
from rpi_llm.runner import TaskJobRunner
from rpi_llm.schemas import GenerationRequest


def request():
    return GenerationRequest.model_validate(
        {
            "model": "local-advisor",
            "system_prompt": "답하세요.",
            "messages": [{"role": "user", "content": "질문"}],
            "max_output_tokens": 20,
            "temperature": 0.7,
        }
    )


class FakeAdapter:
    def __init__(self):
        self.tokens = 10
        self.count_gate = None
        self.generate_gate = None
        self.started = asyncio.Event()
        self.generate_calls = 0
        self.uncertain = False

    async def count_tokens(self, payload):
        if self.count_gate:
            await self.count_gate.wait()
        return self.tokens

    async def generate(self, payload):
        self.generate_calls += 1
        self.started.set()
        if self.generate_gate:
            await self.generate_gate.wait()
        if self.uncertain:
            raise RuntimeUncertain()
        return SimpleNamespace(answer="답변", finish_reason="stop", usage=None)


def setup(timeout=1):
    settings = SimpleNamespace(
        llm_model="local-advisor",
        llm_context_tokens=100,
        llm_output_token_limit=20,
        llm_generation_timeout_seconds=timeout,
    )
    adapter = FakeAdapter()
    queue = ComputeJobQueue()
    queue.model_ready()
    return adapter, queue, TaskJobRunner(settings, adapter, queue)


async def wait_ready(queue):
    async def observe():
        while queue.state != State.READY:
            await asyncio.sleep(0)

    await asyncio.wait_for(observe(), 1)


async def test_one_active_job_rejects_second_without_waiting():
    adapter, queue, runner = setup()
    adapter.generate_gate = asyncio.Event()
    first = asyncio.create_task(runner.submit(request()))
    try:
        await asyncio.wait_for(adapter.started.wait(), 1)
        assert queue.state == State.GENERATING and queue.active_requests == 1
        with pytest.raises(InternalError, match="LLM_BUSY"):
            await runner.submit(request())
        assert adapter.generate_calls == 1
    finally:
        adapter.generate_gate.set()
        assert (await first).answer == "답변"
    assert queue.state == State.READY and queue.active_requests == 0


async def test_deadline_keeps_slot_until_inference_really_finishes():
    adapter, queue, runner = setup(timeout=0.02)
    adapter.generate_gate = asyncio.Event()
    with pytest.raises(InternalError, match="LLM_TIMEOUT"):
        await runner.submit(request())
    assert queue.state == State.QUARANTINED and queue.active_requests == 1
    with pytest.raises(InternalError, match="LLM_NOT_READY"):
        await runner.submit(request())
    adapter.generate_gate.set()
    await wait_ready(queue)
    assert adapter.generate_calls == 1 and queue.active_requests == 0


async def test_client_cancellation_does_not_cancel_compute_job():
    adapter, queue, runner = setup()
    adapter.generate_gate = asyncio.Event()
    caller = asyncio.create_task(runner.submit(request()))
    await asyncio.wait_for(adapter.started.wait(), 1)
    caller.cancel()
    with pytest.raises(asyncio.CancelledError):
        await caller
    assert queue.state == State.GENERATING and queue.active_requests == 1
    adapter.generate_gate.set()
    await wait_ready(queue)


async def test_uncertain_runtime_failure_quarantines_idle_slot():
    adapter, queue, runner = setup()
    adapter.uncertain = True
    with pytest.raises(InternalError, match="LLM_INTERNAL_ERROR"):
        await runner.submit(request())
    assert queue.state == State.QUARANTINED and queue.active_requests == 0
    with pytest.raises(InternalError, match="LLM_NOT_READY"):
        await runner.submit(request())
    queue.recover_idle()
    assert queue.state == State.READY


async def test_token_budget_rejects_before_inference():
    adapter, queue, runner = setup()
    adapter.tokens = 81
    with pytest.raises(InternalError, match="LLM_CONTEXT_TOO_LARGE"):
        await runner.submit(request())
    assert adapter.generate_calls == 0 and queue.state == State.READY


async def test_token_count_deadline_never_starts_inference_after_reply():
    adapter, queue, runner = setup(timeout=0.02)
    adapter.count_gate = asyncio.Event()
    with pytest.raises(InternalError, match="LLM_TIMEOUT"):
        await runner.submit(request())
    assert queue.state == State.QUARANTINED
    adapter.count_gate.set()
    await wait_ready(queue)
    assert adapter.generate_calls == 0
