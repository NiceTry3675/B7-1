import asyncio

import httpx

from app.config import Settings
from app.llm import LLMClient
from app.main import create_app as create_service
from frontend.client import BackendClient
from frontend.controller import Controller, Session
from rpi_llm.config import RPiSettings
from rpi_llm.cts import State as SlotState
from rpi_llm.main import create_app as create_rpi
from service_cts.backend import TurnState, project_turn
from service_cts.front import FrontState, project
from tests.test_rpi_api import FakeRuntime


def stored_turn(service, cid, tid):
    with service.state.db.connect() as conn:
        return conn.execute(
            "SELECT * FROM chat_turns WHERE conversation_id=? AND id=?", (cid, tid)
        ).fetchone()


async def test_front_backend_rpi_handoff_and_failed_retry(tmp_path):
    token = "s" * 32
    runtime = FakeRuntime()
    rpi = create_rpi(
        RPiSettings(
            _env_file=None, llm_service_token=token, llm_model_path=tmp_path / "gemma.gguf"
        ),
        runtime,
    )
    settings = Settings(
        _env_file=None,
        database_url=f"sqlite:///{tmp_path / 'service.db'}",
        llm_base_url="http://rpi.test",
        llm_service_token=token,
    )
    llm = LLMClient(
        settings,
        httpx.AsyncClient(transport=httpx.ASGITransport(app=rpi), base_url="http://rpi.test"),
    )
    service = create_service(settings, llm=llm, token_counter=lambda system, messages: 10)
    controller = Controller(
        BackendClient("http://service.test", transport=httpx.ASGITransport(app=service))
    )
    session = Session()
    async with rpi.router.lifespan_context(rpi):
        async with service.router.lifespan_context(service):
            assert project(session) == FrontState.SIGNED_OUT
            await controller.signup(session, "flow@example.com", "test-password123")
            await controller.login(session, "flow@example.com", "test-password123")
            assert project(session) == FrontState.CHOOSING
            await controller.new_conversation(session, "socrates")
            assert project(session) == FrontState.READY
            assert rpi.state.queue.state == SlotState.READY

            runtime.gate = asyncio.Event()
            assert controller.prepare_send(session, "첫 질문")
            tid, cid = session.pending_id, session.conversation_id
            assert project(session) == FrontState.SENDING
            assert project_turn(stored_turn(service, cid, tid)) == TurnState.ABSENT
            task = asyncio.create_task(controller.transmit(session))
            try:
                await asyncio.wait_for(runtime.started.wait(), 1)
                assert project_turn(stored_turn(service, cid, tid)) == TurnState.PROCESSING
                assert rpi.state.queue.state == SlotState.GENERATING
                assert rpi.state.queue.active_requests == 1
            finally:
                runtime.gate.set()
                await task
            assert project(session) == FrontState.READY
            assert project_turn(stored_turn(service, cid, tid)) == TurnState.COMPLETED
            assert rpi.state.queue.state == SlotState.READY

            runtime.gate = None
            runtime.uncertain = True
            assert controller.prepare_send(session, "실패 질문")
            failed_id = session.pending_id
            await controller.transmit(session)
            assert project(session) == FrontState.FAILED
            assert project_turn(stored_turn(service, cid, failed_id)) == TurnState.FAILED
            assert rpi.state.queue.state == SlotState.QUARANTINED

            runtime.uncertain = False
            runtime.idle = True
            assert await controller.retry(session, "실패 질문")
            assert project(session) == FrontState.SENDING
            assert session.pending_id != failed_id
            new_id = session.pending_id
            await controller.transmit(session)
            assert project(session) == FrontState.READY
            assert project_turn(stored_turn(service, cid, failed_id)) == TurnState.FAILED
            assert project_turn(stored_turn(service, cid, new_id)) == TurnState.COMPLETED
            assert rpi.state.queue.state == SlotState.READY
