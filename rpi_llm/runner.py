import asyncio
import logging
from time import monotonic

from rpi_llm.errors import InternalError, RuntimeUncertain
from rpi_llm.schemas import GenerationResponse

logger = logging.getLogger("rpi_llm")


class TaskJobRunner:
    def __init__(self, settings, adapter, queue):
        self.settings = settings
        self.adapter = adapter
        self.queue = queue

    async def submit(self, request, request_id=None):
        self.queue.claim()
        job = asyncio.create_task(self._run(request, request_id))
        self.queue.attach(job)
        job.add_done_callback(self._observe)
        try:
            return await asyncio.wait_for(
                asyncio.shield(job), self.settings.llm_generation_timeout_seconds
            )
        except TimeoutError as exc:
            if job.done():
                return job.result()
            if self.queue.active_task is not job:
                return await asyncio.shield(job)
            self.queue.deadline()
            if request_id:
                logger.info("llm_deadline request_id=%s", request_id)
            raise InternalError("LLM_TIMEOUT") from exc

    @staticmethod
    def _observe(job):
        if not job.cancelled():
            job.exception()

    async def _run(self, request, request_id):
        start = monotonic()
        known = True
        inference_started = False
        runtime_returned = False
        count_complete = False
        count_failed = False

        def log_failure(code):
            if request_id:
                event = "llm_inference_failure" if inference_started else "llm_request_failure"
                logger.info("%s request_id=%s code=%s", event, request_id, code)

        try:
            input_tokens = await self.adapter.count_tokens(request)
            count_complete = True
            if input_tokens + request.max_output_tokens > self.settings.llm_context_tokens:
                raise InternalError("LLM_CONTEXT_TOO_LARGE")
            if request.max_output_tokens > self.settings.llm_output_token_limit:
                raise InternalError("LLM_INVALID_INPUT")
            if not self.queue.begin_inference():
                raise InternalError("LLM_TIMEOUT")
            inference_started = True
            if request_id:
                logger.info("llm_inference_start request_id=%s", request_id)
            result = await self.adapter.generate(request)
            runtime_returned = True
            response = GenerationResponse(
                model=self.settings.llm_model,
                answer=result.answer,
                finish_reason=result.finish_reason,
                usage=result.usage,
                latency_ms=int((monotonic() - start) * 1000),
            )
            if request_id:
                logger.info(
                    "llm_inference_success request_id=%s latency_ms=%d",
                    request_id,
                    response.latency_ms,
                )
            return response
        except RuntimeUncertain as exc:
            known = not inference_started
            count_failed = not count_complete
            log_failure("LLM_INTERNAL_ERROR")
            raise InternalError("LLM_INTERNAL_ERROR") from exc
        except asyncio.CancelledError:
            known = not inference_started
            count_failed = not count_complete
            log_failure("LLM_INTERNAL_ERROR")
            raise
        except InternalError as exc:
            count_failed = not count_complete
            log_failure(exc.code)
            raise
        except Exception as exc:
            known = not inference_started or runtime_returned
            count_failed = not count_complete
            log_failure("LLM_INTERNAL_ERROR")
            raise InternalError("LLM_INTERNAL_ERROR") from exc
        finally:
            self.queue.finish(known=known, count_failed=count_failed)
