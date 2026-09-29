# M2 ws-7(グループマッチ Group Search)実行報告

- ブランチ: m2-ws-7 / ベース: d5ef2ad
- 日付: 2026-09-29
- 実行者: agent3

## 完了条件の検証結果
| # | 条件 | 結果 | 証拠(コマンド出力の要点) |
|---|---|---|---|
| 1 | make lint / make test | PASS | `All checks passed!` / `959 passed, 159 deselected in 6.84s`(unit全件グリーン。159 deselected=integration〔既存149+本単位10〕) |
| 2 | integration 10試験の収集 | 収集確認済み/test-ci=スーパーバイザー検証待ち | `uv run pytest --collect-only tests/integration/test_matching_groupengine.py -q` → `10 tests collected in 0.05s`(exit 0) |
| 3 | 実時間参照がclock.pyのみ | PASS | `rg -n 'datetime\.now\|utcnow\|time\.time\|time\.monotonic\|time\.sleep\|from time import' backend/src` → `backend/src/latch/core/clock.py:33` の1件のみ。`pytest tests/unit/test_arch_no_direct_time.py` → `1 passed` |
| 4 | alembic 0001〜0004・docs無変更+チェーン確認 | PASS | `git diff --stat main -- 'backend/alembic/versions/0001*' …0004* docs` → 出力なし(空)。チェーン静的確認 `heads= ['0005']`・walk=[0005,0004,0003,0002,0001] |
| 5 | 変更ファイル=§4の22ファイル | PASS | `git diff --name-only main \| sort` → §4の一覧(作成7+変更15)と完全一致(下記コミット一覧後の `git status --short` も空) |
| 6 | テストbasename一意 | PASS | `find backend/tests -name "test_*.py" \| awk -F/ '{print $NF}' \| sort \| uniq -d` → 出力なし(空) |
| 7 | 既存unit試験グリーン維持 | PASS | make test 全件数 959 passed。既存試験の変更は§4列挙の機械的追随のみ(内訳は下記「補足」・期待値の意味は不変) |

**test-ci=スーパーバイザー検証待ち**(STATUS運用ルール1〜3・マイグレーション0005追加のため
migrate/test-ci/docker系は実装側で未実行。wave単独のため並走とのDB取り合いは計画上なし)

## design §5 実装時確認事項の結果
12. uuid[]の @> / && / 部分UNIQUE索引へのON CONFLICT推論: unitのSQLピン・compile検査は
    済み。実DBでの推論確認はスーパーバイザー検証時のintegration試験1・6・9が担う
    (掠んだ場合は推移を記録しINSERT前SELECT FOR UPDATE切替をsupervisorへ相談)
13. HNSW検索2回の実行時間: スーパーバイザー検証時のdense配置試験(試験2)の実行時間を
    記録(テストコードに `group.handle elapsed=` のprintを仕込み済み。
     Layer 5+通知 ≤2秒・06 §1 との照合)

## 固定値の変更有無(design.md §2・本計画§9)
- 実行位置=案A(_run_post_retrieval共通チェーン・design §2.1): 変更なし
- Pool人数緩和検索+Layer 3同一計算(承認事項1・design §2.2): 変更なし
- 種=起点・起点max>=3トリガー(承認事項2・design §2.3): 変更なし
- 0005部分UNIQUE+ON CONFLICT DO NOTHING(承認事項3・design §2.8): 変更なし
- 1対1I-1改修=tx統合(承認事項4・design §2.7-4): 変更なし
- member_scores=seed_id+versions(design §2.3): 変更なし
- uuid[]bind=文字列リテラル+CAST(本計画§9-14): 変更なし
- 本計画§9のIF確定事項(SQL全文・純関数・try_promote手順): 変更なし(下記2点のみ等価な実装追随)
  - §9-8-5: `_nearby_in_tx` の戻り値をlatch_id変数へ代入しない(nearbyはtry_promoteしない
    という§9-8-5自身のdocstring仕様との矛盾解消・観測結果は不変)
  - §9-4手順g: 「return」を集合単位のスキップ(continue)として実装(design §2.5
    「各集合について判定・揃わなければ何もしない」の文言どおり)

## (ws-8・M3への引継ぎ)
- ws-8: Pool≦15の記録は試験2(dense配置)が供給。構造化ログ `group pool built pool_size=`
  が実行時の観察点。K上限裏付け試験(G2)で再利用
- M3-1〜M3-5: グループlatchesの回答は responses への追記(全員YES成立・部分成立なし・
  期限時未揃い=expired)。latches.group_candidate_id から集合を引ける。
  expiry_sweeper は group_candidates.status=candidate の放置掃除も担当
- G2: design §5の解釈記録5〜11はSTATUS「G2時確認事項」③に記載済み

## スーパーバイザー検証手順(test-ci実行時・design §4.3)
1. `make lint && make test` — unit全件グリーン(報告書と同じ結果になること)
2. `docker compose build api worker` — イメージ再ビルド(STATUS運用ルール4)
3. `make migrate` — 0005適用確認(alembic_version=0005・索引 `\di uq_group_candidates_intent_ids_open` の存在)
4. `uv run pytest --collect-only tests/integration/test_matching_groupengine.py -q`
   (backend/内・収集10件の確認。実行前の静的確認)
5. `make test-ci` — 既存全数+本単位integration 10件がグリーン。
   design §5-12のON CONFLICT/包含推論は試験1・6・9が実証。
   design §5-13のHNSW 2回の実行時間は試験2の所要から確認(≤2秒予算・06 §1)
6. 時刻参照がclock.pyのみ: `rg -n 'datetime\.now|utcnow|time\.time|time\.monotonic|
   time\.sleep|from time import' backend/src` が core/clock.py のみ
7. alembic無変更確認: `git diff main -- 'backend/alembic/versions/000[1-4]*'` が空
8. 変更ファイル一覧が本計画§4と一致・`git status` 空・
   `find backend/tests -name "test_*.py" | awk -F/ '{print $NF}' | sort | uniq -d` が空(運用ルール5)
9. test-ci後、残存確認を1回手動実施: users(subject LIKE 'm2ws7-%'=0件)・Redis(prefix掃き)・
   group_candidates・latches・latch_status_events・notifications(prefix由来=0件)

## コミット一覧
```
214a606 feat: マイグレーション0005(group_candidates開いている行の部分UNIQUE索引)
0d63bd5 feat: group_calc純関数群(貪欲法・集約・D-06上位判定・uuid[]組立)
172d7f2 feat: layer1の人数行分離(LAYER1_WHERE_BASE・グループPool検索用・文字列不変)
5f191bc feat: layer4のK_j配分拡張(is_group列・1対1最低4+継続優先・緩和H再検証・close除外)
b35b528 feat: upsert_pair(ID直指定UPSERT)とJevWorkerのgroup_ctx受け渡し
517a546 feat: latch_engine共存改修(グループ除外・|S|人try_promote・D-06上位1)とI-1 tx統合
3174b55 feat: GroupEngine.handle(人数緩和Pool検索・互換行列・貪欲法による集合生成)
3b6b224 feat: GroupEngine.finalize(集約tx・I-1対策)とbuild_group_proposal
d4d9900 feat: 削除Eventのgroup_candidates無効化と_run_post_retrieval共通チェーン配線
(report) test: GroupEngine integration 10試験(収集のみ)とws-7実行報告
```

## 補足(詰まった点・判断した点)
1. **§9-2全文からの機械的修正2件(group_calc.py)**: docstring冒頭が行長88超(E501)のため
   「(06 §7〜§8・design §2.2〜2.3・§2.5〜2.6)」→「(06 §7〜§8・design §2.2〜2.6)」へ短縮、
   コメント内の簡体字「保证」→「保証」。いずれも意味不変。
2. **Task 5**: §9-6実装(常に `relaxed=` キーワード渡し)に対し既存 `_patch_eval` /
   `test_h_fails_pending_then_success_on_next_row` の fake_h スタブが relaxed を受けずFAIL →
   スタブへ `*, relaxed=False` を追加(機械的追随・期待値不変)。また計画書掲載のテストコードの
   fake_targets が async def だったが本物 select_jev_targets は同期純関数のため同期 def へ修正。
3. **Task 6(機械的追随の内訳・期待値の意味は不変)**: `_patch`/`fake_promote` の改名追随
   (_try_promote→try_promote)・`_latch_row` の6要素化(group_candidate_id・score追加)・
   `_patch_promote` の fake_read_parts idsリスト化・`_count_daily_notifications` 試験の
   u0/u1→users(uuid_array_text形式)・`test_record_score_conflict_returns_early` の
   読取順序変更(tx前読取化)追随・task_done/test_try_promote系のタプル形式修正。
4. **Task 6**: 計画§9-8-5本文は `_nearby_in_tx` の戻り値を latch_id へ代入するが、これは
   「nearbyはtry_promoteしない」(同docstring)と矛盾(nearby行までproposed化する)ため
   代入を外した。既存試験 `test_nearby_path_creates_candidate_and_notifies` の観測結果不変。
5. **Task 8**: `d07_allows` のテストスタブを async def にすると coroutine が truthy になり
   `not` 判定をすり抜けるため同期 def とした(本物は同期純関数)。
   `_finalize` 内の未使用変数 `aggregate_score` はDB側 `aggregate_score IS NULL` ガードが
   判定を担うため削除(ruff F841/B007)。
6. **Task 9**: test_process_deleted_closes_group_candidates の順序検証はSQL文字列内の
   index比較ではなくcalls順(2=match_candidates→3=group_candidates)で証明。
7. **integration試験6の構成**: 計画書コメントの「{A,B,C}と{D,E}」は2人集合が成立しない
   (GROUP_MIN=3)ため「{A,B,C}(高)と{A,B,D}(低)」のメンバー重複2集合へ読み替え
   (D-06上位1集合の検証意図は不変・a×bは共有ペアとして高値)。
8. **Task 4 のExpectedとの差異(記録)**: 計画Expected「新規9件FAIL」に対し実際は
   7件FAIL+2件PASS(1対1単独入力の配分試験2件は現行実装でも通る性質)。Task 3 も同様に
   2件FAIL+1件PASS(回帰ピン)。いずれも実装後に全件PASSで固定値どおり。
