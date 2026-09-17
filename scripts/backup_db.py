"""Consistent online backup, including committed WAL pages, without copying live DB files."""

import argparse
import os
import sqlite3
import tempfile
from pathlib import Path


def backup(source: Path, destination: Path):
    if not source.is_file():
        raise ValueError("원본 DB 파일이 없습니다.")
    if source.resolve() == destination.resolve() or destination.exists():
        raise ValueError("기존 파일에 덮어쓰지 않습니다. 새 백업 경로를 지정하세요.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=".backup-", dir=destination.parent)
    os.close(descriptor)
    temporary = Path(name)
    try:
        with sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True) as src:
            with sqlite3.connect(temporary) as dst:
                src.backup(dst)
                if dst.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise ValueError("백업 무결성 검사에 실패했습니다.")
        # Link refuses to overwrite an existing backup even if another process created it.
        os.link(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    backup(args.source, args.destination)
    print("백업 및 무결성 검사 완료")
