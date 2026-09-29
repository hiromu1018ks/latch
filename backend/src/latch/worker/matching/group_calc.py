"""グループマッチ(Group Search)の純関数群・定数(06 §7〜§8・design §2.2〜2.6)。

DB・async・SQLを持たない純計算のみ(unit試験は決定的)。Pool構築の
cheap_score計算はlayer3の純関数をGroupEngineが呼ぶ(ここでは再実装しない)。
C(集約Calibration)はlatch_calc.LATCH_Cをimportして再利用(再定義しない)。
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime

from latch.worker.matching.latch_calc import LATCH_C

POOL_SEARCH_LIMIT = 50  # Pool検索のHNSW取得上限(K_vと同値・design §2.2)
POOL_LIMIT = 15  # 06 §8 D-24(グループ候補Pool上限)
GROUP_MIN = 3  # 06 §7(3〜4人の集合)
GROUP_MAX = 4


@dataclass(frozen=True)
class PoolEntry:
    """貪欲法の入力1件(Pool行または種=起点。design §2.3)。"""

    intent_id: uuid.UUID
    user_id: uuid.UUID
    participants_min: int
    participants_max: int


def normalize_ids(ids: Iterable[uuid.UUID]) -> list[uuid.UUID]:
    """intent_idsのsorted正規化(normalize_pairと同じ規律・design §2.8)。"""
    return sorted(ids)


def group_target_time(time_starts: Iterable[datetime]) -> datetime:
    """対象開始時刻 = max(メンバーtime_start)(pair_target_timeの集合版)。"""
    return max(time_starts)


def uuid_array(ids: Iterable[uuid.UUID]) -> list[uuid.UUID]:
    """uuid[]のbind param値(§9-14修正版): CAST(:x AS uuid[]) へ渡す list。

    asyncpgはuuid[]パラメータにシーケンスを要求する(文字列リテラルは
    「a sized iterable container expected」で拒否 — ws-7スーパーバイザー検証で発見)。
    SQL側のCAST(:x AS uuid[])がパラメータ型をuuid[]へ確定させるため、
    配列型推論への依存も起きない(ws-6 §2規律の可変長版)。
    """
    return list(ids)


def aggregate_score(mutual_scores: list[float]) -> float:
    """aggregate = H × min over ペア(MutualScore) × C(06 §8 D-06)。

    H は呼び出し前の集合再検証通過=1(不成立ならclosedとし集約しない —
    design §2.5手順3・解釈記録5)。丸めない。
    """
    return LATCH_C * min(mutual_scores)


def dominates(
    self_score: float,
    self_ids: list[uuid.UUID],
    other_score: float,
    other_ids: list[uuid.UUID],
) -> bool:
    """D-06通知順序の上位判定: other が self より上位か(design §2.6)。

    aggregate_score降順 → 同点はサイズ昇順 → さらに同点ならintent_id辞書順。
    両idsはsorted正規化済み前提(呼び出し側で保証)。
    """
    if other_score != self_score:
        return other_score > self_score
    if len(other_ids) != len(self_ids):
        return len(other_ids) < len(self_ids)
    return list(other_ids) < list(self_ids)


def _pair_key(a: uuid.UUID, b: uuid.UUID) -> tuple[uuid.UUID, uuid.UUID]:
    """互換行列のキー(a < b 正規化)。"""
    return (a, b) if a < b else (b, a)


def _settled(members: list[PoolEntry]) -> bool:
    """引用#3: |S| >= 3 かつ |S| >= max(min_i) かつ |S| <= min(max_i)。"""
    lo = max(m.participants_min for m in members)
    hi = min(m.participants_max for m in members)
    return len(members) >= GROUP_MIN and lo <= len(members) <= hi


def _feasible_size(entries: list[PoolEntry]) -> bool:
    """人数見込み: [max(min_i), min(max_i)] ∩ [3, 4] が空でない(design §2.3手順3-c)。

    lo > hi(区間自体が空 — 例: min=4の種に max=3の候補で [4,3])も空と判定
    する(design §4.1「min(max_i)=3の候補はmin=4種の集合に入れない」)。
    """
    lo = max(e.participants_min for e in entries)
    hi = min(e.participants_max for e in entries)
    return lo <= hi and lo <= GROUP_MAX and hi >= GROUP_MIN


def build_groups(
    pool: list[PoolEntry],
    seed: PoolEntry,
    compat: frozenset[tuple[uuid.UUID, uuid.UUID]],
) -> list[tuple[list[uuid.UUID], uuid.UUID]]:
    """貪欲法で3〜4人の集合を構成(06 §7・design §2.3手順1〜6)。

    pool はcheap_score降順(同点intent_id昇順)ソート済み・起点を含まない。
    seed(起点)は走査順の先頭に立つ(承認事項2・起点max>=3は呼び出し側が
    保証)。compat は互換ペアの集合(要素は (小id, 大id) タプル・design §2.3の
    互換行列)。戻り値は (sorted正規化intent_ids, 種id) のタプル列表
    (確定順・複数集合可。種idはmember_scores.seed_idと
    build_group_proposalの種先頭に使う)。不成立の集合(手順5)は種のみ消費
    して次の種へ(追加しかけた候補は残る)。
    """
    remaining: list[PoolEntry] = [seed, *pool]
    groups: list[tuple[list[uuid.UUID], uuid.UUID]] = []
    while True:
        # 手順2: 残りのうち先頭の種になれるIntent(max >= 3)
        idx = next(
            (i for i, e in enumerate(remaining) if e.participants_max >= GROUP_MIN),
            None,
        )
        if idx is None:
            break  # 手順6: 種になれるIntentが残っていない
        head = remaining.pop(idx)
        members: list[PoolEntry] = [head]
        for cand in list(remaining):
            if len(members) >= GROUP_MAX:
                break
            if any(cand.user_id == m.user_id for m in members):
                continue  # 作成user_id相異(手順3-b・行列にも同条件がある=二重防御)
            if not all(
                _pair_key(cand.intent_id, m.intent_id) in compat for m in members
            ):
                continue  # 現集合の全メンバーと互換(手順3-a)
            if not _feasible_size([*members, cand]):
                continue  # 人数見込み(手順3-c)
            members.append(cand)
            if _settled(members):
                break  # 手順4: 3人で成立したら4人へ拡張しない(引用#8と整合)
        if _settled(members):
            ids = normalize_ids(m.intent_id for m in members)
            groups.append((ids, head.intent_id))
            won = set(ids)
            remaining = [e for e in remaining if e.intent_id not in won]
        # 不成立(手順5)は種のみ消費(while冒頭で次の種を探す)
    return groups
