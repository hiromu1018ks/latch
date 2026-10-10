-- UI v2 Phase 1B: 参加取り消し(D-08)の schema(alembic 0009と同一意味)。
--
-- - latches.left_user_ids: 参加取り消し済みuser_idのJSON配列(書込拒否・
--   ALREADY_LEFT判定の永続化。statusはmatchedのまま)
-- - messages.sender_id のNULL許容: 参加取り消しのシステム行は送信者を持たない。
--   SQLiteはALTER COLUMN不可のためテーブル再構築で置き換える
--   (0001と同一定義から NOT NULL のみ外す・既存行はそのまま写す)

ALTER TABLE latches ADD COLUMN left_user_ids TEXT NOT NULL DEFAULT '[]';

CREATE TABLE messages_rebuild (
    id TEXT PRIMARY KEY,
    latch_id TEXT NOT NULL REFERENCES latches (id),
    sender_id TEXT REFERENCES users (id),
    body TEXT NOT NULL,
    created_at INTEGER NOT NULL
);
INSERT INTO messages_rebuild (id, latch_id, sender_id, body, created_at)
    SELECT id, latch_id, sender_id, body, created_at FROM messages;
DROP TABLE messages;
ALTER TABLE messages_rebuild RENAME TO messages;
CREATE INDEX IF NOT EXISTS idx_messages_latch ON messages (latch_id, created_at);
