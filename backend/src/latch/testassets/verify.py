"""m4-seed.yaml の機械検査(M4 ws-2・design §4-4・g2-plan §10方式の拡張)。

使い方: make seed-verify ARGS="--path ../docs/testassets/m4-seed.yaml"
検査項目(design §4-4):
(a) 構造(id一意・必須3フィールド・時刻D+1〜D+5内・raw_text300字以内・
    participants値域1-4・visibility/notification_level列挙内・users構造)
(b) 分布比率(時間帯60/90/120/30・カテゴリ150/120/30・志向210/90・
    18-19歳8人とbase+7日でも不変のマージン・visibility/notification全値)
(c) 意図的配置の成立(EX-xxxペアケースをreference_l1で再判定してexpected
    と突合・クラスタの構成属性検査・reject素材5件の構造)
(d) aggregate照合(reference_l1で全ペア独立再計算しYAML集計値と一致)
(e) 地名が許可リスト内
違反はすべてerrorsへ集めて一覧出力し、1件でもあればexit 1。
"""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

import yaml

from latch.testassets.reference_l1 import (
    FAIL_PRIORITY,
    JST,
    age_years_at,
    judge_all,
    judge_pair,
    judge_pair_group,
)
from latch.testassets.vocab import LOCATIONS, time_bucket_of

BUCKET_ORDER = ["day", "evening", "night", "other"]
CATEGORY_ORDER = ["drinking", "meal", "activity"]
TIME_TARGETS = {"day": 60, "evening": 90, "night": 120, "other": 30}
CATEGORY_TARGETS = {"drinking": 150, "meal": 120, "activity": 30}
ORIENTATION_TARGETS = {"one_to_one": 210, "group": 90}
VISIBILITY_VALUES = ["hidden_until_match", "summary_only"]
NOTIFICATION_VALUES = ["proposals_only", "nearby_also", "muted"]
USER_COUNT = 100
INTENT_COUNT = 300
UNDERAGE_COUNT = 8
REJECT_COUNT = 5
SEED_VALUE = 42


def _p(s: str) -> datetime:
    return datetime.fromisoformat(s)


def _intent_for_judge(i: dict) -> dict:
    """YAML読み込み表現(ISO文字列)をjudge_*の内部表現へ変換する。

    reference_l1の判定関数はintent内部表現(datetime実体・reference_l1
    docstring)を要求するため、time.start/endのみdatetimeへ変換する
    (他フィールドはそのまま)。
    """
    s = i["structured"]
    end = s["time"]["end"]
    return {
        **i,
        "structured": {
            **s,
            "time": {
                "start": _p(s["time"]["start"]),
                "end": _p(end) if end is not None else None,
            },
        },
    }


def verify(path: str) -> list[str]:
    """検査を実行し違反メッセージの一覧を返す(空=合格)。"""
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    meta = data["meta"]
    users = data["users"]
    intents = data["intents"]
    rejects = data["intended_reject_cases"]
    cases = data["expectations"]["cases"]
    aggregate = data["expectations"]["aggregate"]
    by_id = {i["id"]: i for i in intents}
    users_by_id = {u["id"]: u for u in users}
    # judge_*用の内部表現ビュー(birth_date=date実体・time=datetime実体)
    users_judge = {
        u["id"]: {**u, "birth_date": _p(u["birth_date"]).date()} for u in users
    }
    by_id_judge = {i["id"]: _intent_for_judge(i) for i in intents}
    base = _p(meta["base_datetime"])
    errs: list[str] = []

    # (a) 構造
    ids = [i["id"] for i in intents] + [u["id"] for u in users]
    if len(ids) != len(set(ids)):
        errs.append("id重複")
    if meta["intent_count"] != INTENT_COUNT or len(intents) != INTENT_COUNT:
        errs.append(f"intent数: meta={meta['intent_count']} 実測={len(intents)}")
    if meta["user_count"] != USER_COUNT or len(users) != USER_COUNT:
        errs.append(f"user数: meta={meta['user_count']} 実測={len(users)}")
    if meta["random_seed"] != SEED_VALUE:
        errs.append(f"random_seed={meta['random_seed']}(固定値42でない)")
    for u in users:
        if not u["display_name"] or u["birth_date"] is None:
            errs.append(f"{u['id']}: users契約欠落(display_name/birth_date)")
        if "profile" not in u:
            errs.append(f"{u['id']}: profile欠落")
    for i in intents:
        s = i["structured"]
        if (
            s["category"]["primary"] is None
            or s["time"]["start"] is None
            or s["location"]["name"] is None
        ):
            errs.append(f"{i['id']}: 必須3フィールド(category/time.start/location)欠落")
            continue
        if not (1 <= len(i["raw_text"]) <= 300):
            errs.append(f"{i['id']}: raw_text字数外({len(i['raw_text'])})")
        start = _p(s["time"]["start"])
        delta_days = (start - base).total_seconds() / 86400
        if not (0.9 < delta_days < 6.0):  # D+1〜D+5(design §2.3)
            errs.append(f"{i['id']}: 時刻がD+1〜D+5外(delta={delta_days:.2f}日)")
        if s["time"]["end"] is not None and _p(s["time"]["end"]) <= start:
            errs.append(f"{i['id']}: end<=start")
        pmin, pmax = s["participants"]["min"], s["participants"]["max"]
        if not (1 <= pmin <= 4 and 1 <= pmax <= 4 and pmin <= pmax):
            errs.append(f"{i['id']}: participants値域外({pmin}/{pmax})")
        if s["visibility"] not in VISIBILITY_VALUES:
            errs.append(f"{i['id']}: visibility値外")
        if s["notification_level"] not in NOTIFICATION_VALUES:
            errs.append(f"{i['id']}: notification_level値外")
        b = s["budget"]
        if b is not None and b["max"] is not None and b["max"] < 0:
            errs.append(f"{i['id']}: budget負値")
        r = s["location"]["radius_m"]
        if r is not None and r < 1:
            errs.append(f"{i['id']}: radius_m<1")
    # case→intent参照整合(EX素材が存在するか)
    for c in cases:
        for sd in c["intents"]:
            if sd not in by_id:
                errs.append(f"{c['id']}: 参照先{sd}が存在しない")

    # (b) 分布比率
    bucket_count = Counter(
        time_bucket_of(_p(i["structured"]["time"]["start"]).astimezone(JST).hour)
        for i in intents
    )
    cat_count = Counter(i["structured"]["category"]["primary"] for i in intents)
    ori_count = Counter(
        "group" if i["structured"]["participants"]["min"] >= 3 else "one_to_one"
        for i in intents
    )
    for name, counts, targets in [
        ("時間帯", bucket_count, TIME_TARGETS),
        ("カテゴリ", cat_count, CATEGORY_TARGETS),
        ("人数志向", ori_count, ORIENTATION_TARGETS),
    ]:
        for key, target in targets.items():
            if counts.get(key, 0) != target:
                errs.append(
                    f"{name}分布: {key} 実測{counts.get(key, 0)} vs 目標{target}"
                )
    underage_ages = Counter(
        age_years_at(_p(u["birth_date"]).date(), base.astimezone(JST).date())
        for u in users
        if age_years_at(_p(u["birth_date"]).date(), base.astimezone(JST).date()) < 20
    )
    n_underage = sum(underage_ages.values())
    if n_underage != UNDERAGE_COUNT:
        errs.append(f"18-19歳ユーザー: 実測{n_underage} vs 目標{UNDERAGE_COUNT}")
    for u in users:
        birth = _p(u["birth_date"]).date()
        if age_years_at(birth, base.astimezone(JST).date()) < 20:
            # D6: base+7日(設計§2.3のマージン上限+2日)でも18-19歳を保つ
            if (
                age_years_at(birth, (base + timedelta(days=7)).astimezone(JST).date())
                >= 20
            ):
                errs.append(f"{u['id']}: 18-19歳マージン不足(base+7日で20歳)")
    for field, values in [
        ("visibility", VISIBILITY_VALUES),
        ("notification_level", NOTIFICATION_VALUES),
    ]:
        seen = {i["structured"][field] for i in intents}
        for v in values:
            if v not in seen:
                errs.append(f"{field}={v}が1件もない(全値カバレッジ)")
    # 18-19歳ユーザーの全intentがalcohol false(design §2.4・規則7の例外)
    for i in intents:
        if i["user"] in {
            u["id"]
            for u in users
            if age_years_at(_p(u["birth_date"]).date(), base.astimezone(JST).date())
            < 20
        }:
            if i["structured"]["alcohol_involved"]:
                errs.append(f"{i['id']}: 18-19歳ユーザー×alcohol true")

    # (c) 意図的配置の成立(EX突合)
    for c in cases:
        if c["kind"] == "pair":
            ok, reason = judge_pair(
                by_id_judge[c["intents"][0]],
                by_id_judge[c["intents"][1]],
                users=users_judge,
                now=base,
            )
            exp_pass = c["expected"] == "pass"
            if ok != exp_pass:
                errs.append(
                    f"{c['id']}: 判定不一致 expected={c['expected']} "
                    f"actual={'pass' if ok else 'fail'}"
                )
            elif not exp_pass and reason != c["fail_reason"]:
                errs.append(
                    f"{c['id']}: fail_reason不一致 "
                    f"expected={c['fail_reason']} actual={reason}"
                )
        elif c["kind"] == "group_cluster":
            members = [by_id[sd] for sd in c["intents"]]
            exp = c["expected"]
            if (
                exp.get("distinct_users") is not None
                and len({m["user"] for m in members}) != exp["distinct_users"]
            ):
                errs.append(f"{c['id']}: distinct_users不一致")
            if exp.get("all_min_ge_3") and not all(
                m["structured"]["participants"]["min"] >= 3 for m in members
            ):
                errs.append(f"{c['id']}: min>=3不成立")
            if exp.get("all_pairs_pass_l1"):
                # people条件を除くクラスタ用判定(D9: 集合検索は1対1の
                # 2∈[min,max]判定の対象外・人数要件はall_min_ge_3が担保)
                members_judge = [by_id_judge[sd] for sd in c["intents"]]
                for x in range(len(members_judge)):
                    for y in range(x + 1, len(members_judge)):
                        ok, reason = judge_pair_group(
                            members_judge[x],
                            members_judge[y],
                            users=users_judge,
                            now=base,
                        )
                        if not ok:
                            errs.append(
                                f"{c['id']}: 全ペアpass違反"
                                f"({members[x]['id']}×{members[y]['id']}: {reason})"
                            )
            if exp.get("same_bucket"):
                for m in members:
                    if (
                        time_bucket_of(
                            _p(m["structured"]["time"]["start"]).astimezone(JST).hour
                        )
                        != exp["same_bucket"]
                    ):
                        errs.append(f"{c['id']}: 同Bucket違反({m['id']})")
            if (
                exp.get("same_place")
                and len({m["structured"]["location"]["name"] for m in members}) != 1
            ):
                errs.append(f"{c['id']}: 同地域違反")
            if (
                exp.get("same_category")
                and len({m["structured"]["category"]["primary"] for m in members}) != 1
            ):
                errs.append(f"{c['id']}: 同カテゴリ違反")
        else:
            errs.append(f"{c['id']}: 不明なkind({c['kind']})")
    if len(rejects) != REJECT_COUNT:
        errs.append(f"intended_reject_cases: 実測{len(rejects)} vs 目標{REJECT_COUNT}")
    for rc in rejects:
        birth = _p(users_by_id[rc["user"]]["birth_date"]).date()
        if age_years_at(birth, base.astimezone(JST).date()) >= 20:
            errs.append(f"{rc['id']}: reject素材ユーザーが20歳以上")
        if not rc["structured_intent"]["alcohol_involved"]:
            errs.append(f"{rc['id']}: alcohol_involved=trueでない")
        if rc["expected_status"] != 422 or rc["expected_code"] != "UNDER_AGE":
            errs.append(f"{rc['id']}: expected 422/UNDER_AGEでない")

    # (d) aggregate照合(reference_l1で全ペア独立再計算)
    verdicts = judge_all([by_id_judge[i["id"]] for i in intents], users_judge, now=base)
    recalc = Counter(r for ok, r in verdicts.values() if not ok)
    n_pass = sum(1 for ok, _ in verdicts.values() if ok)
    checks = [
        ("pair_total", aggregate["pair_total"], len(verdicts)),
        ("layer1_pass", aggregate["layer1_pass"], n_pass),
        ("layer1_fail", aggregate["layer1_fail"], len(verdicts) - n_pass),
    ]
    for name, declared, actual in checks:
        if declared != actual:
            errs.append(f"aggregate.{name}不一致: 記載{declared} vs 再計算{actual}")
    if aggregate["pair_total"] != INTENT_COUNT * (INTENT_COUNT - 1) // 2:
        errs.append(f"aggregate.pair_total={aggregate['pair_total']}(C(n,2)と不一致)")
    for reason in FAIL_PRIORITY:
        if aggregate["fail_breakdown"].get(reason, 0) != recalc.get(reason, 0):
            errs.append(
                f"fail_breakdown.{reason}不一致: "
                f"記載{aggregate['fail_breakdown'].get(reason, 0)}"
                f" vs 再計算{recalc.get(reason, 0)}"
            )

    # (e) 地名許可リスト
    for i in intents:
        if i["structured"]["location"]["name"] not in LOCATIONS:
            errs.append(f"{i['id']}: 地名が許可リスト外")

    return errs


def main(argv: list[str] | None = None) -> int:
    """CLI: make seed-verify ARGS="--path ../docs/testassets/m4-seed.yaml"。"""
    parser = argparse.ArgumentParser(description="m4-seed.yaml機械検査(M4 ws-2)")
    parser.add_argument("--path", default="../docs/testassets/m4-seed.yaml")
    args = parser.parse_args(argv)
    errs = verify(args.path)
    if errs:
        print(f"FAIL: {len(errs)}件の違反")
        for e in errs:
            print(f"  - {e}")
        return 1
    print(
        f"OK: {args.path} の機械検査に合格"
        "(a構造/b分布/c意図的配置/d aggregate照合/e地名)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
