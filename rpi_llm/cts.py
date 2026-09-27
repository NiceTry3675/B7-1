"""Finite transition contract for the single compute slot."""

from collections import deque
from dataclasses import dataclass
from enum import StrEnum
from itertools import product


class State(StrEnum):
    NOT_READY = "not_ready"
    READY = "ready"
    COUNTING = "counting"
    GENERATING = "generating"
    DRAINING_COUNT = "draining_count"
    DRAINING_GENERATION = "draining_generation"
    QUARANTINED = "quarantined"


class Action(StrEnum):
    MODEL_READY = "model_ready"
    MODEL_LOST = "model_lost"
    CLAIM = "claim"
    CONTEXT_VALID = "context_valid"
    CONTEXT_REJECTED = "context_rejected"
    COUNT_FAILED = "count_failed"
    INFERENCE_FINISHED = "inference_finished"
    INFERENCE_UNKNOWN = "inference_unknown"
    DEADLINE = "deadline"
    LATE_FINISHED = "late_finished"
    LATE_UNKNOWN = "late_unknown"
    RUNTIME_IDLE = "runtime_idle"


class NoEdgePolicy(StrEnum):
    BUSY = "reject_busy"
    NOT_READY = "reject_not_ready"
    STALE = "ignore_stale"
    INVALID = "invalid_event"


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
    Edge(State.COUNTING, Action.COUNT_FAILED, State.NOT_READY),
    Edge(State.COUNTING, Action.DEADLINE, State.DRAINING_COUNT),
    Edge(State.GENERATING, Action.INFERENCE_FINISHED, State.READY),
    Edge(State.GENERATING, Action.INFERENCE_UNKNOWN, State.QUARANTINED),
    Edge(State.GENERATING, Action.DEADLINE, State.DRAINING_GENERATION),
    Edge(State.DRAINING_COUNT, Action.LATE_FINISHED, State.READY),
    Edge(State.DRAINING_COUNT, Action.COUNT_FAILED, State.NOT_READY),
    Edge(State.DRAINING_GENERATION, Action.LATE_FINISHED, State.READY),
    Edge(State.DRAINING_GENERATION, Action.LATE_UNKNOWN, State.QUARANTINED),
    Edge(State.QUARANTINED, Action.RUNTIME_IDLE, State.READY),
)


class TransitionSystem:
    def __init__(self, edges=EDGES, no_edges=None):
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
        self._no_edges = dict(no_edges or {})
        for key, policy in self._no_edges.items():
            if (
                len(key) != 2
                or not isinstance(key[0], State)
                or not isinstance(key[1], Action)
                or not isinstance(policy, NoEdgePolicy)
            ):
                raise ValueError("invalid no-edge policy")
            if key in self._index:
                raise ValueError("edge and no-edge policy overlap")

    def outgoing(self, state):
        if not isinstance(state, State):
            raise ValueError("unknown state")
        return tuple(edge for edge in self.edges if edge.source == state)

    def step(self, state, action):
        if not isinstance(state, State) or not isinstance(action, Action):
            raise ValueError("unknown state or action")
        edge = self._index.get((state, action))
        return edge.target if edge is not None else None

    def no_edge(self, state, action):
        if not isinstance(state, State) or not isinstance(action, Action):
            raise ValueError("unknown state or action")
        return self._no_edges.get((state, action))

    def assert_total(self):
        missing = set(product(State, Action)) - self._index.keys() - self._no_edges.keys()
        if missing:
            raise ValueError(f"unclassified state/action pairs: {len(missing)}")

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


EXPECTED_STATES = (
    State.NOT_READY,
    State.READY,
    State.COUNTING,
    State.GENERATING,
    State.DRAINING_COUNT,
    State.DRAINING_GENERATION,
    State.QUARANTINED,
)
EXPECTED_ACTIONS = (
    Action.MODEL_READY,
    Action.MODEL_LOST,
    Action.CLAIM,
    Action.CONTEXT_VALID,
    Action.CONTEXT_REJECTED,
    Action.COUNT_FAILED,
    Action.INFERENCE_FINISHED,
    Action.INFERENCE_UNKNOWN,
    Action.DEADLINE,
    Action.LATE_FINISHED,
    Action.LATE_UNKNOWN,
    Action.RUNTIME_IDLE,
)
if tuple(State) != EXPECTED_STATES or tuple(Action) != EXPECTED_ACTIONS:
    raise ValueError("update the CTS no-edge audit when states or actions change")

ACTION_SCOPES = {
    "readiness": (Action.MODEL_READY, Action.MODEL_LOST),
    "admission": (Action.CLAIM,),
    "count": (Action.CONTEXT_VALID, Action.CONTEXT_REJECTED, Action.COUNT_FAILED),
    "generation": (Action.INFERENCE_FINISHED, Action.INFERENCE_UNKNOWN),
    "timeout_recovery": (
        Action.DEADLINE,
        Action.LATE_FINISHED,
        Action.LATE_UNKNOWN,
        Action.RUNTIME_IDLE,
    ),
}
scoped_actions = [action for actions in ACTION_SCOPES.values() for action in actions]
if len(scoped_actions) != len(Action) or set(scoped_actions) != set(Action):
    raise ValueError("each CTS action must belong to exactly one audit scope")

NO_EDGE_OVERRIDES = {
    (State.NOT_READY, Action.CLAIM): NoEdgePolicy.NOT_READY,
    (State.QUARANTINED, Action.CLAIM): NoEdgePolicy.NOT_READY,
    **{
        (state, Action.CLAIM): NoEdgePolicy.BUSY
        for state in (
            State.COUNTING,
            State.GENERATING,
            State.DRAINING_COUNT,
            State.DRAINING_GENERATION,
        )
    },
    (State.NOT_READY, Action.MODEL_LOST): NoEdgePolicy.STALE,
    (State.READY, Action.MODEL_READY): NoEdgePolicy.STALE,
    (State.NOT_READY, Action.RUNTIME_IDLE): NoEdgePolicy.STALE,
    (State.READY, Action.RUNTIME_IDLE): NoEdgePolicy.STALE,
    (State.DRAINING_COUNT, Action.CONTEXT_VALID): NoEdgePolicy.STALE,
    (State.DRAINING_COUNT, Action.DEADLINE): NoEdgePolicy.STALE,
    (State.DRAINING_GENERATION, Action.DEADLINE): NoEdgePolicy.STALE,
}
edge_keys = {(edge.source, edge.action) for edge in EDGES}
if edge_keys & NO_EDGE_OVERRIDES.keys():
    raise ValueError("CTS no-edge override overlaps a transition")
SLOT_NO_EDGES = {
    pair: NO_EDGE_OVERRIDES.get(pair, NoEdgePolicy.INVALID)
    for pair in product(State, Action)
    if pair not in edge_keys
}
SLOT_CTS = TransitionSystem(EDGES, SLOT_NO_EDGES)
SLOT_CTS.assert_total()
