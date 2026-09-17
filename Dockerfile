FROM python:3.12-slim AS base
COPY --from=ghcr.io/astral-sh/uv:0.9.5 /uv /uvx /bin/
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 UV_COMPILE_BYTECODE=1 \
    PATH="/service/.venv/bin:$PATH" GRADIO_ANALYTICS_ENABLED=False
WORKDIR /service
COPY pyproject.toml uv.lock ./
RUN groupadd --gid 10001 app && useradd --uid 10001 --gid app --create-home app \
    && mkdir /data && chown app:app /data

FROM base AS backend
RUN uv sync --frozen --no-dev --extra tokenizer
COPY app ./app
COPY scripts ./scripts
USER app
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--no-access-log"]

FROM base AS frontend
RUN uv sync --frozen --no-dev
COPY app/__init__.py app/errors.py ./app/
COPY frontend ./frontend
USER app
EXPOSE 7860
CMD ["python", "-m", "frontend.main"]
