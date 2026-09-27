from collections import Counter

import pytest

from frontend.controller import Session
from service_cts.backend import (
    AUTH_CTS,
    TURN_CTS,
    AuthAction,
    AuthState,
    TurnAction,
    TurnState,
    project_turn,
)
from service_cts.core import Edge, FiniteCTS, NoEdgePolicy
from service_cts.front import FRONT_CTS, FrontAction, FrontState, project


@pytest.mark.parametrize(
    "machine,start,depth,cells,edges",
    [
        (FRONT_CTS, FrontState.SIGNED_OUT, 5, 126, 66),
        (AUTH_CTS, AuthState.ABSENT, 2, 16, 4),
        (TURN_CTS, TurnState.ABSENT, 3, 32, 8),
    ],
)
def test_scoped_matrices_are_total_and_walker_reaches_each_edge(
    machine, start, depth, cells, edges
):
    rows = list(machine.cells())
    assert len(rows) == cells
    assert len(machine.edges) == edges
    assert all((target is None) != (policy is None) for _, _, target, policy in rows)
    paths = list(machine.walk(start, depth))
    assert len(paths) == edges
    assert {path[-1] for path in paths} == set(machine.edges)
    assert all(len(path) <= depth for path in paths)


def test_no_edge_categories_do_not_mean_untested_runtime_calls():
    expected = {
        "front": {
            NoEdgePolicy.AUTH_REQUIRED: 4,
            NoEdgePolicy.NEEDS_CONVERSATION: 1,
            NoEdgePolicy.BUSY: 14,
            NoEdgePolicy.STALE: 18,
            NoEdgePolicy.INVALID: 23,
        },
        "backend_auth": {
            NoEdgePolicy.AUTH_REQUIRED: 6,
            NoEdgePolicy.STALE: 3,
            NoEdgePolicy.INVALID: 3,
        },
        "backend_turn": {
            NoEdgePolicy.BUSY: 1,
            NoEdgePolicy.IN_PROGRESS: 1,
            NoEdgePolicy.CONFLICT: 3,
            NoEdgePolicy.STALE: 2,
            NoEdgePolicy.INVALID: 17,
        },
    }
    for machine in (FRONT_CTS, AUTH_CTS, TURN_CTS):
        counts = Counter(policy for _, _, target, policy in machine.cells() if target is None)
        assert counts == expected[machine.name]
    assert FRONT_CTS.no_edge(FrontState.UNKNOWN, FrontAction.SEND) == NoEdgePolicy.BUSY
    assert AUTH_CTS.no_edge(AuthState.REVOKED, AuthAction.USE) == NoEdgePolicy.AUTH_REQUIRED
    assert (
        TURN_CTS.no_edge(TurnState.PROCESSING, TurnAction.REPLAY_SAME) == NoEdgePolicy.IN_PROGRESS
    )


def test_empty_walker_and_bad_graph_fail_explicitly():
    empty = FiniteCTS("empty", FrontState, FrontAction, ())
    assert list(empty.walk(FrontState.SIGNED_OUT, 5)) == []
    assert empty.step(FrontState.SIGNED_OUT, FrontAction.SEND) is None
    with pytest.raises(ValueError, match="unknown CTS"):
        empty.step("signed_out", FrontAction.SEND)
    edge = Edge(FrontState.READY, FrontAction.SEND, FrontState.SENDING)
    with pytest.raises(ValueError, match="duplicate"):
        FiniteCTS("duplicate", FrontState, FrontAction, (edge, edge))


def test_front_projection_rejects_ambiguous_field_combinations():
    assert project(Session()) == FrontState.SIGNED_OUT
    assert project(Session(token="t")) == FrontState.CHOOSING
    assert project(Session(token="t", conversation_id=1)) == FrontState.READY
    with pytest.raises(ValueError, match="signed-out"):
        project(Session(conversation_id=1))
    with pytest.raises(ValueError, match="unknown pending"):
        project(Session(token="t", conversation_id=1, pending_id="id", pending_status="x"))


def test_backend_projection_rejects_inconsistent_turn_rows():
    assert project_turn(None) == TurnState.ABSENT
    with pytest.raises(ValueError, match="invalid persisted"):
        project_turn(
            {"status": "completed", "answer": None, "error_code": None, "completed_at": "now"}
        )
