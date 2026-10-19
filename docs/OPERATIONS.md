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
| `LLM_SERVICE_TOKEN` | 미설정; 최소 32바이트 난수를 ASCII로 인코딩한 비밀 토큰. 공백 없이 32자 이상 |
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
| `COMPOSE_FILE` | 기본 compose.yaml 사용 시 생략. 명시할 때는 해당 파일 경로만 지정 |
| `TOKENIZER_HOST_PATH` | Compose 전용; 호스트의 토크나이저 디렉터리 |

시간 제한은 `연결 < 생성 < 내부 HTTP < 턴 < 프론트 < 프록시 < 정리` 순서여야 합니다.
순서가 잘못되면 시작을 거부합니다. LLM 설정이 빠지거나 토크나이저를 로드할 수 없는 경우
계정·기록 서비스는 유지하고 질문 요청을 `AI_CONFIG_ERROR`로 실패 처리합니다.
운영 시 토큰은 비밀 관리 도구로 주입하세요. `.env`를 쓴다면 읽기 권한을 제한합니다.

## Docker Compose 배포

### 기본 공인 IP HTTP 배포

현재 [API 명세](../API_SPEC.md#31-인증-설계)는 `http://<PUBLIC_IP>/`의 TCP 80으로
외부 접속하는 배포를 기준으로 합니다. 서비스용 도메인·DNS 등록·TLS 인증서가 필요하지 않습니다.
브라우저와 서버 사이의 로그인 정보·대화 내용은 HTTP로 전송되며 암호화되지 않습니다.
클라우드와 라즈베리파이는 Tailscale 등 암호화된 비공개 경로로 연결합니다.

기본 `compose.yaml`은 backend·frontend·proxy를 기동하며 호스트에는 TCP 80만 공개합니다.
NGINX는 인증서 없이 화면을 Gradio로, `/api/v1/*`와 API 문서를 FastAPI로 전달합니다.
backend·frontend 포트는 Compose 내부에 둡니다. HTTP 템플릿은 HTTPS 이동이나 HSTS를 설정하지 않습니다.

`.env`에서 LLM 주소·토큰·모델과 호스트 토크나이저 경로를 준비한 다음 실행합니다.
이전 배포의 `COMPOSE_FILE`에 별도 프록시 override가 있다면 기본 `compose.yaml`만 선택하도록 변경합니다.

```bash
cp -n .env.example .env
mkdir -p models/tokenizer
# .env의 LLM_*와 TOKENIZER_HOST_PATH를 실제 값으로 설정합니다.
uv run python -m scripts.check_deploy
docker compose up -d --build --wait
docker compose exec proxy nginx -t
docker compose ps
```

`check_deploy`는 Docker 접근, 시간 제한, DB 마운트, LLM 설정과 토크나이저 파일을 확인합니다.
점검이 실패하면 원인을 해결한 뒤 기동합니다. `--json`은 자동화용 결과를 제공합니다.
LLM 없이 계정·화면만 배포할 때는 `--allow-unconfigured-llm`을 명시하며 AI 질문은 실패합니다.
설정 점검만으로 실제 토크나이저 로딩이나 RPi 연결 성공이 보장되지는 않습니다.

EC2 보안 그룹의 TCP 80을 열고, 외부 작업 PC에서 공인 IP를 실제 값으로 바꿔 확인합니다.

```bash
PUBLIC_IP=203.0.113.10
curl -fsS -o /dev/null -w '%{http_code}\n' "http://$PUBLIC_IP/"
curl -fsS "http://$PUBLIC_IP/api/v1/health"
```

HTTP 배포의 완료 기준은 외부 네트워크에서 UI·로그인·질문·기록 조회가 동작하고,
`http://<PUBLIC_IP>/api/v1/health`가 HTTP 200과 `{"status":"ok"}`를 반환하는 것입니다.
타임아웃·토크나이저·DB·LLM·백업 검사도 함께 수행합니다.

### 공통 프록시·서비스 운영

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

## 기존 배포 교체

기존 배포를 바꿀 때도 같은 Compose 프로젝트 이름과 `chat_data` 볼륨을 사용합니다.
DB를 백업한 뒤 `docker compose up -d --build --wait`로 변경한 이미지를 적용합니다.
DB 보존을 위해 `docker compose down -v`를 실행하지 않습니다.
이전 배포에서 인증서 갱신 예약을 설치했다면 해당 타이머·cron 작업을 중지하고 제거합니다.

NGINX 설정 확인: `docker compose exec proxy nginx -t`.
공식 참고: [NGINX WebSocket 프록시](https://nginx.org/en/docs/http/websocket.html).

## 컨테이너 로그 보관

모든 Compose 서비스는 `json-file` 드라이버에 파일당 `DOCKER_LOG_MAX_SIZE=10m`,
컨테이너당 `DOCKER_LOG_MAX_FILES=3`을 기본 적용합니다. 오래된 로그는 순환 삭제되며
이는 컨테이너 표준 출력·오류에만 적용됩니다. DB·백업·호스트의 cron 로그는 별도입니다.
설정을 바꾸면 `docker compose up -d --force-recreate`로 컨테이너를 재생성해야 적용됩니다.
조회는 기존처럼 `docker compose logs --tail 100 backend frontend proxy`를 사용합니다.

## 백업과 복원

실행 중인 SQLite 파일이나 WAL 파일을 단순 복사하지 않고 SQLite backup API를 사용합니다.

```bash
uv run python -m scripts.backup_db data/chat.db backups/chat-20260917.db

# 컨테이너의 영속 볼륨 안에 백업한 뒤, 호스트로 꺼냅니다.
docker compose exec backend python -m scripts.backup_db /data/chat.db /data/backups/chat-20260917.db
docker compose cp backend:/data/backups/chat-20260917.db ./backups/chat-20260917.db
```

백업에는 계정·세션 해시·대화 내용이 있으므로 접근을 제한하고 별도 안전한 저장소에도 보관합니다.
스크립트는 새 경로에만 저장하며 무결성을 검사합니다.

### 호스트 정기 백업

`python3 -m scripts.manage_backup --directory /var/backups/b7`는 실행 중인 백엔드에서
SQLite backup API로 스냅샷을 만들고 호스트로 꺼내 무결성 검사 후 게시합니다.
Python 표준 라이브러리와 Docker만 필요합니다. 백엔드 이미지에 최신 스크립트를 먼저 반영하세요.
백엔드가 중지되었거나 복사가 실패하면 기존 백업을 정리하지 않으며 종료 코드 1을 반환합니다.
같은 보관 디렉터리의 동시 작업은 파일 잠금으로 거부합니다.

기본 보관은 14일이며 최신 3개는 기간과 관계없이 유지합니다. `--retention-days`,
`--keep-at-least`로 조정합니다. 이 도구가 사용하는 `chat-시각-난수.db` 이름의 일반 파일만
정리하며 수동 백업·심볼릭 링크는 건드리지 않습니다. 백업은 0600 권한으로 저장합니다.
컨테이너 내부 임시 스냅샷은 작업 후 제거합니다. 호스트 백업 경로는 DB 볼륨과 분리하고,
서버 전체 장애에도 대비하려면 별도 저장소로 복제하는 운영 절차를 추가하세요.

Linux/systemd 배포에는 `deploy/systemd/b7-backup.service`와 `.timer`를 제공합니다.
서비스의 `/srv/B7-1`을 실제 체크아웃 경로로, `/var/backups/b7`를 보관 경로로 수정한 뒤 설치합니다.
root로 실행되므로 체크아웃과 상위 디렉터리는 신뢰할 수 있는 운영자만 쓸 수 있어야 합니다.
예약 작업은 대화형 셸의 환경 변수를 상속하지 않으므로 Compose 프로젝트 이름과 설정을
운영 `.env`에 일관되게 지정합니다.

```bash
sudo install -m 0644 deploy/systemd/b7-backup.{service,timer} /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now b7-backup.timer
sudo systemctl start b7-backup.service
sudo journalctl -u b7-backup.service --since today
```

예약 시각은 호스트 시간대의 매일 02:30 이후 최대 10분 지연이며, 놓친 실행은 다음 기동 때 수행합니다.
중지하려면 `sudo systemctl disable --now b7-backup.timer`를 사용합니다.
서비스 실행 결과와 백업 파일 생성 여부를 확인한 뒤 운영에 사용하세요. 이번 변경에서는 실행하지 않았습니다.

### 복원 도구

`python3 -m scripts.restore_db 백업.db 대상.db`는 무결성·외래 키·스키마 버전과 필드를
검사하기만 합니다. 실제 복원에는 `--apply --services-stopped` 두 옵션이 필요합니다.
`--services-stopped`는 운영자의 확인이며 도구가 다른 DB 접근 프로세스를 자동 검출하지는 않습니다.
복원 전에 백업 타이머와 진행 중인 백업 작업을 중지하고 백엔드·프론트도 중지해야 합니다.

도구는 임시 파일에서 백업을 재검사하고, 기존 DB와 `-wal`, `-shm`, `-journal`을 같은
디렉터리의 `pre-restore-시각-난수/`에 보존한 뒤 교체합니다. 파일 이동·설치 실패 시
이미 옮긴 원본을 되돌립니다. 강제 전원 종료까지 원자적으로 복구하는 도구는 아니므로
중단되면 보존 디렉터리와 대상 파일을 확인한 뒤 서비스를 재개하세요.
보존 디렉터리는 백업 보관 정책으로 삭제되지 않습니다. 복원 DB는 0600 권한이며
기존 UID/GID를 유지하거나 `--uid`, `--gid`로 둘 다 지정합니다.

Compose에서는 호스트 백업을 읽기 전용으로 마운트한 일회성 컨테이너로 복원할 수 있습니다.
아래 경로와 파일명은 실제 백업에 맞게 바꾸세요. 실행 전 검증과 중지 순서를 지킵니다.

```bash
python3 -m scripts.restore_db /var/backups/b7/선택한백업.db /tmp/restore-target.db
sudo systemctl stop b7-backup.timer b7-backup.service
docker compose stop frontend backend
docker compose run --rm --no-deps --user 0 \
  -v /var/backups/b7:/restore:ro backend \
  python -m scripts.restore_db /restore/선택한백업.db /data/chat.db \
  --apply --services-stopped --uid 10001 --gid 10001
# 복원 성공을 확인한 뒤에만 재개합니다.
docker compose up -d --wait backend frontend
sudo systemctl start b7-backup.timer
```

사용자 계정·대화 조회와 DB 접근 권한을 확인한 뒤 복원 완료로 기록합니다.
자동화 없이 수동으로 복원할 경우에도 다음 절차를 따릅니다.

1. 프론트·백엔드를 중지하고 기존 볼륨/DB를 별도 보관합니다.
2. 백업의 `PRAGMA integrity_check`가 `ok`인지 확인합니다.
3. 기존 DB 파일과 남은 `-wal`, `-shm`을 **서비스가 중지된 상태에서 함께 별도 보관**합니다.
4. 백업을 기존 DB 경로로 복사하고 컨테이너 UID/GID 10001이 읽고 쓸 수 있게 설정합니다.
5. 서비스를 재시작하고 로그인·대화 조회를 확인합니다.

## 장애 확인

### 운영 상태 통합 점검

배포 호스트에서 `python3 -m scripts.check_operations --backup-directory /var/backups/b7`를
실행하면 컨테이너 healthcheck, HTTP 화면·API, 컨테이너 내부 SQLite,
LLM 준비 상태와 최신 백업의 무결성·스키마를 모아서 확인합니다. Python 표준 라이브러리와
Docker 접근 권한, 백업 읽기 권한이 필요합니다. `--json`은 자동화용 결과입니다.
하나라도 실패하면 종료 코드 1이며 비밀값·HTTP 오류 원문·대화 내용은 출력하지 않습니다.

기본 HTTP 점검 주소는 서버의 `http://127.0.0.1`입니다. 공인 IP나 다른 HTTP 포트를 확인하려면
`--http-url http://<PUBLIC_IP>[:PORT]`를 지정합니다. URL에는 인증정보·경로·쿼리를 넣지 않습니다.

백업 최대 경과 시간은 기본 26시간이며 `--max-backup-age-hours`로 조정합니다.
백업 예약을 설치하지 않았다면 백업 항목이 실패하는 것이 정상입니다. LLM 미설정·미준비도 실패로 보고합니다.
점검은 생성·재시작·백업·복원을 수행하지 않으며, LLM에는 health만 요청합니다.
LLM 점검은 최대 150초 기다릴 수 있습니다. API health 성공과 별개로 DB를 읽어 검사합니다.
호스트에서의 HTTP 성공은 다른 외부 네트워크에서의 접속 성공을 보장하지 않습니다.

### 전체 Compose 검증 환경

`RUN_COMPOSE_TESTS=1 uv run pytest -q tests/test_compose.py`는 실제 세 이미지의 빌드와
인증서 없는 HTTP 기동, 회원가입·로그인·질문 실패 기록,
프론트 컨테이너의 API 연결, 백엔드 재생성 후 데이터 보존을 확인하도록 작성되어 있습니다.
CI의 `compose-smoke` 작업도 같은 검증을 사용합니다. Docker Engine이 필요합니다.
고유 Compose 프로젝트·임시 볼륨·임의의 로컬 HTTP 포트를 사용하며 운영 `.env`를
읽지 않습니다. 테스트 종료 시 해당 테스트 프로젝트의 볼륨과 이미지만 제거합니다.
LLM 연결은 비워 두므로 실제 추론·실제 라즈베리파이 연동은 검증하지 않습니다.
`tests/test_proxy.py`는 HTTP 라우팅·실시간 응답·전달 헤더·컨테이너 DNS 갱신을 검사합니다.
이번 HTTP 전환과 HTTPS 설정 제거에서는 테스트 코드를 수정했으며 실행하지 않았습니다. 통과 여부는 실행 후 기록해야 합니다.

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
