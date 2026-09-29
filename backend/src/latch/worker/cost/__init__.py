"""Jevコスト保護(D-16・06 §5)と再評価頻度ガード(M2 ws-4・design §2.4)。

Guard呼び出し(Layer 4実行直前)と record_execution(実際のAPI呼び出し後)は
ws-5。本単位のパイプライン組み込みは ReevalGuard のみ(design §2.6)。
"""
