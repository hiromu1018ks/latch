-- D1 core schema (2026-10-08移行計画 §3・T3)
--
-- PostgreSQL(alembic 0001〜0008)からの変換規律(計画§3.1):
--   uuid          -> TEXT(APIと同じ文字列表現)
--   timestamptz   -> INTEGER(UTCマイクロ秒 — domain.time.micros/from_micros)
--   date          -> TEXT(ISO暦日)
--   jsonb         -> TEXT(JSON文字列・json1関数で検索)
--   uuid[]        -> TEXT(JSON配列・表示順を保持)
--   boolean       -> INTEGER CHECK IN (0,1)
--   geography     -> geo_lon REAL + geo_lat REAL(距離はWorkers側でWGS84計算)
--   vector(768)   -> embedding TEXT(JSON配列・cosineはWorkers側計算)
--   numeric(評点) -> REAL(float64・閾値比較は既存のまま)
--
-- 業務状態の正はD1。API層はこのschemaへ command 単位で書く(T3 adapters/d1)。
-- 旧PostgreSQL版は比較基準として残る(合格前に削除しない)。

PRAGMA foreign_keys = ON;

-- ============ 既存テーブル群(意味はalembic版と同一) ============

CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    profile TEXT NOT NULL DEFAULT '{}',
    birth_date TEXT NOT NULL,
    auth_provider TEXT NOT NULL CHECK (auth_provider IN ('google', 'apple')),
    auth_subject TEXT NOT NULL,
    location_preferences TEXT,
    visibility_preferences TEXT,
    trust_score REAL,
    consent_at INTEGER,                -- 0007: 同意記録(Redisではなく行で保持)
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    UNIQUE (auth_provider, auth_subject)
);

CREATE TABLE IF NOT EXISTS intents (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users (id),
    category_primary TEXT NOT NULL,
    category_secondary TEXT,
    alcohol_involved INTEGER NOT NULL CHECK (alcohol_involved IN (0, 1)),
    raw_text TEXT NOT NULL,
    structured_data TEXT NOT NULL DEFAULT '{}',
    geo_lon REAL,                       -- geography(Point,4326)の分解
    geo_lat REAL,
    geo_radius_m INTEGER,
    budget_max INTEGER,
    participants_min INTEGER NOT NULL DEFAULT 2,
    participants_max INTEGER NOT NULL DEFAULT 2,
    visibility TEXT NOT NULL DEFAULT 'hidden_until_match'
        CHECK (visibility IN ('hidden_until_match', 'summary_only')),
    notification_level TEXT NOT NULL DEFAULT 'proposals_only'
        CHECK (notification_level IN ('proposals_only', 'nearby_also', 'muted')),
    status TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1,
    time_start INTEGER,
    time_end INTEGER,
    expires_at INTEGER,
    embedding TEXT,                     -- vector(768) のJSON配列
    embedding_model TEXT,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_intents_user ON intents (user_id, status);
CREATE INDEX IF NOT EXISTS idx_intents_matching
    ON intents (category_primary, status, time_start);
CREATE INDEX IF NOT EXISTS idx_intents_budget
    ON intents (budget_max) WHERE status = 'active';
CREATE INDEX IF NOT EXISTS idx_intents_expires ON intents (expires_at);

CREATE TABLE IF NOT EXISTS match_candidates (
    id TEXT PRIMARY KEY,
    intent_a_id TEXT NOT NULL REFERENCES intents (id),
    intent_b_id TEXT NOT NULL REFERENCES intents (id),
    intent_a_version INTEGER NOT NULL,
    intent_b_version INTEGER NOT NULL,
    prev_latch_score REAL,
    prev_evaluated_at INTEGER,
    retrieval_score REAL,
    cheap_judge_score REAL,
    jev_result TEXT,
    latch_score REAL,
    status TEXT NOT NULL,
    skip_reason TEXT,                   -- 0003
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    UNIQUE (intent_a_id, intent_b_id, intent_a_version, intent_b_version)
);

CREATE TABLE IF NOT EXISTS group_candidates (
    id TEXT PRIMARY KEY,
    intent_ids TEXT NOT NULL            -- JSON配列(3〜4人)
        CHECK (json_array_length(intent_ids) BETWEEN 3 AND 4),
    member_scores TEXT NOT NULL DEFAULT '{}',
    aggregate_score REAL,
    prev_aggregate_score REAL,
    status TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS latches (
    id TEXT PRIMARY KEY,
    intent_ids TEXT NOT NULL            -- JSON配列(2〜4人・表示順保持)
        CHECK (json_array_length(intent_ids) BETWEEN 2 AND 4),
    group_candidate_id TEXT REFERENCES group_candidates (id),
    proposal TEXT NOT NULL,
    score REAL NOT NULL,
    responses TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL,
    response_deadline INTEGER NOT NULL,
    expires_at INTEGER NOT NULL,
    created_at INTEGER NOT NULL,
    completed_at INTEGER
);
-- 「openなlatchのintent集合重複禁止」(0004と同一意味。TEXT配列の等価比較)
CREATE UNIQUE INDEX IF NOT EXISTS uq_latches_intent_ids_open
    ON latches (intent_ids)
    WHERE status IN ('candidate', 'proposed', 'partial_accept');
CREATE UNIQUE INDEX IF NOT EXISTS uq_group_candidates_intent_ids_open
    ON group_candidates (intent_ids)
    WHERE status IN ('candidate', 'proposed');

CREATE TABLE IF NOT EXISTS match_events (
    id TEXT PRIMARY KEY,
    event_type TEXT NOT NULL,
    source_intent_id TEXT NOT NULL,     -- FKなし(削除済みIntentへの遅延Event)
    payload TEXT NOT NULL,
    status TEXT NOT NULL,
    discard_reason TEXT,                -- 削除済み破棄の記録(設計§2.6)
    created_at INTEGER NOT NULL,
    processed_at INTEGER
);
-- 冪等key(event_type, source_intent_id, payload.version) — 式indexは
-- table constraintにできないためCREATE UNIQUE INDEXで(0001と同一意味)
CREATE UNIQUE INDEX IF NOT EXISTS ux_match_events_idempotency
    ON match_events (event_type, source_intent_id,
                     json_extract(payload, '$.version'));

CREATE TABLE IF NOT EXISTS latch_status_events (
    id TEXT PRIMARY KEY,
    latch_id TEXT NOT NULL REFERENCES latches (id),
    from_status TEXT,
    to_status TEXT NOT NULL,
    user_id TEXT,
    created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_latch_status_events_latch
    ON latch_status_events (latch_id, created_at);

CREATE TABLE IF NOT EXISTS blocks (
    id TEXT PRIMARY KEY,
    blocker_id TEXT NOT NULL REFERENCES users (id),
    blocked_id TEXT NOT NULL REFERENCES users (id),
    created_at INTEGER NOT NULL,
    UNIQUE (blocker_id, blocked_id)     -- D1版: 制約へ昇格(重複INSERTを防ぐ)
);

CREATE TABLE IF NOT EXISTS reports (
    id TEXT PRIMARY KEY,
    reporter_id TEXT NOT NULL REFERENCES users (id),
    reportee_id TEXT NOT NULL REFERENCES users (id),
    latch_id TEXT REFERENCES latches (id),
    reason TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS notifications (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users (id),
    type TEXT NOT NULL,
    payload TEXT NOT NULL,
    read_at INTEGER,
    created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_notifications_user
    ON notifications (user_id, created_at);

CREATE TABLE IF NOT EXISTS messages (
    id TEXT PRIMARY KEY,
    latch_id TEXT NOT NULL REFERENCES latches (id),
    sender_id TEXT NOT NULL REFERENCES users (id),
    body TEXT NOT NULL,
    created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_latch ON messages (latch_id, created_at);

CREATE TABLE IF NOT EXISTS calibration_records (
    id TEXT PRIMARY KEY,
    latch_id TEXT REFERENCES latches (id),
    intent_ids TEXT                     -- JSON配列(2〜4)
        CHECK (intent_ids IS NULL
               OR json_array_length(intent_ids) BETWEEN 2 AND 4),
    prediction TEXT NOT NULL,
    proposal_snapshot TEXT NOT NULL,
    actual_responses TEXT NOT NULL,
    matched INTEGER NOT NULL CHECK (matched IN (0, 1)),
    actual_attended INTEGER CHECK (actual_attended IS NULL OR actual_attended IN (0, 1)),
    cancelled_after INTEGER CHECK (cancelled_after IS NULL OR cancelled_after IN (0, 1)),
    anonymized_at INTEGER,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    CHECK (anonymized_at IS NULL OR (latch_id IS NULL AND intent_ids IS NULL))
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_calibration_latch
    ON calibration_records (latch_id) WHERE latch_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS geofeatures (
    id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    kind TEXT NOT NULL,
    name TEXT NOT NULL,
    city_name TEXT,
    pref_name TEXT,
    lon REAL NOT NULL,
    lat REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_geofeatures_name ON geofeatures (name);

-- ============ D1新規: 業務commandの基盤(計画§3.2) ============

-- command marker: 業務更新と同じbatchで確定する冪等化marker。
-- 再配送で同じcommand_idが来た場合、applied_atの有無で結果を再構成する。
CREATE TABLE IF NOT EXISTS commands (
    id TEXT PRIMARY KEY,                -- command_id(内部再配送の識別)
    kind TEXT NOT NULL,                 -- apply_intent/response/safety/proposal
    target_id TEXT NOT NULL,            -- 主対象(latch/intent/user id)
    requested_at INTEGER NOT NULL,
    applied_at INTEGER,
    result_status TEXT,                 -- applied/conflict/rejected
    result_response TEXT                -- 保存済みAPI応答(JSON)
);

-- 巻き戻し用journal: 業務更新と同じbatchで保存(計画§3.2)。
-- replayは状態の再適用のみを行い、LLMや通知を再送しない。
-- 本文を含むため機微データとしてバックアップと同じ保護対象。
CREATE TABLE IF NOT EXISTS journal (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    command_id TEXT NOT NULL REFERENCES commands (id),
    sequence_in_command INTEGER NOT NULL,
    change_kind TEXT NOT NULL,          -- upsert/delete
    table_name TEXT NOT NULL,
    row_id TEXT NOT NULL,
    before_row TEXT,                    -- 変更前JSON(NULL=insert)
    after_row TEXT,                     -- 変更後JSON(NULL=delete)
    reversed_at INTEGER,                -- 旧DBへの再適用済み印
    created_at INTEGER NOT NULL,
    UNIQUE (command_id, sequence_in_command)
);

-- 削除・同意撤回: 対象を利用不可にする状態と削除jobを同時に確定(§3.2)。
-- 物理削除が途中でもAPI・検索・cache・遅延応答から対象へアクセスさせない。
CREATE TABLE IF NOT EXISTS deletion_tombstones (
    id TEXT PRIMARY KEY,
    target_kind TEXT NOT NULL CHECK (target_kind IN ('intent', 'user')),
    target_id TEXT NOT NULL,
    reason TEXT NOT NULL,               -- user_delete/intent_delete/consent_withdraw
    state TEXT NOT NULL DEFAULT 'pending',  -- pending/processing/done
    created_at INTEGER NOT NULL,
    UNIQUE (target_kind, target_id, reason)
);

-- Intent成立の所有権(§3.2): 共有Intentを持つ複数Latchの同時成立を
-- 一意制約で防ぐ。match確定時にINSERTし、競合した側はconflictへ。
CREATE TABLE IF NOT EXISTS intent_match_ownership (
    intent_id TEXT PRIMARY KEY REFERENCES intents (id),
    latch_id TEXT NOT NULL REFERENCES latches (id),
    confirmed_at INTEGER NOT NULL,
    released_at INTEGER                 -- 取消・解散時の解放
);

-- ============ D1新規: session・counter(Redisの置換・§2.3) ============

-- refresh token(family回転・再利用検出 — auth/sessions.py のD1版)
CREATE TABLE IF NOT EXISTS refresh_tokens (
    token_hash TEXT PRIMARY KEY,        -- sha256(token)
    sid TEXT NOT NULL,                  -- session family id
    provider TEXT NOT NULL,
    subject TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'rotated', 'revoked')),
    rotated_to TEXT,                    -- 回転先token_hash
    created_at INTEGER NOT NULL,
    expires_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_refresh_tokens_family
    ON refresh_tokens (sid, status);

-- session family(退会・logoutでの一括失効)
CREATE TABLE IF NOT EXISTS session_families (
    sid TEXT PRIMARY KEY,
    provider TEXT NOT NULL,
    subject TEXT NOT NULL,
    user_id TEXT,
    status TEXT NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'revoked')),
    revoked_at INTEGER,
    created_at INTEGER NOT NULL
);

-- JWT失効リスト(logout・退会後の旧access JWT拒否)
CREATE TABLE IF NOT EXISTS jwt_revocations (
    jti TEXT PRIMARY KEY,
    expires_at INTEGER NOT NULL,        -- TTL(この時刻以降は行削除可能)
    created_at INTEGER NOT NULL
);

-- 期限付きcounter(レート制限・Jev予算・D-16 — Redis INCR/EXPIREの置換)。
-- 原子的な予約・試行記録は UPDATE ... RETURNING で行う(T5で使用)。
CREATE TABLE IF NOT EXISTS usage_counters (
    key TEXT PRIMARY KEY,               -- 'rl:create:{user}:{jst_day}' 等
    value INTEGER NOT NULL DEFAULT 0,
    window_start INTEGER NOT NULL,
    expires_at INTEGER NOT NULL
);

-- ============ D1新規: jobs/outbox 最小schema(T3。完全版は0002_jobs.sql) ============

-- 意思の保存と同じbatchに入れる配送待ち記録(§4)。
-- Queuesへ届ける内容は参照のみ。原文・条件本文を含めない。
CREATE TABLE IF NOT EXISTS outbox (
    id TEXT PRIMARY KEY,
    event_id TEXT NOT NULL UNIQUE,      -- 重複配送検出
    intent_id TEXT,                     -- 対象(無いkindはNULL可)
    intent_version INTEGER,
    reason TEXT NOT NULL,
    due_generation INTEGER,             -- 評価世代(due管理・T8)
    writer_generation INTEGER NOT NULL DEFAULT 1,  -- 切替時の書込権世代
    payload_schema_version INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'sent', 'failed')),
    created_at INTEGER NOT NULL,
    sent_at INTEGER
);
CREATE INDEX IF NOT EXISTS idx_outbox_pending ON outbox (status, created_at);

-- jobs最小(T3): commandが確定させる後続仕事の記録。配送・claim・回収の
-- 完全な列・indexは T5 の 0002_jobs.sql が追加する。
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,                 -- evaluation/notify/retention/...
    ref_id TEXT,                        -- 対象ID(intent/latch/user)
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'running', 'succeeded', 'retryable',
                          'outcome_unknown', 'obsolete', 'failed')),
    attempts INTEGER NOT NULL DEFAULT 0,
    next_due_at INTEGER,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_jobs_pending ON jobs (status, next_due_at);
