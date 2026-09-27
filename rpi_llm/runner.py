import asyncio
from time import monotonic

from rpi_llm.errors import InternalError, RuntimeUncertain
from rpi_llm.schemas import GenerationResponse


class TaskJobRunner:
    def __init__(self, settings, adapter, queue):
        self.settings = settings
        self.adapter = adapter
        self.queue = queue

    async def submit(self, request):
        self.queue.claim()
        job = asyncio.create_task(self._run(request))
        self.queue.attach(job)
        job.add_done_callback(self._observe)
        try:
            return await asyncio.wait_for(
                asyncio.shield(job), self.settings.llm_generation_timeout_seconds
            )
        except TimeoutError as exc:
            self.queue.deadline()
            raise InternalError("LLM_TIMEOUT") from exc

    @staticmethod
    def _observe(job):
        if not job.cancelled():
            job.exception()

    async def _run(self, request):
        start = monotonic()
        known = True
        inference_started = False
        try:
            input_tokens = await self.adapter.count_tokens(request)
            if input_tokens + request.max_output_tokens > self.settings.llm_context_tokens:
                raise InternalError("LLM_CONTEXT_TOO_LARGE")
            if request.max_output_tokens > self.settings.llm_output_token_limit:
                raise InternalError("LLM_INVALID_INPUT")
            if not self.queue.begin_inference():
                raise InternalError("LLM_TIMEOUT")
            inference_started = True
            result = await self.adapter.generate(request)
            return GenerationResponse(
                model=self.settings.llm_model,
                answer=result.answer,
                finish_reason=result.finish_reason,
                usage=result.usage,
                latency_ms=int((monotonic() - start) * 1000),
            )
        except RuntimeUncertain as exc:
            known = not inference_started
            raise InternalError("LLM_INTERNAL_ERROR") from exc
        except asyncio.CancelledError:
            known = not inference_started
            raise
        except InternalError:
            raise
        except Exception as exc:
            known = not inference_started
            raise InternalError("LLM_INTERNAL_ERROR") from exc
        finally:
            self.queue.finish(known=known)
