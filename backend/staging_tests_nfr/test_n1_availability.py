"""n1: 可用性(10 §4.3)。プローブ出力YAMLの検証(ハーネス側)。

プローブ実行自体はmake staging-probe(§8手順6・60分試験窓)が担い・本テストは
結果YAMLの機械検証(成功率99.5%・マーカータグ除外・dense記録)を担う。
自動復旧の観察(docker inspect時刻)は手順の記録を報告書へ転記する。
"""

from __future__ import annotations

from pathlib import Path

from conftest import RESULTS_DIR, record_item


def _latest_probe_yaml() -> Path:
    candidates = sorted(RESULTS_DIR.glob("ws7-probe-*.yaml"))
    if not candidates:
        raise AssertionError(
            "ws7-probe-*.yamlがありません。"
            "make staging-probe(§8手順6)を実行してください"
        )
    return candidates[-1]


def test_n1_10_probe_evidence():
    """プローブ結果の機械検証(成功率99.5%・タグ除外・dense併設)。"""
    import yaml

    path = _latest_probe_yaml()
    report = yaml.safe_load(path.read_text(encoding="utf-8"))
    verdict = report["verdict"]
    assert verdict["verdict_995"] is True, (
        f"成功率が99.5%未満: {verdict['main_success_rate']}"
    )
    assert verdict["main_excluded_planned_outage"] >= 1, (
        "計画停止タグの除外が効いていない(§8手順6のマーカー操作を確認)"
    )
    assert verdict["dense_total"] > 0, "denseプローブ(10秒間隔)の記録がない"
    assert verdict["main_counted"] > 0
    conditions = report["input_conditions"]
    assert conditions["duration_s"] >= 3500, f"試験窓が短い: {conditions['duration_s']}"
    record_item(
        item_id="ws7-n1-10",
        name="可用性プローブ検証(成功率99.5%・タグ除外・dense記録)",
        test="test_n1_10_probe_evidence",
        verdict="pass",
        observed={
            "evidence": str(path),
            "main_success_rate": verdict["main_success_rate"],
            "main_counted": verdict["main_counted"],
            "excluded_planned_outage": verdict["main_excluded_planned_outage"],
            "dense_total": verdict["dense_total"],
            "max_consecutive_failures": verdict["max_consecutive_failures"],
        },
    )
