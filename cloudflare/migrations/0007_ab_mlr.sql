-- A/B基盤・MLRベースラインの schema(alembic 0011と同一意味・05 v0.8・09 §5/D-02)。
--
-- - ab_experiments: A/B実験の登録(start/stopはCLIのみ・公開APIなし)。
--   control/treatment値はTEXT(型はexperiment_keyの適用点が定義)。
-- - ab_assignments: ユーザー割付。PRIMARY KEY (experiment_key, user_id)が
--   割付の安定性(再計算しない)をDBレベルで強制する。
-- - mlr_baselines: MLR週次中央値ベースライン(09 D-02の器・追記のみ・
--   判定はrecorded_at最新行)。users・Intentと紐付かない観測用独立表。

CREATE TABLE IF NOT EXISTS ab_experiments (
    experiment_key TEXT PRIMARY KEY,
    status TEXT NOT NULL CHECK (status IN ('running', 'stopped')),
    control_value TEXT NOT NULL,
    treatment_value TEXT NOT NULL,
    started_at INTEGER NOT NULL,
    stopped_at INTEGER,
    CHECK (status != 'stopped' OR stopped_at IS NOT NULL)
);

CREATE TABLE IF NOT EXISTS ab_assignments (
    experiment_key TEXT NOT NULL REFERENCES ab_experiments (experiment_key),
    user_id TEXT NOT NULL REFERENCES users (id),
    variant TEXT NOT NULL CHECK (variant IN ('control', 'treatment')),
    stratum TEXT NOT NULL,
    assigned_at INTEGER NOT NULL,
    PRIMARY KEY (experiment_key, user_id)
);

CREATE INDEX IF NOT EXISTS idx_ab_assignments_user ON ab_assignments (user_id);

CREATE TABLE IF NOT EXISTS mlr_baselines (
    id TEXT PRIMARY KEY,
    value REAL NOT NULL,
    source_weeks TEXT NOT NULL,
    recorded_at INTEGER NOT NULL
);
