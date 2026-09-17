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
| `PROXY_TIMEOUT_SECONDS` | 180 이상의 정수(초); NGINX 읽기·쓰기 대기 제한에도 적용 |
| `TURN_STALE_SECONDS` | 210; 시작 시 및 10초마다 초과 턴 정리 |
| `LOG_LEVEL` | INFO |
| `SITE_ADDRESS` | Compose 전용; `https://`·포트·경로 없는 외부 DNS 도메인. 기본 `localhost`는 배포용이 아님 |
| `ACME_EMAIL` | Compose 전용; Let's Encrypt 계정 등록 이메일. 최초 인증서 발급 시 필수 |
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
# .env의 SITE_ADDRESS, ACME_EMAIL, LLM_*와 TOKENIZER_HOST_PATH를 실제 값으로 설정합니다.
docker compose up -d --build
# Python 3의 표준 라이브러리만 사용하며, 프로젝트 .env는 Compose가 읽습니다.
python3 -m scripts.manage_tls issue --dry-run
python3 -m scripts.manage_tls issue
docker compose ps
docker compose logs --tail 100 backend frontend proxy
```

NGINX는 첫 실행 시 인증서가 없어도 기동합니다. 이때 80번 포트의 ACME 검증 경로와
`/nginx-health`만 제공하며, 나머지 요청은 503입니다. 로그인·대화 화면은 HTTPS가 준비된 뒤 제공됩니다.
`issue --dry-run`은 테스트 인증 기관으로 도메인 연결을 검사하고 인증서를 저장하지 않습니다.
`issue`는 Certbot webroot 방식으로 실제 인증서를 발급하고 프록시를 재생성해 HTTPS를 활성화합니다.
이 명령은 Let's Encrypt 이용약관에 동의하여 계정을 등록합니다. 도메인의 DNS와 80/443 포트를
이 서버로 연결한 뒤 실행하세요. 공인 인증서 없이 로컬에서 개발할 때는 README의 8000/7860 실행을 사용합니다.

인증서는 `tls_certificates`, 검증 파일은 `acme_webroot` 볼륨을 공유합니다. NGINX에서는 두 볼륨 모두
읽기 전용입니다. 인증서 경로는 `/etc/letsencrypt/live/<SITE_ADDRESS>/fullchain.pem`과 `privkey.pem`입니다.
인증서가 준비되면 일반 HTTP 요청은 HTTPS로 308 전환하며 ACME 경로는 계속 HTTP로 제공합니다.
프록시 healthcheck는 프로세스 확인이므로 인증서 준비 여부는 `https://<SITE_ADDRESS>`에서 따로 확인합니다.

NGINX는 `/api/v1/*`, `/docs`, `/docs/*`, `/openapi.json`, `/redoc`를 백엔드로 전달하고
나머지는 Gradio로 전달합니다. SSE 응답 버퍼링을 끄고 WebSocket Upgrade 헤더를 전달하며,
AI 응답 대기를 위해 프록시 제한을 180초 이상으로 설정했습니다. 컨테이너 재생성으로 내부 IP가 바뀌면
Docker DNS로 다시 조회합니다. 접근 로그에는 쿼리 문자열·인증 헤더·요청 본문을 기록하지 않습니다.

백엔드·프론트 포트는 호스트에 직접 공개하지 않습니다. 이 제한을 전제로 두 서비스는 내부 프록시의
전달 헤더를 신뢰하며, NGINX가 클라이언트가 보낸 `X-Forwarded-*` 값을 덮어씁니다.
추론 API도 외부에 공개하지 않습니다.

백엔드·프론트 컨테이너는 비관리자 계정으로 실행하며 SQLite는 `chat_data` 볼륨에 보존됩니다.
백엔드는 항상 `--workers 1`, 복제본 1개를 사용합니다. 재배포 시
`docker compose up -d --build`를 사용하며 **`docker compose down -v`는 DB까지 삭제하므로 사용하지 마세요.**

`/api/v1/health`와 Compose healthcheck는 프로세스 확인입니다. LLM의 준비 여부는 다음으로 별도 확인합니다.

```bash
docker compose exec backend python -m scripts.check_llm
```

이 점검은 인증된 `/internal/v1/health`만 호출하고 생성 슬롯을 사용하지 않습니다.
health 성공 후에도 generate는 `AI_BUSY` 등을 반환할 수 있습니다.

## 인증서 자동 갱신과 기존 배포 교체

Certbot 컨테이너 자체는 스케줄러를 실행하지 않습니다. 최초 발급 후 다음 검사를 수행하고,
배포 호스트에서 Docker 실행 권한이 있는 계정의 crontab에 하루 두 번 갱신 작업을 등록합니다.

```bash
python3 -m scripts.manage_tls renew --dry-run
```

```cron
17 3,15 * * * cd /srv/B7-1 && /usr/bin/python3 -m scripts.manage_tls renew >> /srv/B7-1/tls-renew.log 2>&1
```

`/srv/B7-1`은 실제 배포 디렉터리로 바꿉니다. cron의 PATH에서 `docker`를 찾을 수 있어야 합니다.
갱신은 만료가 가까운 인증서에만 수행됩니다. 성공 후 `nginx -t`와 무중단 reload로 새 인증서를 적용하며,
Certbot 실패 시 reload하지 않습니다. 갱신 로그·실패 알림을 운영 환경에서 확인하세요.
개인 키가 포함된 `tls_certificates` 볼륨은 Git에 넣지 않고 접근을 제한합니다.

기존 배포를 바꿀 때도 같은 Compose 프로젝트 이름과 `chat_data` 볼륨을 사용합니다.
DB를 백업한 뒤 `docker compose up -d --build`로 프록시를 교체하고 위 최초 발급 절차를 수행합니다.
이전 프록시의 인증서 저장 형식은 사용하지 않으므로 최초 발급이 끝날 때까지 짧은 서비스 중단이 있습니다.
DB 보존을 위해 `docker compose down -v`를 실행하지 않습니다.

NGINX 설정 확인: `docker compose exec proxy nginx -t`.
공식 참고: [NGINX WebSocket 프록시](https://nginx.org/en/docs/http/websocket.html),
[Certbot webroot·갱신](https://eff-certbot.readthedocs.io/en/stable/using.html).

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
