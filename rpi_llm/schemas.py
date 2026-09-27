from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")


class Message(StrictModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1)

    @model_validator(mode="after")
    def nonblank(self):
        if not self.content.strip():
            raise ValueError("message content is blank")
        return self


class GenerationRequest(StrictModel):
    model: str = Field(min_length=1)
    system_prompt: str = Field(min_length=1)
    messages: list[Message] = Field(min_length=1, max_length=11)
    max_output_tokens: int = Field(ge=1, le=512)
    temperature: float = Field(ge=0, le=1)

    @model_validator(mode="after")
    def validate_conversation(self):
        if not self.system_prompt.strip():
            raise ValueError("system prompt is blank")
        if len(self.messages) % 2 != 1:
            raise ValueError("conversation must end with a user message")
        for index, message in enumerate(self.messages):
            if message.role != ("user" if index % 2 == 0 else "assistant"):
                raise ValueError("conversation roles must alternate from user")
        return self


class Usage(StrictModel):
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)


class GenerationResponse(StrictModel):
    model: str
    answer: str = Field(min_length=1)
    finish_reason: Literal["stop", "length"]
    usage: Usage | None
    latency_ms: int = Field(ge=0)


class HealthResponse(StrictModel):
    status: Literal["ready"] = "ready"
    model: str
    active_requests: int = Field(ge=0)
    max_concurrency: int = Field(ge=1)
