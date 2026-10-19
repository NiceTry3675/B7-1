#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."
export HF_HUB_OFFLINE=1
export TRANSFORMERS_OFFLINE=1
export GRADIO_ANALYTICS_ENABLED=False

python_bin=${PYTHON_BIN:-.venv/bin/python}
ruff_bin=${RUFF_BIN:-.venv/bin/ruff}

"$python_bin" -m scripts.audit_service_cts
"$python_bin" -m scripts.analyze_v3dv_tile
"$python_bin" -m pytest -q \
  tests/test_service_cts_model.py tests/test_front_cts_flow.py \
  tests/test_backend_cts_flow.py tests/test_service_cts_e2e.py \
  tests/test_rpi_cts.py tests/test_rpi_runner.py \
  tests/test_rpi_adapter.py tests/test_rpi_api.py tests/test_rpi_flow.py
"$ruff_bin" check .
"$ruff_bin" format --check .
"$python_bin" -m pytest -q
