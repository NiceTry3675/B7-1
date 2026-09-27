import pytest
from pydantic import ValidationError

from rpi_llm.config import RPiSettings
from rpi_llm.cts import SLOT_CTS, Action, Edge, State, TransitionSystem
from rpi_llm.errors import InternalError
from rpi_llm.queue import ComputeJobQueue
from rpi_llm.schemas import GenerationRequest


def settings(**changes):
    return RPiSettings(
        _env_file=None,
        llm_service_token="s" * 32,
        llm_model_path="/tmp/gemma-3-1b.gguf",
        **changes,
    )


def payload(**changes):
    data = {
        "model": "local-advisor",
        "system_prompt": "답하세요.",
        "messages": [{"role": "user", "content": "질문"}],
        "max_output_tokens": 256,
        "temperature": 0.7,
    }
    return GenerationRequest.model_validate({**data, **changes})


def test_walker_reaches_each_edge_without_infinite_cycles():
    paths = list(SLOT_CTS.walk(State.NOT_READY))
    assert {path[-1] for path in paths} == set(SLOT_CTS.edges)
    assert all(len(path) <= len(SLOT_CTS.edges) for path in paths)
    assert all(
        path[index].target == path[index + 1].source
        for path in paths
        for index in range(len(path) - 1)
    )
    assert SLOT_CTS.step(State.READY, Action.DEADLINE) is None


def test_walker_handles_state_with_no_edges_and_scoped_depth():
    empty = TransitionSystem(())
    assert empty.outgoing(State.READY) == ()
    assert empty.step(State.READY, Action.CLAIM) is None
    assert list(empty.walk(State.READY)) == []
    assert list(SLOT_CTS.walk(State.NOT_READY, max_depth=0)) == []
    assert all(len(path) <= 2 for path in SLOT_CTS.walk(State.NOT_READY, max_depth=2))


def test_transition_validator_rejects_ambiguous_or_unknown_edges():
    edge = Edge(State.READY, Action.CLAIM, State.COUNTING)
    with pytest.raises(ValueError, match="duplicate"):
        TransitionSystem((edge, edge))
    with pytest.raises(ValueError, match="unknown state"):
        TransitionSystem((Edge("ready", Action.CLAIM, State.COUNTING),))
    with pytest.raises(ValueError, match="unknown action"):
        TransitionSystem((Edge(State.READY, "claim", State.COUNTING),))
    with pytest.raises(ValueError, match="unknown state"):
        SLOT_CTS.outgoing("ready")


def test_compute_queue_has_one_slot_and_no_waiting_list():
    queue = ComputeJobQueue()
    with pytest.raises(InternalError, match="LLM_NOT_READY"):
        queue.claim()
    queue.model_ready()
    queue.claim()
    assert queue.state == State.COUNTING
    with pytest.raises(InternalError, match="LLM_BUSY"):
        queue.claim()
    queue.deadline()
    assert queue.state == State.DRAINING_COUNT
    with pytest.raises(InternalError, match="LLM_BUSY"):
        queue.claim()
    queue.recover_idle()
    assert queue.state == State.DRAINING_COUNT


@pytest.mark.parametrize(
    "changes",
    [
        {"messages": []},
        {"messages": [{"role": "assistant", "content": "wrong"}]},
        {"messages": [{"role": "user", "content": " "}]},
        {"messages": [{"role": "user", "content": "hi"}, {"role": "user", "content": "again"}]},
        {"system_prompt": "  "},
        {"system_prompt": "x" * 8193},
        {"messages": [{"role": "user", "content": "x" * 8193}]},
        {"model": "x" * 129},
        {"max_output_tokens": True},
        {"temperature": 1.2},
        {"unexpected": "field"},
    ],
)
def test_request_validator_rejects_invalid_shapes(changes):
    with pytest.raises(ValidationError):
        payload(**changes)


@pytest.mark.parametrize(
    "changes",
    [
        {"llm_runtime_url": "http://remote.example:8080"},
        {"llm_runtime_url": "http://127.0.0.1:8080/extra"},
        {"llm_runtime_url": "http://user:pass@127.0.0.1:8080"},
        {"llm_max_concurrency": 2},
        {"llm_context_tokens": 256},
        {"llm_runtime_timeout_seconds": 100},
        {"llm_runtime_probe_timeout_seconds": 120},
    ],
)
def test_rpi_configuration_rejects_unsafe_or_inconsistent_values(changes):
    with pytest.raises(ValidationError):
        settings(**changes)


def test_service_token_must_fit_http_header(tmp_path):
    with pytest.raises(ValidationError):
        RPiSettings(
            _env_file=None,
            llm_service_token="한" * 11,
            llm_model_path=tmp_path / "gemma.gguf",
        )
