# RPi 추론 슬롯 CTS

Front와 Backend를 포함한 표는 [전체 서비스 CTS](SERVICE_CTS.md)에 있다.

전체 pytest 통과 수는 CTS 경우의 수가 아니다. 이 CTS의 단위는 **상태 하나와
행동 하나의 조합**이다. 현재 상태 7개 × 행동 12개 = 84칸을 전부 분류했다.
전이가 15칸이고, 간선이 없는 69칸에도 거절·오래된 이벤트·잘못된 이벤트
중 하나의 의미를 붙였다. 분류되지 않은 칸이 생기면 검증이 실패한다.

## 상태

| 상태 | 뜻 | 작업자 |
| --- | --- | --- |
| `NOT_READY` | 모델 또는 토큰 계산 경로를 다시 확인해야 함 | 없음 |
| `READY` | 새 작업을 받을 수 있음 | 없음 |
| `COUNTING` | 입력 토큰 계산 중 | 동작 중 |
| `GENERATING` | llama-server에 생성 요청을 보낸 상태 | 동작 중 |
| `DRAINING_COUNT` | 호출자에게 시간 초과를 답했지만 토큰 계산은 진행 중 | 동작 중 |
| `DRAINING_GENERATION` | 호출자에게 시간 초과를 답했지만 생성은 진행 중 | 동작 중 |
| `QUARANTINED` | 작업은 끝났지만 서버의 실제 종료 상태가 불확실함 | 없음 |

`DRAINING_*`와 `QUARANTINED`를 나눈 이유는 슬롯 해제 조건이 다르기
때문이다. 진행 중인 작업에는 새 생성을 겹치지 않게 `LLM_BUSY`를
반환한다. 불확실하게 끝난 작업에는 `LLM_NOT_READY`를 반환하고
`/slots`가 비어 있음을 확인한 뒤에만 `READY`로 돌아간다.

## 전이와 간선 없음

| 시작 | 행동 | 도착 |
| --- | --- | --- |
| `NOT_READY` | `MODEL_READY` | `READY` |
| `READY` | `MODEL_LOST` | `NOT_READY` |
| `READY` | `CLAIM` | `COUNTING` |
| `COUNTING` | `CONTEXT_VALID` | `GENERATING` |
| `COUNTING` | `CONTEXT_REJECTED` | `READY` |
| `COUNTING` | `COUNT_FAILED` | `NOT_READY` |
| `COUNTING` | `DEADLINE` | `DRAINING_COUNT` |
| `GENERATING` | `INFERENCE_FINISHED` | `READY` |
| `GENERATING` | `INFERENCE_UNKNOWN` | `QUARANTINED` |
| `GENERATING` | `DEADLINE` | `DRAINING_GENERATION` |
| `DRAINING_COUNT` | `LATE_FINISHED` | `READY` |
| `DRAINING_COUNT` | `COUNT_FAILED` | `NOT_READY` |
| `DRAINING_GENERATION` | `LATE_FINISHED` | `READY` |
| `DRAINING_GENERATION` | `LATE_UNKNOWN` | `QUARANTINED` |
| `QUARANTINED` | `RUNTIME_IDLE` | `READY` |

간선이 없으면 `step()`은 `None`을 반환한다. `no_edge()`는 그 이유를
돌려준다. `reject_busy` 4칸은 진행 중인 슬롯에 `CLAIM`한 경우,
`reject_not_ready` 2칸은 모델이 준비되지 않았거나 격리된 경우다.
`ignore_stale` 7칸은 중복 준비 신호·중복 타이머 등 이전 이벤트다.
나머지 `invalid_event` 56칸은 그 상태에서 발생하면 안 되는 내부 행동이고,
큐는 상태를 바꾸지 않고 오류를 낸다. 새 상태나 행동을 추가하면 이 표의
분류도 명시적으로 고쳐야 한다.

## 범위별 검증

| 범위 | 전체 칸 | 전이 | busy | not ready | stale | invalid |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 준비 상태 | 14 | 2 | 0 | 0 | 2 | 10 |
| 작업 접수 | 7 | 1 | 4 | 2 | 0 | 0 |
| 토큰 계산 | 21 | 4 | 0 | 0 | 1 | 16 |
| 추론 | 14 | 2 | 0 | 0 | 0 | 12 |
| 시간 초과·복구 | 28 | 6 | 0 | 0 | 4 | 18 |

Walker는 `NOT_READY`에서 시작해 최대 **5개 간선**까지만 걷는다.
이 범위의 경로 18개로 전이 15개 모두에 도달한다. 간선이 하나도 없는
그래프에서는 경로가 0개다. `--full`은 84칸과 각 전이의 도달 경로를
보여준다.

```bash
.venv/bin/python -m scripts.audit_rpi_cts
.venv/bin/python -m scripts.audit_rpi_cts --full
.venv/bin/python -m pytest -q tests/test_rpi_cts.py tests/test_rpi_runner.py tests/test_rpi_api.py
bash scripts/verify_rpi_offline.sh
```

순수 CTS 검사는 **84칸의 분류와 15개 간선의 도달성**을 확인한다.
러너·API Mock 테스트는 토큰 계산 실패, 두 종류의 시간 초과,
늦은 성공·불확실한 종료, 접수 거절, 복구를 실제 작업 흐름으로 확인한다.
이 숫자는 GPU 실행 횟수나 실제 Gemma 품질을 뜻하지 않는다. 실제 모델
서버의 취소·재시작과 외부 접속은 [RPi 검증 순서](RPI_LLM.md#verification-order)의
후속 단계로 남는다.
