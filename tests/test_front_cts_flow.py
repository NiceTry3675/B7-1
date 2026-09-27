import httpx

from app.errors import APIError
from frontend.client import BackendClient, ClientError
from frontend.controller import Controller, Session
from service_cts.front import FRONT_CTS, FrontAction, FrontState, project


def verify(before, action, session):
    after = project(session)
    assert FRONT_CTS.step(before, action) == after
    return after


async def test_front_success_failure_retry_and_logout(app, fake_llm):
    controller = Controller(BackendClient("http://test", transport=httpx.ASGITransport(app=app)))
    session = Session()
    state = project(session)
    await controller.signup(session, "cts@example.com", "test-password123")
    await controller.login(session, "cts@example.com", "test-password123")
    state = verify(state, FrontAction.LOGIN_OK, session)
    await controller.new_conversation(session, "socrates")
    state = verify(state, FrontAction.CREATE_OK, session)
    assert controller.prepare_send(session, "첫 질문")
    state = verify(state, FrontAction.SEND, session)
    await controller.transmit(session)
    state = verify(state, FrontAction.SEND_OK, session)
    fake_llm.error = APIError("AI_BUSY")
    assert controller.prepare_send(session, "두 번째 질문")
    state = verify(state, FrontAction.SEND, session)
    await controller.transmit(session)
    assert FRONT_CTS.step(state, FrontAction.SEND_ERROR) == FrontState.UNKNOWN
    state = verify(FrontState.UNKNOWN, FrontAction.RECORD_FAILED, session)
    failed_id = session.pending_id
    fake_llm.error = None
    assert await controller.retry(session, "두 번째 질문")
    assert session.pending_id != failed_id
    state = verify(FrontState.FAILED, FrontAction.RETRY_FAILED, session)
    await controller.transmit(session)
    state = verify(state, FrontAction.SEND_OK, session)
    await controller.logout(session)
    verify(state, FrontAction.LOGOUT_OK, session)


async def test_front_unknown_result_reuses_original_question_id():
    class Offline:
        async def request(self, method, path, token=None, body=None):
            raise ClientError("NETWORK_ERROR")

        async def pages(self, path, token):
            return []

    controller = Controller(Offline())
    session = Session(token="token", conversation_id=1)
    state = project(session)
    assert controller.prepare_send(session, " original ")
    state = verify(state, FrontAction.SEND, session)
    original_id = session.pending_id
    await controller.transmit(session)
    state = verify(state, FrontAction.SEND_ERROR, session)
    assert state == FrontState.UNKNOWN
    assert not controller.prepare_send(session, "새 질문")
    assert project(session) == FrontState.UNKNOWN
    assert await controller.retry(session, "changed")
    verify(state, FrontAction.RETRY_UNKNOWN, session)
    assert session.pending_id == original_id and session.pending_question == "original"


async def test_front_record_poll_and_switch_conversation():
    class Records:
        def __init__(self):
            self.turn = {
                "id": "pending",
                "question": "질문",
                "status": "processing",
                "answer": None,
                "error_code": None,
            }

        async def pages(self, path, token):
            return [self.turn] if path.endswith("/turns") else []

    api = Records()
    controller = Controller(api)
    session = Session(token="token", conversation_id=1)
    state = project(session)
    await controller.reconcile(session)
    state = verify(state, FrontAction.RECORD_PROCESSING, session)
    assert not controller.prepare_send(session, "새 질문")
    assert project(session) == FrontState.PROCESSING
    api.turn = {**api.turn, "status": "failed", "error_code": "AI_BUSY"}
    await controller.reconcile(session)
    state = verify(state, FrontAction.RECORD_FAILED, session)
    await controller.open_conversation(session, 2)
    verify(state, FrontAction.OPEN, session)


async def test_signed_out_open_cannot_leave_a_conversation_id():
    class NoNetwork:
        async def pages(self, path, token):
            raise AssertionError("signed-out UI must not call the backend")

    session = Session()
    await Controller(NoNetwork()).open_conversation(session, 12)
    assert project(session) == FrontState.SIGNED_OUT
    assert session.conversation_id is None
