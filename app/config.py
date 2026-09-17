from pathlib import Path

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "sqlite:///./data/chat.db"
    session_ttl_seconds: int = Field(default=43200, gt=0)
    llm_base_url: str = ""
    llm_service_token: str = Field(default="", repr=False)
    llm_model: str = "local-advisor"
    llm_tokenizer_path: str = ""
    llm_context_tokens: int = Field(default=4096, gt=0)
    llm_max_output_tokens: int = Field(default=256, ge=1, le=512)
    llm_connect_timeout_seconds: float = Field(default=5, gt=0)
    llm_generation_timeout_seconds: float = Field(default=120, gt=0)
    llm_timeout_seconds: float = Field(default=130, gt=0)
    turn_timeout_seconds: float = Field(default=145, gt=0)
    backend_timeout_seconds: float = Field(default=160, gt=0)
    proxy_timeout_seconds: float = Field(default=180, ge=180)
    turn_stale_seconds: float = Field(default=210, gt=0)
    log_level: str = "INFO"

    @model_validator(mode="after")
    def validate_limits(self):
        chain = [
            self.llm_connect_timeout_seconds,
            self.llm_generation_timeout_seconds,
            self.llm_timeout_seconds,
            self.turn_timeout_seconds,
            self.backend_timeout_seconds,
            self.proxy_timeout_seconds,
            self.turn_stale_seconds,
        ]
        if any(a >= b for a, b in zip(chain, chain[1:], strict=False)):
            raise ValueError(
                "연결 < 생성 < 내부 HTTP < 턴 < 프론트 < 프록시 < 정리 제한이어야 합니다."
            )
        if self.turn_timeout_seconds - self.llm_timeout_seconds < 5:
            raise ValueError("턴 제한에는 DB 정리용으로 최소 5초를 더 확보해야 합니다.")
        if self.llm_context_tokens <= self.llm_max_output_tokens:
            raise ValueError("컨텍스트 크기는 출력 예산보다 커야 합니다.")
        if not self.database_url.startswith("sqlite:///"):
            raise ValueError("DATABASE_URL은 sqlite:/// 경로여야 합니다.")
        if self.database_url == "sqlite:///:memory:":
            raise ValueError("연결 간 영속성을 위해 SQLite 파일을 사용하세요.")
        return self

    @property
    def database_path(self) -> Path:
        return Path(self.database_url.removeprefix("sqlite:///"))
