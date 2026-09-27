# api/worker 共用イメージ(design §3.1)。実依存のみ(--no-dev)をfrozenで導入。
FROM python:3.13-slim

COPY --from=ghcr.io/astral-sh/uv:0.12.19 /uv /uvx /usr/local/bin/

WORKDIR /app

# 依存レイヤー(srcの変更で再インストールされないよう分離)
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY src ./src
RUN uv sync --frozen --no-dev

ENV PATH="/app/.venv/bin:$PATH"

RUN useradd --create-home --uid 1000 latch
USER latch

CMD ["uvicorn", "latch.main:app", "--host", "0.0.0.0", "--port", "8000"]
