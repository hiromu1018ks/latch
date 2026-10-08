"""BaselineManifestの再生成(意図した基準変更時にのみ使う)。

実行: cd backend && uv run python -m contracts.regen_manifest
(tests/ が import path にある前提 — pytest外で使う場合は
 PYTHONPATH=tests を付ける)
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from contracts import contract as contract_mod  # noqa: E402


def main() -> int:
    manifest = contract_mod.build_baseline_manifest()
    path = contract_mod.store_manifest(manifest)
    print(f"wrote {path}")
    for key, value in manifest.items():
        if isinstance(value, dict):
            print(f"  {key}:")
            for k, v in value.items():
                print(f"    {k}: {v}")
        else:
            print(f"  {key}: {value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
