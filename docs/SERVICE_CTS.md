# Front → Backend → RPi CTS

**84는 RPi 슬롯 하나의 상태×행동 칸 수**다. 전체 pytest 테스트 수나
서비스 전체 전이 수가 아니다. 이 문서는 실제 코드의 상태를 네 범위로 나눠
기록한다. 전역 상태를 한 번에 곱하면 동시에 존재할 수 없는 화면·DB·GPU
상태까지 생기므로, 각 범위의 표와 경계 흐름을 따로 검증한다.
회원가입 입력, 위인 목록, 대화 생성·조회 같은 단발성 API는 이 상태 표의
칸 수에 더하지 않고 기존 API 시험으로 확인한다.

| 범위 | 상태 | 행동 | 분류된 칸 | 실제 전이 | 간선 없음 |
| --- | ---: | ---: | ---: | ---: | ---: |
| Front 사용자 세션 | 7 | 18 | 126 | 66 | 60 |
| Backend 인증 세션 | 4 | 4 | 16 | 4 | 12 |
| Backend 질문 ID별 DB 턴 | 4 | 8 | 32 | 8 | 24 |
| RPi 전체 추론 슬롯 | 7 | 12 | 84 | 15 | 69 |
| **합계** | | | **258** | **93** | **165** |

## Front: 화면에 남는 상태

`frontend/controller.py`의 사용자별 `Session`에서 `token`, 현재 대화,
질문 ID, `pending_status`, `sending`을 읽어 상태를 판정한다.
질문 ID·내용·상태가 서로 맞지 않는 세션은 정상 상태로 억지 분류하지 않고
오류로 처리한다. 행동은 버튼 클릭 자체가 아니라 콜백의 확정된 결과다.

| 상태 | 뜻 |
| --- | --- |
| `SIGNED_OUT` | 로그인 토큰 없음 |
| `CHOOSING` | 로그인했지만 대화는 선택하지 않음 |
| `READY` | 대화를 열었고 새 질문을 보낼 수 있음 |
| `SENDING` | 질문 ID를 보존한 채 HTTP 요청 중 |
| `PROCESSING` | DB에 처리 중 턴이 보여 결과를 기다림 |
| `UNKNOWN` | 응답이 사라져 DB 저장 여부를 아직 모름 |
| `FAILED` | DB에 실패 턴이 확정됨 |

| 시작 상태 | 행동 | 도착 상태 |
| --- | --- | --- |
| 모든 상태 | `LOGIN_OK`, `LOGIN_ERROR` | 각각 `CHOOSING`, `SIGNED_OUT` |
| 모든 상태 | `LOGOUT_OK`, `LOGOUT_ERROR`, `AUTH_EXPIRED` | 각각 `SIGNED_OUT`, 기존 상태, `SIGNED_OUT` |
| `CHOOSING`, `READY`, `FAILED` | `CREATE_OK`, `CREATE_ERROR` | 각각 `READY`, 기존 상태 |
| `CHOOSING`, `READY`, `PROCESSING`, `UNKNOWN`, `FAILED` | `OPEN` | `READY` |
| `READY`, `SENDING`, `PROCESSING`, `UNKNOWN`, `FAILED` | `CONVERSATION_LOST` | `CHOOSING` |
| `READY`, `FAILED` | `SEND` | `SENDING` |
| `SENDING` | `SEND_OK`, `SEND_ERROR` | 각각 `READY`, `UNKNOWN` |
| `READY`, `UNKNOWN`, `PROCESSING` | `RECORD_PROCESSING` | `PROCESSING` |
| `UNKNOWN`, `PROCESSING` | `RECORD_COMPLETED`, `RECORD_FAILED`, `RECORD_MISSING` | 각각 `READY`, `FAILED`, `UNKNOWN` |
| `UNKNOWN`, `FAILED` | 각각 `RETRY_UNKNOWN`, `RETRY_FAILED` | `SENDING` |

예를 들어 `READY → SEND → SENDING` 뒤 성공 응답이면 `READY`로,
응답이 유실되면 먼저 `UNKNOWN`으로 간다. `UNKNOWN`에서는 같은 질문
ID로 결과를 확인한다. DB에 `failed`가 확정되었을 때만 새 ID로
재시도한다. 로그인 해제나 대화 변경 뒤 늦게 온 응답은 화면에 반영하지
않는다. 로그인하지 않은 상태의 오래된 “대화 열기” 콜백도 대화 ID를
남기지 않도록 막았다. 잘못된 대화 ID도 기존 화면 상태를 유지한다.

Front의 간선 없음은 `auth_required` 4, `needs_conversation` 1,
`busy` 14, `stale` 18, `invalid_event` 23칸이다. 화면에서 막힌
전송과 늦은 응답 무시는 이 중 의도한 결과이고, 미분류 칸을 뜻하지 않는다.

## Backend: 인증과 저장된 턴을 별도로 본다

인증 CTS는 **토큰 한 개**의 `ABSENT`, `ACTIVE`, `EXPIRED`,
`REVOKED`를 다룬다. 발급은 `ABSENT→ACTIVE`, 사용은 `ACTIVE` 유지,
로그아웃은 `ACTIVE→REVOKED`, 시간 만료는 `ACTIVE→EXPIRED`다.
만료·폐기된 토큰 사용은 간선 없이 `AUTH_REQUIRED`다. 새 로그인은
기존 토큰을 되살리는 전이가 아니라 새 인증 세션 행을 만든다.

턴 CTS는 **(대화 ID, 질문 UUID) 한 개**의 `ABSENT`,
`PROCESSING`, `COMPLETED`, `FAILED`를 다룬다. 같은 UUID 재전송과
다른 UUID의 동시 요청을 구별한다.

| 시작 | 행동 | 결과 |
| --- | --- | --- |
| `ABSENT` | 새 UUID 접수 | `PROCESSING` 저장 |
| `PROCESSING` | 같은 UUID·같은 질문 | `409 MESSAGE_IN_PROGRESS`, 상태 유지 |
| 기존 턴 | 같은 UUID·다른 질문 | `409 MESSAGE_ID_CONFLICT`, 상태 유지 |
| `ABSENT` | 다른 UUID가 이미 처리 중 | `409 CONVERSATION_BUSY`, 새 턴 없음 |
| `PROCESSING` | AI 성공·실패 | `COMPLETED` 또는 `FAILED` 저장 |
| `PROCESSING` | 오래된 턴 정리 | `FAILED / REQUEST_INTERRUPTED` 저장 |
| `COMPLETED` | 같은 UUID 재전송 | 저장된 턴을 `200`으로 반환, AI 재호출 없음 |
| `FAILED` | 같은 UUID 재전송 | 저장된 오류 반환, AI 재호출 없음 |
| 완료·실패 턴 | 늦은 AI 결과 | 기존 DB 상태 유지 |

실패 뒤 **새 UUID**로 재시도하면 다른 턴의 `ABSENT→PROCESSING`이
시작된다. 기존 실패 턴이 다시 `PROCESSING`으로 바뀌는 것은 아니다.
DB의 `CHECK`와 부분 고유 인덱스도 이 규칙을 지킨다.

## 세 범위가 만나는 한 흐름

```text
Front READY → SENDING
Backend 턴 ABSENT → PROCESSING
RPi 슬롯 READY → COUNTING → GENERATING
성공: Backend COMPLETED, Front READY, RPi READY
실패: Backend FAILED, Front FAILED, RPi는 결과에 따라 READY 또는 QUARANTINED
재시도: Front가 새 UUID를 만들고 Backend에 새 턴을 시작
```

ASGITransport와 가짜 런타임을 사용하는 통합 시험에서 실제
Controller→FastAPI→RPi 어댑터→SQLite→Controller를 이어 성공,
불확실한 런타임 실패, 실패 턴 보존, 새 ID 재시도를 확인한다.

## 검증 명령과 한계

감사 스크립트는 범위별 칸 수와 간선 없음 이유를 출력한다. `--scope`와
`--full`을 함께 쓰면 원하는 표 하나만 펼칠 수 있다. Walker는 Front
최대 5간선, 인증 2간선, 턴 3간선, RPi 5간선으로 제한하면서 각 범위의
전이에 모두 도달한다.

```bash
.venv/bin/python -m scripts.audit_service_cts
.venv/bin/python -m scripts.audit_service_cts --scope front --full
.venv/bin/python -m scripts.audit_service_cts --scope turn --full
bash scripts/verify_service_offline.sh
```

**258칸의 분류·93개 간선의 도달성**은 모델 검사다. Front/Backend의
실제 콜백과 SQLite 전이는 별도 Mock 시험으로 확인했다. 모델 검사만으로
실제 Gemma 응답, GPU 실행, 외부 HTTPS 접속까지 검증했다고 보지 않는다.
[RPi5 CPU 실서버 검증](VERIFICATION.md#rpi5-실제-cpu-서버--2026-09-28)은
모델 검사와 별도로 기록했다.
[RPi 슬롯 내부 CTS](CTS.md)와 [실모델 후속 순서](RPI_LLM.md#verification-order)는
각각 따로 열 수 있다.
