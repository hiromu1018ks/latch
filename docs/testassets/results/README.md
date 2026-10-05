# docs/testassets/results/ — 証拠索引とG4 7項目対応表

- 位置づけ: 10 §5「証拠は試験ID・実施日・環境・入力条件・合否とともにdocs/testassets/results/へ保管する」の置き場。本READMEはこのディレクトリの索引であり・G4(12 M4=11 §5の7項目をstagingで先行確認)の判定用に証拠を対応付けた表。
- 読み方の注意: 対応表は「どの証拠がどの項目を支えるか」の索引である。機械判定の記録値と証拠パスの引用が本体で・**量的合格線の正式判定(性能5目標・総当たり等)はG4の人間側が行う**(ws-6 design §2.7)。対応表から合格宣言をしない。

## 1. ファイルの命名規則

`{単位プレフィックス}-{領域}-{タイムスタンプYYYYMMDD-HHMMSS}.yaml`(例: ws7-probe-20261005-104449.yaml)。タイムスタンプは実施完了時刻・UTC実時間(プローブ等は試験窓完了時)。

## 2. G4 7項目×証拠対応表

| # | G4項目(11 §5) | 証拠 | 状態・備考 |
|---|---|---|---|
| ① | 27項目グリーン | ci分=**test-ci常設緑(最新1754 passed・2026-10-05)**。10 §3「ci(毎コミット)」の字義どおり・毎コミットで再実行されていること自体が証拠で・個別YAMLは作らない。ゲート時の証拠YAML: g1-result-*.yaml 4点(202115・203258・203517・210042)・g2-jev-result-*.yaml 3点(122004・134810・144518)。staging分=ws3-staging-result-20261003-231505.yaml(33 items全pass)・#5本格検証=ws6-bruteforce-10000/50000/100000-*.yaml(3点) | 充足(承認§5-8: ci分はtest-ci+G1/G2時YAML+M2 ws-8報告書の参照で充足・staging再実行はしない) |
| ② | 追加試験グリーン(D-10年齢制限・D-23成立後ブロック・FR-21表示規制・FR-17自己ペア除外) | ws3-staging-result-20261003-231505.yaml 内のD-10(422 UNDER_AGE)・D-23(409 CHAT_READONLY)・FR-21(本文テンプレートバイト一致・サマリ語不含)・FR-17のitems。実施記録: docs/plans/STATUS.md「G3判定資料」③(2026-10-02) | 充足 |
| ③ | 非機能試験グリーン | 性能5目標=ws6-load-20261004-123006.yaml(本命)+ws6-load-20261004-{105203,105538,113320}.yaml(経過3本)。総当たり=ws6-bruteforce-*.yaml(3点)。可用性=ws7-probe-20261005-104449.yaml。バックアップ復旧・縮退・Queue障害=ws7-nfr-result-*.yaml(5点・n2/n3/n5ほか) | 充足(記録値の引用のみ。正式判定はG4の人間側) |
| ④ | 測定開始状態(Observability稼働・MLR日次集計稼働) | ws4-staging-result-20261004-124823.yaml(items 8全pass=3群+アクセス系メトリクス露出・prometheus 4 target up・MLR日次集計ジョブJST 0時発火+実測一致) | 充足 |
| ⑤ | 告知・同意とログ許可リスト | **現存の証拠YAMLなし**。参考: docs/plans/STATUS.md ws-5節にunit 5 passed(2026-10-03)の実行記録への言及があるが・10 §5形式の証拠はresults/に未整備。→ 本READMEの§3の採取手順で採取する | 欠け=本単位で採取(スーパーバイザー実施) |
| ⑥ | alert/circuit breakerの動作確認 | D-16 alert=ws7-nfr-result-*.yaml(n3: 80%で発報1回+源流レポート添付)。Queue lag alert=同n5(発報→resolve往復)。circuit breaker=M2 ws-8のci環境E2E(切替・双障害skipped・開放/半開往復・マージf1eb6eb)+test-ci常設で緑維持。ci分は証拠YAMLでなくtest-ci+M2 ws-8報告書の参照(design §1.3の表⑥) | 充足 |
| ⑦ | ロールバック手順・担当・連絡経路 | docs/plans/M4/ws-8-rollback.md(手順書本体)+walkthrough証拠ws8-rollback-walkthrough-*.yaml(§4の採取手順・スーパーバイザー実施)+確定欄(手順書§7・§8=人間領域・G4判定時に入力) | 本単位で整備(実施済み要素と確定欄を区別して読むこと) |

## 3. 欠け証拠(ws-5分3点)の採取手順(スーパーバイザー実施)

ws-5報告書「スーパーバイザー検証時の手順4」の3点を・そのまま採取手順として示す。証拠形式は10 §5準拠(試験ID・実施日・環境・入力条件・合否)。staging環境の実施はスーパーバイザーが行う(ws-5〜ws-7と同一の役割分担)。**実施の順序は3-1→3-2→3-3**(3-3のログ照合は・3-1で作成した本文を含むIntentのログが流れた後に行う)。

### 3-1. 同意フローのstaging実行記録(試験ID: ws5-consent)

未同意ユーザーのIntent作成が422で拒まれ・同意POST後にactiveで受理される順序を観察する。前提: staging環境がup済み(`make staging-reset` 直後でよい)・.staging/keys/に鍵ペアがある。

```bash
# 1) 未同意ユーザーのJWTを発行する(標準出力にJWTが出る・それをTOKENへ)
cd backend && uv run python -m latch.auth issue-idp-token --provider google --subject ws8-consent-u1 --private-key ../.staging/keys/idp_test_private.pem
cd ..
#    ※表示されたJWT文字列をコピーして TOKEN='<JWT>' と設定する(単独実行してから使う)

# 2) usersの作成(未同意のまま。display_name+birth_date必須)
curl -s -o /dev/null -w '%{http_code}\n' -X POST -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' -d '{"display_name":"ws8-c1","birth_date":"1990-06-15"}' http://127.0.0.1:18000/v1/users
#    期待: 201

# 3) 未同意でのIntent作成→422(エラーコードCONSENT_REQUIREDを含む応答本文を残す)
curl -s -w '\n%{http_code}\n' -X POST -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' -d '{"raw_text":"今夜天文館あたりで居酒屋に行きたい。のんびり席を温めたい","status":"active","structured_intent":{"category":{"primary":"drinking","secondary":"居酒屋"},"alcohol_involved":true,"time":{"start":"<実施日以降の未来時刻+09:00>"},"location":{"name":"天文館","radius_m":1000},"budget":{"max":3500},"participants":{"min":2,"max":2},"soft_constraints":["のんびり席を温めたい"],"visibility":"hidden_until_match","notification_level":"proposals_only"}}' http://127.0.0.1:18000/v1/intents
#    期待: 422 + CONSENT_REQUIRED(※time.startは実施日の未来・+7日上限内に差し替える)

# 4) 同意の記録
curl -s -o /dev/null -w '%{http_code}\n' -X POST -H "Authorization: Bearer $TOKEN" http://127.0.0.1:18000/v1/users/me/consent
#    期待: 200

# 5) 同意後のIntent作成→201
curl -s -o /dev/null -w '%{http_code}\n' -X POST -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' -d '{同一のbody}' http://127.0.0.1:18000/v1/intents
#    期待: 201
```

記録するもの: 各curlの時刻・ステータス(422の応答本文を含む)・JWTのsubject(機微値のJWT自体は記録しない)。Intent作成bodyはm4-seed.yamlの実例(SD-001と同型・staging実績のある構造)を流用している。

### 3-2. 許可リスト登録表+機械検査結果(試験ID: ws5-logallowlist)

1. 機械検査の実行結果:

```bash
cd backend && uv run pytest tests/unit/test_log_allowlist.py -v
```

期待: 5 passed(許可リスト外のログ・禁止フィールドの突合等)。

2. 登録表の写し: ALLOWED_STRUCTURED_LOGS=latch.llm.send→SendRecord・latch.push.send→PushSendRecord(出典: backend/src/latch/core/logallow.py:21)。許可リスト一覧=問い合わせ開示資料の雛形として・各レコードのフィールド一覧を添付する。

### 3-3. ログサンプルのraw_text非混入照合(試験ID: ws5-logsample)

docker logsから構造化ログの実サンプルを取り・raw_text相当が含まれないことを確認する(ws-3報告書のpushログ読取と同型)。

```bash
docker compose -f compose.staging.yaml logs --no-color worker1 --tail 2000 > /tmp/ws8-w1.log
rg -c 'latch.llm.send|latch.push.send' /tmp/ws8-w1.log
#    期待: 1以上(対象ログが現れる。0ならapi側のログも見る)
rg -c '居酒屋|raw_text' /tmp/ws8-w1.log
#    期待: 0(本文相当と生値のキー名がログに現れないこと)
```

記録するもの: 照合したログの範囲(行数・期間)・対象ログのサンプル(latch.llm.send等のフィールド一覧)・検出0件の出力。lint相当の機械突合(08 §2.4のFORBIDDEN_FIELD_NAMES=raw_text・geo・lat等が含まれない)はtests/unit/test_log_allowlist.pyが担保しているため・本採取は実ログでの観察補完である。

## 4. walkthrough証拠(試験ID: ws8-rollback-walkthrough)

「ロールバック手順書が整備された」ことの実証(design §2.6・承認§5-4)。docs/plans/M4/ws-8-rollback.mdの手順をstagingで1回通して・10 §5形式で記録する。実施はスーパーバイザー・採取手順は手順書§6.2・§3.2・付録Bのとおり。合否判定基準:

- 1世代戻し: 戻し側と復帰側の両方でAPI healthy+GET /v1/users/me 200。
- 停止手順のドライラン: 段1(worker停止)・段2(nginx受付遮断)・段3(api全停止)の各段で「止まるべき経路が止まり・残すべき経路が生きている」ことの観察記録。
- alembic downgradeを1度も実行していないこと(スキーマ前進の規律)。
- 10 §5の全項目(試験ID・実施日・環境・入力条件・合否)が揃っていること。

## 5. 既存ファイル一覧

| プレフィックス | 内容 | 実在数(2026-10-05時点) |
|---|---|---|
| g1- | G1精度ゲート(Parser構造化・alcohol)の実測 | 4 |
| g2-jev- | G2日本語評価(Jev)の実測 | 3 |
| ws3-staging- | 機能試験27項目のstaging実行 | 1 |
| ws4-staging- | Observability受け入れ | 1 |
| ws6-load- | 性能5目標(123006が本命・他3点は経過) | 4 |
| ws6-bruteforce- | 総当たり3段階 | 3 |
| ws7-nfr-result- | バックアップ復旧・縮退・Queue障害等 | 5 |
| ws7-probe- | 可用性(合成監視) | 1 |
| ws5-・ws8- | (未整備) | 0(本READMEの§3・§4で採取) |
