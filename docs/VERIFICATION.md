# 구현 검증 기록

2026-09-17, Python 3.12 / WSL Ubuntu 환경에서 검증했습니다.

- 자동 테스트 62개 통과: API 계약, 인증, 사용자 격리, 중복·동시 전송, 문맥,
  내부 HTTP 오류·전체 시간 제한, DB 실패 우선 처리, 중단 복구, 영속성,
  Gradio 콜백 직렬화, 응답 유실·로그아웃 후 늦은 응답 차단, SQLite 온라인 백업.
- Ruff 코드 검사 및 포맷 검사 통과.
- `docker compose config --quiet` 통과.
- 별도 임시 SQLite DB와 테스트용 포트로 백엔드·Gradio를 실행하고 브라우저에서
  회원가입 → 로그인 → 위인 선택 → 새 대화 → 질문 전송 흐름을 확인했습니다.
- 테스트 중 로그인 후 대화 영역이 숨겨지는 문제를 발견해 화면 컨테이너를 수정했습니다.
- LLM 미설정에서 오류 안내·질문 보존·수동 재요청 버튼이 동작하는지 확인했습니다.
- 로그아웃 후 로그인 화면 복귀와 대화 내용 초기화를 브라우저에서 확인했습니다.

## NGINX 마이그레이션 검증

- Docker Engine에서 NGINX 이미지 빌드와 `docker compose --profile tls config --quiet` 통과.
- `RUN_PROXY_TESTS=1 GRADIO_ANALYTICS_ENABLED=False uv run pytest -q`: 전체 69개 통과.
- 격리된 실제 NGINX 컨테이너, 테스트 인증서와 테스트용 HTTP 서버로 다음을 확인했습니다.
  - 인증서 발급 전 ACME 검증 경로·healthcheck 제공 및 일반 요청 503 처리.
  - HTTP → HTTPS 전환, API·문서·Gradio 경로 분기, 전달 헤더 처리와 인증 헤더 보존.
  - SSE 응답 즉시 전달, WebSocket 연결 전환, 접근 로그의 쿼리 문자열 제외.
  - 인증서 교체 후 reload 적용과 백엔드 컨테이너 재생성 후 DNS 재조회·연결 복구.
- 인증서 발급 설정 누락, dry-run의 인증서 적용 방지, 갱신 실패 시 reload 방지 검사 통과.
- Ruff 코드·포맷 검사, 프록시 시작 스크립트의 셸 문법 검사 통과.

## 실제 배포에서 남은 확인

- 백엔드·프론트 이미지를 포함한 전체 Compose 배포는 미검증입니다.
- 클라우드·공인 도메인 정보가 없어 외부 HTTPS 배포와 실제 인증 기관의 발급·갱신은 수행하지 않았습니다.
- 실제 라즈베리파이 내부 API, 토큰·모델 별칭, 토크나이저/템플릿 일치 여부는 미검증입니다.
- 로컬 모델 구현·성능·품질·전체 추론 슬롯·실제 중단 동작은 요청에 따라 제외했습니다.

테스트 대체 응답은 `tests/`에서만 사용하며 제품 실행 경로에 모의 답변 모드는 없습니다.

## RPi 어댑터와 GPU 연구 패치 — 2026-09-28

- `bash scripts/verify_rpi_offline.sh`: RPi 대상 mock 54개 통과,
  전체 135개 통과·4개 skip, Ruff 검사·포맷 검사 통과. CTS walker의
  간선 없음·범위 제한, 단일 작업 큐, 취소·시간 초과 슬롯 유지,
  llama-server MockTransport, 인증된 내부 API, 서비스→DB 저장 흐름을 포함합니다.
  실제 작업에서만 시작·성공 로그를 남기는지, 입력 길이 상한,
  ASCII 서비스 토큰·Bearer 표기·응답 캐시 금지도 확인했습니다.
  느린 MockTransport로 readiness/slot 점검의 전체 마감과 런타임 호출의
  전체 마감, 추론 시작 뒤 시간 초과의 격리 경로도 확인했습니다.
  [CTS 감사](CTS.md)는 테스트 수와 별도로 상태 7×행동 12의 84칸을
  전이 15개, busy 4개, not ready 2개, stale 7개, invalid 56개로 분류하고
  Walker가 5간선 안에서 전이 15개 모두에 도달함을 확인했습니다.
  Docker 프록시 4개는 이 실행에서 활성화하지 않았습니다.
- 현재 llama-server 소스의 채팅 생성 경로와 입력 토큰 계산 경로는
  `oaicompat_chat_params_parse`를 공유합니다. `cache_prompt=false`에서는
  이전 prompt 재사용 길이를 0으로 둡니다. 이는 소스 확인이며 실제
  Gemma 채팅 템플릿의 런타임 검증을 대신하지 않습니다.
- `scripts.analyze_v3dv_tile`: VC7의 scalar·packed 4×8 MMQ·K-quant MMQ
  small tile에서 2-warp `WN=32`의 2,048개 계산 좌표와 shared B row 63,
  `WN=16`의 1,024개 고유 좌표와 최고 row 31을 재현했습니다.
  한 warp인 nonquant matmul-id의 `WN=32` 유지도 확인했습니다.
- `scripts.census_dot4x8`: 현재 Vulkan shader source의
  `dotPacked4x8EXT` 호출 위치는 matvecq 40개, MMQ 14개, flash-attention
  1개 파일 계열로 확인했습니다. 이는 정적 소스 위치 수이며 실제 실행 횟수는 아닙니다.
- llama.cpp `1f368f3`와 Mesa `f6f72d4b21c`의 별도 깨끗한 작업트리에서
  patch bundle의 dry-run, 실제 적용, 결과 파일의 격리 작업트리 대조,
  재적용 감지, `git diff --check`를 통과했습니다. 원래 llama.cpp 작업트리의
  미커밋 Vulkan 수정에는 적용을 거부했습니다.
- patched `ggml-vulkan.cpp` translation unit은 기존 Vulkan build의
  컴파일 명령에서 `-O0`로, Mesa `vir_register_allocate.c` translation unit은
  기존 origin-main build 명령으로 각각 컴파일했습니다. 두 빌드 모두
  바이너리 링크나 모델·GPU 실행 검증은 아닙니다.
- 평가용 CPU Gemma 추론, 실제 llama-server 준비·복구, GPU shader
  correctness·성능, 외부 HTTPS 서비스는 이 기록에서 미실행입니다.
  `scripts.check_tokenizer_match --run`은 동일한 로컬 템플릿 입력을
  Transformers와 llama-server에서 비교하도록 준비했지만 모델 서버와
  토크나이저 파일이 아직 준비되지 않아 실행하지 않았습니다. 이 검사는
  텍스트 생성 없이 토큰 수만 요청합니다.
  순서와 명령은 [RPi 가이드](RPI_LLM.md#verification-order)에 있습니다.

## Front·Backend CTS 확장 — 2026-09-28

- `bash scripts/verify_service_offline.sh`: Front·Backend·RPi를 묶은
  Mock 시험 69개 통과, 전체 135개 통과·4개 skip, Ruff 코드·포맷 검사 통과.
  외부 HTTP와 실제 모델 요청은 사용하지 않았습니다.
- [전체 CTS 감사](SERVICE_CTS.md): Front 126칸·66전이, Backend 인증
  16칸·4전이, Backend 턴 32칸·8전이, RPi 84칸·15전이를 각각 분류했습니다.
  합계 258칸·93전이이며, 범위별 Walker는 정해진 깊이 안에서 모든
  간선에 도달했습니다. 이는 전체 pytest 테스트 수와 다른 수치입니다.
- 실제 Controller 콜백에서 로그인·질문·실패·UUID 재시도를, SQLite에서
  같은 UUID 중복·다른 UUID 처리 충돌·만료·폐기·늦은 결과 보존을
  확인했습니다. 통합 시험은 Front→Backend→RPi Mock→DB→Front의
  성공과 실패 복구를 한 흐름으로 확인했습니다.
- 로그아웃 상태에서 오래된 대화 열기 콜백이 대화 ID를 남기던 경로를
  차단하고, 잘못된 대화 ID 입력도 거절합니다. 실모델과 외부 배포 단계는
  위 미실행 항목 그대로입니다.
