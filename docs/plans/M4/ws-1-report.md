# M4 ws-1(staging・load環境構築)実行報告

- ブランチ: m4-ws-1 / ベース: e1eabca
- 日付: 2026-10-03
- 実行者: agent3

## 状態: BLOCKED(仕様矛盾の報告による実装停止)

Task 1の検証段階で**計画書内部の矛盾**(計画書が指示する変更が、計画書が禁止する
既存試験への変更を必須にする)を発見したため、起動指示「仕様矛盾を発見したら実装を
止めて報告せよ」に従い、Task 2以降に着手せず停止した。Task 1の実装自体は計画書§8の
コードどおり完了しており、worktreeに未コミットで残している(下記)。

### 発見した矛盾

- 計画書 §8 Task 1: `Settings` へ `clock_mode`・`auth_testkeys_dir` を追記せよ。
  キー名・env名(`LATCH_AUTH_TESTKEYS_DIR`)は固定値(§0・§4・Task 5・compose・
  §7手順がこの名前を参照)。
- 計画書 §5 禁止: 「`backend/tests/` の§4に列挙した以外の既存試験」は変更禁止。
  `backend/tests/unit/auth/test_auth_settings.py` は§4に**列挙されていない**。
  §3「既存試験への影響事前特定(Self-Review・計画時点で全数確認済み)」にも
  この試験の記載がない。
- しかし同試験の `test_auth_settings_are_exactly_eight_fields`
  (tests/unit/auth/test_auth_settings.py:57)は「`auth_` プレフィックスを持つ
  Settingsフィールドは8項目ぴったり」の**集合一致機械検査**であり、
  `auth_testkeys_dir`(auth_プレフィックス)の追加で**必ず**FAILする。
- したがって「Task 1を実行する」「§4列挙外の試験に触らない」「完了条件1の
  make test グリーン」の3つは両立しない。解消にはスーパーバイザーの裁定が要る
  (下記の候補)。

### 証拠

- Task 1 Step 2(RED確認): `cd backend && uv run pytest tests/unit/test_settings.py -v`
  → 期待どおり `AttributeError: 'Settings' object has no attribute 'clock_mode'`
  で2件FAIL・既存4件PASS(計画書§8 Task 1 Step 2の期待どおりの失敗)。
- Task 1 Step 3(実装: 計画書記載のコードをそのまま適用)後:
  - `make lint` → PASS(274 files already formatted / All checks passed)
  - `make test` → **`1 failed, 1219 passed, 261 deselected`**。失敗は1件のみ:
    - `tests/unit/auth/test_auth_settings.py::test_auth_settings_are_exactly_eight_fields`
    - `AssertionError: ... Extra items in the left set: 'auth_testkeys_dir'`
      (tests/unit/auth/test_auth_settings.py:62)
- Task 1のコミット規律(§0: 毎コミット make lint && make test グリーン後に
  コミット)を満たせないため**コミットは0件**。worktreeに計画書どおりの
  2ファイル変更が未コミットで残存(git status: `M backend/src/latch/settings.py` /
  `M backend/tests/unit/test_settings.py`・計+22行のみ)。

### スーパーバイザーの裁定が必要な解消候補(agent3は勝手に決めない)

- **候補A(変更が小さい・推奨)**: `test_auth_settings_are_exactly_eight_fields`
  の期待集合へ `"auth_testkeys_dir"` を1行追記する(機械検査の趣旨=認証設定の
  過剰供給防止は維持され、8項目→9項目の追認になる。§5禁止の一時解除が前提)。
  副次変更: 計画書§3の事前特定一覧に同試験を加筆する(報告書の補足でよい)。
- **候補B(変更が大きい・非推奨)**: settingsキー名を `auth_` 前綴外へ変更
  (例: `staging_testkeys_dir`)。固定値変更となり、env名・compose・Task 5試験・
  §7手順・design §2.4との整合を取り直す必要がある。
- **候補C(不可と考える)**: `auth_testkeys_dir` を追加しない — Task 5
  (staging専用鍵ペア注入・Review Focus 4)が成立しなくなる。

## 完了条件の検証結果

| # | 条件 | 結果 | 証拠(コマンド出力の要点) |
|---|---|---|---|
| 1 | make lint / make test(unit件数) | **BLOCKED** | lint PASS / test `1 failed, 1219 passed`(失敗=上記矛盾の1件のみ) |
| 2 | テストbasename一意 | PASS | `find backend/tests -name "test_*.py" \| awk -F/ '{print $NF}' \| sort \| uniq -d` → 空 |
| 3 | 実時間参照がclock.pyのみ・arch test | 未実施 | Task 2以降未着手 |
| 4 | ci無変更・依存・alembic無変更 | 未実施 | Task 2以降未着手 |
| 5 | compose config妥当性 | 未実施 | Task 7未着手 |
| 6 | 受け入れ手順1〜9 | 未実施 | Task 9未着手 |
| 7 | 変更ファイル=§4の19ファイル | 未実施 | BLOCKEDのため不成立(現状diff=2ファイル) |
| 8 | worker実稼働確認 | 未実施 | Task 9未着手 |

## make test-ci(1480維持)

- **スーパーバイザー検証待ち**(§0の指示によりagent3は未実行。本単位は
  マイグレーション追加なし)

## 受け入れ確認の記録(10 第5節の形式: 試験ID・実施日・環境・入力条件・合否)

- 未実施(BLOCKED・Task 9に到達していない)

## 固定値の変更有無(design §2・本計画)

- 案B(別プロジェクト)・ポート表・`latch:clock:control`・基準点JSON形式・
  6種choices・`.staging/keys`: **変更なし**(固定値には一切触れていない。
  上記矛盾の解消はスーパーバイザーの裁定を待つ)
- 本計画の追加2点(test_clock_wiring.py・.gitignore): 未実施

## コミット一覧

`(コミット0件 — Task 1のコミット規律「make lint && make test グリーン後に
コミット」を満たせなかったため。git log main..HEAD は空)`

## 補足(詰まった点・判断した点)

- 詰まった点=本報告冒頭「状態: BLOCKED」のとおり。実装の進め方に曖昧さはなく、
  計画書§8 Task 1のRED→GREEN自体は計画どおり完結した(Step 4で
  tests/unit/test_settings.py は6件全PASS)。
- 判断した点: ①既存試験を勝手に直さなかった(§5禁止・「計画書にない判断は
  勝手に決めない」)。②キー名を勝手に変えなかった(固定値)。③コミットせず
  worktreeに未コミット変更として残した(規律「毎コミットグリーン」の遵守と、
  スーパーバイザーがTask 1の実装を再利用できるようにするため)。
- 候補Aの裁定をいただいた場合の追記行は具体的には1行:
  `tests/unit/auth/test_auth_settings.py` の期待集合(62行目付近)への
  `"auth_testkeys_dir",` 追記。 Task 1を再コミット可能になる。
