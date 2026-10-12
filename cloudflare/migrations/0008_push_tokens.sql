-- プッシュ通知トークン登録の schema(alembic 0012と同一意味・05 v0.9・backlog V-01)。
--
-- - push_tokens: FCMトークン(Web Push)の登録。tokenはUNIQUE(端末+ブラウザ単位)。
--   同一トークンの別ユーザー再登録は所有権移動(upsert・アプリ層)。
--   user_idはFK CASCADE(退会でトークンも消える)。

CREATE TABLE IF NOT EXISTS push_tokens (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    token TEXT NOT NULL,
    platform TEXT NOT NULL DEFAULT 'web' CHECK (platform IN ('web')),
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    last_seen_at INTEGER,
    UNIQUE (token)
);
CREATE INDEX IF NOT EXISTS idx_push_tokens_user ON push_tokens (user_id);
