"""Validate a backup or restore an offline database while preserving its previous files."""

import argparse
import os
import shutil
import sqlite3
import tempfile
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from scripts.backup_store import verify_backup

REQUIRED_COLUMNS = {
    "users": {"id", "email", "password_hash", "created_at"},
    "auth_sessions": {"id", "user_id", "token_hash", "expires_at", "revoked_at", "created_at"},
    "personas": {"id", "name", "description", "system_prompt", "is_active", "sort_order"},
    "conversations": {"id", "user_id", "persona_id", "created_at", "updated_at"},
    "chat_turns": {
        "conversation_id",
        "id",
        "sequence",
        "status",
        "question",
        "answer",
        "error_code",
        "created_at",
        "completed_at",
    },
}


def inspect_backup(source):
    verify_backup(source)
    with closing(
        sqlite3.connect(source.resolve().as_uri() + "?mode=ro&immutable=1", uri=True)
    ) as db:
        if db.execute("PRAGMA user_version").fetchone()[0] != 1:
            raise ValueError("현재 서비스의 스키마 버전 1 백업만 복원할 수 있습니다.")
        for table, expected in REQUIRED_COLUMNS.items():
            actual = {row[1] for row in db.execute(f"PRAGMA table_info({table})")}
            if not expected.issubset(actual):
                raise ValueError("서비스 DB 테이블·필드가 일치하지 않습니다.")
        if db.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise ValueError("백업에 외래 키 위반이 있습니다.")


def restore(source, target, *, apply=False, services_stopped=False, uid=None, gid=None):
    if (uid is None) != (gid is None) or any(v is not None and v < 0 for v in (uid, gid)):
        raise ValueError("UID와 GID를 함께 0 이상의 정수로 지정하세요.")
    previous = [target, *(target.with_name(target.name + s) for s in ("-wal", "-shm", "-journal"))]
    if source.resolve() in {path.resolve() for path in previous}:
        raise ValueError("복원 원본은 현재 DB와 별도 경로여야 합니다.")
    if any(path.is_symlink() or (path.exists() and not path.is_file()) for path in previous):
        raise ValueError("대상 DB와 WAL/저널은 일반 파일이어야 합니다.")
    inspect_backup(source)
    if not apply:
        return None
    if not services_stopped:
        raise ValueError("백엔드·프론트·백업 예약 중지를 확인하고 --services-stopped를 지정하세요.")
    if not target.parent.is_dir():
        raise ValueError("대상 DB 디렉터리가 없습니다.")
    owner = target.stat() if target.exists() else target.parent.stat()
    uid, gid = (owner.st_uid, owner.st_gid) if uid is None else (uid, gid)
    descriptor, name = tempfile.mkstemp(prefix=".restore-", dir=target.parent)
    os.close(descriptor)
    staged = Path(name)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    archive = target.parent / f"pre-restore-{stamp}-{uuid4().hex[:8]}"
    moved = []
    try:
        shutil.copyfile(source, staged)
        inspect_backup(staged)
        staged.chmod(0o600)
        os.chown(staged, uid, gid)
        with staged.open("rb") as handle:
            os.fsync(handle.fileno())
        archive.mkdir(mode=0o700)
        try:
            for path in previous:
                if path.exists():
                    destination = archive / path.name
                    os.replace(path, destination)
                    moved.append((path, destination))
            os.replace(staged, target)
        except BaseException:
            # A failed install must return already-moved files to their original names.
            for original, saved in reversed(moved):
                os.replace(saved, original)
            raise
    finally:
        staged.unlink(missing_ok=True)
    return archive


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("target", type=Path)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--services-stopped", action="store_true")
    parser.add_argument("--uid", type=int)
    parser.add_argument("--gid", type=int)
    args = parser.parse_args()
    try:
        archive = restore(
            args.source,
            args.target,
            apply=args.apply,
            services_stopped=args.services_stopped,
            uid=args.uid,
            gid=args.gid,
        )
    except (OSError, ValueError, sqlite3.Error):
        parser.exit(1, "복원 실패: 백업 무결성·스키마, 중지 확인, 경로와 파일 권한을 확인하세요.\n")
    if archive:
        print(f"복원 완료. 이전 DB·WAL·저널 보존 경로: {archive}")
    else:
        print(
            "백업 무결성·스키마 확인 완료. "
            "적용하려면 서비스 중지 후 --apply --services-stopped 지정."
        )


if __name__ == "__main__":
    main()
