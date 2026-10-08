"""契約ケースとBaselineManifestの定義(計画 §6 T0・Interfaces)。

ContractCase は「request / clock / actor / expected_response / expected_effects」
をデータとして持つ(計画書T0 Interfaces)。期待結果に秘密情報・実利用者データを
含まない。HTTP経路・JSON契約・error code の一致を移行版でも保証するための
共通資産であり、片方の実装だけが満たす検査(SQL文字列・Redis鍵・binding名)は
ここに持ち込まない。

BaselineManifest は比較基準の固定(code_revision / schema_head / prompt_hashes /
model_ids / limits / fixture_hashes)。試験実行ごとに再生成し、保存済みmanifestと
突き合わせることで「基準が知らないうちに動く」ことを検出する。
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

BACKEND_DIR = Path(__file__).resolve().parents[2]
REPO_DIR = BACKEND_DIR.parent
FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"

# 将来回帰ケースの分類(T0: SQL fakeでは見逃す系統。バグを正解fixtureにしない)
REGRESSION_CATEGORIES = (
    "isolation_arg_inversion",  # 隔離(quarantine)書込みの引数逆転
    "redelivery",  # 同一仕事の再配送(at-least-once)
    "fairness",  # due仕事の公平な回収(先頭偏り・世代欠落)
    "redis_success_then_rollback",  # 外部成功後にDB更新が失敗する境界
)


@dataclass(frozen=True)
class ContractRequest:
    """1件のHTTP要求。actor=None は匿名。

    raw_body はMALFORMED_REQUEST系(本文がJSONでない)の試験用。
    path 内の {var} は setup ステップの capture で置換する。
    """

    method: str
    path: str
    json: Any = None
    raw_body: str | None = None
    actor: str | None = None
    headers: dict[str, str] = field(default_factory=dict)
    # 参照実装が固定Clockで動く場合の前提時刻(None=実装既定)
    note: str = ""


@dataclass(frozen=True)
class Expect:
    """期待する応答。

    error_code は error.code(05 第5節の形式)。
    json_equals は「その key 以下の部分一致」。リストは完全一致。
    json_contains は key の存在と型(値までは問わない)確認用。
    capture は応答JSONから切り出す変数名→パス(e.g. {"intent_id": "id"})。
    """

    status: int
    error_code: str | None = None
    json_equals: dict[str, Any] | None = None
    json_contains: tuple[str, ...] = ()
    capture: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class ContractCase:
    case_id: str
    area: str
    summary: str
    request: ContractRequest
    expect: Expect
    setup: tuple[tuple[str, ContractRequest, Expect], ...] = ()
    # label → request。応答の capture 変数は後続step・本体requestで使える
    # 事前状態の名前付きseed(implementation側harnessが用意する。e.g. pair_latch)
    seed: tuple[str, dict[str, Any]] | None = None
    effects: str = ""
    source_test: str = ""
    regression: str = ""  # REGRESSION_CATEGORIES のいずれか(将来回帰ケース)
    planned_for: str = ""  # 移行タスク番号(T3/T5等。参照実装では実行しない)

    @property
    def executes_on_reference(self) -> bool:
        return not self.planned_for


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_text(text: str) -> str:
    return _sha256_bytes(text.encode("utf-8"))


def _git_revision() -> str:
    out = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=REPO_DIR,
        capture_output=True,
        text=True,
        check=True,
    )
    return out.stdout.strip()


def _alembic_head() -> str:
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    script = ScriptDirectory.from_config(cfg)
    return script.get_current_head()


def build_baseline_manifest() -> dict[str, Any]:
    """BaselineManifest を現コードから生成する。

    limits・model・prompt は実装モジュールから直接importした値を使う
    (写しの drifting を防ぐ)。import失敗は基準破壊として表面化させる。
    """
    from latch.intents.prompt import PARSER_SYSTEM_PROMPT
    from latch.llm.anthropic import ANTHROPIC_PARSER_MODEL
    from latch.llm.anthropic_jev import ANTHROPIC_JEV_FALLBACK_MODEL
    from latch.llm.gemini import GEMINI_EMBEDDING_MODEL
    from latch.llm.jev import JEV_MODEL, JEV_QUESTIONS
    from latch.worker.matching.latch_calc import LATCH_THRESHOLD
    from latch.worker.matching.layer2 import K_VECTORS
    from latch.worker.matching.layer3 import K_CHEAP
    from latch.worker.matching.layer4 import K_J, ONE_ON_ONE_MIN

    fixture_files = {
        "g1_parser_struct": "g1-parser-struct.yaml",
        "g1_alcohol": "g1-alcohol.yaml",
        "g2_jev_goldset": "g2-jev-goldset.yaml",
        "m4_seed": "m4-seed.yaml",
    }
    fixtures_root = REPO_DIR / "docs" / "testassets"
    fixture_hashes = {
        key: _sha256_bytes((fixtures_root / name).read_bytes())
        for key, name in fixture_files.items()
    }
    jev_questions_json = json.dumps(JEV_QUESTIONS, ensure_ascii=False, sort_keys=True)
    return {
        "code_revision": _git_revision(),
        "schema_head": _alembic_head(),
        "prompt_hashes": {
            "parser_system_prompt": _sha256_text(PARSER_SYSTEM_PROMPT),
            "jev_questions": _sha256_text(jev_questions_json),
        },
        "model_ids": {
            "parser": ANTHROPIC_PARSER_MODEL,
            "jev_fallback": ANTHROPIC_JEV_FALLBACK_MODEL,
            "embedding": GEMINI_EMBEDDING_MODEL,
            "jev": JEV_MODEL,
        },
        "limits": {
            "k_vectors": K_VECTORS,
            "k_cheap": K_CHEAP,
            "k_jev": K_J,
            "one_on_one_min": ONE_ON_ONE_MIN,
            "latch_threshold": LATCH_THRESHOLD,
        },
        "fixture_hashes": fixture_hashes,
    }


def load_stored_manifest() -> dict[str, Any] | None:
    import yaml

    path = FIXTURES_DIR / "baseline_manifest.yaml"
    if not path.exists():
        return None
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def store_manifest(manifest: dict[str, Any]) -> Path:
    import yaml

    FIXTURES_DIR.mkdir(parents=True, exist_ok=True)
    path = FIXTURES_DIR / "baseline_manifest.yaml"
    path.write_text(
        yaml.safe_dump(manifest, allow_unicode=True, sort_keys=True),
        encoding="utf-8",
    )
    return path


def manifest_drift(stored: dict[str, Any], current: dict[str, Any]) -> list[str]:
    """stored と current の差分を「基準のどの面が動いたか」で返す。"""

    def walk(a: Any, b: Any, prefix: str) -> list[str]:
        if isinstance(a, dict) and isinstance(b, dict):
            diffs: list[str] = []
            for key in sorted(set(a) | set(b)):
                if key not in a:
                    diffs.append(f"{prefix}.{key}: 追加")
                elif key not in b:
                    diffs.append(f"{prefix}.{key}: 消失")
                else:
                    diffs.extend(walk(a[key], b[key], f"{prefix}.{key}"))
            return diffs
        if a != b:
            return [f"{prefix}: {a!r} → {b!r}"]
        return []

    return walk(stored, current, "manifest")


# -- B0(改善なし移植版)の呼出回数記録 --------------------------------------
#
# T0要件:「既存の同版再利用と画面debounceを含め、追加改善前の回数を記録する」。
# 改善(I1〜I6)の削減効果はこのB0記録との差で計算する(計画 §5.1)。


def b0_counts_path() -> Path:
    return FIXTURES_DIR / "b0_baseline_counts.yaml"
