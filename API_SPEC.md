# 위인 챗봇 API 설계서

- 버전: 0.3 (구현 전 설계 초안)
- 작성일: 2026-09-17
- 대상: Gradio 프론트엔드, FastAPI 백엔드, SQLite, 라즈베리파이의 로컬 LLM
- 기준: [프로젝트 요구사항](requirement.md), 2026-09-14 회의록, 이후 합의한 챗봇 방식·로컬 LLM 사용·팀 역할 분담
- 이 문서의 URL, 필드, 제한값, 인증 방식은 구현을 위한 제안이다. 아직 구현되거나 검증된 API는 아니다.

### 0.3 변경 사항

- AI 응답은 라즈베리파이에서 실행하는 로컬 LLM으로 생성한다. 외부 생성형 AI 서비스로 자동 전환하지 않는다.
- 이재훈(로컬 LLM·라즈베리파이), 권순형(클라우드 및 배포), 이진걸(백엔드 및 DB), 임준현(프론트)으로 담당을 반영했다.
- 프론트용 API 10개와 별도로 백엔드 ↔ 라즈베리파이 내부 API 2개를 정의했다.
- 생성 시간, 동시 처리, 장비 연결 장애, 모델 준비 상태, 서비스 간 인증 규칙을 보완했다.
- 기존 프론트용 API 경로와 정상 응답 형식은 유지한다. 공급자 할당량 오류를 제거하고 `AI_BUSY`, `AI_NOT_READY`를 추가했다.

## 1. 서비스 범위

로그인한 사용자가 위인을 선택하고 고민을 입력하면, 해당 위인의 관점을 반영한 AI 답변을 받는다. 같은 대화에서는 앞선 질문·답변을 활용하며, 사용자는 자신의 대화 기록을 다시 열 수 있다.

### 이번 MVP에 포함

- 회원가입, 로그인, 로그아웃, 내 정보 확인
- 위인 목록 조회 및 위인을 지정한 새 대화 생성
- 질문 전송, AI 답변 표시, 대화 기록 저장·조회
- 빈 입력·길이 검증, AI 오류·시간 초과 안내, 운영 로그
- Gradio 단일 UI 및 외부 접속 가능한 배포
- 라즈베리파이 로컬 LLM 및 클라우드 백엔드와의 내부 HTTP API 연동

### 이번 MVP에서 제외

- 게시판, 공개 게시글, 댓글, 게시글 수정·삭제
- React, 소셜 로그인, 비밀번호 재설정, 파일 첨부
- 답변 스트리밍, 프론트에 노출되는 작업 큐 API
- 대화 삭제 및 기존 대화의 위인 변경
- 별도 AI 분류 서비스, 영속 작업 큐, 모델 학습·파인튜닝

한 대화에는 위인 한 명을 고정한다. 위인을 바꾸려면 새 대화를 만든다. 새 대화를 만들어도 이전 기록은 유지한다. AI 답변은 생성이 끝난 뒤 한 번에 표시한다.

## 2. 전체 연결 구조와 담당 범위

```text
사용자 브라우저
    ↕ Gradio 화면 및 이벤트
Gradio Python 콜백
    ↕ HTTP JSON + 사용자별 Bearer 토큰
클라우드 FastAPI /api/v1
    ├─ SQLite: 사용자, 인증 세션, 위인, 대화, 질문·답변
    └─ 비공개 연결 + 서비스 토큰
           ↕ 내부 HTTP JSON API
       라즈베리파이 LLM API /internal/v1
           └─ 추론 런타임 + 로컬 모델 파일
```

Gradio와 서비스 FastAPI는 동일 클라우드 서버에 배포할 수 있다. Gradio의 서버 측 콜백이 서비스 FastAPI를 HTTP로 호출하고, 서비스 FastAPI가 라즈베리파이의 LLM API를 호출한다. SQLite는 서비스 백엔드 측 영속 디스크에 둔다. 라즈베리파이는 사용자 계정이나 대화 DB를 직접 조회하지 않는다.

서비스 API와 라즈베리파이 내부 API는 서로 다른 실행 위치와 인증 체계를 가진다. 내부 API는 이재훈이 구현하는 얇은 래퍼를 기준으로 하며, 모델 실행기의 고유 요청 형식을 이 문서의 형식으로 변환한다. Gradio는 라즈베리파이 주소·서비스 토큰을 사용하지 않는다. 동일 프로세스 내 HTTP 호출이나 장시간 추론 대기를 구현할 때 이벤트 루프를 막지 않도록 비동기 처리를 사용한다.

| 담당자 | 확정 역할 | 해당 역할의 세부 작업안 |
| --- | --- | --- |
| 이재훈 | 로컬 LLM(라즈베리파이) | 모델·양자화·추론 런타임 선정, 모델 로딩, 내부 LLM API, 생성 중단·동시 처리 제어, 성능·응답 품질 확인 |
| 권순형 | 클라우드 및 배포 | 외부 URL·HTTPS, 클라우드 ↔ 라즈베리파이 연결, 서비스 토큰 주입, 프로세스 운영, SQLite 영속 저장·백업 |
| 이진걸 | 백엔드 및 DB | 서비스 FastAPI, 인증·소유권·검증, SQLite 스키마·저장·조회, 위인 프롬프트·문맥 구성, 내부 LLM API 호출, 오류 처리, API 명세·DB 확인 가이드 |
| 임준현 | 프론트 | Gradio 화면, 사용자별 상태, API 호출, 로딩·오류 안내, 기록 표시, 화면 흐름 확인 |

역할명과 담당자는 확정 사항이며, 세부 작업안은 중복 구현을 줄이기 위한 제안이다. 회원·대화 데이터의 설계와 저장은 이진걸이 맡고, 모델 실행과 라즈베리파이 내부 API는 이재훈이 맡는다.

### 2.1 담당자 간 연결 지점

- 이재훈 ↔ 이진걸: 6.4절 내부 API, 모델 별칭, 입력 문맥 한도, 출력 예산, 타임아웃, 오류 코드를 맞춘다. 위인 프롬프트는 이진걸이 관리하고 이재훈이 모델 적합성을 함께 검토한다.
- 이진걸 ↔ 임준현: 5절의 JSON·인증 헤더·오류 코드를 기준으로 연결한다. 프론트는 모델 실행기의 응답 원문이 아닌 서비스 FastAPI 응답만 처리한다.
- 이재훈 ↔ 권순형: 라즈베리파이 접근 주소, 사설망/VPN/보안 터널, 접근 허용 범위, 서비스 인증, 모델 파일 배치·프로세스 재시작 방법을 맞춘다.
- 이진걸 ↔ 권순형: SQLite 경로·파일 권한·백업·재배포 시 보존 방법과 프록시 대기 시간을 맞춘다. MVP는 영속 디스크를 가진 단일 서비스 백엔드 인스턴스를 기준으로 한다.
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
| 비밀정보 | 실제 비밀번호, 사용자 토큰, LLM 서비스 토큰을 문서·로그에 기록하지 않음. 예시는 무효한 설명용 값만 사용 |

아래 예시 ID와 시각은 설명용이다. 로그인 토큰 예시는 실제 토큰이 아니다. 오류 본문은 7절을 공통 적용한다.

### 3.1 인증 설계

Gradio 콜백에서 명시적으로 전달하기 쉬운 Bearer 토큰 방식을 사용한다. 이 절의 토큰은 사용자 로그인용이며, 라즈베리파이 연결용 서비스 토큰과 구분한다.

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

이 표는 Gradio가 호출하는 서비스 API다. 라즈베리파이 내부 API는 6.4절에 별도로 정의한다.

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
- 대표 오류: `401 AUTH_REQUIRED`, `404 CONVERSATION_NOT_FOUND`, `409 CONVERSATION_BUSY`, `409 MESSAGE_IN_PROGRESS`, `409 MESSAGE_ID_CONFLICT`, `422 INVALID_INPUT`, `422 CONTEXT_TOO_LARGE`, `502 AI_UNAVAILABLE`, `503 AI_BUSY`, `503 AI_NOT_READY`, `503 AI_CONFIG_ERROR`, `504 AI_TIMEOUT`, `500 DB_ERROR`.

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

이 규칙은 정상 처리와 완료된 결과의 재요청 중복을 방지한다. 서버 중단·라즈베리파이 측 처리 지속까지 포함한 추론 호출의 정확히 한 번 실행을 보장하지는 않는다. 서비스 백엔드에서 내부 LLM 요청을 자동 재전송하지 않는다.

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

서비스 FastAPI 프로세스가 응답하는지만 확인한다. DB·라즈베리파이 연결·모델 준비 상태를 보장하는 점검은 아니다. 비밀정보와 내부 주소를 노출하지 않으며, 상태 확인을 위해 추론을 실행하지 않는다. 라즈베리파이의 준비 상태는 6.4절 내부 API로 따로 확인한다.

## 6. 대화 처리와 저장 규칙

### 6.1 질문 처리 순서

1. 요청 ID를 생성하고 요청 수신 로그를 남긴다.
2. 토큰, 대화 소유권, 입력을 검사한다.
3. 짧은 DB 트랜잭션으로 중복 ID와 처리 중 턴을 검사하고 `processing` 턴을 저장한다. 실패하면 AI를 호출하지 않는다.
4. 같은 대화의 완료된 턴으로 문맥을 만들고 AI 호출 시작 로그를 남긴다. 라즈베리파이 내부 API를 한 번 호출한다.
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
- 이재훈은 실제 모델의 토크나이저·채팅 템플릿·컨텍스트 한도를 이진걸과 공유한다. 백엔드와 내부 LLM API는 같은 계산 기준을 사용하고, 내부 API에서도 최종 토큰 수를 검사한다.
- 모델 입력 한도 계산에는 채팅 템플릿과 특수 토큰을 포함한다. 출력 예산은 우선 256토큰, 요청 상한은 512토큰을 제안하며 라즈베리파이 성능 측정 후 확정한다. 문서의 2,000자 제한만으로 토큰 한도 준수를 보장하지 않는다.

### 6.3 타임아웃과 중단 복구

다음 값은 라즈베리파이 실측 전 제안이며 성능 보장이 아니다. 모델·출력 길이에 따라 조정하되 아래 대기 시간의 순서를 유지한다.

| 구간 | 초기 제안 | 적용 담당 |
| --- | --- | --- |
| 백엔드 → 라즈베리파이 연결 수립 | 최대 5초 | 이진걸 |
| 라즈베리파이 요청 접수 후 토큰 검사·추론·응답 생성 전체 | 최대 120초 | 이재훈 |
| 백엔드 → 라즈베리파이 전체 HTTP 호출 | 최대 130초, 연결 시간 포함 | 이진걸 |
| 서비스 FastAPI 턴 처리 | 최대 145초, DB 처리 포함 | 이진걸 |
| Gradio → 서비스 FastAPI HTTP 대기 | 최대 160초 | 임준현 |
| 배포 프록시 응답 대기 | 180초 이상 | 권순형 |
| 미완료 processing 정리 기준 | 생성 후 210초 | 이진걸 |

- `연결 제한 < LLM 처리 제한 < 내부 호출 제한 < 백엔드 턴 제한 < 프론트 대기 < 프록시 대기 < 미완료 정리 기준`을 지킨다. 한 값을 늘리면 관련 설정도 함께 검토한다.
- 개별 소켓 읽기 타임아웃만으로 전체 호출 시간을 제한했다고 보지 않는다. 백엔드와 라즈베리파이는 각각 전체 처리 시간을 제어한다.
- 서비스 FastAPI의 전체 턴 처리 제한을 넘으면 `504 AI_TIMEOUT`으로 실패 처리한다. DB 오류가 이미 확인되었거나 실패 상태 저장 자체가 불가능한 경우에는 `500 DB_ERROR`를 반환한다. 정리·오류 응답을 위한 짧은 시간도 전체 제한 안에 확보한다.
- 자동 AI 재시도는 MVP에서 하지 않는다.
- 서버 중단 등으로 `processing`이 남으면, 서버 시작 시와 이후 10초 간격의 정리 작업에서 생성 후 210초가 지난 턴을 `failed / REQUEST_INTERRUPTED`로 전환한다.
- 늦게 도착한 AI 결과는 해당 턴이 여전히 `processing`일 때만 저장한다. 이미 실패 처리된 턴을 다시 완료로 바꾸지 않는다.
- AI 답변을 받았어도 DB 저장이 실패하면 성공 응답을 주지 않고 `500 DB_ERROR`를 반환한다. 실패 상태 기록도 불가능하면 서버 로그를 남기고 복구 시 위 정리 규칙을 적용한다.
- 네트워크 연결이 끊겼다는 이유만으로 백엔드·AI 처리가 취소되었다고 간주하지 않는다. 재접속 후 저장 기록을 확인한다.
- 라즈베리파이는 처리 제한에 도달하면 추론 중단을 시도한다. 실제 중단 전에는 동시 처리 슬롯을 해제하지 않는다. 런타임 중단이 안 되면 모델 작업자를 재시작하고 모델 준비가 끝날 때까지 `LLM_NOT_READY`를 반환한다.

### 6.4 라즈베리파이 내부 LLM API

이 절은 이재훈과 이진걸이 함께 구현할 **프로젝트 자체 규격**이다. 특정 모델 런타임에서 기본 제공하는 API라고 가정하지 않는다. 예를 들어 llama.cpp의 HTTP 서버를 선택하면 이재훈이 내부 래퍼에서 해당 런타임 요청·응답을 변환한다. 실행기와 모델은 아직 미정이다. [llama.cpp 서버 공식 문서](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md)

#### 공통 연결·인증

- 호출자: 서비스 FastAPI. 브라우저·Gradio에서 직접 호출하지 않는다.
- 기본 주소: 서버 설정 `LLM_BASE_URL`. 요청 본문으로 주소를 받지 않는다.
- 인증: `Authorization: Bearer <LLM_SERVICE_TOKEN>`. 두 서버에 같은 비밀값을 주입한다. 사용자 로그인용 토큰과 별개다.
- 형식: `Content-Type: application/json`. 두 내부 API 모두 인증이 필요하다.
- 서비스 백엔드의 `X-Request-ID`를 그대로 전달하고 라즈베리파이가 응답 헤더와 로그에 같은 값을 사용한다. 이 헤더는 추적용이며 인증·중복 방지 수단은 아니다.
- 클라우드와 라즈베리파이의 연결은 접근 제한된 사설망·암호화 VPN·보안 터널을 사용한다. VPN 외부 구간에서 직접 HTTP를 노출하지 않으며, HTTPS를 쓰면 인증서를 검증한다.
- 집·교실의 라즈베리파이와 클라우드는 같은 LAN이라고 가정하지 않는다. 실제 연결 방식·주소·방화벽·재연결 방법은 권순형과 이재훈이 확정한다.
- 원시 모델 실행기 포트는 래퍼에서만 접근하게 제한하고, 인증 없는 추론 서비스를 인터넷에 공개하지 않는다.

| 기능 | 메서드 | 내부 경로 | 성공 코드 |
| --- | --- | --- | --- |
| 답변 생성 | POST | `/internal/v1/generate` | 200 |
| 모델 준비 상태 | GET | `/internal/v1/health` | 200 |

#### 답변 생성

`POST /internal/v1/generate`

요청 예시:

```json
{
  "model": "local-advisor",
  "system_prompt": "소크라테스의 관점을 참고해 사용자가 고민을 성찰하도록 한국어로 답하세요.",
  "messages": [
    {"role": "user", "content": "진로를 결정하기 어려워요."},
    {"role": "assistant", "content": "어떤 일을 할 때 의미를 느끼나요?"},
    {"role": "user", "content": "사람들을 도울 때 의미를 느껴요."}
  ],
  "max_output_tokens": 256,
  "temperature": 0.7
}
```

`local-advisor`는 배포 시 실제 모델 파일에 연결할 논리적 별칭 예시다. 모델 제품명·크기·양자화 형식을 확정한 값이 아니다.

| 필드 | 검증 규칙 |
| --- | --- |
| `model` | 필수 문자열. 서버가 로드하도록 설정한 모델 별칭과 일치해야 함 |
| `system_prompt` | 필수 비어 있지 않은 문자열. 서비스 백엔드가 DB에서 구성 |
| `messages` | 필수 배열. 최근 최대 5개 완료 턴 + 현재 질문으로 1~11개, user/assistant 교대, 첫·마지막 역할은 user |
| `messages[].role` | `user` 또는 `assistant`. 시스템 지시는 별도 필드로 전달 |
| `messages[].content` | 비어 있지 않은 문자열. 합산 토큰 수로 최종 제한 |
| `max_output_tokens` | 필수 정수, 초기 제안 1~512. 실제 모델 한도를 추가 검사 |
| `temperature` | 필수 숫자, 0~1. 초기 제안 0.7 |

알 수 없는 필드는 거부한다. 입력+출력 예산이 실제 로드한 컨텍스트 크기를 넘으면 `422 LLM_CONTEXT_TOO_LARGE`를 반환한다. 요청 단위로 입력·생성 상태를 분리하고, 이전 사용자의 대화 상태를 재사용하지 않는다. 모델은 미리 로드하며 요청마다 다른 모델을 다운로드하거나 바꾸지 않는다.

응답 `200 OK`:

```json
{
  "model": "local-advisor",
  "answer": "사람을 돕는 여러 활동 중 어떤 경험이 특히 기억에 남는지 떠올려 보세요.",
  "finish_reason": "stop",
  "usage": {
    "input_tokens": 120,
    "output_tokens": 40
  },
  "latency_ms": 18000
}
```

- `answer`는 비어 있지 않은 최종 텍스트다. 스트리밍은 사용하지 않는다.
- `finish_reason`은 `stop`(정상 종료) 또는 `length`(출력 예산 도달)다. `length`도 비어 있지 않은 텍스트라면 성공으로 저장하며 자동 이어쓰기는 하지 않는다.
- `usage`는 실제 토크나이저 값이며, 실행기에서 제공하지 못하면 객체 전체를 `null`로 반환한다. 임의 수치를 만들지 않는다.
- `latency_ms`는 라즈베리파이에서 측정한 처리 시간의 정수 밀리초다. 예시 수치는 성능 측정 결과가 아니다.
- 백엔드는 내부 응답을 검사하고 `answer`를 5.8절 응답으로 변환한다. 실행기 응답·사용량·내부 주소는 프론트 계약에 추가하지 않는다.

#### 모델 준비 상태

`GET /internal/v1/health` — 요청 본문 없음.

모델 로드와 설정 검증이 완료되었을 때 `200 OK`:

```json
{
  "status": "ready",
  "model": "local-advisor",
  "active_requests": 0,
  "max_concurrency": 1
}
```

모델이 로드되어 한 요청을 처리 중이어도 `ready`이며 `active_requests=1`이다. 이때 새 생성 요청은 `LLM_BUSY`로 거부할 수 있다. 아직 로딩 중이거나 모델 작업자가 중단되었으면 `503 LLM_NOT_READY`를 반환한다. 점검 요청 자체는 추론을 수행하지 않고 추론 슬롯을 소비하지 않는다. 백엔드는 health 성공 직후라도 generate 실패를 별도로 처리한다.

#### 동시 처리와 대기열

- 초기 설정은 라즈베리파이 전체에서 동시 추론 1건, 대기열 0건이다. 각 대화마다 1건이라는 뜻이 아니다.
- 슬롯은 원자적으로 획득한다. 여러 래퍼 작업자를 실행해 각각 1건씩 모델을 호출하지 않도록 단일 추론 관리자를 둔다.
- 사용 중이면 새 요청은 바로 `503 LLM_BUSY`와 `Retry-After: 5`를 반환한다. 대기 시간 5초는 완료 보장이 아니며 자동 재전송하지 않는다.
- 요청 취소·연결 끊김·시간 초과 때도 실제 추론이 종료되기 전에는 슬롯을 해제하지 않는다. 남은 추론이 새 요청과 겹치지 않게 한다.
- 작업 큐가 필요해지면 별도 비동기 작업 설계를 검토한다. 현재 동기 API에 무제한 대기를 추가하지 않는다.

#### 내부 오류와 서비스 API 변환

내부 오류 본문 예시:

```json
{
  "error": {
    "code": "LLM_BUSY",
    "message": "모델이 다른 요청을 처리 중입니다.",
    "details": []
  },
  "request_id": "req_9f4c1d"
}
```

| 내부 API 또는 네트워크 상황 | 프론트용 서비스 API 응답 | 처리 |
| --- | --- | --- |
| 401 `LLM_UNAUTHORIZED`: 서비스 토큰 누락·불일치 | `503 AI_CONFIG_ERROR` | 내부 인증 설정 확인. 사용자 로그인 만료로 처리하지 않음 |
| 422 `LLM_INVALID_INPUT`: 내부 요청 형식 오류 | `503 AI_CONFIG_ERROR` | 백엔드·LLM 담당자가 규격 확인 |
| 404 `LLM_MODEL_NOT_FOUND`: 모델 별칭 불일치 | `503 AI_CONFIG_ERROR` | 배포 모델 설정 확인 |
| 422 `LLM_CONTEXT_TOO_LARGE`: 문맥 초과 | `422 CONTEXT_TOO_LARGE` | 질문 축약 안내, 문맥 제한 설정 확인 |
| 503 `LLM_BUSY`: 추론 슬롯 사용 중 | `503 AI_BUSY` | 실패 턴 저장, 잠시 후 재시도 안내. `Retry-After: 5` 전달 |
| 503 `LLM_NOT_READY`: 로딩 중·작업자 중단 | `503 AI_NOT_READY` | 준비 중 또는 일시 중단 안내 |
| 504 `LLM_TIMEOUT` 또는 내부 HTTP 시간 초과 | `504 AI_TIMEOUT` | 시간 초과 안내 |
| 500 `LLM_INTERNAL_ERROR`, 연결 거부·이름 해석 실패·연결 종료, 응답 형식 오류·빈 답변 | `502 AI_UNAVAILABLE` | 장비·네트워크·모델 오류로 안내 |

표에 없는 내부 오류는 원문을 노출하지 않고 `502 AI_UNAVAILABLE`로 처리한다. 모든 실패는 DB 저장 가능 시 해당 턴을 `failed`로 기록하며, 저장 자체가 실패하면 `500 DB_ERROR`를 우선한다. 내부 API도 기본 검증·인증·예외 응답을 위 JSON 형식으로 맞춘다. 내부 401에는 `WWW-Authenticate: Bearer`를 포함한다.

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
- 스택 트레이스, SQL, 입력된 비밀번호, 내부 모델 응답 원문, 라즈베리파이 주소는 클라이언트에 반환하지 않는다.
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
| 500 | `DB_ERROR` | DB 읽기·쓰기 실패. 성공 안내 금지 |
| 500 | `INTERNAL_ERROR` | 예상하지 못한 서버 오류 |
| 502 | `AI_UNAVAILABLE` | AI 연결·처리 실패 또는 잘못된 AI 응답 |
| 503 | `REQUEST_INTERRUPTED` | 서버 중단 등으로 기존 처리가 완료되지 못함 |
| 503 | `AI_CONFIG_ERROR` | 내부 서비스 인증·모델 설정 오류. 로그인 화면으로 보내지 않음 |
| 503 | `AI_BUSY` | 라즈베리파이가 다른 질문 처리 중. 자동 재시도 없이 안내 |
| 503 | `AI_NOT_READY` | 로컬 모델 준비 중 또는 모델 작업자 중단 |
| 504 | `AI_TIMEOUT` | AI 처리 제한 시간 초과 |

처리 중인 턴의 상태가 변할 때까지 기록을 확인하는 경우 2초 간격으로 조회하고, 화면 이탈·로그아웃 시 중단한다. 일반 전송 성공 경로에서는 주기적 조회가 필요 없다.

## 8. 최소 DB 구조

별도 ERD 작성 시 아래 관계를 기준으로 한다. 외래 키 검사를 활성화하고, 비밀번호·토큰 원문은 저장하지 않는다.

DB 담당은 이진걸이다. 데이터베이스는 클라우드 서비스 백엔드 측에서 관리하며, 라즈베리파이 내부 API는 DB 계정이나 사용자 로그인 세션에 접근하지 않는다.

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
| 모델 사용 중·준비 중 | 로그인 상태와 채팅 화면을 유지하고 상태 안내. 사용자가 다시 요청할 때 새 질문 ID 생성 | `AI_BUSY` / `AI_NOT_READY` 오류 사용 |
| 응답 유실 | 해당 ID의 기록 확인. 처리 중이면 상태 확인, 완료면 기존 답변 표시 | `GET /conversations/{id}/turns` |
| 로그아웃 | 서버 세션 폐기 후 사용자 상태와 채팅 화면 초기화 | `POST /auth/logout` |

위 표의 경로 앞에는 모두 `/api/v1`가 붙는다. HTTP 요청의 인증 헤더는 각 콜백에서 해당 사용자의 토큰으로 만든다.

진행 중 사용자가 로그아웃하거나 다른 대화로 이동했다면, 늦게 도착한 응답을 현재 화면에 표시하지 않는다. 응답의 대화 ID와 현재 로그인 세션을 확인한다. 사용자 입력과 모델 출력의 임의 HTML·스크립트가 실행되지 않도록 표시한다.

로컬 LLM은 지연 시간이 아직 측정되지 않았으므로 응답 완료 예상 시간을 단정하지 않는다. 요청 중에는 “답변을 생성하고 있습니다”를 표시하고 중복 전송을 막는다. 프론트의 HTTP 대기 시간은 6.3절에 맞춘다. Gradio 이벤트 큐를 사용하더라도 다른 사용자의 로그인·기록 조회까지 장시간 추론 뒤에 묶이지 않게 구성한다.

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

라즈베리파이는 같은 `request_id`로 `llm_request_received`, `llm_inference_start`, `llm_inference_success/failure`, `llm_busy`, `model_ready/not_ready` 이벤트를 기록한다. 모델 이름, 처리 시간, 실제 토큰 수(제공 가능 시)를 남기되 질문 원문과 서비스 토큰은 기록하지 않는다. 장비 장애 때 권순형과 이재훈이 연결 로그와 모델 로그를 함께 확인한다.

### 환경 변수 이름 제안

| 이름 | 적용 위치 | 용도 / 초기 제안 |
| --- | --- | --- |
| `DATABASE_URL` | 백엔드 | SQLite 영속 파일 경로 |
| `BACKEND_BASE_URL` | Gradio | 서비스 FastAPI 기본 주소 |
| `BACKEND_TIMEOUT_SECONDS` | Gradio | 전체 HTTP 대기 160초 |
| `LLM_BASE_URL` | 백엔드 | 라즈베리파이 내부 API 주소. `/internal/v1` 앞의 기본 주소 |
| `LLM_SERVICE_TOKEN` | 백엔드 + 라즈베리파이 | 동일한 서비스 인증 토큰, 필수 비밀값 |
| `LLM_MODEL` | 백엔드 + 라즈베리파이 | 동일한 모델 별칭, 예: `local-advisor` |
| `LLM_MODEL_PATH` | 라즈베리파이 | 실제 로컬 모델 파일 위치. 실행기에 맞춰 적용 |
| `LLM_CONTEXT_TOKENS` | 백엔드 + 라즈베리파이 | 실제 로드한 컨텍스트 크기. 모델 및 메모리 측정 후 동일하게 확정 |
| `LLM_MAX_OUTPUT_TOKENS` | 백엔드 | 요청 출력 예산, 초기 256 |
| `LLM_OUTPUT_TOKEN_LIMIT` | 라즈베리파이 | 허용 출력 예산 상한, 초기 512 |
| `LLM_CONNECT_TIMEOUT_SECONDS` | 백엔드 | 연결 수립 제한 5초 |
| `LLM_TIMEOUT_SECONDS` | 백엔드 | 내부 HTTP 전체 호출 제한 130초 |
| `LLM_GENERATION_TIMEOUT_SECONDS` | 라즈베리파이 | 접수 후 토큰 검사·추론·응답 생성 전체 제한 120초 |
| `LLM_MAX_CONCURRENCY` | 라즈베리파이 | 초기 1, 대기열 없음 |
| `TURN_TIMEOUT_SECONDS` | 백엔드 | 턴 처리 상한 145초 |
| `TURN_STALE_SECONDS` | 백엔드 | 미완료 턴 정리 기준 210초 |
| `SESSION_TTL_SECONDS` | 백엔드 | 로그인 세션 유효기간 43200초 |
| `LOG_LEVEL` | 각 서비스 | 운영 로그 수준 |

실제 값은 `.env`에 설정하고 Git에서 제외한다. `.env.example`에는 변수 이름과 비밀이 아닌 예시만 제공한다. DB 파일도 Git에서 제외하고 배포 시 영속 저장 경로를 사용한다. 실행·배포 명령은 구현 후 README에 별도로 작성한다.

위 환경 변수는 이 프로젝트 코드가 읽어 적용하는 설정이며 모델 실행기가 자동으로 해석한다고 가정하지 않는다. 프록시의 180초 이상 대기는 권순형이 배포 설정에서 적용한다. 서비스 시작 시 필수 설정과 시간 제한의 순서를 검사한다. 모델 파일·컨텍스트 크기·실행기 버전은 이재훈이 실측 후 확정한다.

`LLM_SERVICE_TOKEN`은 최소 32바이트의 안전한 난수로 생성해 두 서버에 주입하고, 누락되면 내부 연동 설정 오류로 처리한다. 개발 PC·라즈베리파이·클라우드의 실제 주소·토큰 값은 이 문서에 적지 않는다. 분리 배포 시 Gradio 서비스에는 LLM 서비스 토큰을 주입하지 않는다.

## 11. 구현 완료 확인 기준

- [ ] 가입 후 로그인할 수 있고, 응답에 비밀번호·해시가 없다.
- [ ] 비로그인·만료 토큰으로 대화 기능을 호출하면 401이다.
- [ ] 로그아웃한 토큰은 다시 사용할 수 없다.
- [ ] 두 사용자 또는 두 브라우저의 토큰·대화 화면이 섞이지 않는다.
- [ ] 새 대화의 위인이 고정되고 기존 기록은 유지된다.
- [ ] 첫 질문 후 후속 질문에서 같은 대화의 문맥을 활용한다.
- [ ] 다른 대화·사용자의 내용은 AI 문맥에 포함되지 않는다.
- [ ] 빈 입력·공백·길이 초과를 프론트와 백엔드에서 처리한다.
- [ ] 설정된 LLM 처리 제한·내부 HTTP 제한을 넘으면 오류를 표시하며 서비스가 계속 동작한다.
- [ ] 라즈베리파이 사용 중·모델 준비 중·장비 연결 실패·서비스 토큰 오류를 구분해 안내한다.
- [ ] 서비스 토큰이 프론트 코드·화면·API 응답·로그·Git에 노출되지 않는다.
- [ ] 토큰 없는 내부 generate·health 요청은 모두 401로 거부한다.
- [ ] 서로 다른 대화에서 동시에 질문해도 라즈베리파이 전체 추론은 초기 설정대로 1건만 실행된다.
- [ ] 추론 시간 초과 후 실제 작업 종료·복구가 확인되기 전에 슬롯을 해제하지 않는다.
- [ ] DB 저장 실패를 성공처럼 표시하지 않는다.
- [ ] 같은 질문 ID를 재전송해도 완료된 AI 호출을 반복하지 않는다.
- [ ] 같은 대화의 동시 질문을 처리 규칙대로 제한한다.
- [ ] 서버 중단 후 남은 processing 턴이 정리되고 다시 질문할 수 있다.
- [ ] 기록이 페이지 순서대로 표시되고 재접속 후에도 유지된다.
- [ ] 타인 대화에 대한 질문·조회가 모두 404다.
- [ ] 요청·AI 호출·DB 저장 성공 및 실패를 요청 ID로 추적할 수 있다.
- [ ] 배포 주소에서 Gradio 로그인부터 질문·기록 조회까지 동작한다.
- [ ] 재배포 후 SQLite 기록이 유지되고 클라우드에서 라즈베리파이 내부 API 호출이 가능하다.
- [ ] 라즈베리파이 재시작·네트워크 단절 시 오류를 안내하고 연결·모델 복구 후 다시 질문할 수 있다.
- [ ] 실제 모델로 단일·동시 요청의 지연과 메모리를 측정해 모델·출력 예산·제한값을 확정한다.
- [ ] 실제 FastAPI `/docs`의 요청·응답·오류 명세가 이 문서와 일치한다.

## 12. 팀에서 확정할 항목과 참고 문서

API의 기본 계약은 위 내용으로 구현할 수 있다. 아래 내용은 연결 대상과 배포 환경에 맞춰 확정한다.

| 항목 | 현재 설계 | 확정 담당 |
| --- | --- | --- |
| 위인 목록·설명·프롬프트 | 소크라테스는 예시. 팀 합의 후 DB 초기 데이터·프롬프트 반영 | 이진걸, 이재훈 품질 검토 |
| 로컬 모델·양자화·실행기 | 라즈베리파이의 실제 RAM·OS·성능으로 선정. 모델 사용 조건 확인 | 이재훈 |
| 컨텍스트 크기·출력 예산 | 최근 최대 5턴, 출력 256·상한 512토큰 제안. 실측 후 확정 | 이재훈 + 이진걸 |
| 내부 LLM API | 6.4절 자체 규격 구현 및 두 서버 연결 시험 | 이재훈 + 이진걸 |
| 클라우드 ↔ 라즈베리파이 경로 | 사설망/VPN/보안 터널 방식·주소·접근 제어 확정 | 권순형 + 이재훈 |
| 동시 처리·시간 제한 | 동시 1건·대기열 없음, LLM 120초 등 초기 제안 검증 | 이재훈 + 이진걸 + 권순형, 임준현 프론트 설정 반영 |
| SQLite 스키마·저장·조회 | 8절을 기준으로 구현 및 DB 확인 가이드 작성 | 이진걸 |
| 외부 URL·Gradio 배포 경로·DB 영속 경로 | 배포 환경에서 설정 | 권순형, 임준현·이진걸 연동 확인 |
| 전체 흐름 시험 총괄 | 아직 미정. 각자 담당 기능의 시험은 수행 | 팀에서 지정 |

그 밖의 브랜치·PR·개인별 유의미한 커밋 10회 이상·역할 기록은 원래 요구사항대로 관리한다. 이 API 문서가 배포 가이드와 전체 프로젝트 문서를 대신하지는 않는다.

참고한 공식 문서:

- [Gradio State: 사용자 세션 상태](https://www.gradio.app/docs/gradio/state)
- [Gradio Interface State: 전역 상태와 사용자 상태 구분](https://www.gradio.app/guides/interface-state)
- [FastAPI Security: HTTP Bearer 인증](https://fastapi.tiangolo.com/tutorial/security/)
- [FastAPI Handling Errors: 공통 오류 처리](https://fastapi.tiangolo.com/tutorial/handling-errors/)
- [FastAPI HTTPS 배포](https://fastapi.tiangolo.com/deployment/https/)
- [llama.cpp HTTP 서버: 실행기 선택 시 참고](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md)

위 문서는 프레임워크 동작 참고 자료다. 이 설계의 엔드포인트·상태값·제한값은 프로젝트용으로 정한 것이다.
