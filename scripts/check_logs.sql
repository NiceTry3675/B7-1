-- Local administrator inspection only. Never select password_hash or token_hash for reports.
PRAGMA foreign_keys;
PRAGMA integrity_check;
SELECT c.id AS conversation_id, c.user_id, p.name, c.created_at, c.updated_at
FROM conversations c JOIN personas p ON p.id=c.persona_id ORDER BY c.id DESC LIMIT 20;
SELECT conversation_id, id, sequence, status, error_code, created_at, completed_at
FROM chat_turns ORDER BY conversation_id DESC, sequence ASC LIMIT 100;
-- For an authorized evaluation, inspect a chosen conversation locally:
-- SELECT question, answer FROM chat_turns WHERE conversation_id=34 ORDER BY sequence;
