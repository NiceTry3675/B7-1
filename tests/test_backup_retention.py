import sqlite3
import subprocess
from datetime import UTC, datetime

import pytest

from scripts import manage_backup
from scripts.backup_store import prune, verify_backup


def test_retention_preserves_minimum_and_unrelated_files(tmp_path):
    for day in range(1, 5):
        (tmp_path / f"chat-2026010{day}T000000Z-12345678.db").touch()
    unrelated = tmp_path / "manual.db"
    unrelated.touch()
    outside = tmp_path / "outside.db"
    outside.touch()
    (tmp_path / "chat-20250101T000000Z-12345678.db").symlink_to(outside)
    assert prune(tmp_path, days=14, keep=2, now=datetime(2026, 2, 1, tzinfo=UTC)) == 2
    assert (tmp_path / "chat-20260104T000000Z-12345678.db").exists()
    assert (tmp_path / "chat-20260103T000000Z-12345678.db").exists()
    assert unrelated.exists() and outside.exists()


def test_export_failure_does_not_prune(tmp_path, monkeypatch):
    old = tmp_path / "chat-20200101T000000Z-12345678.db"
    old.touch()

    def fail(*args, **kwargs):
        if "rm" not in args:
            raise subprocess.CalledProcessError(1, ["docker"])

    monkeypatch.setattr(manage_backup, "compose", fail)
    with pytest.raises(subprocess.CalledProcessError):
        manage_backup.export_backup(tmp_path, days=1, keep=1)
    assert old.exists()


def test_incomplete_and_corrupt_backups_are_rejected(tmp_path):
    source = tmp_path / "bad.db"
    source.write_text("not sqlite")
    with pytest.raises(sqlite3.DatabaseError):
        verify_backup(source)
    source.with_name("bad.db-wal").touch()
    with pytest.raises(ValueError, match="WAL"):
        verify_backup(source)
