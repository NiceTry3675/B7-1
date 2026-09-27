"""Finite transition contract for the single compute slot."""

from collections import deque
from dataclasses import dataclass
from enum import StrEnum


class State(StrEnum):
    NOT_READY = "not_ready"
    READY = "ready"
    COUNTING = "counting"
    GENERATING = "generating"
    QUARANTINED = "quarantined"


class Action(StrEnum):
    MODEL_READY = "model_ready"
    MODEL_LOST = "model_lost"
    CLAIM = "claim"
    CONTEXT_VALID = "context_valid"
    CONTEXT_REJECTED = "context_rejected"
    INFERENCE_FINISHED = "inference_finished"
    INFERENCE_UNKNOWN = "inference_unknown"
    DEADLINE = "deadline"
    LATE_FINISHED = "late_finished"
    RUNTIME_IDLE = "runtime_idle"


@dataclass(frozen=True)
class Edge:
    source: State
    action: Action
    target: State


EDGES = (
    Edge(State.NOT_READY, Action.MODEL_READY, State.READY),
    Edge(State.READY, Action.MODEL_LOST, State.NOT_READY),
    Edge(State.READY, Action.CLAIM, State.COUNTING),
    Edge(State.COUNTING, Action.CONTEXT_VALID, State.GENERATING),
    Edge(State.COUNTING, Action.CONTEXT_REJECTED, State.READY),
    Edge(State.GENERATING, Action.INFERENCE_FINISHED, State.READY),
    Edge(State.GENERATING, Action.INFERENCE_UNKNOWN, State.QUARANTINED),
    Edge(State.GENERATING, Action.DEADLINE, State.QUARANTINED),
    Edge(State.QUARANTINED, Action.LATE_FINISHED, State.READY),
    Edge(State.QUARANTINED, Action.RUNTIME_IDLE, State.READY),
)


class TransitionSystem:
    def __init__(self, edges=EDGES):
        self.edges = tuple(edges)
        self._index = {}
        for edge in self.edges:
            if not isinstance(edge.source, State) or not isinstance(edge.target, State):
                raise ValueError("edge contains an unknown state")
            if not isinstance(edge.action, Action):
                raise ValueError("edge contains an unknown action")
            key = (edge.source, edge.action)
            if key in self._index:
                raise ValueError("duplicate state/action edge")
            self._index[key] = edge

    def outgoing(self, state):
        if not isinstance(state, State):
            raise ValueError("unknown state")
        return tuple(edge for edge in self.edges if edge.source == state)

    def step(self, state, action):
        if not isinstance(state, State) or not isinstance(action, Action):
            raise ValueError("unknown state or action")
        edge = self._index.get((state, action))
        return edge.target if edge is not None else None

    def walk(self, start, max_depth=None):
        """Yield every reachable edge path, bounded even when the graph has cycles."""
        if not isinstance(start, State):
            raise ValueError("unknown start state")
        depth = len(self.edges) if max_depth is None else max_depth
        if depth < 0:
            raise ValueError("max_depth must be nonnegative")
        pending = deque([(start, (), frozenset())])
        while pending:
            state, path, used = pending.popleft()
            if len(path) >= depth:
                continue
            outgoing = self.outgoing(state)
            if not outgoing:
                continue
            for edge in outgoing:
                if edge in used:
                    continue
                next_path = (*path, edge)
                yield next_path
                pending.append((edge.target, next_path, used | {edge}))


SLOT_CTS = TransitionSystem()
