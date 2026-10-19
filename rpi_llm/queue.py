import asyncio

from rpi_llm.cts import SLOT_CTS, Action, NoEdgePolicy, State
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
            policy = SLOT_CTS.no_edge(self.state, action)
            raise RuntimeError(f"no CTS edge: {self.state}/{action} ({policy})")
        self.state = target

    def model_ready(self):
        if SLOT_CTS.no_edge(self.state, Action.MODEL_READY) == NoEdgePolicy.STALE:
            return False
        self._advance(Action.MODEL_READY)
        return True

    def model_lost(self):
        if SLOT_CTS.no_edge(self.state, Action.MODEL_LOST) == NoEdgePolicy.STALE:
            return False
        self._advance(Action.MODEL_LOST)
        return True

    def claim(self):
        policy = SLOT_CTS.no_edge(self.state, Action.CLAIM)
        if policy == NoEdgePolicy.NOT_READY:
            raise InternalError("LLM_NOT_READY")
        if policy == NoEdgePolicy.BUSY:
            raise InternalError("LLM_BUSY")
        self._advance(Action.CLAIM)

    def attach(self, task):
        if self.state != State.COUNTING or self.active_task is not None:
            raise RuntimeError("claim must precede task attachment")
        self.active_task = task

    def begin_inference(self):
        if SLOT_CTS.no_edge(self.state, Action.CONTEXT_VALID) == NoEdgePolicy.STALE:
            return False
        self._advance(Action.CONTEXT_VALID)
        return True

    def deadline(self):
        if SLOT_CTS.no_edge(self.state, Action.DEADLINE) == NoEdgePolicy.STALE:
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
        if self.active_task is not None:
            raise RuntimeError("cannot recover while a job is active")
        if SLOT_CTS.no_edge(self.state, Action.RUNTIME_IDLE) == NoEdgePolicy.STALE:
            return False
        self._advance(Action.RUNTIME_IDLE)
        return True
