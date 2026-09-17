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
