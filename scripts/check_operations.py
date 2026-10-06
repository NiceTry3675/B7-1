"""Read-only deployment checks: containers, HTTPS, API, SQLite, LLM and latest backup."""

import argparse
import http.client
import json
import re
import sqlite3
import ssl
import subprocess
import time
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

from scripts.backup_store import stored_backups
from scripts.compose_support import Check, compose, configuration, report
from scripts.restore_db import inspect_backup

DB_PROBE = (
    "import sqlite3; from contextlib import closing; from app.config import Settings; "
    "uri = Settings().database_path.resolve().as_uri() + '?mode=ro'; "
    "db = sqlite3.connect(uri, uri=True, timeout=5); "
    "assert db.execute('PRAGMA quick_check').fetchall() == [('ok',)]; "
    "assert db.execute('PRAGMA user_version').fetchone()[0] == 1; "
    "assert db.execute('PRAGMA foreign_key_check').fetchone() is None; db.close()"
)


def https_request(domain, path):
    with closing(
        http.client.HTTPSConnection(domain, timeout=10, context=ssl.create_default_context())
    ) as c:
        c.connect()
        expires = ssl.cert_time_to_seconds(c.sock.getpeercert()["notAfter"])
        c.request("GET", path)
        response = c.getresponse()
        # Never follow a redirect or emit response content from a failing endpoint.
        return response.status, response.read(1024 * 1024), expires


def https_checks(domain, *, min_cert_days=14):
    if not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?", domain):
        return [Check("https", False, "SITE_ADDRESS 형식을 확인하세요.")]
    checks = []
    try:
        status, _, expires = https_request(domain, "/")
        days = (expires - time.time()) / 86400
        checks.extend(
            [
                Check("certificate", days >= min_cert_days, f"인증서 잔여 기간 {int(days)}일."),
                Check("frontend_https", status == 200, "HTTPS 화면 경로 응답 확인."),
            ]
        )
    except (OSError, ValueError, KeyError, http.client.HTTPException):
        checks.append(
            Check("frontend_https", False, "DNS·TLS 인증서·HTTPS 화면 연결을 확인하세요.")
        )
    try:
        status, payload, _ = https_request(domain, "/api/v1/health")
        checks.append(
            Check(
                "public_api",
                status == 200 and json.loads(payload) == {"status": "ok"},
                "HTTPS 프록시를 통한 서비스 API 확인.",
            )
        )
    except (OSError, ValueError, KeyError, http.client.HTTPException):
        checks.append(Check("public_api", False, "HTTPS API 경로 연결 또는 응답 오류."))
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
    parser.add_argument("--min-cert-days", type=int, default=14)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    if args.max_backup_age_hours <= 0 or args.min_cert_days < 1:
        parser.error("백업 허용 시간과 인증서 최소 잔여 일수는 양수여야 합니다.")
    checks = container_checks()
    try:
        config = configuration()
        domain = str(config["services"]["proxy"]["environment"]["SITE_ADDRESS"])
        checks.extend(https_checks(domain, min_cert_days=args.min_cert_days))
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
        checks.append(Check("configuration", False, "유효 Compose 설정을 읽을 수 없습니다."))
    checks.extend(backend_checks())
    checks.append(backup_check(args.backup_directory, max_age_hours=args.max_backup_age_hours))
    raise SystemExit(report(checks, as_json=args.json))


if __name__ == "__main__":
    main()
