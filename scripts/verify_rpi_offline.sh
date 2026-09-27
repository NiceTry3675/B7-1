#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."
export HF_HUB_OFFLINE=1
export TRANSFORMERS_OFFLINE=1
export GRADIO_ANALYTICS_ENABLED=False

python_bin=${PYTHON_BIN:-.venv/bin/python}
ruff_bin=${RUFF_BIN:-.venv/bin/ruff}

"$python_bin" -m pytest -q \
  tests/test_rpi_cts.py tests/test_rpi_runner.py \
  tests/test_rpi_adapter.py tests/test_rpi_api.py tests/test_rpi_flow.py
"$ruff_bin" check rpi_llm scripts/apply_llama_patch.py \
  scripts/rpi_llama_adapter.py scripts/check_rpi_generation.py tests/test_rpi_*.py
"$ruff_bin" format --check rpi_llm scripts/apply_llama_patch.py \
  scripts/rpi_llama_adapter.py scripts/check_rpi_generation.py tests/test_rpi_*.py
"$python_bin" -m pytest -q
