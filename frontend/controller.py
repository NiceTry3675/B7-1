from dataclasses import dataclass, field

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

