"""group_calc純関数のunit試験(design §4.1・06 §7〜§8 D-06/D-24)。"""

import uuid
from datetime import UTC, datetime, timedelta

from latch.worker.matching.group_calc import (
    GROUP_MAX,
    GROUP_MIN,
    POOL_LIMIT,
    POOL_SEARCH_LIMIT,
    PoolEntry,
    aggregate_score,
    build_groups,
    dominates,
    group_target_time,
    normalize_ids,
    uuid_array_text,
)

NOW = datetime(2026, 10, 1, 12, 0, 0, tzinfo=UTC)


def _uid(n: int) -> uuid.UUID:
    return uuid.UUID(f"00000000-0000-4000-8000-{n:012d}")


def _entry(
    n: int, *, pmin: int = 2, pmax: int = 4, user: int | None = None
) -> PoolEntry:
    return PoolEntry(
        intent_id=_uid(n),
        user_id=_uid(user if user is not None else 1000 + n),
        participants_min=pmin,
        participants_max=pmax,
    )


def _compat(*pairs: tuple[int, int]) -> frozenset[tuple[uuid.UUID, uuid.UUID]]:
    """全互換にするには全組み合わせを列挙する(ここでは指定ペアのみ互換)。"""
    out: set[tuple[uuid.UUID, uuid.UUID]] = set()
    for a, b in pairs:
        x, y = _uid(a), _uid(b)
        out.add((x, y) if x < y else (y, x))
    return frozenset(out)


def _full_compat(ids: list[int]) -> frozenset[tuple[uuid.UUID, uuid.UUID]]:
    return _compat(*[(a, b) for i, a in enumerate(ids) for b in ids[i + 1 :]])


def test_constants_pin_docs_values():
    assert POOL_LIMIT == 15  # 06 §8 D-24
    assert POOL_SEARCH_LIMIT == 50  # design §2.2(K_vと同値)
    assert GROUP_MIN == 3 and GROUP_MAX == 4  # 06 §7


def test_seed_max_ge_3_builds_three_member_group():
    """種(max>=3)+互換2件 → 3人集合確定(design §2.3手順1〜4)。"""
    compat = _full_compat([1, 2, 3])
    groups = build_groups([_entry(2), _entry(3)], _entry(1, pmax=4), compat)
    assert groups == [(normalize_ids([_uid(1), _uid(2), _uid(3)]), _uid(1))]


def test_pool_entry_max_lt_3_not_in_group_and_not_seed():
    """max=2のPool要素は人数見込みで弾かれ(手順3-c)・種にも選ばれない。

    種1+{8,9}で第1集合。残り[7(max=2),10]は7が種になれず10単独では
    3人未満 → 戻りは1集合のみ(もし7が集合に入っていれば{1,7,8}になる)。
    """
    compat = _full_compat([1, 2, 3, 4, 5, 6, 7, 8, 9, 10])
    groups = build_groups(
        [_entry(7, pmax=2), _entry(8), _entry(9), _entry(10)],
        _entry(1),
        compat,
    )
    assert groups == [(normalize_ids([_uid(1), _uid(8), _uid(9)]), _uid(1))]


def test_seed_max_lt_3_returns_empty():
    """防御: max<3の起点は呼び出し側(GroupEngine)が弾く前提・空リスト。"""
    groups = build_groups([_entry(2), _entry(3)], _entry(1, pmax=2), _compat())
    assert groups == []


def test_three_member_group_does_not_extend_to_four():
    """3人で成立したら4人へ拡張しない(引用#8のサイズ昇順と整合)。"""
    compat = _full_compat([1, 2, 3, 4])
    groups = build_groups([_entry(2), _entry(3), _entry(4)], _entry(1), compat)
    assert len(groups[0][0]) == 3


def test_min4_seed_collects_four_members():
    """min=4の種は4人まで追加を続ける(|S|>=max(min_i)=4で確定)。"""
    compat = _full_compat([1, 2, 3, 4])
    groups = build_groups(
        [_entry(2), _entry(3), _entry(4)], _entry(1, pmin=4, pmax=4), compat
    )
    assert len(groups[0][0]) == 4


def test_unreachable_group_not_generated():
    """min=4の種に候補2件 → |S|=3で確定できず不成立(手順5)。"""
    compat = _full_compat([1, 2, 3])
    groups = build_groups([_entry(2), _entry(3)], _entry(1, pmin=4), compat)
    assert groups == []


def test_inclusion_rejects_candidate_whose_max_is_too_small():
    """人数包含の逐次判定: min=4種の集合にmax=3の候補は入らない(手順3-c)。"""
    compat = _full_compat([1, 2, 3, 4, 5, 6])
    # 種min=4・候補(2..6)のうち max=3 の候補(2,3)は見込みが弾く
    groups = build_groups(
        [
            _entry(2, pmax=3),
            _entry(3, pmax=3),
            _entry(4),
            _entry(5),
            _entry(6),
        ],
        _entry(1, pmin=4, pmax=4),
        compat,
    )
    assert groups == [
        (
            normalize_ids([_uid(1), _uid(4), _uid(5), _uid(6)]),
            _uid(1),
        )
    ]


def test_user_conflict_skips_candidate():
    """作成user_id相異違反の候補はスキップ(手順3-b)。"""
    compat = _full_compat([1, 2, 3, 4])
    groups = build_groups(
        [_entry(2, user=1001), _entry(3), _entry(4)],  # 2は種(1)と同user
        _entry(1),
        compat,
    )
    assert groups == [
        (
            normalize_ids([_uid(1), _uid(3), _uid(4)]),
            _uid(1),
        )
    ]


def test_incompatible_candidate_skipped():
    """互換行列にないペアを含む候補はスキップ(手順3-a・全メンバーと互換)。"""
    compat = _full_compat([1, 3, 4])  # 1×2・2×3・2×4は非互換
    groups = build_groups([_entry(2), _entry(3), _entry(4)], _entry(1), compat)
    assert groups == [
        (
            normalize_ids([_uid(1), _uid(3), _uid(4)]),
            _uid(1),
        )
    ]


def test_multiple_groups_second_seed_from_remaining_pool():
    """複数集合: 確定集合のメンバーを除き残りから次の種(引用#16の下地)。"""
    compat = _full_compat([1, 2, 3, 4, 5, 6])
    groups = build_groups(
        [_entry(2), _entry(3), _entry(4), _entry(5), _entry(6)],
        _entry(1),
        compat,
    )
    assert [sorted(g[0]) for g in groups] == [
        sorted([_uid(1), _uid(2), _uid(3)]),
        sorted([_uid(4), _uid(5), _uid(6)]),
    ]
    assert groups[1][1] == _uid(4)  # 第2集合の種は残り走査先頭


def test_scan_order_follows_input_pool_order():
    """走査順は入力pool順(cheap降順ソートは呼び出し側の責務)。"""
    compat = _full_compat([1, 3, 4])  # 1×2も非互換(2は仲間に入れない)
    groups = build_groups([_entry(2), _entry(3), _entry(4)], _entry(1), compat)
    assert groups == [(normalize_ids([_uid(1), _uid(3), _uid(4)]), _uid(1))]


def test_aggregate_takes_min_over_pairs():
    assert aggregate_score([0.9, 0.7, 0.85]) == 0.7  # C=1.0(LATCH_C)
    assert aggregate_score([0.83]) == 0.83


def test_normalize_ids_sorts():
    a, b = _uid(9), _uid(2)
    assert normalize_ids([a, b]) == [b, a]


def test_group_target_time_takes_max():
    t1 = NOW + timedelta(hours=1)
    t2 = NOW + timedelta(hours=3)
    assert group_target_time([t1, t2, NOW]) == t2


def test_uuid_array_text_format():
    assert uuid_array_text([_uid(1), _uid(2)]) == (
        "{" + f'"{_uid(1)}"' + "," + f'"{_uid(2)}"' + "}"
    )


def test_dominates_ordering():
    a = [_uid(1), _uid(2), _uid(3)]
    b = [_uid(4), _uid(5), _uid(6)]
    small = [_uid(1), _uid(2)]
    assert dominates(0.7, a, 0.9, b)  # スコア降順
    assert not dominates(0.9, a, 0.7, b)
    assert dominates(0.8, a, 0.8, small)  # 同点はサイズ昇順(小さい方が上位)
    assert not dominates(0.8, small, 0.8, a)
    c = [_uid(1), _uid(2), _uid(9)]  # 辞書順で a < c
    assert dominates(0.8, a, 0.8, c) is False
    assert dominates(0.8, c, 0.8, a)  # aの方が上位
