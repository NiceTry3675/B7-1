# 시대를 넘어, 나누는 대화

로그인한 사용자가 위인을 선택하고 고민을 나누는 Gradio + FastAPI 서비스입니다.
학생·진로와 일상의 선택을 고민하는 사용자를 위한 프로젝트이며,
인증·사용자별 대화 기록·최근 문맥·오류 복구를 [API_SPEC.md](API_SPEC.md) v0.3에 맞춰 구현했습니다.

## 구현 범위

- 서비스 API 10개: 회원가입, 로그인, 로그아웃, 내 정보, 위인 목록, 새 대화, 대화 목록, 질문, 기록, 상태 확인
- Argon2id 비밀번호, 12시간 Bearer 세션, 토큰 해시 저장, 대화 소유권 검사
- SQLite WAL, 외래 키, 대화별 처리 중 턴 1개 제한, 질문 UUID 멱등성, 중단된 턴 정리
- 최근 완료된 5개 턴 문맥, 정확한 토크나이저 기반 입력 예산, LLM HTTP 연동·오류 변환
- Gradio 사용자별 상태, 모든 기록 페이지 조회, 결과 유실 복구, 수동 재시도, 중복 전송 차단
- Docker Compose, NGINX HTTPS·Certbot 인증서 관리, 영속 볼륨, 온라인 백업, CI와 자동 테스트

**로컬 LLM 실행, 모델 다운로드·로딩·추론, 라즈베리파이 내부 API 서버는 구현 대상에서 제외했습니다.**
외부 생성형 AI로 전환하거나 가짜 답변을 반환하지 않습니다. 연결 설정이 없으면 질문이
`failed / AI_CONFIG_ERROR`로 저장됩니다. 계정·대화·기록 기능은 사용할 수 있습니다.

초기 위인은 소크라테스·세종대왕·마리 퀴리입니다. 팀 확정 전 제안 데이터이며
`app/seed.py`에서 관리합니다. 시드는 처음 추가할 때만 적용하므로 기존 DB 프롬프트를
자동으로 덮어쓰지 않습니다. 실제 인물의 발언인 것처럼 표현하지 않도록 안내합니다.

## 로컬 실행 (Python 3.12+, uv)

WSL/Linux 기준이며 저장소 루트에서 실행합니다.

```bash
cp .env.example .env
uv sync --frozen --group dev
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1 --no-access-log
```

별도 터미널:

```bash
uv run python -m frontend.main
```

- 화면: <http://127.0.0.1:7860>
- API 문서: <http://127.0.0.1:8000/docs>
- 프로세스 확인: <http://127.0.0.1:8000/api/v1/health>

회원가입 후 같은 화면에서 로그인합니다. 위인을 골라 새 대화를 만들고 질문합니다.
LLM을 연결하기 전에는 설정 오류가 표시되는 것이 정상입니다. 새로고침 후에는 다시 로그인하며
DB의 이전 대화 목록을 불러옵니다. 로그아웃은 현재 토큰만 폐기합니다.

## 구조와 주요 파일

```text
브라우저 → Gradio :7860 → FastAPI :8000 → SQLite 영속 파일
                                      └→ 비공개 네트워크 → 라즈베리파이 내부 LLM API (별도 구현)
```

배포 시 브라우저는 NGINX의 HTTPS 주소로 접속합니다. NGINX는 화면 요청을 Gradio로,
`/api/v1/*`와 API 문서 요청을 FastAPI로 전달합니다. 인증서 최초 발급과 자동 갱신 등록은
[운영 가이드](docs/OPERATIONS.md#docker-compose-배포)를 따릅니다.

| 경로 | 역할 |
| --- | --- |
| `app/main.py` | 앱 생성·종료, 라우터 등록, 요청 추적, 공통 오류 처리 |
| `app/routers/auth.py` | 회원가입·로그인·로그아웃·내 정보 API |
| `app/routers/personas.py` | 위인 목록 API |
| `app/routers/conversations.py` | 대화 생성·목록·기록 조회, 질문·답변 처리 API |
| `app/routers/health.py` | 프로세스 상태 확인 API |
| `app/dependencies.py` | 공통 인증·DB·설정 의존성, 비밀번호·토큰 처리, 비동기 작업 실행 |
| `app/db.py` | 스키마, 트랜잭션, 시드, 멱등성, 중단 복구 |
| `app/llm.py` | 내부 HTTP 계약, 토큰 계산, 문맥 자르기, 오류 변환 |
| `frontend/main.py` | Gradio 화면과 이벤트 |
| `frontend/controller.py` | 사용자별 상태, 응답 유실 확인, 재시도 |
| `frontend/client.py` | 사용자별 Bearer 헤더, 전체 HTTP 제한 |
| `tests/` | API·동시성·LLM 계약·프론트 흐름 검증 |
| `deploy/nginx/` | NGINX 이미지, 인증서 유무에 따른 초기 기동·HTTPS 설정 |
| `scripts/manage_tls.py` | Certbot 최초 발급·갱신 및 NGINX 인증서 적용 |
| `docs/OPERATIONS.md` | 배포, 환경 변수, 백업·복원·장애 처리 |
| `docs/DB_GUIDE.md` | ERD와 DB 평가 방법 |

Gradio와 백엔드는 별도 프로세스이며 HTTP 비동기 호출을 사용합니다. 비밀번호 해싱·DB·토큰 계산은
이벤트 루프 밖에서 수행합니다. 추론을 기다리는 동안 SQLite 쓰기 트랜잭션을 유지하지 않습니다.
Gradio 토큰과 현재 대화는 `gr.State`에만 저장하고, HTTP 클라이언트에 공용 인증 헤더를 두지 않습니다.
로그인 세션과 대화가 바뀌면 이전 요청의 결과를 화면에 적용하지 않습니다.

## LLM 담당자에게 전달할 연결 계약

라즈베리파이는 명세 6.4절의 `POST /internal/v1/generate`, `GET /internal/v1/health`를 별도로 제공해야 합니다.
백엔드는 서비스 토큰과 `X-Request-ID`를 전달합니다. HTTP 요청은 자동 재시도하지 않습니다.

1. 사설망/VPN으로 접근할 주소를 `LLM_BASE_URL`에 설정합니다. `/internal/v1`은 붙이지 않습니다.
2. 최소 32바이트 난수로 생성한 서비스 토큰을 백엔드와 라즈베리파이에 동일하게 주입합니다.
3. 모델 별칭, 컨텍스트 크기, 출력 예산, 시간 제한을 양쪽에서 맞춥니다.
4. 실제 모델과 동일한 **토크나이저 파일 및 채팅 템플릿**을 백엔드에도 배치합니다.
   백엔드는 모델 가중치 없이 토큰 수만 계산합니다.

```bash
uv sync --frozen --group dev --extra tokenizer
# .env에 LLM_TOKENIZER_PATH=/절대/경로/토크나이저 설정
uv run python -m scripts.check_llm
```

토크나이저는 Transformers `apply_chat_template(..., tokenize=True, add_generation_prompt=True)`를
사용하고 네트워크 다운로드·원격 코드를 허용하지 않습니다. 라즈베리파이 래퍼도 같은 시스템 메시지
구조·템플릿·특수 토큰을 사용해야 합니다. 토크나이저/템플릿이 없거나 로드할 수 없으면
`AI_CONFIG_ERROR`로 처리합니다. 문자 수를 임의 토큰 수로 환산하지 않습니다.
다른 실행기가 별도 템플릿을 사용한다면 `ChatTokenCounter`를 실제 입력 직렬화에 맞춰 교체하고
양쪽 계산값을 비교해야 합니다. 내부 API의 최종 토큰 검사도 필요합니다.

## API 사용 예시

```bash
curl -s http://127.0.0.1:8000/api/v1/auth/signup \
  -H 'Content-Type: application/json' \
  -d '{"email":"student@example.com","password":"Example-only-password1!"}'

curl -s http://127.0.0.1:8000/api/v1/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"email":"student@example.com","password":"Example-only-password1!"}'

# 로그인 응답의 access_token을 터미널 변수 TOKEN에 설정한 뒤 사용합니다.
curl -s http://127.0.0.1:8000/api/v1/conversations \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"persona_id":"socrates"}'

# 응답에서 얻은 대화 ID를 사용합니다. UUID는 질문마다 새로 생성합니다.
curl -s http://127.0.0.1:8000/api/v1/conversations/1/turns \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"client_message_id":"53367fb2-4e2b-4dcf-89c2-2fc17df87b96","content":"진로가 고민이에요."}'
```

정상 응답·오류 JSON 전체는 [API_SPEC.md](API_SPEC.md)와 실행 중인 `/docs`에서 확인합니다.
성공 턴은 201, 완료된 같은 질문 ID의 재요청은 200입니다. 실패한 질문 ID는 저장된 오류를
돌려주며 AI를 재호출하지 않습니다. 실패가 확정된 뒤 사용자가 다시 요청할 때만 새 UUID를 만듭니다.

## 검증

```bash
uv run pytest -q
uv run ruff check .
uv run ruff format --check .
```

Docker Engine이 있는 환경에서는 실제 NGINX 컨테이너의 HTTP/HTTPS·라우팅·실시간 응답도 검사합니다.

```bash
RUN_PROXY_TESTS=1 uv run pytest -q tests/test_proxy.py
```

테스트는 임시 DB와 테스트 전용 LLM 응답을 사용하며, 실제 모델을 호출하지 않습니다.
검증 범위에는 두 사용자 격리, 만료·폐기 토큰, 동시 전송, 실패 재요청, 최근 문맥,
내부 오류 매핑, HTTP 전체 시간 제한, 응답 유실, 재시작 후 보존, 화면 구성 등이 포함됩니다.

실제 라즈베리파이 연결·성능 측정과 공인 도메인의 HTTPS 배포 확인은 대상 환경에서 수행해야 합니다.
실제 모델의 출력 품질·추론 슬롯 제어·중단은 이 구현의 테스트 결과에 포함되지 않습니다.

## 팀 역할 및 협업

| 담당자 | 명세상의 역할 | 이 구현에서 준비한 영역 |
| --- | --- | --- |
| 이재훈 | 로컬 LLM·라즈베리파이 | 제외. 내부 API 계약과 연결 가이드를 제공 |
| 권순형 | 클라우드·배포 | Compose, HTTPS 프록시, 영속 볼륨, 백업 절차 |
| 이진걸 | 백엔드·DB | API 10개, 인증, SQLite, 문맥·LLM 클라이언트, 테스트 |
| 임준현 | 프론트 | Gradio, 사용자 상태, 기록·실패·재시도 흐름 |

위 표는 담당 영역과 준비된 코드의 연결이며, 개인별 실제 기여·커밋 내역을 증명하지 않습니다.
각 담당자가 검토·통합한 내용을 PR에 기록하세요. `main`을 배포 브랜치로 유지하고
기능 브랜치 → PR → CI 통과 → 리뷰 후 병합 흐름을 권장합니다.
요구사항의 개인별 유의미한 커밋 10회와 PR 이력은 실제 팀 작업으로 충족해야 합니다.

구현 참고: [FastAPI 예외 처리](https://fastapi.tiangolo.com/tutorial/handling-errors/),
[FastAPI lifespan](https://fastapi.tiangolo.com/advanced/events/),
[Gradio Timer](https://www.gradio.app/docs/gradio/timer),
[Gradio Chatbot](https://www.gradio.app/docs/gradio/chatbot).
