"""Standalone SQLite backup validation and narrowly scoped retention."""

import re
import sqlite3
from contextlib import closing
from datetime import UTC, datetime, timedelta

BACKUP_NAME = re.compile(r"chat-(\d{8}T\d{6}Z)-[0-9a-f]{8}\.db")


def verify_backup(path):
    if path.is_symlink() or not path.is_file():
        raise ValueError("일반 파일 형태의 백업이 필요합니다.")
    if any(path.with_name(path.name + suffix).exists() for suffix in ("-wal", "-shm", "-journal")):
        raise ValueError("WAL/저널 없이 완결된 백업을 사용하세요.")
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro&immutable=1", uri=True)) as db:
        if db.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
            raise ValueError("백업 무결성 검사에 실패했습니다.")


def stored_backups(directory):
    items = []
    for path in directory.iterdir():
        match = BACKUP_NAME.fullmatch(path.name)
        if not match or path.is_symlink() or not path.is_file():
            continue
        try:
            stamp = datetime.strptime(match[1], "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)
        except ValueError:
            continue
        items.append((stamp, path))
    return sorted(items, key=lambda item: (item[0], item[1].name), reverse=True)


def prune(directory, *, days=14, keep=3, now=None):
    if days < 1 or keep < 1:
        raise ValueError("보관 일수와 최소 개수는 1 이상이어야 합니다.")
    cutoff = (now or datetime.now(UTC)) - timedelta(days=days)
    removed = 0
    for stamp, path in stored_backups(directory)[keep:]:
        if stamp < cutoff:
            path.unlink()
            removed += 1
    return removed
