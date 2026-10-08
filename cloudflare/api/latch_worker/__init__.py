"""Cloudflare API Worker(T1互換性probe・2026-10-08移行計画 §6 T1)。

probeは stub のみ。外部送信(JWKS・LLM)は行わない — 計画T1の
「probe用handlerはstubのみで外部送信しない」規律。

実配備でのprobeは別承認後に行う(本番 Workers 実計測)。
"""
