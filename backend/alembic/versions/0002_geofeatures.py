"""geofeatures: 地物テーブル(単一テーブル+source列。design §2.1-A・§3.1)。

- CREATE/DROP EXTENSION は行わない(postgisは0001が作成済み — design §2.8)
- 時刻列にDB時刻関数のDEFAULTを付けない(created_atはClock由来の明示値)
- CHECKはdesignが明記したsource 2値のみ(0001と同じ線)
- データ取り込みはマイグレーションに含めない(CLI import-isj/import-osm)

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-27
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE geofeatures (
            id bigint GENERATED ALWAYS AS IDENTITY,
            source text NOT NULL
                CHECK (source IN ('isj_town', 'osm_poi')),
            kind text NOT NULL,
            name text NOT NULL,
            normalized_name text NOT NULL,
            full_normalized_name text,
            pref_name text,
            city_name text,
            source_code text NOT NULL,
            attrs jsonb NOT NULL DEFAULT '{}',
            geom geography(Point, 4326) NOT NULL,
            created_at timestamptz NOT NULL,
            CONSTRAINT geofeatures_pkey PRIMARY KEY (id)
        )
    """)
    # 地理検索(逆転の最近傍・将来のLayer 1)と正転の名称等値照合
    op.execute("CREATE INDEX idx_geofeatures_geom ON geofeatures USING GIST (geom)")
    op.execute("CREATE INDEX idx_geofeatures_name ON geofeatures (normalized_name)")


def downgrade() -> None:
    op.execute("DROP TABLE geofeatures")
