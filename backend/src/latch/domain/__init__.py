"""DB非依存の業務計算・契約型の共通domain(2026-10-08移行計画 §6 T2)。

この配下からSQLAlchemy・asyncpg・Redis・Pub/Sub・ネイティブ地理処理へ
到達してはならない(tests/unit/test_domain_imports.py が強制する)。
PostgreSQL版とWorkers版の双方が同じ計算を共有するための境界。
"""
