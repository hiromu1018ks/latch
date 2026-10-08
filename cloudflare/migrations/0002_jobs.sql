-- D1 jobs完全版(T5・計画§4) — 配送・claim・回収・試行記録
-- 0001_core.sql の最小jobs/outboxに列・indexを追加する。

-- jobs: lease・fencing・回収に必要な列(計画§4)
ALTER TABLE jobs ADD COLUMN kind_version INTEGER NOT NULL DEFAULT 1;
ALTER TABLE jobs ADD COLUMN lease_owner TEXT;
ALTER TABLE jobs ADD COLUMN lease_expires_at INTEGER;
ALTER TABLE jobs ADD COLUMN fencing_token INTEGER NOT NULL DEFAULT 0;
ALTER TABLE jobs ADD COLUMN last_error TEXT;
ALTER TABLE jobs ADD COLUMN max_attempts INTEGER NOT NULL DEFAULT 5;

-- 評価の処理権(I1・計画§5.2): 厳密な評価キーで原子的に確保
CREATE TABLE IF NOT EXISTS evaluation_claims (
    evaluation_key TEXT PRIMARY KEY,      -- EvaluationKey.pair_key+hash
    kind TEXT NOT NULL,                   -- jev/embedding/parse
    owner TEXT NOT NULL,
    lease_expires_at INTEGER NOT NULL,
    fencing_token INTEGER NOT NULL DEFAULT 1,
    intent_versions TEXT NOT NULL DEFAULT '{}',  -- 対象版(適用先確認)
    status TEXT NOT NULL DEFAULT 'claimed'
        CHECK (status IN ('claimed', 'completed', 'obsolete')),
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);

-- 全LLM試行の記録(計画§4: 送信前にprepared・応答後にusage追記)。
-- 本文を持たない。usage不明はNULL(0円として計上しない)。
CREATE TABLE IF NOT EXISTS llm_attempts (
    attempt_id TEXT PRIMARY KEY,
    system TEXT NOT NULL,                 -- parser/embedding/jev/jev_fallback
    job_id TEXT,
    evaluation_key TEXT,
    requested_provider TEXT NOT NULL,
    requested_model TEXT NOT NULL,
    actual_provider TEXT,
    actual_model TEXT,                    -- fallback時の実応答モデル
    price_version TEXT,                   -- 見積単価表の版
    status TEXT NOT NULL
        CHECK (status IN ('prepared', 'sending', 'succeeded', 'failed',
                          'unknown')),
    error_kind TEXT,                      -- timeout/provider_error/invalid_output
    input_tokens INTEGER,
    output_tokens INTEGER,
    cost_estimate_micros INTEGER,         -- 推定費用(未確定でも概算可)
    cost_confirmed_micros INTEGER,        -- 確認済み費用(不明はNULLのまま)
    sdk_internal_retry INTEGER NOT NULL DEFAULT 0,  -- SDK内部再試行回数
    started_at INTEGER NOT NULL,
    prepared_at INTEGER NOT NULL,
    finished_at INTEGER,
    created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_llm_attempts_job ON llm_attempts (job_id);
CREATE INDEX IF NOT EXISTS idx_llm_attempts_status_time
    ON llm_attempts (status, prepared_at);
