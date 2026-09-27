from pathlib import Path
from urllib.parse import urlsplit

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class RPiSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    llm_service_token: str = Field(repr=False)
    llm_model: str = "local-advisor"
    llm_model_path: Path
    llm_runtime_url: str = "http://127.0.0.1:8080"
    llm_context_tokens: int = Field(default=4096, gt=0)
    llm_output_token_limit: int = Field(default=512, ge=1, le=512)
    llm_generation_timeout_seconds: float = Field(default=120, gt=0)
    llm_runtime_timeout_seconds: float = Field(default=130, gt=0)
    llm_max_concurrency: int = Field(default=1, ge=1, le=1)

    @field_validator("llm_service_token")
    @classmethod
    def validate_token(cls, value):
        if len(value.encode()) < 32 or any(char.isspace() for char in value):
            raise ValueError("LLM_SERVICE_TOKEN must be at least 32 bytes without whitespace")
        return value

    @field_validator("llm_model")
    @classmethod
    def validate_alias(cls, value):
        if not value or len(value) > 128 or value != value.strip():
            raise ValueError("LLM_MODEL must be a nonempty alias of at most 128 characters")
        return value

    @field_validator("llm_model_path")
    @classmethod
    def validate_model_path(cls, value):
        if not value.is_absolute() or value.suffix.lower() != ".gguf":
            raise ValueError("LLM_MODEL_PATH must be an absolute GGUF path")
        return value

    @field_validator("llm_runtime_url")
    @classmethod
    def validate_runtime_url(cls, value):
        url = urlsplit(value)
        if (
            url.scheme != "http"
            or url.hostname not in {"127.0.0.1", "::1"}
            or url.username
            or url.password
            or url.path not in {"", "/"}
            or url.query
            or url.fragment
        ):
            raise ValueError("LLM_RUNTIME_URL must be a loopback HTTP origin")
        return value.rstrip("/")

    @model_validator(mode="after")
    def validate_limits(self):
        if self.llm_context_tokens <= self.llm_output_token_limit:
            raise ValueError("LLM_CONTEXT_TOKENS must exceed the output limit")
        if self.llm_runtime_timeout_seconds <= self.llm_generation_timeout_seconds:
            raise ValueError("runtime timeout must exceed generation timeout")
        return self
