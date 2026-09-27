import asyncio

from rpi_llm.cts import SLOT_CTS, Action, State
from rpi_llm.errors import InternalError


class ComputeJobQueue:
    """One active compute job, no waiting jobs, in one wrapper process."""

    def __init__(self):
        self.state = State.NOT_READY
        self.active_task: asyncio.Task | None = None

    @property
    def active_requests(self):
        return int(self.active_task is not None and not self.active_task.done())

    def _advance(self, action):
        target = SLOT_CTS.step(self.state, action)
        if target is None:
            raise RuntimeError(f"invalid slot transition: {self.state}/{action}")
        self.state = target

    def model_ready(self):
        if self.state == State.NOT_READY:
            self._advance(Action.MODEL_READY)

    def model_lost(self):
        if self.state == State.READY:
            self._advance(Action.MODEL_LOST)

    def claim(self):
        if self.state == State.READY:
            self._advance(Action.CLAIM)
            return
        if self.state in {State.NOT_READY, State.QUARANTINED}:
            raise InternalError("LLM_NOT_READY")
        raise InternalError("LLM_BUSY")

    def attach(self, task):
        if self.state != State.COUNTING or self.active_task is not None:
            raise RuntimeError("claim must precede task attachment")
        self.active_task = task

    def begin_inference(self):
        if self.state == State.DRAINING_COUNT:
            return False
        self._advance(Action.CONTEXT_VALID)
        return True

    def deadline(self):
        if self.state in {State.DRAINING_COUNT, State.DRAINING_GENERATION}:
            return False
        self._advance(Action.DEADLINE)
        return True

    def finish(self, *, known, count_failed=False):
        if self.active_task is not asyncio.current_task():
            raise RuntimeError("only the active job may finish")
        if self.state == State.COUNTING:
            self._advance(Action.COUNT_FAILED if count_failed else Action.CONTEXT_REJECTED)
        elif self.state == State.GENERATING:
            self._advance(Action.INFERENCE_FINISHED if known else Action.INFERENCE_UNKNOWN)
        elif self.state == State.DRAINING_COUNT:
            self._advance(Action.COUNT_FAILED if count_failed else Action.LATE_FINISHED)
        elif self.state == State.DRAINING_GENERATION:
            self._advance(Action.LATE_FINISHED if known else Action.LATE_UNKNOWN)
        else:
            raise RuntimeError(f"job cannot finish in {self.state}")
        self.active_task = None

    def recover_idle(self):
        if self.state == State.QUARANTINED and self.active_task is None:
            self._advance(Action.RUNTIME_IDLE)
