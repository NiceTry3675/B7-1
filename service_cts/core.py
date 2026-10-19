from collections import deque
from dataclasses import dataclass
from enum import StrEnum
from itertools import product


class NoEdgePolicy(StrEnum):
    AUTH_REQUIRED = "auth_required"
    NEEDS_CONVERSATION = "needs_conversation"
    BUSY = "busy"
    IN_PROGRESS = "in_progress"
    CONFLICT = "conflict"
    STALE = "stale"
    INVALID = "invalid_event"


@dataclass(frozen=True)
class Edge:
    source: StrEnum
    action: StrEnum
    target: StrEnum


class FiniteCTS:
    def __init__(self, name, states, actions, edges, special_no_edges=None):
        self.name = name
        self.states = tuple(states)
        self.actions = tuple(actions)
        if not self.states or not self.actions:
            raise ValueError("CTS needs states and actions")
        self.state_type = type(self.states[0])
        self.action_type = type(self.actions[0])
        self.edges = tuple(edges)
        self._edges = {}
        for edge in self.edges:
            if not isinstance(edge.source, self.state_type) or not isinstance(
                edge.target, self.state_type
            ):
                raise ValueError("unknown CTS state")
            if not isinstance(edge.action, self.action_type):
                raise ValueError("unknown CTS action")
            key = edge.source, edge.action
            if key in self._edges:
                raise ValueError("duplicate CTS edge")
            self._edges[key] = edge
        special = dict(special_no_edges or {})
        for (state, action), policy in special.items():
            if not isinstance(state, self.state_type) or not isinstance(action, self.action_type):
                raise ValueError("unknown no-edge cell")
            if not isinstance(policy, NoEdgePolicy):
                raise ValueError("invalid no-edge policy")
            if (state, action) in self._edges:
                raise ValueError("edge and no-edge policy overlap")
        self._no_edges = {
            pair: special.get(pair, NoEdgePolicy.INVALID)
            for pair in product(self.states, self.actions)
            if pair not in self._edges
        }

    def step(self, state, action):
        self._validate(state, action)
        edge = self._edges.get((state, action))
        return edge.target if edge else None

    def no_edge(self, state, action):
        self._validate(state, action)
        return self._no_edges.get((state, action))

    def _validate(self, state, action):
        if not isinstance(state, self.state_type) or not isinstance(action, self.action_type):
            raise ValueError("unknown CTS state or action")

    def cells(self):
        for state, action in product(self.states, self.actions):
            yield state, action, self.step(state, action), self.no_edge(state, action)

    def walk(self, start, max_depth):
        if not isinstance(start, self.state_type) or max_depth < 0:
            raise ValueError("invalid CTS walk scope")
        pending = deque([(start, ())])
        visited_states = {start}
        while pending:
            state, path = pending.popleft()
            if len(path) >= max_depth:
                continue
            outgoing = (edge for edge in self.edges if edge.source == state)
            for edge in outgoing:
                next_path = (*path, edge)
                yield next_path
                if edge.target not in visited_states:
                    visited_states.add(edge.target)
                    pending.append((edge.target, next_path))
