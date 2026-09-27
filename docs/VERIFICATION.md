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

- `bash scripts/verify_rpi_offline.sh`: RPi 대상 mock 46개 통과,
  전체 112개 통과·4개 skip, Ruff 검사·포맷 검사 통과. CTS walker의
  간선 없음·범위 제한, 단일 작업 큐, 취소·시간 초과 슬롯 유지,
  llama-server MockTransport, 인증된 내부 API, 서비스→DB 저장 흐름을 포함합니다.
  실제 작업에서만 시작·성공 로그를 남기는지, 입력 길이 상한,
  ASCII 서비스 토큰·Bearer 표기·응답 캐시 금지도 확인했습니다.
  느린 MockTransport로 readiness/slot 점검의 전체 마감과 런타임 호출의
  전체 마감, 추론 시작 뒤 시간 초과의 격리 경로도 확인했습니다.
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
  순서와 명령은 [RPi 가이드](RPI_LLM.md#verification-order)에 있습니다.
