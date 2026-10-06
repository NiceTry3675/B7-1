import os
import sqlite3

import pytest

from app.db import Database
from scripts.backup_db import backup
from scripts.restore_db import restore


def snapshot(tmp_path):
    db = Database(tmp_path / "source.db")
    db.initialize()
    db.signup("restored@example.org", "test-only-hash")
    result = tmp_path / "snapshot.db"
    backup(db.path, result)
    return result


def test_restore_requires_stop_confirmation_and_preserves_old_files(tmp_path):
    source = snapshot(tmp_path)
    target = tmp_path / "chat.db"
    target.write_bytes(b"previous database")
    wal = tmp_path / "chat.db-wal"
    wal.write_bytes(b"previous wal")
    assert restore(source, target) is None
    with pytest.raises(ValueError, match="services-stopped"):
        restore(source, target, apply=True)
    assert target.read_bytes() == b"previous database"
    archive = restore(source, target, apply=True, services_stopped=True)
    assert (archive / "chat.db").read_bytes() == b"previous database"
    assert (archive / "chat.db-wal").read_bytes() == b"previous wal"
    assert not wal.exists()
    assert target.stat().st_mode & 0o777 == 0o600
    assert Database(target).find_user("restored@example.org") is not None


def test_failed_install_rolls_back_moved_database(tmp_path, monkeypatch):
    source = snapshot(tmp_path)
    target = tmp_path / "chat.db"
    target.write_bytes(b"original")
    replace = os.replace

    def fail_staged(src, dst):
        if src.name.startswith(".restore-"):
            raise OSError("simulated disk failure")
        replace(src, dst)

    monkeypatch.setattr(os, "replace", fail_staged)
    with pytest.raises(OSError):
        restore(source, target, apply=True, services_stopped=True)
    assert target.read_bytes() == b"original"


def test_unrelated_database_is_not_a_service_backup(tmp_path):
    source = tmp_path / "unrelated.db"
    with sqlite3.connect(source) as db:
        db.execute("CREATE TABLE unrelated(id INTEGER)")
    target = tmp_path / "chat.db"
    target.write_bytes(b"keep")
    with pytest.raises(ValueError, match="스키마"):
        restore(source, target, apply=True, services_stopped=True)
    assert target.read_bytes() == b"keep"
