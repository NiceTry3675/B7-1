import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

from app.errors import APIError
from app.seed import COMMON_PROMPT, PERSONAS


def now():
    return datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
 id INTEGER PRIMARY KEY, email TEXT NOT NULL UNIQUE, password_hash TEXT NOT NULL,
 created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS auth_sessions (
 id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id),
 token_hash TEXT NOT NULL UNIQUE, expires_at TEXT NOT NULL, revoked_at TEXT,
 created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS sessions_user ON auth_sessions(user_id);
CREATE TABLE IF NOT EXISTS personas (
 id TEXT PRIMARY KEY, name TEXT NOT NULL, description TEXT NOT NULL,
 system_prompt TEXT NOT NULL, is_active INTEGER NOT NULL CHECK(is_active IN (0,1)),
 sort_order INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS conversations (
 id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id),
 persona_id TEXT NOT NULL REFERENCES personas(id), created_at TEXT NOT NULL,
 updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS conversations_user ON conversations(user_id, id DESC);
CREATE TABLE IF NOT EXISTS chat_turns (
 conversation_id INTEGER NOT NULL REFERENCES conversations(id), id TEXT NOT NULL,
 sequence INTEGER NOT NULL CHECK(sequence > 0),
 status TEXT NOT NULL CHECK(status IN ('processing','completed','failed')),
 question TEXT NOT NULL, answer TEXT, error_code TEXT, created_at TEXT NOT NULL,
 completed_at TEXT,
 PRIMARY KEY (conversation_id, id), UNIQUE (conversation_id, sequence),
 CHECK((status='processing' AND answer IS NULL AND error_code IS NULL AND completed_at IS NULL)
 OR (status='completed' AND answer IS NOT NULL AND length(trim(answer))>0 AND error_code IS NULL
     AND completed_at IS NOT NULL)
 OR (status='failed' AND answer IS NULL AND error_code IS NOT NULL AND completed_at IS NOT NULL))
);
CREATE UNIQUE INDEX IF NOT EXISTS one_processing_turn ON chat_turns(conversation_id)
 WHERE status='processing';
CREATE INDEX IF NOT EXISTS stale_turns ON chat_turns(created_at) WHERE status='processing';
PRAGMA user_version=1;
"""


class Database:
    def __init__(self, path):
        self.path = path

    @contextmanager
    def connect(self, write=False):
        conn = sqlite3.connect(self.path, timeout=1)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA foreign_keys=ON")
            if write:
                conn.execute("BEGIN IMMEDIATE")
            yield conn
            if write:
                conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    def initialize(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(SCHEMA)
        with self.connect(write=True) as conn:
            for pid, name, description, prompt, order in PERSONAS:
                conn.execute(
                    "INSERT OR IGNORE INTO personas VALUES(?,?,?,?,1,?)",
                    (pid, name, description, COMMON_PROMPT + prompt, order),
                )

    def signup(self, email, password_hash):
        with self.connect(write=True) as conn:
            if conn.execute("SELECT 1 FROM users WHERE email=?", (email,)).fetchone():
                raise APIError("EMAIL_ALREADY_EXISTS")
            created = now()
            cur = conn.execute(
                "INSERT INTO users(email,password_hash,created_at) VALUES(?,?,?)",
                (email, password_hash, created),
            )
            return {"id": cur.lastrowid, "email": email, "created_at": created}

    def find_user(self, email):
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
            return dict(row) if row else None

    def new_session(self, user_id, token_hash, ttl):
        expires = (datetime.now(UTC) + timedelta(seconds=ttl)).isoformat().replace("+00:00", "Z")
        with self.connect(write=True) as conn:
            conn.execute(
                "INSERT INTO auth_sessions(user_id,token_hash,expires_at,created_at) "
                "VALUES(?,?,?,?)",
                (user_id, token_hash, expires, now()),
            )

    def authenticate(self, token_hash):
        with self.connect() as conn:
            row = conn.execute(
                "SELECT u.id,u.email,u.created_at,s.id AS session_id FROM auth_sessions s "
                "JOIN users u ON u.id=s.user_id WHERE s.token_hash=? "
                "AND s.revoked_at IS NULL AND s.expires_at>?",
                (token_hash, now()),
            ).fetchone()
            if not row:
                raise APIError("AUTH_REQUIRED")
            return dict(row)

    def logout(self, session_id):
        with self.connect(write=True) as conn:
            conn.execute("UPDATE auth_sessions SET revoked_at=? WHERE id=?", (now(), session_id))

    def personas(self):
        with self.connect() as conn:
            return [
                dict(r)
                for r in conn.execute(
                    "SELECT id,name,description FROM personas WHERE is_active=1 "
                    "ORDER BY sort_order,id"
                )
            ]

    @staticmethod
    def conversation(row):
        return {
            "id": row["id"],
            "persona": {"id": row["persona_id"], "name": row["name"]},
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    @staticmethod
    def owned(conn, user_id, cid):
        row = conn.execute(
            "SELECT c.*,p.name,p.system_prompt FROM conversations c "
            "JOIN personas p ON p.id=c.persona_id WHERE c.id=? AND c.user_id=?",
            (cid, user_id),
        ).fetchone()
        if not row:
            raise APIError("CONVERSATION_NOT_FOUND")
        return row

    def new_conversation(self, user_id, persona_id):
        with self.connect(write=True) as conn:
            if not conn.execute(
                "SELECT 1 FROM personas WHERE id=? AND is_active=1", (persona_id,)
            ).fetchone():
                raise APIError("PERSONA_NOT_FOUND")
            stamp = now()
            cur = conn.execute(
                "INSERT INTO conversations(user_id,persona_id,created_at,updated_at) "
                "VALUES(?,?,?,?)",
                (user_id, persona_id, stamp, stamp),
            )
            return self.conversation(self.owned(conn, user_id, cur.lastrowid))

    def conversations(self, user_id, limit, offset):
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT c.*,p.name FROM conversations c JOIN personas p "
                "ON p.id=c.persona_id WHERE c.user_id=? ORDER BY c.id DESC "
                "LIMIT ? OFFSET ?",
                (user_id, limit + 1, offset),
            ).fetchall()
            return {
                "items": [self.conversation(r) for r in rows[:limit]],
                "limit": limit,
                "offset": offset,
                "has_more": len(rows) > limit,
            }

    def turns(self, user_id, cid, limit, offset):
        with self.connect() as conn:
            self.owned(conn, user_id, cid)
            rows = conn.execute(
                "SELECT * FROM chat_turns WHERE conversation_id=? "
                "ORDER BY sequence LIMIT ? OFFSET ?",
                (cid, limit + 1, offset),
            ).fetchall()
            return {
                "conversation_id": cid,
                "items": [dict(r) for r in rows[:limit]],
                "limit": limit,
                "offset": offset,
                "has_more": len(rows) > limit,
            }

    def begin_turn(self, user_id, cid, tid, question):
        with self.connect(write=True) as conn:
            persona = self.owned(conn, user_id, cid)
            row = conn.execute(
                "SELECT * FROM chat_turns WHERE conversation_id=? AND id=?", (cid, tid)
            ).fetchone()
            if row:
                if row["question"] != question:
                    raise APIError("MESSAGE_ID_CONFLICT", turn_id=tid)
                if row["status"] == "processing":
                    raise APIError("MESSAGE_IN_PROGRESS", turn_id=tid)
                if row["status"] == "failed":
                    raise APIError(row["error_code"], turn_id=tid)
                return dict(row), None, None
            if conn.execute(
                "SELECT 1 FROM chat_turns WHERE conversation_id=? AND status='processing'", (cid,)
            ).fetchone():
                raise APIError("CONVERSATION_BUSY", turn_id=tid)
            sequence = conn.execute(
                "SELECT COALESCE(MAX(sequence),0)+1 FROM chat_turns WHERE conversation_id=?", (cid,)
            ).fetchone()[0]
            stamp = now()
            conn.execute(
                "INSERT INTO chat_turns(conversation_id,id,sequence,status,question,created_at) "
                "VALUES(?,?,?,'processing',?,?)",
                (cid, tid, sequence, question, stamp),
            )
            conn.execute("UPDATE conversations SET updated_at=? WHERE id=?", (stamp, cid))
            history = conn.execute(
                "SELECT question,answer FROM chat_turns WHERE conversation_id=? "
                "AND status='completed' ORDER BY sequence DESC LIMIT 5",
                (cid,),
            ).fetchall()
            return None, persona["system_prompt"], [dict(r) for r in reversed(history)]

    def finish_turn(self, cid, tid, *, answer=None, error=None):
        with self.connect(write=True) as conn:
            stamp = now()
            cur = conn.execute(
                "UPDATE chat_turns SET status=?,answer=?,error_code=?,completed_at=? "
                "WHERE conversation_id=? AND id=? AND status='processing'",
                ("failed" if error else "completed", answer, error, stamp, cid, tid),
            )
            if cur.rowcount:
                conn.execute("UPDATE conversations SET updated_at=? WHERE id=?", (stamp, cid))
            row = conn.execute(
                "SELECT * FROM chat_turns WHERE conversation_id=? AND id=?", (cid, tid)
            ).fetchone()
            if row is None:
                raise sqlite3.DatabaseError("Missing turn")
            return dict(row)

    def cleanup(self, stale_seconds):
        cutoff = (
            (datetime.now(UTC) - timedelta(seconds=stale_seconds))
            .isoformat()
            .replace("+00:00", "Z")
        )
        with self.connect(write=True) as conn:
            stamp = now()
            conn.execute(
                "UPDATE conversations SET updated_at=? WHERE id IN "
                "(SELECT conversation_id FROM chat_turns WHERE status='processing' "
                "AND created_at<?)",
                (stamp, cutoff),
            )
            return conn.execute(
                "UPDATE chat_turns SET status='failed',error_code='REQUEST_INTERRUPTED',"
                "completed_at=? WHERE status='processing' AND created_at<?",
                (stamp, cutoff),
            ).rowcount
