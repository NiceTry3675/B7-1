"""Export a live Compose database to the host, then rotate verified backup files."""

import argparse
import fcntl
import os
import sqlite3
import subprocess
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from scripts.backup_store import prune, verify_backup
from scripts.compose_support import ROOT, compose


def export_backup(directory, *, days=14, keep=3):
    if days < 1 or keep < 1:
        raise ValueError("보관 일수와 최소 개수는 1 이상이어야 합니다.")
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (directory / ".backup.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        name = f"chat-{stamp}-{uuid4().hex[:8]}.db"
        remote = f"/data/backups/.export-{uuid4().hex}.db"
        destination = directory / name
        with tempfile.TemporaryDirectory(prefix=".export-", dir=directory) as temporary:
            snapshot = Path(temporary) / "snapshot.db"
            try:
                compose(
                    "exec",
                    "-T",
                    "backend",
                    "python",
                    "-m",
                    "scripts.backup_db",
                    "/data/chat.db",
                    remote,
                    timeout=300,
                )
                compose("cp", f"backend:{remote}", str(snapshot), timeout=300)
                snapshot.chmod(0o600)
                verify_backup(snapshot)
                # Publish only a complete backup. Refuse to overwrite any existing path.
                with snapshot.open("rb") as handle:
                    os.fsync(handle.fileno())
                os.link(snapshot, destination)
            finally:
                # A failed removal is surfaced, and retention is skipped in that case.
                compose("exec", "-T", "backend", "rm", "-f", "--", remote)
        removed = prune(directory, days=days, keep=keep)
    return destination, removed


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, default=ROOT / "backups")
    parser.add_argument("--retention-days", type=int, default=14)
    parser.add_argument("--keep-at-least", type=int, default=3)
    args = parser.parse_args()
    try:
        _, removed = export_backup(
            args.directory, days=args.retention_days, keep=args.keep_at_least
        )
    except (OSError, ValueError, sqlite3.Error, subprocess.SubprocessError):
        parser.exit(
            1, "백업 실패: Docker 접근, 저장 공간, 백업 파일과 작업 중복 여부를 확인하세요.\n"
        )
    print(f"백업 무결성 확인 및 호스트 저장 완료; 오래된 백업 {removed}개 정리")


if __name__ == "__main__":
    main()
