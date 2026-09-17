# 위인 챗봇 API 설계서

- 버전: 0.2 (구현 전 설계 초안)
- 작성일: 2026-09-17
- 대상: Gradio 프론트엔드, FastAPI 백엔드, SQLite, Gemini API 연동
- 기준: [프로젝트 요구사항](requirement.md), 2026-09-14 회의록, 이후 합의한 챗봇 방식·Gemini 사용·팀 역할 분담
- 이 문서의 URL, 필드, 제한값, 인증 방식은 구현을 위한 제안이다. 아직 구현되거나 검증된 API는 아니다.

### 0.2 변경 사항

- 로컬 LLM·Edge AI 서버 대신 FastAPI에서 Gemini API를 호출한다.
- 이재훈(DB), 권순형(클라우드 및 배포), 이진걸(백엔드), 임준현(프론트)으로 담당을 반영했다.
- Gemini 환경 변수, 공급자 오류 변환, DB·백엔드·배포 간 협업 경계를 보완했다.
- 기존 프론트용 API 경로와 정상 응답 형식은 유지한다. 오류 코드에는 호출 한도와 Gemini 설정 오류를 추가한다.

## 1. 서비스 범위

로그인한 사용자가 위인을 선택하고 고민을 입력하면, 해당 위인의 관점을 반영한 AI 답변을 받는다. 같은 대화에서는 앞선 질문·답변을 활용하며, 사용자는 자신의 대화 기록을 다시 열 수 있다.

### 이번 MVP에 포함

- 회원가입, 로그인, 로그아웃, 내 정보 확인
- 위인 목록 조회 및 위인을 지정한 새 대화 생성
- 질문 전송, AI 답변 표시, 대화 기록 저장·조회
- 빈 입력·길이 검증, AI 오류·시간 초과 안내, 운영 로그
- Gradio 단일 UI 및 외부 접속 가능한 배포

### 이번 MVP에서 제외

- 게시판, 공개 게시글, 댓글, 게시글 수정·삭제
- React, 소셜 로그인, 비밀번호 재설정, 파일 첨부
- 답변 스트리밍, 프론트에 노출되는 작업 큐 API
- 대화 삭제 및 기존 대화의 위인 변경
- 로컬 LLM 실행, Raspberry Pi·Edge AI 서버 구축, 자체 AI 분류·작업 큐

한 대화에는 위인 한 명을 고정한다. 위인을 바꾸려면 새 대화를 만든다. 새 대화를 만들어도 이전 기록은 유지한다. AI 답변은 생성이 끝난 뒤 한 번에 표시한다.

## 2. 전체 연결 구조와 담당 범위

```text
사용자 브라우저
    ↕ Gradio 화면 및 이벤트
Gradio Python 콜백
    ↕ HTTP JSON + 사용자별 Bearer 토큰
FastAPI /api/v1
    ├─ SQLite: 사용자, 인증 세션, 위인, 대화, 질문·답변
    └─ Gemini 연동 모듈 → Google Gemini API (서버 환경 변수의 API 키 사용)
```

Gradio와 FastAPI는 동일 서버에 배포할 수 있다. 이 문서는 Gradio의 서버 측 콜백이 FastAPI를 HTTP로 호출하는 방식을 기준으로 한다. Gemini API 호출과 키 읽기는 FastAPI에서만 수행한다. Gradio 코드에는 Gemini 키가 필요하지 않다. 내부 HTTP 호출을 같은 프로세스에서 실행한다면 비동기 클라이언트를 사용해 이벤트 루프를 막지 않는다.

| 담당자 | 확정 역할 | 해당 역할의 세부 작업안 |
| --- | --- | --- |
| 이재훈 | DB | SQLite 스키마·ERD, 초기 데이터, 조회·저장 함수, 제약·트랜잭션, DB 확인 가이드 |
| 권순형 | 클라우드 및 배포 | 배포 환경, 외부 URL·HTTPS, 환경 변수 주입, SQLite 영속 저장·백업, 운영 로그 확인 |
| 이진걸 | 백엔드 | FastAPI, 인증·소유권·검증, Gemini 연동, 위인별 프롬프트·문맥 구성, 오류 처리, API 명세 유지 |
| 임준현 | 프론트 | Gradio 화면, 사용자별 상태, API 호출, 로딩·오류 안내, 기록 표시, 화면 흐름 확인 |

역할명과 담당자는 확정 사항이며, 세부 작업안은 중복 구현을 줄이기 위한 제안이다. 별도의 로컬 LLM 담당은 두지 않는다.

### 2.1 담당자 간 연결 지점

- 이재훈 ↔ 이진걸: 테이블·필드·조회 함수와 트랜잭션 경계를 먼저 합의한다. DB 담당이 제약과 저장 함수를 만들고, 백엔드 담당이 API에서 호출한다. 인증 판단과 HTTP 응답은 백엔드가 담당한다.
- 이진걸 ↔ 임준현: 이 문서의 JSON·인증 헤더·오류 코드를 기준으로 연결한다. 프론트는 Gemini 응답 원문이 아닌 FastAPI 응답만 처리한다.
- 이진걸 ↔ 권순형: 사용할 Gemini 모델, 키 주입, 호출 제한·시간 제한, 배포 설정을 맞춘다. 실제 키를 저장소나 문서에 붙여 넣지 않는다.
- 이재훈 ↔ 권순형: 배포 환경의 SQLite 경로·파일 권한·백업·재배포 시 보존 방법을 맞춘다. MVP는 영속 디스크를 가진 단일 백엔드 인스턴스를 기준으로 한다.
- 전체 흐름 시험의 총괄 담당자는 아직 미정이다. 각자 담당 영역의 검증과 작업 요약을 작성한다.

프론트가 보낸 사용자 ID나 대화 내역을 권한·문맥의 근거로 신뢰하지 않는다. 로그인한 사용자와 저장된 기록은 백엔드가 확인한다.

## 3. 공통 규칙

| 항목 | 규칙 |
| --- | --- |
| 기본 경로 | `/api/v1` |
| 본문 형식 | 요청·응답 모두 JSON. 본문이 있는 요청은 `Content-Type: application/json` |
| 인증 | 보호된 API에 `Authorization: Bearer <access_token>` |
| ID | 사용자·대화 ID는 양의 정수, 위인 ID는 서버가 제공하는 문자열, 턴 ID는 UUID 문자열 |
| 시간 | ISO 8601 UTC, 예: `2026-09-17T05:30:00Z` |
| 요청 추적 | 서버가 요청마다 ID를 생성해 `X-Request-ID` 응답 헤더에 제공 |
| 소유권 | 다른 사용자의 대화와 존재하지 않는 대화는 모두 `404 CONVERSATION_NOT_FOUND` |
| 알 수 없는 입력 필드 | `422 INVALID_INPUT`으로 거부 |
| 빈 목록 | `200`과 빈 `items` 배열 |
| 비밀정보 | 비밀번호, 토큰, AI API 키를 응답 예시 외 실제 문서·로그에 기록하지 않음 |

아래 예시 ID와 시각은 설명용이다. 로그인 토큰 예시는 실제 토큰이 아니다. 오류 본문은 7절을 공통 적용한다.

### 3.1 인증 설계

Gradio 콜백에서 명시적으로 전달하기 쉬운 Bearer 토큰 방식을 사용한다. 앞서 논의한 쿠키 방식 대신 이 문서에서는 아래 방식으로 통일한다.

- 로그인 성공 시 암호학적으로 안전한 무작위 토큰을 발급한다. 최소 32바이트의 난수를 사용하고 유효기간은 12시간으로 한다.
- JWT를 전제로 하지 않는다. 서버의 `auth_sessions` 테이블에 토큰 해시, 사용자, 만료 시각, 폐기 시각을 저장한다.
- 비밀번호는 Argon2id 등 비밀번호 전용 해시로 저장한다. 토큰 해시와 비밀번호 해시는 서로 다른 목적이다.
- 보호된 요청마다 토큰의 존재·만료·폐기 여부를 검사한다.
- 로그아웃은 현재 토큰만 폐기한다. 다른 로그인 세션에는 영향을 주지 않는다.
- MVP에 갱신 토큰은 없다. 만료 시 다시 로그인한다.
- 인증 오류에는 `WWW-Authenticate: Bearer` 헤더를 제공한다.
- 외부 연결은 HTTPS를 사용한다.

Gradio는 토큰과 현재 대화 ID를 사용자별 `gr.State`에 보관한다. 전역 변수나 공용 HTTP 클라이언트의 기본 인증 헤더에 저장하지 않는다. 토큰은 화면 컴포넌트에 출력하지 않고, 요청마다 해당 사용자의 헤더를 전달한다.

페이지 새로고침·새 탭·서버 재시작 후 자동 로그인 복원은 MVP 범위에서 제외한다. 다시 로그인하면 DB의 대화 목록을 불러온다. Gradio의 세션 식별자 자체는 인증 수단이 아니다. 화면에서 버튼을 숨겨도 FastAPI 인증 검사는 항상 필요하다.

### 3.2 입력 제한

| 입력 | 제약 |
| --- | --- |
| `email` | 앞뒤 공백 제거, 이메일 형식, 최대 254자. MVP에서는 전체 소문자로 정규화해 중복 검사 |
| `password` | 가입 시 10~128자. 앞뒤 공백을 자동 제거하지 않음 |
| `content` | 앞뒤 공백 제거 후 1~2,000자. 공백만 있으면 거부 |
| `persona_id` | 활성 위인 목록에 존재해야 함 |
| `client_message_id` | 클라이언트가 새 질문마다 생성하는 UUID |
| 경로의 `conversation_id` | 양의 정수 |
| `limit` | 정수 1~100, 기본 20 |
| `offset` | 정수 0 이상, 기본 0 |

## 4. API 목록

아래 경로는 기본 경로 `/api/v1`를 포함한다.

| 기능 | 메서드 | 경로 | 인증 | 성공 코드 |
| --- | --- | --- | --- | --- |
| 회원가입 | POST | `/api/v1/auth/signup` | 불필요 | 201 |
| 로그인 | POST | `/api/v1/auth/login` | 불필요 | 200 |
| 로그아웃 | POST | `/api/v1/auth/logout` | 필요 | 204 |
| 내 정보 | GET | `/api/v1/me` | 필요 | 200 |
| 위인 목록 | GET | `/api/v1/personas` | 필요 | 200 |
| 새 대화 | POST | `/api/v1/conversations` | 필요 | 201 |
| 내 대화 목록 | GET | `/api/v1/conversations` | 필요 | 200 |
| 질문 전송 | POST | `/api/v1/conversations/{conversation_id}/turns` | 필요 | 201 / 중복 완료 요청 200 |
| 대화 기록 | GET | `/api/v1/conversations/{conversation_id}/turns` | 필요 | 200 |
| 프로세스 상태 | GET | `/api/v1/health` | 불필요 | 200 |

`turn`은 사용자 질문 하나와 그 AI 답변을 묶은 단위다. Gradio는 완료된 턴 하나를 사용자 말풍선과 AI 말풍선 두 개로 표시한다. 이 구조로 질문·응답·사용자·시간을 함께 추적한다.

## 5. 상세 명세

### 5.1 회원가입

`POST /api/v1/auth/signup`

요청:

```json
{
  "email": "student@example.com",
  "password": "Example-only-password1!"
}
```

응답 `201 Created`:

```json
{
  "id": 12,
  "email": "student@example.com",
  "created_at": "2026-09-17T05:00:00Z"
}
```

- 가입 후 자동 로그인하지 않는다. 로그인 화면으로 이동한다.
- 오류: `409 EMAIL_ALREADY_EXISTS`, `422 INVALID_INPUT`, `500 DB_ERROR`.
- 응답에 비밀번호나 비밀번호 해시를 포함하지 않는다.

### 5.2 로그인

`POST /api/v1/auth/login`

요청:

```json
{
  "email": "student@example.com",
  "password": "Example-only-password1!"
}
```

응답 `200 OK`:

```json
{
  "access_token": "EXAMPLE_TOKEN_NOT_VALID",
  "token_type": "bearer",
  "expires_in": 43200,
  "user": {
    "id": 12,
    "email": "student@example.com"
  }
}
```

- `expires_in`의 단위는 초다. 로그인 응답에 `Cache-Control: no-store`를 설정한다.
- 존재하지 않는 이메일과 비밀번호 불일치는 같은 `401 INVALID_CREDENTIALS`로 처리한다.
- 기타 오류: `422 INVALID_INPUT`, `500 DB_ERROR`.
- JSON으로 처리하는 자체 로그인 API다. OAuth2 폼 로그인 규격으로 문서화하지 않는다. OpenAPI 인증 스키마는 HTTP Bearer를 사용한다.

### 5.3 로그아웃

`POST /api/v1/auth/logout`

인증 헤더를 보내고 요청 본문은 보내지 않는다.

응답: `204 No Content`, 본문 없음.

- 현재 인증 세션을 폐기한다.
- 토큰이 누락되거나 만료·폐기되었다면 `401 AUTH_REQUIRED`.
- 저장 실패 시 `500 DB_ERROR`. 이 경우 서버 폐기가 완료되었다고 안내하지 않는다.
- 프론트는 성공 또는 `401`이면 토큰·대화 화면·선택 상태를 지우고 로그인 화면으로 이동한다.

### 5.4 내 정보

`GET /api/v1/me`

요청 본문 없음. 응답 `200 OK`:

```json
{
  "id": 12,
  "email": "student@example.com",
  "created_at": "2026-09-17T05:00:00Z"
}
```

로그인 상태 확인에 사용한다. 공통 인증·DB 오류가 적용된다.

### 5.5 위인 목록

`GET /api/v1/personas`

요청 본문 없음. 응답 `200 OK`:

```json
{
  "items": [
    {
      "id": "socrates",
      "name": "소크라테스",
      "description": "질문을 통해 고민의 전제를 함께 살펴봅니다."
    }
  ]
}
```

- 예시 인물이며 실제 목록은 팀이 초기 데이터로 확정한다.
- 활성 위인만 반환하며, 표시 순서는 서버의 `sort_order`, `id` 오름차순이다.
- 시스템 프롬프트, 모델 연결 정보, 비밀정보는 반환하지 않는다.
- 소규모 고정 목록이므로 페이지 구분은 없다.

### 5.6 새 대화 생성

`POST /api/v1/conversations`

요청:

```json
{
  "persona_id": "socrates"
}
```

응답 `201 Created`:

```json
{
  "id": 34,
  "persona": {
    "id": "socrates",
    "name": "소크라테스"
  },
  "created_at": "2026-09-17T05:20:00Z",
  "updated_at": "2026-09-17T05:20:00Z"
}
```

- 소유자는 인증 토큰으로 결정한다. 요청에 `user_id`를 받지 않는다.
- 이 단계에서는 AI를 호출하지 않는다.
- 존재하지 않거나 비활성인 위인은 `404 PERSONA_NOT_FOUND`.
- 기타 오류: 공통 인증·입력·DB 오류.
- 대화 생성은 자동 재시도하지 않는다. 응답을 받지 못하면 대화 목록을 확인한다.

### 5.7 내 대화 목록

`GET /api/v1/conversations?limit=20&offset=0`

응답 `200 OK`:

```json
{
  "items": [
    {
      "id": 34,
      "persona": {
        "id": "socrates",
        "name": "소크라테스"
      },
      "created_at": "2026-09-17T05:20:00Z",
      "updated_at": "2026-09-17T05:30:05Z"
    }
  ],
  "limit": 20,
  "offset": 0,
  "has_more": false
}
```

- 현재 사용자 소유의 대화만 반환한다.
- 정렬은 `id DESC`로 고정한다. 답변을 받아도 목록 순서는 바뀌지 않는다.
- 다음 페이지는 `offset + limit`로 조회한다. 새 대화를 생성하면 첫 페이지부터 새로 조회한다.
- `updated_at`은 턴 생성 또는 상태 변경 시 갱신한다.
- 프론트 목록에는 예를 들어 `소크라테스 · 09/17 14:20`으로 표시한다.

### 5.8 질문 전송 및 AI 답변

`POST /api/v1/conversations/34/turns`

요청:

```json
{
  "client_message_id": "53367fb2-4e2b-4dcf-89c2-2fc17df87b96",
  "content": "진로를 결정하기 어려워요."
}
```

응답 `201 Created`:

```json
{
  "id": "53367fb2-4e2b-4dcf-89c2-2fc17df87b96",
  "conversation_id": 34,
  "status": "completed",
  "question": "진로를 결정하기 어려워요.",
  "answer": "어떤 일을 할 때 가장 의미를 느끼는지부터 생각해 볼까요?",
  "error_code": null,
  "created_at": "2026-09-17T05:30:00Z",
  "completed_at": "2026-09-17T05:30:05Z"
}
```

- `id`는 요청의 `client_message_id`다. DB 키는 `(conversation_id, id)` 조합이다.
- AI 응답과 완료 상태를 DB에 저장한 뒤 성공 응답을 보낸다.
- `content` 외에 사용자 ID, 위인 ID, 시스템 프롬프트, 이전 대화 목록을 받지 않는다.
- 답변이 비어 있거나 형식이 잘못되면 `502 AI_UNAVAILABLE`로 처리한다.
- 대표 오류: `401 AUTH_REQUIRED`, `404 CONVERSATION_NOT_FOUND`, `409 CONVERSATION_BUSY`, `409 MESSAGE_IN_PROGRESS`, `409 MESSAGE_ID_CONFLICT`, `422 INVALID_INPUT`, `429 AI_RATE_LIMITED`, `502 AI_UNAVAILABLE`, `503 AI_CONFIG_ERROR`, `504 AI_TIMEOUT`, `500 DB_ERROR`.

#### 중복 전송과 재시도

프론트는 새 질문마다 UUID를 하나 만들고, 해당 질문의 결과가 확인될 때까지 유지한다. 같은 질문을 네트워크 오류 때문에 다시 확인할 때는 같은 ID를 사용한다.

| 기존 기록 | 같은 ID로 요청했을 때 동작 |
| --- | --- |
| 없음 | `processing` 턴을 저장하고 AI 호출 |
| 질문 내용이 다름 | `409 MESSAGE_ID_CONFLICT` |
| `processing` | `409 MESSAGE_IN_PROGRESS`, `Retry-After: 2` |
| `completed` | AI를 다시 호출하지 않고 기존 턴을 `200`으로 반환 |
| `failed` | 저장된 `error_code`에 대응하는 실패 응답을 반환. AI 재호출 없음 |

내용 비교는 앞뒤 공백 제거 후 수행한다. 다른 ID의 질문이 같은 대화에서 처리 중이면 `409 CONVERSATION_BUSY`를 반환한다. 완료 여부가 불명확하면 기록 조회에서 ID를 먼저 찾는다. 실패가 확정된 질문을 사용자가 다시 생성하도록 요청할 때만 새 ID를 만든다.

이 규칙은 정상 처리와 완료된 결과의 재요청 중복을 방지한다. 서버 중단·AI 측 처리 지속까지 포함한 외부 AI 호출의 정확히 한 번 실행을 보장하지는 않는다.

### 5.9 대화 기록 조회

`GET /api/v1/conversations/34/turns?limit=20&offset=0`

응답 `200 OK`:

```json
{
  "conversation_id": 34,
  "items": [
    {
      "id": "53367fb2-4e2b-4dcf-89c2-2fc17df87b96",
      "conversation_id": 34,
      "status": "completed",
      "question": "진로를 결정하기 어려워요.",
      "answer": "어떤 일을 할 때 가장 의미를 느끼는지부터 생각해 볼까요?",
      "error_code": null,
      "created_at": "2026-09-17T05:30:00Z",
      "completed_at": "2026-09-17T05:30:05Z"
    }
  ],
  "limit": 20,
  "offset": 0,
  "has_more": false
}
```

- 소유권을 확인한 뒤 DB에 저장된 기록을 반환한다.
- 정렬은 대화 내 순번인 `sequence ASC`(오래된 순)로 고정한다.
- `sequence`는 DB 내부 정렬용이며 턴 생성 시 대화 안에서 중복 없이 증가시킨다.
- `has_more=true`이면 `offset + limit`로 이어서 조회한다. MVP 프론트는 대화를 열 때 모든 페이지를 순서대로 불러온다.
- `processing`이면 `answer`, `error_code`, `completed_at`은 모두 `null`이다.
- `failed`이면 `answer=null`, `error_code`는 7절 오류 코드, `completed_at`은 실패 확정 시각이다.
- 실패한 턴도 기록에 남긴다. 실패 안내를 AI 답변 말풍선으로 저장하거나 다음 문맥에 포함하지 않는다.
- 프론트는 이미 표시한 턴의 ID를 기준으로 상태를 갱신해 중복 말풍선을 방지한다.

### 5.10 프로세스 상태 확인

`GET /api/v1/health`

응답 `200 OK`:

```json
{
  "status": "ok"
}
```

FastAPI 프로세스가 응답하는지만 확인한다. DB나 Gemini API가 정상임을 보장하는 준비 상태 점검은 아니다. 비밀정보와 내부 주소는 노출하지 않으며 상태 확인을 위해 Gemini 유료 호출을 수행하지 않는다.

## 6. 대화 처리와 저장 규칙

### 6.1 질문 처리 순서

1. 요청 ID를 생성하고 요청 수신 로그를 남긴다.
2. 토큰, 대화 소유권, 입력을 검사한다.
3. 짧은 DB 트랜잭션으로 중복 ID와 처리 중 턴을 검사하고 `processing` 턴을 저장한다. 실패하면 AI를 호출하지 않는다.
4. 같은 대화의 완료된 턴으로 문맥을 만들고 AI 호출 시작 로그를 남긴다.
5. AI 호출 성공 또는 실패 로그를 남긴다.
6. 성공 시 답변과 `completed` 상태를 함께 저장한다. 실패 시 `failed`와 오류 코드를 저장한다.
7. 저장 성공·실패를 기록하고 API 응답을 반환한다.

AI를 기다리는 동안 SQLite 쓰기 트랜잭션을 열어 두지 않는다. 애플리케이션 메모리 잠금에만 의존하지 않고, `(conversation_id, id)` 고유 제약과 대화별 `processing` 턴을 최대 하나로 제한하는 DB 제약을 적용한다.

### 6.2 문맥 구성

```text
서버의 위인별 시스템 프롬프트
→ 같은 대화에서 완료된 최근 최대 5개 턴의 질문·답변
→ 현재 질문
```

- 이전 턴은 오래된 순서부터 입력한다.
- 다른 사용자·다른 대화의 기록과 `failed/processing` 턴은 제외한다.
- Gradio가 화면에서 가지고 있는 기록을 문맥의 원본으로 사용하지 않는다.
- 실제 모델의 컨텍스트 한도에서 출력 예산을 뺀 범위를 넘으면 가장 오래된 턴부터 제거한다. 질문·답변 한 쌍을 함께 제거한다.
- 시스템 프롬프트와 현재 질문만으로도 한도를 넘으면 `422 CONTEXT_TOO_LARGE`로 실패 처리한다.
- Gemini 모델·입력 토큰 계산 방식·출력 예산은 백엔드 담당 이진걸이 선택한 모델 기준으로 설정한다. 문서의 2,000자 제한만으로 토큰 한도 준수를 보장하지 않는다.

### 6.3 타임아웃과 중단 복구

- FastAPI → Gemini 전체 호출 제한: 30초. SDK 내부 재시도나 공급자 응답 대기도 이 예산에 포함한다.
- FastAPI의 턴 전체 처리 목표 상한: 45초. DB 대기도 유한한 제한을 둔다.
- Gradio → FastAPI HTTP 대기 제한: 50초. 배포 프록시는 60초 이상으로 설정한다.
- 자동 AI 재시도는 MVP에서 하지 않는다.
- 서버 중단 등으로 `processing`이 남으면, 서버 시작 시 및 주기적인 정리 작업에서 생성 후 60초가 지난 턴을 `failed / REQUEST_INTERRUPTED`로 전환한다.
- 늦게 도착한 AI 결과는 해당 턴이 여전히 `processing`일 때만 저장한다. 이미 실패 처리된 턴을 다시 완료로 바꾸지 않는다.
- AI 답변을 받았어도 DB 저장이 실패하면 성공 응답을 주지 않고 `500 DB_ERROR`를 반환한다. 실패 상태 기록도 불가능하면 서버 로그를 남기고 복구 시 위 정리 규칙을 적용한다.
- 네트워크 연결이 끊겼다는 이유만으로 백엔드·AI 처리가 취소되었다고 간주하지 않는다. 재접속 후 저장 기록을 확인한다.

### 6.4 Gemini 연동 규칙

- Python 연동은 공식 Google GenAI SDK(`google-genai`)를 기준으로 한다. 실제 모델 ID와 SDK 버전은 구현 시 호출 검증 후 고정한다. [공식 SDK 안내](https://ai.google.dev/gemini-api/docs/libraries)
- 백엔드는 `GEMINI_API_KEY`를 읽어 클라이언트에 명시적으로 전달한다. `GEMINI_MODEL`로 모델을 지정한다. 키가 없으면 서버 시작 단계에서 설정 오류로 처리한다.
- Gemini 키는 서비스 로그인용 Bearer 토큰과 별개다. 사용자별 로그인 토큰을 Gemini에 전달하지 않는다. Gradio 상태·HTML·브라우저 코드·API 응답에 Gemini 키를 포함하지 않는다. [공식 키 관리 안내](https://ai.google.dev/gemini-api/docs/api-key)
- 매 질문마다 DB에서 읽은 해당 대화의 문맥을 전달한다. 모든 사용자가 공유하는 SDK 채팅 세션에 대화 이력을 쌓지 않는다.
- 위인 프롬프트와 과거 질문·답변은 Gemini가 요구하는 입력 형식으로 변환한다. SDK 객체나 공급자 필드는 프론트에 반환하지 않고 이 문서의 `answer`와 오류 형식으로 변환한다.
- 전송 내용은 현재 질문·같은 대화의 최근 문맥·위인 프롬프트로 한정한다. 이메일, 로그인 토큰, 비밀번호는 보내지 않는다.
- Google AI Studio에서 현재 지원하는 키 유형과 선택한 모델의 호출 가능 여부를 확인한다. Gemini 호출 한도·프로젝트 사용량·비용 확인은 백엔드와 클라우드 담당이 함께 맡는다. 구체적인 모델·요금제·예산은 아직 미정이다.
- SDK 자동 재시도는 비활성화하거나 위 단일 호출 정책과 일치하도록 설정한다. 공급자 오류로 화면이 끝없이 재시도하지 않게 한다.

아래는 프로젝트에서 정한 오류 변환 규칙이다. Gemini 오류의 의미는 [공식 문제 해결 문서](https://ai.google.dev/gemini-api/docs/troubleshooting)를 참고한다.

| Gemini에서 발생한 상황 | 우리 API 응답 | 처리 |
| --- | --- | --- |
| 429: 속도·토큰·할당량 초과 | `429 AI_RATE_LIMITED` | 실패 턴 저장, 호출 제한 안내. 공급자가 재시도 대기 시간을 제공하면 유효한 값만 `Retry-After`로 전달 |
| API 키 거부·권한 부족·모델 설정 오류 | `503 AI_CONFIG_ERROR` | 로그인 만료로 처리하지 않음. 화면에는 서비스 설정 오류 안내, 담당자가 서버 로그 확인 |
| 5xx 장애·네트워크 실패·사용 가능한 텍스트 없음 | `502 AI_UNAVAILABLE` | 실패 턴 저장, 일반 오류 안내 |
| 호출 제한 시간 초과 | `504 AI_TIMEOUT` | 실패 턴 저장, 시간 초과 안내 |

할당량 소진은 잠시 기다려도 해결되지 않을 수 있으므로 프론트에서 자동 재전송하지 않는다. 모델의 안전 필터 등으로 텍스트가 없을 때도 실제 오류 결과를 확인하고 일반 실패로 표시하며, 빈 답변을 성공으로 저장하지 않는다.

## 7. 공통 오류 응답

```json
{
  "error": {
    "code": "AI_TIMEOUT",
    "message": "응답이 지연되고 있어요. 잠시 후 다시 시도해 주세요.",
    "details": []
  },
  "request_id": "req_9f4c1d",
  "turn_id": "53367fb2-4e2b-4dcf-89c2-2fc17df87b96"
}
```

- `request_id`는 해당 HTTP 응답의 `X-Request-ID`와 같다. 재요청에는 새 요청 ID를 부여한다.
- `turn_id`는 해당 질문 ID를 확인할 수 있고 소유권 검사도 통과한 경우에만 제공하며, 그 외에는 `null`이다.
- `details`는 항상 배열이다. 입력 오류는 `[{"field":"content","message":"질문을 입력해 주세요."}]`처럼 제공한다.
- 스택 트레이스, SQL, 입력된 비밀번호, 공급자 응답 원문은 클라이언트에 반환하지 않는다.
- FastAPI의 기본 검증 오류와 인증 예외도 이 형태로 변환한다.

| 상태 코드 | 오류 코드 | 의미 / 프론트 동작 |
| --- | --- | --- |
| 401 | `AUTH_REQUIRED` | 토큰 누락·무효·만료·폐기. 로그인 화면으로 이동 |
| 401 | `INVALID_CREDENTIALS` | 로그인 정보 불일치. 동일한 일반 안내 표시 |
| 404 | `PERSONA_NOT_FOUND` | 선택 가능한 위인이 아님. 목록 새로고침 |
| 404 | `CONVERSATION_NOT_FOUND` | 대화 없음 또는 타인 소유. 내 목록으로 이동 |
| 409 | `EMAIL_ALREADY_EXISTS` | 이미 가입된 이메일 |
| 409 | `CONVERSATION_BUSY` | 같은 대화에서 다른 질문 처리 중 |
| 409 | `MESSAGE_IN_PROGRESS` | 같은 질문 처리 중. 2초 이상 후 기록 확인 |
| 409 | `MESSAGE_ID_CONFLICT` | 같은 ID를 다른 내용에 재사용함 |
| 422 | `INVALID_INPUT` | 형식·길이·필수값 오류 |
| 422 | `CONTEXT_TOO_LARGE` | 모델에 보낼 입력이 너무 큼. 질문 축약 안내 |
| 429 | `AI_RATE_LIMITED` | Gemini 호출 한도 또는 할당량 초과. 자동 재시도 없이 안내 |
| 500 | `DB_ERROR` | DB 읽기·쓰기 실패. 성공 안내 금지 |
| 500 | `INTERNAL_ERROR` | 예상하지 못한 서버 오류 |
| 502 | `AI_UNAVAILABLE` | AI 연결·처리 실패 또는 잘못된 AI 응답 |
| 503 | `REQUEST_INTERRUPTED` | 서버 중단 등으로 기존 처리가 완료되지 못함 |
| 503 | `AI_CONFIG_ERROR` | Gemini 키·권한·모델 설정 오류. 로그인 화면으로 보내지 않음 |
| 504 | `AI_TIMEOUT` | AI 처리 제한 시간 초과 |

처리 중인 턴의 상태가 변할 때까지 기록을 확인하는 경우 2초 간격으로 조회하고, 화면 이탈·로그아웃 시 중단한다. 일반 전송 성공 경로에서는 주기적 조회가 필요 없다.

## 8. 최소 DB 구조

별도 ERD 작성 시 아래 관계를 기준으로 한다. 외래 키 검사를 활성화하고, 비밀번호·토큰 원문은 저장하지 않는다.

| 테이블 | 주요 필드 및 제약 |
| --- | --- |
| `users` | `id`, `email UNIQUE`, `password_hash`, `created_at` |
| `auth_sessions` | `id`, `user_id → users.id`, `token_hash UNIQUE`, `expires_at`, `revoked_at`, `created_at` |
| `personas` | `id`, `name`, `description`, `system_prompt`, `is_active`, `sort_order` |
| `conversations` | `id`, `user_id → users.id`, `persona_id → personas.id`, `created_at`, `updated_at` |
| `chat_turns` | `conversation_id → conversations.id`, `id`, `sequence`, `status`, `question`, `answer`(null 허용), `error_code`(null 허용), `created_at`, `completed_at`(null 허용) |

`chat_turns`에는 `(conversation_id, id)` 복합 기본 키, `(conversation_id, sequence)` 고유 제약, `status='processing'`인 행의 `conversation_id`에 부분 고유 인덱스를 둔다. 상태값은 `processing / completed / failed`로 제한한다. 사용자 식별은 `chat_turns → conversations → users` 관계로 추적한다.

위인 데이터는 MVP 운영 중 고정한다. 관리용 수정 API는 만들지 않는다. 기존 대화에 연결된 위인 데이터는 삭제하지 않는다.

### 평가용 DB 확인 예시

1. 계정 A로 로그인한다.
2. 위인을 선택해 새 대화를 만들고 질문한다.
3. `GET /api/v1/conversations`에서 대화 ID를 확인한다.
4. `GET /api/v1/conversations/{id}/turns`로 질문·답변·생성 시각을 확인한다.
5. 계정 B의 토큰으로 같은 기록에 접근해 `404`를 확인한다.
6. 서버 재시작 후 A로 다시 로그인하여 기존 기록이 남아 있는지 확인한다.

## 9. Gradio 화면과 API 연결

| 사용자 행동 | 프론트 동작 | API |
| --- | --- | --- |
| 회원가입 | 입력 검증, 성공 후 로그인 화면 | `POST /auth/signup` |
| 로그인 | 토큰을 사용자 상태에 저장, 위인·대화 목록 로딩 | `POST /auth/login`, `GET /personas`, `GET /conversations` |
| 새 대화 | 위인 선택 후 생성, 대화 ID 저장, 채팅 영역 초기화 | `POST /conversations` |
| 이전 대화 선택 | 위인 표시 고정, 기록 전체를 페이지 단위로 로딩 | `GET /conversations/{id}/turns` |
| 질문 전송 | UUID 생성, 사용자 질문 임시 표시, 입력·위인 변경·새 대화 버튼 잠금 | `POST /conversations/{id}/turns` |
| 답변 성공 | 임시 질문을 저장된 턴으로 확정, AI 답변 표시, 입력 복구 | 전송 응답 사용 |
| 실패 | 질문 입력 보존, 오류 안내, 명시적 재시도 제공 | 필요 시 기록 조회 |
| 응답 유실 | 해당 ID의 기록 확인. 처리 중이면 상태 확인, 완료면 기존 답변 표시 | `GET /conversations/{id}/turns` |
| 로그아웃 | 서버 세션 폐기 후 사용자 상태와 채팅 화면 초기화 | `POST /auth/logout` |

위 표의 경로 앞에는 모두 `/api/v1`가 붙는다. HTTP 요청의 인증 헤더는 각 콜백에서 해당 사용자의 토큰으로 만든다.

진행 중 사용자가 로그아웃하거나 다른 대화로 이동했다면, 늦게 도착한 응답을 현재 화면에 표시하지 않는다. 응답의 대화 ID와 현재 로그인 세션을 확인한다. 사용자 입력과 모델 출력의 임의 HTML·스크립트가 실행되지 않도록 표시한다.

## 10. 운영 로그와 환경 변수

### 로그 이벤트

```text
INFO request_received request_id=req_9f4c1d path=/api/v1/conversations/34/turns
INFO db_save_success request_id=req_9f4c1d user_id=12 conversation_id=34 turn_id=... status=processing
INFO ai_call_start request_id=req_9f4c1d user_id=12 conversation_id=34 turn_id=...
INFO ai_call_success request_id=req_9f4c1d latency_ms=5000
INFO db_save_success request_id=req_9f4c1d user_id=12 conversation_id=34 turn_id=... status=completed
```

실패 이벤트는 `ai_call_failure`, `db_save_failure`로 기록하고 안전한 오류 코드와 소요 시간을 남긴다. 인증 전에 사용자 ID를 추측해 기록하지 않는다. 질문·답변 원문은 DB에 저장하며 운영 로그에는 기본적으로 출력하지 않는다. 토큰·비밀번호·키는 기록하지 않는다.

### 환경 변수 이름 제안

| 이름 | 용도 |
| --- | --- |
| `DATABASE_URL` | SQLite 연결 경로 |
| `BACKEND_BASE_URL` | Gradio 콜백이 호출하는 FastAPI 기본 주소 |
| `GEMINI_API_KEY` | FastAPI에서만 읽는 Gemini API 키, 필수·비밀값 |
| `GEMINI_MODEL` | 사용 권한과 동작을 확인한 Gemini 모델 ID, 필수 |
| `GEMINI_TIMEOUT_SECONDS` | 백엔드에서 적용하는 전체 호출 제한, 기본 30 |
| `GEMINI_MAX_OUTPUT_TOKENS` | 모델에 맞춘 출력 예산 |
| `SESSION_TTL_SECONDS` | 기본 43200 |
| `LOG_LEVEL` | 운영 로그 수준 |

실제 값은 `.env`에 설정하고 Git에서 제외한다. `.env.example`에는 변수 이름과 비밀이 아닌 예시만 제공한다. DB 파일도 Git에서 제외하고 배포 시 영속 저장 경로를 사용한다. 실행·배포 명령은 구현 후 README에 별도로 작성한다.

로컬 AI 서버 주소용 `AI_BASE_URL`은 사용하지 않는다. `GEMINI_MODEL`, `GEMINI_TIMEOUT_SECONDS`, `GEMINI_MAX_OUTPUT_TOKENS`는 이 프로젝트가 읽어 SDK 설정으로 전달하는 변수이며 SDK가 자동으로 적용하는 설정은 아니다. 모델 ID와 출력 예산은 코드에 임의의 기본값을 넣기 전에 팀에서 확정한다. 분리 배포 시 Gradio 서비스에는 Gemini 키를 주입하지 않는다.

## 11. 구현 완료 확인 기준

- [ ] 가입 후 로그인할 수 있고, 응답에 비밀번호·해시가 없다.
- [ ] 비로그인·만료 토큰으로 대화 기능을 호출하면 401이다.
- [ ] 로그아웃한 토큰은 다시 사용할 수 없다.
- [ ] 두 사용자 또는 두 브라우저의 토큰·대화 화면이 섞이지 않는다.
- [ ] 새 대화의 위인이 고정되고 기존 기록은 유지된다.
- [ ] 첫 질문 후 후속 질문에서 같은 대화의 문맥을 활용한다.
- [ ] 다른 대화·사용자의 내용은 AI 문맥에 포함되지 않는다.
- [ ] 빈 입력·공백·길이 초과를 프론트와 백엔드에서 처리한다.
- [ ] AI 실패·30초 초과 상황에서 오류를 표시하며 서버가 계속 동작한다.
- [ ] Gemini 429와 키·권한 오류를 각각 호출 제한·서비스 설정 오류로 안내한다.
- [ ] Gemini 키가 프론트 코드·화면·API 응답·로그·Git에 노출되지 않는다.
- [ ] DB 저장 실패를 성공처럼 표시하지 않는다.
- [ ] 같은 질문 ID를 재전송해도 완료된 AI 호출을 반복하지 않는다.
- [ ] 같은 대화의 동시 질문을 처리 규칙대로 제한한다.
- [ ] 서버 중단 후 남은 processing 턴이 정리되고 다시 질문할 수 있다.
- [ ] 기록이 페이지 순서대로 표시되고 재접속 후에도 유지된다.
- [ ] 타인 대화에 대한 질문·조회가 모두 404다.
- [ ] 요청·AI 호출·DB 저장 성공 및 실패를 요청 ID로 추적할 수 있다.
- [ ] 배포 주소에서 Gradio 로그인부터 질문·기록 조회까지 동작한다.
- [ ] 재배포 후 SQLite 기록이 유지되고 배포 서버에서 Gemini 호출이 가능하다.
- [ ] 실제 FastAPI `/docs`의 요청·응답·오류 명세가 이 문서와 일치한다.

## 12. 팀에서 확정할 항목과 참고 문서

API의 기본 계약은 위 내용으로 구현할 수 있다. 아래 내용은 연결 대상과 배포 환경에 맞춰 확정한다.

| 항목 | 현재 설계 | 확정 담당 |
| --- | --- | --- |
| 위인 목록·설명·프롬프트 | 소크라테스는 예시. 팀이 내용 합의 후 초기 데이터·프롬프트 반영 | 이진걸 + 이재훈, 팀 검토 |
| Gemini 모델·컨텍스트·출력 예산 | 실제 사용 가능한 모델 선택 및 호출 검증 필요 | 이진걸 |
| Gemini 프로젝트·키·할당량·비용 | 배포 환경에 키 주입, 사용량·예산 기준 합의 필요 | 권순형 + 이진걸 |
| AI 지연 | 전체 호출 30초 가정, 실측 후 필요 시 관련 제한값 함께 변경 | 이진걸 + 권순형 |
| DB 함수와 트랜잭션 경계 | 8절 스키마를 기준으로 인터페이스 합의 | 이재훈 + 이진걸 |
| 외부 URL·Gradio 배포 경로·DB 영속 경로 | 배포 환경에서 설정 | 권순형, 임준현·이재훈 연동 확인 |
| 전체 흐름 시험 총괄 | 아직 미정. 각자 담당 기능의 시험은 수행 | 팀에서 지정 |

그 밖의 브랜치·PR·개인별 유의미한 커밋 10회 이상·역할 기록은 원래 요구사항대로 관리한다. 이 API 문서가 배포 가이드와 전체 프로젝트 문서를 대신하지는 않는다.

참고한 공식 문서:

- [Gradio State: 사용자 세션 상태](https://www.gradio.app/docs/gradio/state)
- [Gradio Interface State: 전역 상태와 사용자 상태 구분](https://www.gradio.app/guides/interface-state)
- [FastAPI Security: HTTP Bearer 인증](https://fastapi.tiangolo.com/tutorial/security/)
- [FastAPI Handling Errors: 공통 오류 처리](https://fastapi.tiangolo.com/tutorial/handling-errors/)
- [Gemini API 공식 SDK](https://ai.google.dev/gemini-api/docs/libraries)
- [Gemini API 키 관리](https://ai.google.dev/gemini-api/docs/api-key)
- [Gemini API 오류 처리](https://ai.google.dev/gemini-api/docs/troubleshooting)

위 문서는 프레임워크 동작 참고 자료다. 이 설계의 엔드포인트·상태값·제한값은 프로젝트용으로 정한 것이다.
