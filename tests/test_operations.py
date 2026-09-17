import sqlite3

import pytest

from scripts.backup_db import backup


def test_online_backup_includes_wal(tmp_path):
    source, destination = tmp_path / "source.db", tmp_path / "backup.db"
    with sqlite3.connect(source) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("CREATE TABLE sample(value TEXT)")
        conn.execute("INSERT INTO sample VALUES('persisted')")
        conn.commit()
        backup(source, destination)
        with sqlite3.connect(destination) as restored:
            assert restored.execute("SELECT value FROM sample").fetchone()[0] == "persisted"
        with pytest.raises(ValueError):
            backup(source, destination)
        with pytest.raises(ValueError):
            backup(source, source)
