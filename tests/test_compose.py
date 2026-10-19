"""Opt-in full-stack HTTP smoke test with disposable project and data."""

import json
import os
import subprocess
from pathlib import Path
from uuid import uuid4

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_COMPOSE_TESTS") != "1", reason="Full Compose smoke test is opt-in"
)


@pytest.fixture
def stack(tmp_path):
    project = "b7-smoke-" + uuid4().hex[:10]
    environment = os.environ.copy()
    environment.update(
        LLM_BASE_URL="",
        LLM_SERVICE_TOKEN="",
        LLM_MODEL="local-advisor",
        LLM_CONTEXT_TOKENS="4096",
        LLM_MAX_OUTPUT_TOKENS="256",
        LLM_CONNECT_TIMEOUT_SECONDS="5",
        LLM_GENERATION_TIMEOUT_SECONDS="120",
        LLM_TIMEOUT_SECONDS="130",
        TURN_TIMEOUT_SECONDS="145",
        BACKEND_TIMEOUT_SECONDS="160",
        PROXY_TIMEOUT_SECONDS="180",
        TURN_STALE_SECONDS="210",
        SESSION_TTL_SECONDS="43200",
        LOG_LEVEL="INFO",
        DOCKER_LOG_MAX_SIZE="10m",
        DOCKER_LOG_MAX_FILES="3",
        TOKENIZER_HOST_PATH=str(tmp_path / "tokenizer"),
        COMPOSE_PROFILES="",
    )
    (tmp_path / "tokenizer").mkdir()
    empty_env = tmp_path / "empty.env"
    empty_env.write_text("")
    generated = tmp_path / "compose.json"

    def run(*args, file=generated, check=True, timeout=180):
        return subprocess.run(
            [
                "docker",
                "compose",
                "--project-directory",
                str(ROOT),
                "--env-file",
                str(empty_env),
                "-p",
                project,
                "-f",
                str(file),
                *args,
            ],
            cwd=ROOT,
            env=environment,
            check=check,
            capture_output=True,
            text=True,
            timeout=timeout,
        )

    config = json.loads(run("config", "--format", "json", file=ROOT / "compose.yaml").stdout)
    # Never inherit an operator's named volumes, published ports or image tags.
    for name in ("backend", "frontend", "proxy"):
        config["services"][name]["image"] = f"{project}-{name}:smoke"
        config["services"][name]["restart"] = "no"
    config["services"]["backend"]["volumes"] = [
        {"type": "volume", "source": "chat_data", "target": "/data"},
        {
            "type": "bind",
            "source": str(tmp_path / "tokenizer"),
            "target": "/tokenizer",
            "read_only": True,
        },
    ]
    config["services"]["proxy"]["ports"] = [
        {"target": 80, "published": "0", "host_ip": "127.0.0.1"}
    ]
    config["volumes"] = {"chat_data": {"name": f"{project}_chat_data"}}
    config["networks"] = {"default": {"name": f"{project}_default"}}
    generated.write_text(json.dumps(config))
    try:
        run("build", timeout=900)
        run("up", "-d", "--wait", "--wait-timeout", "180", timeout=240)
        yield run
    finally:
        # Only this generated project owns the named resources removed here.
        run("down", "--volumes", "--remove-orphans", "--rmi", "all", check=False)


def test_full_stack_http_without_certificates_and_persistence(stack):
    run = stack
    run(
        "exec",
        "-T",
        "backend",
        "python",
        "-c",
        "from importlib.util import find_spec; assert find_spec('gradio') is None; "
        "assert find_spec('transformers') is not None",
    )
    run(
        "exec",
        "-T",
        "frontend",
        "python",
        "-c",
        "from importlib.util import find_spec; assert find_spec('gradio') is not None; "
        "assert find_spec('transformers') is None",
    )
    address = run("port", "proxy", "80").stdout.strip()
    with httpx.Client(base_url=f"http://{address}", trust_env=False, timeout=30) as c:
        assert c.get("/nginx-health").status_code == 200
        response = c.get("/")
        assert response.status_code == 200
        assert "location" not in response.headers
        assert "strict-transport-security" not in response.headers
        assert c.get("/api/v1/health").json() == {"status": "ok"}
        assert "/api/v1/conversations" in c.get("/openapi.json").json()["paths"]
        credentials = {"email": "smoke@example.org", "password": "Smoke-only-password123!"}
        assert c.post("/api/v1/auth/signup", json=credentials).status_code == 201
        login = c.post("/api/v1/auth/login", json=credentials)
        assert login.status_code == 200
        headers = {"Authorization": "Bearer " + login.json()["access_token"]}
        created = c.post("/api/v1/conversations", headers=headers, json={"persona_id": "socrates"})
        assert created.status_code == 201
        path = f"/api/v1/conversations/{created.json()['id']}/turns"
        body = {"client_message_id": str(uuid4()), "content": "배포 연결 확인"}
        failed = c.post(path, headers=headers, json=body)
        assert failed.status_code == 503
        assert failed.json()["error"]["code"] == "AI_CONFIG_ERROR"
        before = c.get(path, headers=headers).json()["items"]
        assert len(before) == 1 and before[0]["status"] == "failed"
        # Exercise the real frontend image's client over the Compose network.
        run(
            "exec",
            "-T",
            "frontend",
            "python",
            "-c",
            "import asyncio; from frontend.client import BackendClient; "
            "assert asyncio.run(BackendClient('http://backend:8000').request('GET', '/health'))"
            " == {'status': 'ok'}",
        )
        run("up", "-d", "--no-deps", "--force-recreate", "--wait", "backend")
        # nginx DNS caches may briefly retain the old backend address after recreation.
        # Query persistence through the frontend container directly, without a timing assertion.
        verified = run(
            "exec",
            "-T",
            "frontend",
            "python",
            "-c",
            "import asyncio, json, sys; from frontend.client import BackendClient; "
            "result = asyncio.run(BackendClient('http://backend:8000').request("
            "'GET', sys.argv[1], sys.argv[2])); print(json.dumps(result))",
            path.removeprefix("/api/v1"),
            login.json()["access_token"],
        )
        assert json.loads(verified.stdout)["items"] == before
