# 실행·배포·복구 가이드

## 설정

`.env.example`을 `.env`로 복사하고 실제 값은 Git에 넣지 않습니다. Pydantic Settings는
프로세스 환경 변수를 `.env`보다 우선합니다. 프론트는 `UISettings`에 선언된 변수만 사용합니다.
분리 배포 시 프론트 컨테이너에는 LLM 서비스 토큰을 전달하지 않습니다.

| 변수 | 기본값 / 의미 |
| --- | --- |
| `DATABASE_URL` | `sqlite:///./data/chat.db`; 절대 경로는 `sqlite:////data/chat.db` |
| `SESSION_TTL_SECONDS` | 43200 |
| `BACKEND_BASE_URL` | `http://127.0.0.1:8000`; `/api/v1` 앞의 주소 |
| `BACKEND_TIMEOUT_SECONDS` | 160; 프론트의 전체 HTTP 호출 제한 |
| `GRADIO_SERVER_NAME`, `GRADIO_SERVER_PORT` | `127.0.0.1`, 7860 |
| `LLM_BASE_URL` | 미설정; 사설망의 라즈베리파이 기본 주소 |
| `LLM_SERVICE_TOKEN` | 미설정; 최소 32바이트 난수로 생성한 비밀 토큰 |
| `LLM_MODEL` | `local-advisor`; 양쪽에서 같은 별칭 사용 |
| `LLM_TOKENIZER_PATH` | 미설정; 실제 모델과 동일한 로컬 토크나이저·템플릿 경로 |
| `LLM_CONTEXT_TOKENS` | 4096; 제안값이므로 실제 모델과 맞출 것 |
| `LLM_MAX_OUTPUT_TOKENS` | 256; 1~512 |
| `LLM_CONNECT_TIMEOUT_SECONDS` | 5 |
| `LLM_GENERATION_TIMEOUT_SECONDS` | 120; 시간 순서 검증용. 실제 추론 제한은 래퍼가 적용 |
| `LLM_TIMEOUT_SECONDS` | 130; 백엔드의 내부 HTTP 전체 호출 제한 |
| `TURN_TIMEOUT_SECONDS` | 145; 마지막 저장·응답에 3초 예약 |
| `PROXY_TIMEOUT_SECONDS` | 180 이상; Compose가 Caddy에도 전달 |
| `TURN_STALE_SECONDS` | 210; 시작 시 및 10초마다 초과 턴 정리 |
| `LOG_LEVEL` | INFO |
| `SITE_ADDRESS` | Compose 전용; 외부 DNS 도메인 |
| `TOKENIZER_HOST_PATH` | Compose 전용; 호스트의 토크나이저 디렉터리 |

시간 제한은 `연결 < 생성 < 내부 HTTP < 턴 < 프론트 < 프록시 < 정리` 순서여야 합니다.
순서가 잘못되면 시작을 거부합니다. LLM 설정이 빠지거나 토크나이저를 로드할 수 없는 경우
계정·기록 서비스는 유지하고 질문 요청을 `AI_CONFIG_ERROR`로 실패 처리합니다.
운영 시 토큰은 비밀 관리 도구로 주입하세요. `.env`를 쓴다면 읽기 권한을 제한합니다.

## Docker Compose 배포

영속 디스크가 있는 단일 서버, Docker Engine + Compose, 서버에 연결된 도메인과 80/443 포트가 필요합니다.
라즈베리파이와 클라우드는 VPN 등 암호화된 비공개 경로로 연결합니다.
도메인·클라우드 계정·LLM 서버는 이 저장소에 포함되어 있지 않습니다.

```bash
cp .env.example .env
mkdir -p models/tokenizer
# .env의 SITE_ADDRESS, LLM_*와 TOKENIZER_HOST_PATH를 실제 값으로 설정합니다.
docker compose up -d --build
docker compose ps
docker compose logs --tail 100 backend frontend proxy
```

Caddy는 실제 도메인에 자동 HTTPS를 제공합니다. localhost 기본값은 공인 배포 주소가 아니며
로컬 인증서 신뢰 설정이 필요하므로 로컬 개발은 README의 8000/7860 실행을 사용합니다.
백엔드·프론트 포트는 호스트에 직접 공개하지 않습니다. 추론 API도 외부에 공개하지 않습니다.
Gradio 대화 요청과 큐 이벤트를 위해 프록시 대기를 180초 이상으로 설정했습니다.

컨테이너는 비관리자 계정으로 실행하며 SQLite는 `chat_data` 볼륨에 보존됩니다.
백엔드는 항상 `--workers 1`, 복제본 1개를 사용합니다. 재배포 시
`docker compose up -d --build`를 사용하며 **`docker compose down -v`는 DB까지 삭제하므로 사용하지 마세요.**

`/api/v1/health`와 Compose healthcheck는 프로세스 확인입니다. LLM의 준비 여부는 다음으로 별도 확인합니다.

```bash
docker compose exec backend python -m scripts.check_llm
```

이 점검은 인증된 `/internal/v1/health`만 호출하고 생성 슬롯을 사용하지 않습니다.
health 성공 후에도 generate는 `AI_BUSY` 등을 반환할 수 있습니다.

## 백업과 복원

실행 중인 SQLite 파일이나 WAL 파일을 단순 복사하지 않고 SQLite backup API를 사용합니다.

```bash
uv run python -m scripts.backup_db data/chat.db backups/chat-20260917.db

# 컨테이너의 영속 볼륨 안에 백업한 뒤, 호스트로 꺼냅니다.
docker compose exec backend python -m scripts.backup_db /data/chat.db /data/backups/chat-20260917.db
docker compose cp backend:/data/backups/chat-20260917.db ./backups/chat-20260917.db
```

백업에는 계정·세션 해시·대화 내용이 있으므로 접근을 제한하고 별도 안전한 저장소에도 보관합니다.
스크립트는 새 경로에만 저장하며 무결성을 검사합니다. 정기 백업 주기는 운영자가 설정합니다.

복원은 다음 절차를 따릅니다.

1. 프론트·백엔드를 중지하고 기존 볼륨/DB를 별도 보관합니다.
2. 백업의 `PRAGMA integrity_check`가 `ok`인지 확인합니다.
3. 기존 DB 파일과 남은 `-wal`, `-shm`을 **서비스가 중지된 상태에서 함께 별도 보관**합니다.
4. 백업을 기존 DB 경로로 복사하고 컨테이너 UID/GID 10001이 읽고 쓸 수 있게 설정합니다.
5. 서비스를 재시작하고 로그인·대화 조회를 확인합니다.

## 장애 확인

화면 문의 번호와 `X-Request-ID`로 `request_received`, `db_save_success/failure`,
`ai_call_start/success/failure`를 연결합니다. 질문·답변 원문과 비밀번호·토큰은 운영 로그에 출력하지 않습니다.
서비스 재시작 시 또는 10초 정리 작업에서 생성 후 210초를 넘긴 `processing`을
`failed / REQUEST_INTERRUPTED`로 바꿉니다. 늦은 답변이 이 상태를 덮어쓰지 않습니다.

| 오류 | 운영 확인 |
| --- | --- |
| `AI_CONFIG_ERROR` | 서비스 토큰, 모델 별칭, 토크나이저·템플릿, 내부 요청 규격 |
| `AI_NOT_READY` | 라즈베리파이 모델 로딩/작업자 복구 |
| `AI_BUSY` | 전체 추론 슬롯 점유. 자동 재호출 없이 사용자 재시도 |
| `AI_UNAVAILABLE` | 비공개 네트워크·DNS·장비·내부 응답 JSON |
| `AI_TIMEOUT` | 실제 추론 종료·슬롯 복구, 시간 제한 순서 |
| `DB_ERROR` | 디스크 공간·권한·잠금. 성공으로 안내하지 말고 저장 기록 확인 |

백엔드에서 요청이 끝났다는 사실은 실제 추론 종료를 보장하지 않습니다. 라즈베리파이 담당자가
실제 작업 종료 전 슬롯을 해제하지 않는지 검증해야 합니다. 모델 파일, 추론 런타임,
슬롯·중단 관리 구현은 제외 범위입니다.
