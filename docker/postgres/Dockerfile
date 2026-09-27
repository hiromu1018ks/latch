# design §2.5: 公式postgres(Debian/PGDG)に拡張2つをaptで足す。
# CREATE EXTENSION はws-1のマイグレーションが行う(雛形は同梱まで)。
FROM postgres:17

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        postgresql-17-postgis-3 \
        postgresql-17-pgvector \
    && rm -rf /var/lib/apt/lists/*
