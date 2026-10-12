-- 体感アンケート回答の schema(alembic 0010と同一意味・05 v0.7・09 §2.3)。
--
-- - survey_responses: ラウンド(onboarding/week4)別の回答(ユーザー単位・単発)。
--   answersは5段階値のJSON TEXT・free_commentはweek4のみ任意。
--   UNIQUE (user_id, round)が「単発の配信」(09 §2.3)をDBレベルで強制する。
--   個別Intentとの紐付け列を持たない(08 §2.4)

CREATE TABLE IF NOT EXISTS survey_responses (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users (id),
    round TEXT NOT NULL CHECK (round IN ('onboarding', 'week4')),
    answers TEXT NOT NULL,
    free_comment TEXT,
    created_at INTEGER NOT NULL,
    UNIQUE (user_id, round)
);
