"""T0契約資産(2026-10-08 Cloudflare移行計画 §6 T0)。

参照実装(PostgreSQL)と移行実装(D1/Workers)の双方へ同じケースを流すための
共通モジュール群。実装詳細(SQL・Redis鍵・binding)へ依存する検査はここに
置かず、各実装のintegration試験が担う。
"""
