import asyncio

import httpx

from frontend.client import BackendClient, ClientError
from frontend.controller import Controller, Session


async def test_two_ui_sessions_and_failed_retry(app, fake_llm):
    controller = Controller(BackendClient("http://test", transport=httpx.ASGITransport(app=app)))
    a, b = Session(), Session()
    for session, email in ((a, "a@example.com"), (b, "b@example.com")):
        await controller.signup(session, email, "test-password123")
        await controller.login(session, email, "test-password123")
        await controller.new_conversation(session, "socrates")
    assert a.token != b.token and a.conversation_id != b.conversation_id
    assert controller.prepare_send(a, "질문 A")
    await controller.transmit(a)
    assert len(a.messages()) == 2 and b.messages() == []
    from app.errors import APIError

    fake_llm.error = APIError("AI_BUSY")
    assert controller.prepare_send(b, "질문 B")
    tid = b.pending_id
    await controller.transmit(b)
    assert b.pending_status == "failed" and b.pending_question == "질문 B"
    assert b.token and len(b.messages()) == 1
    fake_llm.error = None
    assert await controller.retry(b, "질문 B")
    assert b.pending_id != tid
    await controller.transmit(b)
    assert b.pending_id is None and len(b.messages()) == 3
    await controller.logout(a)
    assert not a.token and not a.turns and not a.conversation_id
    assert b.token


async def test_late_response_does_not_restore_logged_out_session(app, fake_llm):
    controller = Controller(BackendClient("http://test", transport=httpx.ASGITransport(app=app)))
    session = Session()
    await controller.signup(session, "late@example.com", "test-password123")
    await controller.login(session, "late@example.com", "test-password123")
    await controller.new_conversation(session, "socrates")
    controller.prepare_send(session, "late")
    fake_llm.gate = asyncio.Event()
    task = asyncio.create_task(controller.transmit(session))
    try:
        for _ in range(100):
            if fake_llm.calls:
                break
            await asyncio.sleep(0.01)
        await controller.logout(session)
    finally:
        fake_llm.gate.set()
        await task
    assert session.token is None and session.messages() == []


async def test_lost_response_reconciles_without_regeneration(app, fake_llm):
    class LostResponse(BackendClient):
        async def request(self, method, path, *args, **kwargs):
            result = await super().request(method, path, *args, **kwargs)
            if method == "POST" and path.endswith("/turns"):
                raise ClientError("NETWORK_ERROR")
            return result

    controller = Controller(LostResponse("http://test", transport=httpx.ASGITransport(app=app)))
    session = Session()
    await controller.signup(session, "lost@example.com", "test-password123")
    await controller.login(session, "lost@example.com", "test-password123")
    await controller.new_conversation(session, "socrates")
    controller.prepare_send(session, "result")
    await controller.transmit(session)
    assert session.pending_id is None and len(session.messages()) == 2
    assert len(fake_llm.calls) == 1


async def test_unknown_result_explicit_retry_preserves_id_and_content():
    class Offline:
        async def pages(self, *args):
            return []

    controller = Controller(Offline())
    session = Session(token="token", conversation_id=1)
    controller.prepare_send(session, " original ")
    tid = session.pending_id
    session.sending = False
    session.pending_status = "unknown"
    assert await controller.retry(session, "changed")
    assert session.pending_id == tid and session.pending_question == "original"


async def test_logout_db_failure_keeps_session():
    class Broken:
        async def request(self, *args):
            raise ClientError("DB_ERROR")

    session = Session(token="token", conversation_id=1)
    await Controller(Broken()).logout(session)
    assert session.token == "token" and session.conversation_id == 1


def test_gradio_builds():
    from frontend.main import build_ui

    demo = build_ui()
    assert len(demo.config["dependencies"]) >= 10
    states = [c for c in demo.config["components"] if c["type"] == "state"]
    assert len(states) == 1


async def test_gradio_callback_serialization(app):
    from gradio.state_holder import SessionState

    from frontend.main import build_ui

    demo = build_ui(api=BackendClient("http://test", transport=httpx.ASGITransport(app=app)))
    state = SessionState(demo)
    indices = {fn.fn.__name__: key for key, fn in demo.fns.items()}
    credentials = [None, "callback@example.com", "test-password123"]
    await demo.process_api(indices["do_signup"], credentials, state=state)
    login = await demo.process_api(indices["do_login"], credentials, state=state)
    assert login["data"][2]["visible"]
    created = await demo.process_api(indices["do_create"], [None, "socrates"], state=state)
    assert created["data"][5]["value"] > 0
    sent = await demo.process_api(indices["do_send"], [None, "테스트 질문"], state=state)
    assert sent["is_generating"]
    completed = await demo.process_api(
        indices["do_send"], [], state=state, iterator=sent["iterator"]
    )
    assert len(completed["data"][6]) == 2
    logout = await demo.process_api(indices["do_logout"], [None], state=state)
    assert not logout["data"][2]["visible"]
    assert logout["data"][6] == []
