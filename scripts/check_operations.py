"""Read-only deployment checks: containers, HTTP, API, SQLite, LLM and latest backup."""

import argparse
import http.client
import json
import sqlite3
import subprocess
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

from scripts.backup_store import stored_backups
from scripts.compose_support import Check, compose, report
from scripts.restore_db import inspect_backup

DB_PROBE = (
    "import sqlite3; from contextlib import closing; from app.config import Settings; "
    "uri = Settings().database_path.resolve().as_uri() + '?mode=ro'; "
    "db = sqlite3.connect(uri, uri=True, timeout=5); "
    "assert db.execute('PRAGMA quick_check').fetchall() == [('ok',)]; "
    "assert db.execute('PRAGMA user_version').fetchone()[0] == 1; "
    "assert db.execute('PRAGMA foreign_key_check').fetchone() is None; db.close()"
)


def http_request(host, port, path):
    with closing(http.client.HTTPConnection(host, port=port, timeout=10)) as c:
        c.request("GET", path)
        response = c.getresponse()
        # Inspect the original response; a redirect must not hide a wrong proxy configuration.
        return response.status, response.read(1024 * 1024)


def http_checks(base_url):
    try:
        url = urlsplit(base_url)
        port = url.port if url.port is not None else 80
        if (
            url.scheme != "http"
            or not url.hostname
            or url.username is not None
            or url.password is not None
            or url.path not in {"", "/"}
            or url.query
            or url.fragment
            or not 1 <= port <= 65535
        ):
            raise ValueError("Invalid HTTP origin")
    except ValueError:
        return [
            Check("http", False, "--http-url에 인증정보·경로 없는 http://주소[:포트]를 지정하세요.")
        ]
    checks = []
    for name, path in (("frontend_http", "/"), ("public_api", "/api/v1/health")):
        ok = False
        try:
            status, payload = http_request(url.hostname, port, path)
            ok = status == 200
            if name == "public_api":
                ok = ok and json.loads(payload) == {"status": "ok"}
        except (OSError, ValueError, http.client.HTTPException):
            pass
        checks.append(Check(name, ok, "HTTP 프록시 응답 확인. 주소·포트·서비스 상태를 확인하세요."))
    return checks


def backup_check(directory, *, max_age_hours=26, now=None):
    try:
        items = stored_backups(directory)
        if not items:
            return Check("backup", False, "관리 도구가 생성한 백업이 없습니다.")
        stamp, path = items[0]
        age = ((now or datetime.now(UTC)) - stamp).total_seconds() / 3600
        if not 0 <= age <= max_age_hours:
            return Check("backup", False, "최신 백업 시각이 허용 범위를 벗어났습니다.")
        inspect_backup(path)
        return Check("backup", True, "최신 백업의 시각·무결성·서비스 스키마 확인.")
    except (OSError, ValueError, sqlite3.Error):
        return Check("backup", False, "백업 경로·권한·무결성·서비스 스키마를 확인하세요.")


def container_checks():
    try:
        raw = compose("ps", "--all", "--format", "json").stdout
        try:
            rows = json.loads(raw)
        except ValueError:
            rows = [json.loads(line) for line in raw.splitlines() if line.strip()]
        if isinstance(rows, dict):
            rows = [rows]
        checks = []
        for name in ("backend", "frontend", "proxy"):
            matches = [row for row in rows if row.get("Service") == name]
            ok = len(matches) == 1 and matches[0].get("State") == "running"
            ok = ok and matches[0].get("Health") == "healthy"
            checks.append(Check(name, ok, "단일 컨테이너 실행 및 Docker healthcheck 확인."))
        return checks
    except (OSError, ValueError, TypeError, AttributeError, subprocess.SubprocessError):
        return [Check("containers", False, "Compose 컨테이너 상태를 읽을 수 없습니다.")]


def backend_checks():
    checks = []
    for name, command, timeout in (
        ("database", ["python", "-c", DB_PROBE], 20),
        ("llm", ["python", "-m", "scripts.check_llm"], 150),
    ):
        try:
            compose("exec", "-T", "backend", *command, timeout=timeout)
            checks.append(Check(name, True, "백엔드 컨테이너에서 읽기 전용 점검 완료."))
        except (OSError, subprocess.SubprocessError):
            checks.append(
                Check(name, False, "백엔드 내부 점검 실패. 해당 서비스 설정·상태를 확인하세요.")
            )
    return checks


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backup-directory", type=Path, default=Path("/var/backups/b7"))
    parser.add_argument("--max-backup-age-hours", type=float, default=26)
    parser.add_argument(
        "--http-url",
        default="http://127.0.0.1",
        help="HTTP 점검 주소. 기본값은 서버의 로컬 80번 포트.",
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    if args.max_backup_age_hours <= 0:
        parser.error("백업 허용 시간은 양수여야 합니다.")
    checks = container_checks()
    checks.extend(http_checks(args.http_url))
    checks.extend(backend_checks())
    checks.append(backup_check(args.backup_directory, max_age_hours=args.max_backup_age_hours))
    raise SystemExit(report(checks, as_json=args.json))


if __name__ == "__main__":
    main()
