import json
import subprocess
import time
from datetime import UTC, datetime
from types import SimpleNamespace

from scripts import check_operations as checks


def test_containers_must_exist_and_be_healthy(monkeypatch):
    rows = [
        {"Service": "backend", "State": "running", "Health": "healthy"},
        {"Service": "frontend", "State": "running", "Health": "unhealthy"},
    ]
    monkeypatch.setattr(checks, "compose", lambda *a, **k: SimpleNamespace(stdout=json.dumps(rows)))
    result = {item.name: item.ok for item in checks.container_checks()}
    assert result == {"backend": True, "frontend": False, "proxy": False}


def test_expiring_certificate_and_bad_api_are_reported_separately(monkeypatch):
    monkeypatch.setattr(
        checks,
        "https_request",
        lambda host, path: (200, b'{"status":"unexpected"}', time.time() + 2 * 86400),
    )
    result = {item.name: item.ok for item in checks.https_checks("chat.example.org")}
    assert result == {"certificate": False, "frontend_https": True, "public_api": False}


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
