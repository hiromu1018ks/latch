-- D1 再評価スケジュール(T8・計画§5.4・I3) — due時刻と変更世代
--
-- 旧構成(30分bucket + Redis 30分抑制)の欠陥対策:
-- - 抑制中の新しい版を「消さない」: 仕事はgenerationを進めて残る
-- - 完了は条件付き更新(claim後に来た更新を上書きしない)
-- - due照会はindex・上限付きbatch・cursorで公平(全Intent scanなし)

CREATE TABLE IF NOT EXISTS intent_evaluation_schedule (
    intent_id TEXT PRIMARY KEY,
    requested_revision INTEGER NOT NULL,   -- 要求時のIntent版
    requested_generation INTEGER NOT NULL, -- 評価世代(要求ごとに進む)
    evaluated_generation INTEGER,          -- 評価済み世代(NULL=未評価)
    dirty_reason TEXT NOT NULL,            -- initial/updated/timer/catchup/...
    next_evaluate_at INTEGER NOT NULL,     -- 次回必要時刻(min of candidates)
    lease_owner TEXT,
    lease_expires_at INTEGER,
    fencing_token INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL DEFAULT 'due'
        CHECK (status IN ('due', 'claimed', 'paused')),
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);
-- due照会: next_evaluate_at順・未回収世代(公平cursor用にintent_idを第2キーに)
CREATE INDEX IF NOT EXISTS idx_ies_due
    ON intent_evaluation_schedule (next_evaluate_at, intent_id)
    WHERE status = 'due';
