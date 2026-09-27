# LATCH backend

FastAPIバックエンド(uv・srcレイアウト・Python 3.13)。仕様は `../docs/` を参照。

## クイックスタート

```bash
mise install            # .mise.toml の uv を導入(初回のみ)
make setup              # uv sync(backend/.venv 構築)
make lint && make test  # 毎コミットの規律
make up && make ps      # ci常設環境(db/redis/api/worker)
make test-ci            # unit+integration
```

## 規律

- 製品コード(`src/latch/`)で実時間を直接参照しない — すべて `latch.core.clock` 経由(C2)。
  `tests/unit/test_arch_no_direct_time.py` が毎コミットで強制する
- `python`/`pip` を直接使わない。`uv run` 経由
- 新しい依存は計画書のスコープ確認を経て追加する(現状: fastapi/uvicorn/pydantic-settings のみ)
