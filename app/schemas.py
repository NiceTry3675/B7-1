from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)


class Credentials(Input):
    email: EmailStr = Field(max_length=254)
    password: str = Field(min_length=1, max_length=128, strict=True)

    @field_validator("email", mode="before")
    @classmethod
    def normalize_email(cls, value):
        return value.strip().lower() if isinstance(value, str) else value


class Signup(Credentials):
    password: str = Field(min_length=10, max_length=128, strict=True)


class NewConversation(Input):
    persona_id: str = Field(min_length=1, max_length=100, strict=True)


class NewTurn(Input):
    client_message_id: UUID
    content: str = Field(min_length=1, max_length=2000, strict=True)

    @field_validator("content", mode="before")
    @classmethod
    def trim_content(cls, value):
        return value.strip() if isinstance(value, str) else value


class UserBrief(BaseModel):
    id: int
    email: str


class User(UserBrief):
    created_at: str


class Login(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int
    user: UserBrief


class PersonaBrief(BaseModel):
    id: str
    name: str


class Persona(PersonaBrief):
    description: str


class Personas(BaseModel):
    items: list[Persona]


class Conversation(BaseModel):
    id: int
    persona: PersonaBrief
    created_at: str
    updated_at: str


class ConversationPage(BaseModel):
    items: list[Conversation]
    limit: int
    offset: int
    has_more: bool


class Turn(BaseModel):
    id: str
    conversation_id: int
    status: Literal["processing", "completed", "failed"]
    question: str
    answer: str | None
    error_code: str | None
    created_at: str
    completed_at: str | None


class TurnPage(BaseModel):
    conversation_id: int
    items: list[Turn]
    limit: int
    offset: int
    has_more: bool


class Health(BaseModel):
    status: Literal["ok"] = "ok"
