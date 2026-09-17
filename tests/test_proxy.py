"""Run with RUN_PROXY_TESTS=1; uses isolated containers and local test certificates."""

import os
import ssl
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
        "SITE_ADDRESS=https://bad.example",
        "SITE_ADDRESS=host;bad",
        "PROXY_TIMEOUT_SECONDS=179",
    ],
)
def test_proxy_rejects_invalid_configuration(proxy_image, setting):
    result = docker("run", "--rm", "-e", setting, proxy_image, check=False)
    assert result.returncode != 0
    assert "must" in result.stdout + result.stderr


def test_proxy_bootstrap_https_routing_streaming_and_reload(proxy_image, tmp_path):
    name = "b7-proxy-test-" + uuid4().hex[:10]
    proxy = name + "-proxy"
    certificates, webroot = tmp_path / "certificates", tmp_path / "webroot"
    certificates.mkdir()
    webroot.mkdir(mode=0o755)
    challenge = webroot / ".well-known/acme-challenge"
    challenge.mkdir(parents=True)
    (challenge / "test-token").write_text("test-proof")
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
            "SITE_ADDRESS=proxy.test",
            "-e",
            "PROXY_TIMEOUT_SECONDS=181",
            "-v",
            f"{certificates}:/etc/letsencrypt:ro",
            "-v",
            f"{webroot}:/var/www/certbot:ro",
            "-p",
            "127.0.0.1::80",
            "-p",
            "127.0.0.1::443",
            proxy_image,
        )
        return [
            int(docker("port", proxy, str(p)).stdout.strip().rsplit(":", 1)[1]) for p in (80, 443)
        ]

    def request(url, **kwargs):
        # The integration test creates its own certificate and publishes only loopback ports.
        with httpx.Client(verify=False, trust_env=False, timeout=5) as client:
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

    def certificate():
        path = certificates / "live/proxy.test"
        path.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            [
                "openssl",
                "req",
                "-x509",
                "-newkey",
                "rsa:2048",
                "-nodes",
                "-days",
                "1",
                "-subj",
                "/CN=proxy.test",
                "-addext",
                "subjectAltName=DNS:proxy.test",
                "-keyout",
                str(path / "privkey.pem"),
                "-out",
                str(path / "fullchain.pem"),
            ],
            check=True,
            capture_output=True,
        )
        return (path / "fullchain.pem").read_text()

    try:
        start_upstream("backend", 8000)
        start_upstream("frontend", 7860)
        http_port, _ = start_proxy()
        http = f"http://127.0.0.1:{http_port}"
        await_ready(http + "/nginx-health")
        assert request(http + "/.well-known/acme-challenge/test-token").text == "test-proof"
        assert request(http + "/").status_code == 503
        assert request(http + "/api/v1/me").status_code == 503

        certificate()
        docker("rm", "-f", proxy)
        http_port, tls_port = start_proxy()
        http, https = f"http://127.0.0.1:{http_port}", f"https://127.0.0.1:{tls_port}"
        await_ready(http + "/nginx-health")
        redirected = request(http + "/docs?example=1")
        assert redirected.status_code == 308
        assert redirected.headers["location"] == "https://proxy.test/docs?example=1"
        assert request(http + "/.well-known/acme-challenge/test-token").text == "test-proof"
        await_ready(https + "/")
        for path in ("/api/v1/me", "/docs", "/docs/oauth2-redirect", "/openapi.json", "/redoc"):
            response = request(https + path)
            assert response.json()["service"] == "backend"
            assert response.json()["path"] == path
            assert response.headers["strict-transport-security"] == "max-age=31536000"
        assert request(https + "/").json()["service"] == "frontend"
        with httpx.Client(verify=False, trust_env=False, timeout=5) as client:
            posted = client.post(
                https + "/api/v1/conversations/1/turns",
                json={"content": "hello"},
                headers={
                    "Authorization": "Bearer test-token",
                    "X-Forwarded-Proto": "http",
                    "X-Forwarded-For": "untrusted",
                    "Host": "proxy.test",
                },
            ).json()
        assert posted["method"] == "POST" and '"content":"hello"' in posted["body"]
        assert posted["headers"]["Authorization"] == "Bearer test-token"
        assert posted["headers"]["X-Forwarded-Proto"] == "https"
        assert posted["headers"]["X-Forwarded-For"] != "untrusted"

        with httpx.Client(verify=False, trust_env=False, timeout=5) as client:
            started = time.monotonic()
            with client.stream("GET", https + "/gradio_api/queue/data") as response:
                lines = response.iter_lines()
                assert next(lines) == "data: first"
                assert time.monotonic() - started < 2
                assert "data: last" in list(lines)
        websocket = request(
            https + "/ws",
            headers={
                "Connection": "Upgrade",
                "Upgrade": "websocket",
                "Sec-WebSocket-Version": "13",
                "Sec-WebSocket-Key": "dGhlIHNhbXBsZSBub25jZQ==",
            },
        )
        assert websocket.status_code == 101
        assert websocket.headers["sec-websocket-accept"] == "s3pPLMBiTxaQ9kYGzzhZRbK+xOo="

        request(https + "/?secret=query-must-not-be-logged")
        assert "query-must-not-be-logged" not in docker("logs", proxy).stdout
        old = ssl.get_server_certificate(("127.0.0.1", tls_port))
        new = certificate()
        assert old != new
        docker("exec", proxy, "nginx", "-t")
        docker("exec", proxy, "nginx", "-s", "reload")
        for _ in range(50):
            if ssl.get_server_certificate(("127.0.0.1", tls_port)) == new:
                break
            time.sleep(0.1)
        else:
            pytest.fail("NGINX did not activate the renewed certificate")

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
        await_ready(https + "/api/v1/health")
        assert request(https + "/api/v1/health").json()["service"] == "backend"
    finally:
        docker("rm", "-f", *names, check=False)
        docker("network", "rm", name, check=False)
