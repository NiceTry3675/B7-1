import json
import subprocess
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from scripts import check_operations as checks


def test_containers_must_exist_and_be_healthy(monkeypatch):
    rows = [
        {"Service": "backend", "State": "running", "Health": "healthy"},
        {"Service": "frontend", "State": "running", "Health": "unhealthy"},
    ]
    monkeypatch.setattr(checks, "compose", lambda *a, **k: SimpleNamespace(stdout=json.dumps(rows)))
    result = {item.name: item.ok for item in checks.container_checks()}
    assert result == {"backend": True, "frontend": False, "proxy": False}


def test_http_probe_uses_public_ip(monkeypatch):
    requests = []

    def request(host, port, path):
        requests.append((host, port, path))
        return 200, b'{"status":"ok"}' if path == "/api/v1/health" else b"UI"

    monkeypatch.setattr(checks, "http_request", request)
    result = checks.http_checks("http://203.0.113.10")
    assert {item.name: item.ok for item in result} == {"frontend_http": True, "public_api": True}
    assert requests == [("203.0.113.10", 80, "/"), ("203.0.113.10", 80, "/api/v1/health")]


def test_http_probe_reports_redirect_and_invalid_api_without_echoing_content(monkeypatch):
    def request(host, port, path):
        return (308, b"PRIVATE") if path == "/" else (200, b"SECRET")

    monkeypatch.setattr(checks, "http_request", request)
    result = checks.http_checks("http://127.0.0.1:8080")
    assert all(not item.ok for item in result)
    assert "PRIVATE" not in repr(result) and "SECRET" not in repr(result)


@pytest.mark.parametrize(
    "url",
    ["https://127.0.0.1", "http://user:secret@127.0.0.1", "http://127.0.0.1/path", "http://host:0"],
)
def test_http_probe_rejects_invalid_origin_before_connecting(monkeypatch, url):
    monkeypatch.setattr(
        checks, "http_request", lambda *args: pytest.fail("Unexpected HTTP request")
    )
    assert not checks.http_checks(url)[0].ok


def test_backup_check_rejects_old_or_corrupt_snapshots(tmp_path):
    source = tmp_path / "chat-20260101T000000Z-12345678.db"
    source.write_text("broken")
    assert not checks.backup_check(tmp_path, now=datetime(2026, 1, 3, tzinfo=UTC)).ok
    assert not checks.backup_check(tmp_path, now=datetime(2026, 1, 1, 1, tzinfo=UTC)).ok


def test_backend_failure_never_echoes_command_output(monkeypatch):
    def failed(*args, **kwargs):
        raise subprocess.CalledProcessError(1, ["docker"], output="PRIVATE", stderr="SECRET")

    monkeypatch.setattr(checks, "compose", failed)
    result = checks.backend_checks()
    assert all(not item.ok for item in result)
    assert "PRIVATE" not in repr(result) and "SECRET" not in repr(result)
