"""D1 adapter群(Workers版・2026-10-08移行計画 §6 T3)。

database.py は D1 API と同じ面(prepare/bind/first/all/run/batch)を
ローカルSQLiteで実装する。実Workersではenv.DBが同じAPIを提供するため、
adapter・試験はどちらでも動く(実D1での確認は別承認後)。
"""
