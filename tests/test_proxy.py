"""Run with RUN_PROXY_TESTS=1; isolated HTTP proxy integration."""

import os
import subprocess
import time
from pathlib import Path
from uuid import uuid4

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_PROXY_TESTS") != "1", reason="Docker proxy integration is opt-in"
)


def docker(*args, check=True):
    result = subprocess.run(["docker", *args], capture_output=True, text=True, timeout=180)
    if check and result.returncode:
        pytest.fail(result.stdout + result.stderr)
    return result


@pytest.fixture(scope="module")
def proxy_image():
    name = "b7-proxy-test-" + uuid4().hex[:10]
    docker("build", "-t", name, str(ROOT / "deploy/nginx"))
    try:
        yield name
    finally:
        docker("image", "rm", name, check=False)


@pytest.mark.parametrize(
    "setting",
    [
        "PROXY_TIMEOUT_SECONDS=179",
        "PROXY_TIMEOUT_SECONDS=not-a-number",
    ],
)
def test_proxy_rejects_invalid_configuration(proxy_image, setting):
    result = docker("run", "--rm", "-e", setting, proxy_image, check=False)
    assert result.returncode != 0
    assert "must" in result.stdout + result.stderr


def test_proxy_http_routing_streaming_and_dns_refresh(proxy_image):
    name = "b7-proxy-test-" + uuid4().hex[:10]
    proxy = name + "-proxy"
    names = [proxy, name + "-backend", name + "-frontend", name + "-reserve"]
    docker("network", "create", name)

    def start_upstream(service, port):
        docker(
            "run",
            "-d",
            "--name",
            name + "-" + service,
            "--network",
            name,
            "--network-alias",
            service,
            "-v",
            f"{ROOT / 'tests/proxy_upstream.py'}:/upstream.py:ro",
            "python:3.12-alpine",
            "python",
            "/upstream.py",
            service,
            str(port),
        )

    def start_proxy():
        docker(
            "run",
            "-d",
            "--name",
            proxy,
            "--network",
            name,
            "-e",
            "PROXY_TIMEOUT_SECONDS=181",
            "-p",
            "127.0.0.1::80",
            proxy_image,
        )
        return int(docker("port", proxy, "80").stdout.strip().rsplit(":", 1)[1])

    def request(url, **kwargs):
        with httpx.Client(trust_env=False, timeout=5) as client:
            return client.get(url, **kwargs)

    def await_ready(url):
        for _ in range(100):
            try:
                if request(url).status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            time.sleep(0.2)
        pytest.fail(docker("logs", proxy, check=False).stdout)

    try:
        start_upstream("backend", 8000)
        start_upstream("frontend", 7860)
        http_port = start_proxy()
        base_url = f"http://127.0.0.1:{http_port}"
        await_ready(base_url + "/nginx-health")
        await_ready(base_url + "/")
        response = request(base_url + "/")
        assert response.status_code == 200
        assert "location" not in response.headers
        assert "strict-transport-security" not in response.headers
        for path in ("/api/v1/me", "/docs", "/docs/oauth2-redirect", "/openapi.json", "/redoc"):
            response = request(base_url + path)
            assert response.json()["service"] == "backend"
            assert response.json()["path"] == path
            assert "strict-transport-security" not in response.headers
        assert request(base_url + "/").json()["service"] == "frontend"
        with httpx.Client(trust_env=False, timeout=5) as client:
            posted = client.post(
                base_url + "/api/v1/conversations/1/turns",
                json={"content": "hello"},
                headers={
                    "Authorization": "Bearer test-token",
                    "X-Forwarded-Proto": "untrusted",
                    "X-Forwarded-For": "untrusted",
                    "Host": "proxy.test",
                },
            ).json()
        assert posted["method"] == "POST" and '"content":"hello"' in posted["body"]
        assert posted["headers"]["Authorization"] == "Bearer test-token"
        assert posted["headers"]["X-Forwarded-Proto"] == "http"
        assert posted["headers"]["X-Forwarded-For"] != "untrusted"

        with httpx.Client(trust_env=False, timeout=5) as client:
            started = time.monotonic()
            with client.stream("GET", base_url + "/gradio_api/queue/data") as response:
                lines = response.iter_lines()
                assert next(lines) == "data: first"
                assert time.monotonic() - started < 2
                assert "data: last" in list(lines)
        websocket = request(
            base_url + "/ws",
            headers={
                "Connection": "Upgrade",
                "Upgrade": "websocket",
                "Sec-WebSocket-Version": "13",
                "Sec-WebSocket-Key": "dGhlIHNhbXBsZSBub25jZQ==",
            },
        )
        assert websocket.status_code == 101
        assert websocket.headers["sec-websocket-accept"] == "s3pPLMBiTxaQ9kYGzzhZRbK+xOo="

        request(base_url + "/?secret=query-must-not-be-logged")
        assert "query-must-not-be-logged" not in docker("logs", proxy).stdout
        # Occupy the old IP before recreating the backend, so DNS refresh is exercised.
        docker("rm", "-f", name + "-backend")
        docker(
            "run",
            "-d",
            "--name",
            name + "-reserve",
            "--network",
            name,
            "--entrypoint",
            "sleep",
            proxy_image,
            "60",
        )
        start_upstream("backend", 8000)
        await_ready(base_url + "/api/v1/health")
        assert request(base_url + "/api/v1/health").json()["service"] == "backend"
    finally:
        docker("rm", "-f", *names, check=False)
        docker("network", "rm", name, check=False)
