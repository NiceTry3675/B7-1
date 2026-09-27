from dataclasses import dataclass


ERRORS = {
    "LLM_UNAUTHORIZED": (401, "서비스 인증이 필요합니다."),
    "LLM_INVALID_INPUT": (422, "요청 형식이 올바르지 않습니다."),
    "LLM_MODEL_NOT_FOUND": (404, "설정된 모델을 찾을 수 없습니다."),
    "LLM_CONTEXT_TOO_LARGE": (422, "대화가 모델 문맥 한도를 넘었습니다."),
    "LLM_BUSY": (503, "모델이 다른 요청을 처리 중입니다."),
    "LLM_NOT_READY": (503, "모델이 준비되지 않았습니다."),
    "LLM_TIMEOUT": (504, "모델 응답 시간이 초과됐습니다."),
    "LLM_INTERNAL_ERROR": (500, "모델 처리에 실패했습니다."),
}


@dataclass
class InternalError(Exception):
    code: str

    @property
    def status(self):
        return ERRORS[self.code][0]

    @property
    def message(self):
        return ERRORS[self.code][1]


class RuntimeUncertain(Exception):
    """The runtime may still be processing a request after transport failure."""
