from pydantic import BaseModel, Field

COMMON_ERRORS = ("AUTH_REQUIRED", "INVALID_INPUT", "DB_ERROR", "INTERNAL_ERROR")

ERRORS = {
    "AUTH_REQUIRED": (401, "다시 로그인해 주세요."),
    "INVALID_CREDENTIALS": (401, "이메일 또는 비밀번호가 올바르지 않습니다."),
    "PERSONA_NOT_FOUND": (404, "선택할 수 없는 위인입니다. 목록을 새로고침해 주세요."),
    "CONVERSATION_NOT_FOUND": (404, "대화를 찾을 수 없습니다."),
    "EMAIL_ALREADY_EXISTS": (409, "이미 가입된 이메일입니다."),
    "CONVERSATION_BUSY": (409, "이 대화에서 다른 질문을 처리하고 있습니다."),
    "MESSAGE_IN_PROGRESS": (409, "질문을 처리하고 있습니다. 잠시 후 기록을 확인해 주세요."),
    "MESSAGE_ID_CONFLICT": (409, "같은 질문 ID에 다른 내용을 사용할 수 없습니다."),
    "INVALID_INPUT": (422, "입력 내용을 확인해 주세요."),
    "CONTEXT_TOO_LARGE": (422, "질문이 모델의 입력 한도를 초과합니다. 질문을 줄여 주세요."),
    "DB_ERROR": (500, "저장소 처리에 실패했습니다. 기록을 확인한 뒤 다시 시도해 주세요."),
    "INTERNAL_ERROR": (500, "서버 오류가 발생했습니다. 잠시 후 다시 시도해 주세요."),
    "AI_UNAVAILABLE": (502, "AI에 연결할 수 없습니다. 잠시 후 다시 시도해 주세요."),
    "REQUEST_INTERRUPTED": (503, "서버 중단으로 답변을 완료하지 못했습니다. 다시 요청해 주세요."),
    "AI_CONFIG_ERROR": (503, "AI 연결 설정이 준비되지 않았습니다. 관리자에게 문의해 주세요."),
    "AI_BUSY": (503, "AI가 다른 질문에 답변 중입니다. 잠시 후 다시 요청해 주세요."),
    "AI_NOT_READY": (503, "AI 모델을 준비하고 있습니다. 잠시 후 다시 요청해 주세요."),
    "AI_TIMEOUT": (504, "응답이 지연되고 있어요. 잠시 후 다시 시도해 주세요."),
}


class ErrorDetail(BaseModel):
    field: str
    message: str


class ErrorBody(BaseModel):
    code: str
    message: str
    details: list[ErrorDetail] = Field(default_factory=list)


class ErrorResponse(BaseModel):
    error: ErrorBody
    request_id: str
    turn_id: str | None = None


class APIError(Exception):
    def __init__(self, code: str, *, turn_id: str | None = None, details=None):
        self.code = code
        self.status, self.message = ERRORS[code]
        self.turn_id = turn_id
        self.details = details or []
        self.headers = {}
        if self.status == 401:
            self.headers["WWW-Authenticate"] = "Bearer"
        if code in {"MESSAGE_IN_PROGRESS", "AI_BUSY"}:
            self.headers["Retry-After"] = "2" if code == "MESSAGE_IN_PROGRESS" else "5"
        super().__init__(code)


def error_docs(*codes):
    result = {}
    for code in codes:
        status = ERRORS[code][0]
        entry = result.setdefault(status, {"model": ErrorResponse, "description": ""})
        entry["description"] += f"{code}: {ERRORS[code][1]}\n"
    return result
