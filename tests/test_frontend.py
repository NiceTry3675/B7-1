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


async def test_logout_db_failure_keeps_session():
    class Broken:
        async def request(self, *args):
            raise ClientError("DB_ERROR")

    session = Session(token="token", conversation_id=1)
    await Controller(Broken()).logout(session)
    assert session.token == "token" and session.conversation_id == 1


