from dataclasses import dataclass, field
from uuid import uuid4

from frontend.client import ClientError


@dataclass
class Session:
    token: str | None = field(default=None, repr=False)
    email: str = ""
    conversation_id: int | None = None
    personas: list = field(default_factory=list)
    conversations: list = field(default_factory=list)
    turns: list = field(default_factory=list)
    pending_id: str | None = None
    pending_question: str = ""
    pending_status: str | None = None
    sending: bool = False
    epoch: int = 0
    notice: str = "로그인하거나 새 계정을 만들어 주세요."

    def clear(self):
        self.epoch += 1
        self.token = None
        self.email = ""
        self.conversation_id = None
        self.personas = []
        self.conversations = []
        self.turns = []
        self.clear_pending()

    def clear_pending(self):
        self.pending_id = None
        self.pending_question = ""
        self.pending_status = None
        self.sending = False

    def matches(self, snapshot):
        return (self.epoch, self.token, self.conversation_id) == snapshot

    def snapshot(self):
        return self.epoch, self.token, self.conversation_id

    def messages(self):
        messages = []
        for turn in self.turns:
            messages.append({"role": "user", "content": turn["question"]})
            if turn["status"] == "completed":
                messages.append({"role": "assistant", "content": turn["answer"]})
        if self.pending_id and not any(t["id"] == self.pending_id for t in self.turns):
            messages.append({"role": "user", "content": self.pending_question})
        return messages


class Controller:
    def __init__(self, api):
        self.api = api

    def handle_error(self, session, error):
        if error.code == "AUTH_REQUIRED":
            session.clear()
        if error.code == "CONVERSATION_NOT_FOUND":
            session.epoch += 1
            session.conversation_id = None
            session.turns = []
            session.clear_pending()
        session.notice = error.message
        if error.request_id:
            session.notice += f" (문의 번호: {error.request_id})"

    async def signup(self, session, email, password):
        snapshot = session.snapshot()
        try:
            await self.api.request(
                "POST", "/auth/signup", body={"email": email, "password": password}
            )
            if session.matches(snapshot):
                session.notice = "가입이 완료되었습니다. 로그인해 주세요."
        except ClientError as exc:
            if session.matches(snapshot):
                self.handle_error(session, exc)

    async def login(self, session, email, password):
        session.clear()
        snapshot = session.snapshot()
        try:
            data = await self.api.request(
                "POST", "/auth/login", body={"email": email, "password": password}
            )
            if not session.matches(snapshot):
                return
            session.token, session.email = data["access_token"], data["user"]["email"]
            session.notice = "위인을 골라 새 대화를 시작하거나 이전 대화를 열어 주세요."
            await self.refresh(session)
        except ClientError as exc:
            if session.epoch == snapshot[0]:
                self.handle_error(session, exc)

    async def logout(self, session):
        snapshot = session.snapshot()
        try:
            await self.api.request("POST", "/auth/logout", session.token)
        except ClientError as exc:
            if not session.matches(snapshot):
                return
            if exc.code != "AUTH_REQUIRED":
                self.handle_error(session, exc)
                return
        if session.matches(snapshot):
            session.clear()
            session.notice = "로그아웃했습니다."

    async def refresh(self, session):
        snapshot = session.snapshot()
        try:
            personas = await self.api.request("GET", "/personas", session.token)
            conversations = await self.api.pages("/conversations", session.token)
            if session.matches(snapshot):
                session.personas, session.conversations = personas["items"], conversations
        except ClientError as exc:
            if session.matches(snapshot):
                self.handle_error(session, exc)

    async def new_conversation(self, session, persona_id):
        if session.sending or (session.pending_id and session.pending_status != "failed"):
            return
        snapshot = session.snapshot()
        try:
            data = await self.api.request(
                "POST", "/conversations", session.token, {"persona_id": persona_id}
            )
            if not session.matches(snapshot):
                return
            session.epoch += 1
            session.conversation_id = data["id"]
            session.turns = []
            session.clear_pending()
            session.notice = f"{data['persona']['name']}의 관점으로 대화를 시작합니다."
            await self.refresh(session)
        except ClientError as exc:
            if session.matches(snapshot):
                self.handle_error(session, exc)
                if exc.code == "NETWORK_ERROR":
                    session.notice = (
                        "대화 생성 여부를 확인할 수 없습니다. 목록을 새로고침해 주세요."
                    )

    async def open_conversation(self, session, cid):
        if session.sending or not cid:
            return
        session.epoch += 1
        session.conversation_id = int(cid)
        session.turns = []
        session.clear_pending()
        await self.reconcile(session)

    def prepare_send(self, session, content):
        if session.sending:
            return False
        if not session.token or not session.conversation_id:
            session.notice = "로그인 후 대화를 선택해 주세요."
            return False
        content = content.strip()
        if not 1 <= len(content) <= 2000:
            session.notice = "질문을 1~2,000자로 입력해 주세요."
            return False
        if session.pending_id and session.pending_status != "failed":
            session.notice = "앞선 질문의 결과를 먼저 확인해 주세요."
            return False
        session.pending_id = str(uuid4())
        session.pending_question = content
        session.pending_status = "processing"
        session.sending = True
        session.notice = "답변을 생성하고 있습니다."
        return True

    async def transmit(self, session):
        snapshot = session.snapshot()
        tid, question = session.pending_id, session.pending_question
        try:
            turn = await self.api.request(
                "POST",
                f"/conversations/{snapshot[2]}/turns",
                snapshot[1],
                {"client_message_id": tid, "content": question},
            )
            if not session.matches(snapshot):
                return
            session.turns = [t for t in session.turns if t["id"] != tid] + [turn]
            session.clear_pending()
            session.notice = "답변이 저장되었습니다."
        except ClientError as exc:
            if not session.matches(snapshot):
                return
            session.sending = False
            # An error response can be lost after a successful DB commit. Only records resolve it.
            self.handle_error(session, exc)
            if session.matches(snapshot) and session.pending_id:
                session.pending_status = "unknown"
                await self.reconcile(session, keep_notice=True)

    async def reconcile(self, session, keep_notice=False):
        if not session.token or not session.conversation_id or session.sending:
            return
        snapshot = session.snapshot()
        try:
            turns = await self.api.pages(f"/conversations/{snapshot[2]}/turns", snapshot[1])
            if not session.matches(snapshot):
                return
            session.turns = turns
            turn = next((t for t in turns if t["id"] == session.pending_id), None)
            if turn is None and not session.pending_id:
                turn = next((t for t in turns if t["status"] == "processing"), None)
            if turn:
                if turn["status"] == "completed":
                    session.clear_pending()
                    session.notice = "저장된 답변을 확인했습니다."
                else:
                    session.pending_id, session.pending_question = turn["id"], turn["question"]
                    session.pending_status = turn["status"]
                    if turn["status"] == "failed":
                        self.handle_error(session, ClientError(turn["error_code"]))
                    elif not keep_notice:
                        session.notice = "답변을 생성하고 있습니다. 2초 간격으로 상태를 확인합니다."
            elif session.pending_id:
                session.pending_status = "unknown"
                session.notice = (
                    "아직 저장된 결과가 없습니다. ‘결과 확인 / 다시 요청’을 눌러 확인하세요."
                )
            elif not keep_notice:
                failed = next((t for t in reversed(turns) if t["status"] == "failed"), None)
                failed_count = sum(t["status"] == "failed" for t in turns)
                session.notice = (
                    (
                        f"기록을 불러왔습니다. 실패한 질문 {failed_count}개. "
                        + ClientError(failed["error_code"]).message
                    )
                    if failed
                    else "기록을 불러왔습니다."
                )
        except ClientError as exc:
            if session.matches(snapshot):
                self.handle_error(session, exc)

    async def retry(self, session, content):
        if session.sending:
            return False
        if session.pending_id:
            await self.reconcile(session)
            if session.pending_status == "processing":
                return False
            if session.pending_status == "unknown":
                # Explicit user retry only, with the same UUID AND original normalized content.
                session.sending = True
                session.notice = "같은 질문의 결과를 다시 확인하고 있습니다."
                return True
            if session.pending_id is None:
                return False
        return self.prepare_send(session, content or session.pending_question)
