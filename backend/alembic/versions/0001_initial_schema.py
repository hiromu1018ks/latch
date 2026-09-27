"""初期スキーマ: PostgreSQL+PostGIS+pgvector上の12テーブルとIndex(05 §2〜§3・06 §9)。

- CREATE EXTENSION(postgis / vector)— IF NOT EXISTS付きで冪等。downgradeでは
  DROPしない(public拡張はws-4の地物テーブル等が共有するリソース。design §2.11)
- friendshipsはMVP対象外のため作らない(05 §1〜2)
- CHECKは05が明記したもののみ(§2.8)。status値域はアプリ層の遷移管理(05 §6)
- 時刻列にDB時刻関数のDEFAULTを付けない(時刻はClock由来の明示値 — 10 第1節)
- uuid PKにはDEFAULT gen_random_uuid()を付ける(05はUUID生成方法を規定しない)
- 本番・stagingでは拡張作成権限を持つロールでの実行を前提(design §2.11)

Revision ID: 0001
Revises:
Create Date: 2026-09-27
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 拡張(design §2.11: IF NOT EXISTS付きで冪等)
    op.execute("CREATE EXTENSION IF NOT EXISTS postgis")
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    # --- 12テーブル(FK依存順。05 §2の表のカラム順どおり) ---

    # users
    op.execute("""
        CREATE TABLE users (
            id uuid NOT NULL DEFAULT gen_random_uuid(),
            display_name text NOT NULL,
            profile jsonb NOT NULL DEFAULT '{}',
            birth_date date NOT NULL,
            auth_provider text NOT NULL
                CHECK (auth_provider IN ('google', 'apple')),
            auth_subject text NOT NULL,
            location_preferences jsonb,
            visibility_preferences jsonb,
            trust_score numeric,
            created_at timestamptz NOT NULL,
            updated_at timestamptz NOT NULL,
            CONSTRAINT users_pkey PRIMARY KEY (id),
            CONSTRAINT uq_users_auth_provider_subject UNIQUE (auth_provider, auth_subject)
        )
    """)

    # intents
    op.execute("""
        CREATE TABLE intents (
            id uuid NOT NULL DEFAULT gen_random_uuid(),
            user_id uuid NOT NULL,
            category_primary text NOT NULL,
            alcohol_involved boolean NOT NULL,
            raw_text text NOT NULL,
            structured_data jsonb NOT NULL DEFAULT '{}',
            geo_center geography(Point, 4326),
            geo_radius_m integer,
            budget_max integer,
            participants_min smallint NOT NULL DEFAULT 2,
            participants_max smallint NOT NULL DEFAULT 2,
            visibility text NOT NULL DEFAULT 'hidden_until_match'
                CHECK (visibility IN ('hidden_until_match', 'summary_only')),
            notification_level text NOT NULL DEFAULT 'proposals_only'
                CHECK (notification_level IN ('proposals_only', 'nearby_also', 'muted')),
            status text NOT NULL,
            version integer NOT NULL DEFAULT 1,
            time_start timestamptz,
            time_end timestamptz,
            expires_at timestamptz,
            embedding vector(768),
            embedding_model text,
            created_at timestamptz NOT NULL,
            updated_at timestamptz NOT NULL,
            CONSTRAINT intents_pkey PRIMARY KEY (id),
            CONSTRAINT fk_intents_user FOREIGN KEY (user_id) REFERENCES users (id)
        )
    """)

    # match_candidates
    op.execute("""
        CREATE TABLE match_candidates (
            id uuid NOT NULL DEFAULT gen_random_uuid(),
            intent_a_id uuid NOT NULL,
            intent_b_id uuid NOT NULL,
            intent_a_version integer NOT NULL,
            intent_b_version integer NOT NULL,
            prev_latch_score numeric,
            prev_evaluated_at timestamptz,
            retrieval_score numeric,
            cheap_judge_score numeric,
            jev_result jsonb,
            latch_score numeric,
            status text NOT NULL,
            created_at timestamptz NOT NULL,
            updated_at timestamptz NOT NULL,
            CONSTRAINT match_candidates_pkey PRIMARY KEY (id),
            CONSTRAINT uq_match_candidates_intent_versions
                UNIQUE (intent_a_id, intent_b_id, intent_a_version, intent_b_version),
            CONSTRAINT fk_match_candidates_intent_a
                FOREIGN KEY (intent_a_id) REFERENCES intents (id),
            CONSTRAINT fk_match_candidates_intent_b
                FOREIGN KEY (intent_b_id) REFERENCES intents (id)
        )
    """)

    # group_candidates(intent_idsは配列参照のためFKなし。同一user検査はアプリ層 — 05 §2)
    op.execute("""
        CREATE TABLE group_candidates (
            id uuid NOT NULL DEFAULT gen_random_uuid(),
            intent_ids uuid[] NOT NULL
                CHECK (cardinality(intent_ids) BETWEEN 3 AND 4),
            member_scores jsonb NOT NULL DEFAULT '{}',
            aggregate_score numeric,
            prev_aggregate_score numeric,
            status text NOT NULL,
            created_at timestamptz NOT NULL,
            updated_at timestamptz NOT NULL,
            CONSTRAINT group_candidates_pkey PRIMARY KEY (id)
        )
    """)

    # latches(created_at NOT NULL / completed_at NULL は design §2.9 の解釈)
    op.execute("""
        CREATE TABLE latches (
            id uuid NOT NULL DEFAULT gen_random_uuid(),
            intent_ids uuid[] NOT NULL
                CHECK (cardinality(intent_ids) BETWEEN 2 AND 4),
            group_candidate_id uuid,
            proposal jsonb NOT NULL,
            score numeric NOT NULL,
            responses jsonb NOT NULL DEFAULT '[]',
            status text NOT NULL,
            response_deadline timestamptz NOT NULL,
            expires_at timestamptz NOT NULL,
            created_at timestamptz NOT NULL,
            completed_at timestamptz,
            CONSTRAINT latches_pkey PRIMARY KEY (id),
            CONSTRAINT fk_latches_group_candidate
                FOREIGN KEY (group_candidate_id) REFERENCES group_candidates (id)
        )
    """)

    # match_events(source_intent_id はFKなし — 削除済みIntentへの遅延Event保存の担保。
    # design §2.6)
    op.execute("""
        CREATE TABLE match_events (
            id uuid NOT NULL DEFAULT gen_random_uuid(),
            event_type text NOT NULL,
            source_intent_id uuid NOT NULL,
            payload jsonb NOT NULL,
            status text NOT NULL,
            created_at timestamptz NOT NULL,
            processed_at timestamptz,
            CONSTRAINT match_events_pkey PRIMARY KEY (id)
        )
    """)

    # latch_status_events(from_status NULL可 / user_id NULL可はシステム起因 — 05 §2)
    op.execute("""
        CREATE TABLE latch_status_events (
            id uuid NOT NULL DEFAULT gen_random_uuid(),
            latch_id uuid NOT NULL,
            from_status text,
            to_status text NOT NULL,
            user_id uuid,
            created_at timestamptz NOT NULL,
            CONSTRAINT latch_status_events_pkey PRIMARY KEY (id),
            CONSTRAINT fk_latch_status_events_latch
                FOREIGN KEY (latch_id) REFERENCES latches (id)
        )
    """)

    # blocks(UNIQUE(blocker_id, blocked_id)は05に明記がなくアプリ層 — design §2.9)
    op.execute("""
        CREATE TABLE blocks (
            id uuid NOT NULL DEFAULT gen_random_uuid(),
            blocker_id uuid NOT NULL,
            blocked_id uuid NOT NULL,
            created_at timestamptz NOT NULL,
            CONSTRAINT blocks_pkey PRIMARY KEY (id),
            CONSTRAINT fk_blocks_blocker FOREIGN KEY (blocker_id) REFERENCES users (id),
            CONSTRAINT fk_blocks_blocked FOREIGN KEY (blocked_id) REFERENCES users (id)
        )
    """)

    # reports(latch_id NULL可 = LATCH文脈を欠く通報も想定。status値域はM3 — design §2.9)
    op.execute("""
        CREATE TABLE reports (
            id uuid NOT NULL DEFAULT gen_random_uuid(),
            reporter_id uuid NOT NULL,
            reportee_id uuid NOT NULL,
            latch_id uuid,
            reason text NOT NULL,
            status text NOT NULL,
            created_at timestamptz NOT NULL,
            CONSTRAINT reports_pkey PRIMARY KEY (id),
            CONSTRAINT fk_reports_reporter FOREIGN KEY (reporter_id) REFERENCES users (id),
            CONSTRAINT fk_reports_reportee FOREIGN KEY (reportee_id) REFERENCES users (id),
            CONSTRAINT fk_reports_latch FOREIGN KEY (latch_id) REFERENCES latches (id)
        )
    """)

    # notifications
    op.execute("""
        CREATE TABLE notifications (
            id uuid NOT NULL DEFAULT gen_random_uuid(),
            user_id uuid NOT NULL,
            type text NOT NULL,
            payload jsonb NOT NULL,
            read_at timestamptz,
            created_at timestamptz NOT NULL,
            CONSTRAINT notifications_pkey PRIMARY KEY (id),
            CONSTRAINT fk_notifications_user FOREIGN KEY (user_id) REFERENCES users (id)
        )
    """)

    # messages
    op.execute("""
        CREATE TABLE messages (
            id uuid NOT NULL DEFAULT gen_random_uuid(),
            latch_id uuid NOT NULL,
            sender_id uuid NOT NULL,
            body text NOT NULL,
            created_at timestamptz NOT NULL,
            CONSTRAINT messages_pkey PRIMARY KEY (id),
            CONSTRAINT fk_messages_latch FOREIGN KEY (latch_id) REFERENCES latches (id),
            CONSTRAINT fk_messages_sender FOREIGN KEY (sender_id) REFERENCES users (id)
        )
    """)

    # calibration_records(匿名化一貫CHECK — 05 §2・design §2.8)
    op.execute("""
        CREATE TABLE calibration_records (
            id uuid NOT NULL DEFAULT gen_random_uuid(),
            latch_id uuid,
            intent_ids uuid[]
                CHECK (cardinality(intent_ids) BETWEEN 2 AND 4),
            prediction jsonb NOT NULL,
            proposal_snapshot jsonb NOT NULL,
            actual_responses jsonb NOT NULL,
            matched boolean NOT NULL,
            actual_attended boolean,
            cancelled_after boolean,
            anonymized_at timestamptz,
            created_at timestamptz NOT NULL,
            updated_at timestamptz NOT NULL,
            CONSTRAINT calibration_records_pkey PRIMARY KEY (id),
            CONSTRAINT fk_calibration_records_latch
                FOREIGN KEY (latch_id) REFERENCES latches (id),
            CONSTRAINT ck_calibration_records_anonymized
                CHECK (anonymized_at IS NULL OR (latch_id IS NULL AND intent_ids IS NULL))
        )
    """)

    # --- Index(05 §3のSQLそのまま) ---

    # アクティブIntentのマッチング検索(Layer 1)
    op.execute("""
        CREATE INDEX idx_intents_matching ON intents
          (category_primary, time_start, expires_at)
          WHERE status = 'active'
    """)
    # 地理(PostGIS)。draftのgeo_centerはNULLのため対象外(05 §3)
    op.execute("CREATE INDEX idx_intents_geo ON intents USING GIST (geo_center)")
    # Hard Constraint用の部分Index
    op.execute(
        "CREATE INDEX idx_intents_budget ON intents (budget_max) WHERE status = 'active'"
    )
    op.execute("""
        CREATE INDEX idx_intents_participants ON intents (participants_min, participants_max)
          WHERE status = 'active'
    """)
    # 期限処理・所有者参照。draftを含む(v0.4 — 05 §3)
    op.execute("""
        CREATE INDEX idx_intents_expires ON intents (expires_at)
          WHERE status IN ('draft', 'active', 'paused')
    """)
    op.execute("CREATE INDEX idx_intents_user ON intents (user_id, status)")
    # Vector(pgvector, HNSW)。draftはEmbeddingしないためNULL(05 §3)
    op.execute("""
        CREATE INDEX idx_intents_embedding ON intents
          USING hnsw (embedding vector_cosine_ops)
    """)

    # --- idempotencyの式UNIQUE索引(06 §9手順2・design §2.5 ---
    # versionはtextのままintegerキャストしない: 毒ペイロードのINSERT自体が
    # 索引評価エラーで失敗するとquarantined保存経路がDB側で塞がれるため)
    op.execute("""
        CREATE UNIQUE INDEX ux_match_events_idempotency ON match_events
          (event_type, source_intent_id, (payload->>'version'))
    """)


def downgrade() -> None:
    # テーブルを逆順DROP(拡張はDROPしない — design §2.11)
    op.execute("DROP TABLE calibration_records")
    op.execute("DROP TABLE messages")
    op.execute("DROP TABLE notifications")
    op.execute("DROP TABLE reports")
    op.execute("DROP TABLE blocks")
    op.execute("DROP TABLE latch_status_events")
    op.execute("DROP TABLE match_events")
    op.execute("DROP TABLE latches")
    op.execute("DROP TABLE group_candidates")
    op.execute("DROP TABLE match_candidates")
    op.execute("DROP TABLE intents")
    op.execute("DROP TABLE users")
