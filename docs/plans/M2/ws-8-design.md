# M2 ws-8(縮退運転+G2ハーネス)設計メモ

- 作成: 2026-09-30(agent1)
- 前提: M2 ws-1〜ws-7 マージ済み(最終 1563752+ff771ed・マージ後main test-ci 1125 passed)。実行wave上の後続なし(最終単位)で並走なし
- 参照仕様: 12 M2(スコープ10・G2完了条件) / 06 §8 D-15・§5・§9 / 07 §1・§4 / 10 §3〜§4.7・§1 / 02 §4(#5〜#12) / 09 §4〜§4.2 / g2-jev-goldset-plan(§5・§8・§11)
- 本単位の実装は外部SDKのAPI事実に依存しない(breakerは純部品・G2ハーネスは既存
  TypeSafeJevProvider/AnthropicJevFallbackProvider を再利用)。よって**context7での新規一次確認は
  不要**(2026-09-29指示は「設計に外部SDKのAPI事実を記すとき」)。07 §4・ws-5設計§1.3の
  確定済み事実(429/529/timeout切替・model=jev-1.13.0固定・timeout 6秒)を引用するのみ
- ws-7からの引継ぎ(STATUS ws-7節・スーパーバイザー追記):
  (a) ci常設workerがテストのAPI発行Intentを非同期処理しteardown後に孤立行を作る問題→本単位で対抗策(§2.9)
  (b) Minor引継ぎ4件→§2.10で拾う/回すを判断
  (c) G2日本語評価の実行ハーネス整備→goldset-plan §11手順3が「ハーネス(ws-8)」と明記済み。本単位で整備する(§2.8)

## 1. 目的と前提となるdocsの確定値

### 1.1 この単位の目的

M2の最終単位として、G2完了条件(12 M2)の機械側判定材料をすべて揃える。

1. **circuit breaker**(06 D-15 FR-10): 第一候補TypeSafe Jev呼び出しの継続障害検知。
   測定窓1分・エラー率50%超 or p95レイテンシ超過で開放、開放中はフォールバックLLM継続、
   60秒後半開・1リクエスト試験。ws-5が確保した注入ポイント(gateway.judge_pair第一候補側)へ実装
2. **縮退E2E**(10 §4.5): 切替(429/529/timeout→フォールバック)・双障害skipped保留・提案ゼロ・
   breaker開放→半開・復旧後のskipped再評価を、実DB・実Redis・スタブGatewayで実証
3. **02#5〜#12 E2E対応表**(02 §4・10 §3): 各項目を既存試験へ対応付け、不足(#5観察・#7更新・#8時間)
   を新設してG2証拠を固める
4. **K上限裏付け試験**(10 §4.6): 密集配置(一次候補100件超)で Vector≤50 / Cheap≤20 / Jev≤8回 /
   Pool≤15 が単一シナリオで同時に守られ、切り詰めが決定的であることを記録で検証(#11裏付け)
5. **冪等性**(10 §4.7・G2条件3): 同一Event2回投入でmatch_candidatesが二重生成しない
6. **G2日本語評価ハーネス**(g2-jev-goldset-plan §11): make g2-gate で520ペア両経路の評価を
   1コマンド実行できるようにする(評価実行自体・合格判定はG2時・本単位外)
7. **運用対抗策**(引継ぎa): make test-ci のworker復帰前に常設subscriptionを掃除し、
   ci常設workerによる孤立行(group_candidates/latches)の発生を構造的に止める

マイグレーション**なし**(breakerはプロセス内状態・skip_reason等の列は既存)。依頼追加なし
(pyyamlはws-6導入済み)。実行waveはws-8が単独のため共有ci-dbのalembic_version取り合いは計画上なし。

### 1.2 引用確定値(節番号つき)

| # | 確定値 | 出典 |
|---|---|---|
| 1 | circuit breaker初期値(FR-10): 測定窓1分の間に(a)LLM Gateway経由の第一候補Jev呼び出しのエラー率が50%を超える、または(b)同呼び出しのp95レイテンシがtimeout(6秒)を超える、のいずれかで開放。開放中は第一候補の呼び出しを停止しフォールバックLLMで判定を継続。フォールバックLLM自体も継続して失敗する場合のみ候補はskippedで保留。開放から60秒後に半開へ移行し、半開では第一候補へ1リクエストのみを試験し、成功なら閉じ、失敗なら開放へ戻す。開放⇔半開の往復は障害の継続する限り何度でも許す。窓1分はJev予算5秒に対して十分短く、障害検知から切替・提案見送りへの移行を1〜2分で完了。50%のしきい値は単発のtimeoutやレート制限のスパイクでは発動せず継続障害のみを拾う | 06 §8 D-15 |
| 2 | timeoutは第一候補・フォールバックとも6秒(層別予算Jev ≤5秒+1秒吸収)。双方で再試行を設けない | 07 §1・06 §1 |
| 3 | 切替条件: 429・529・timeout 6秒・接続障害→即座にフォールバックLLMへ切替(backoff再試行なし)。LLMProviderError(400系)・JevOutputInvalidError(出力検証失敗)は切替せず伝播 | 07 §4切替表・ws-5実装 |
| 4 | フォールバックLLMの失敗(timeout・429・5xx・出力検証失敗)→縮退へ(候補はskippedで保留) | 07 §4・06 D-15 |
| 5 | 縮退(D-15)の3条件: (1)第一候補とフォールバック双方のAPI障害 (2)D-16回数上限到達 (3)Jev予算・頻度制限由来のスキップ。提案は見送り・候補はmatch_candidates.status=skippedで保留。保留は日次リセット後や障害回復後のMatch Event、および再評価経路(Bucket再評価・頻度制限解除後の次のEvent)で再評価される。単発の429・529・timeoutは第一候補内のフォールバック切替で吸収し、継続障害のみcircuit breakerが拾う | 06 §8 D-15 |
| 6 | 縮退試験の確認事項: (1)第一候補の429・529・timeout注入でフォールバックLLMへ切替え判定継続(jev_resultのprovider=fallback_llm記録含む) (2)フォールバックも継続失敗でJevスキップ・候補は「Jev未判定」保留 (3)提案が発生しない (4)circuit breakerが開放し、開放中は第一候補を呼ばずフォールバックで継続し、一定時間後に半開で第一候補へ再試験(しきい値は引用#1) (5)Parser失敗時の503/422区別(実施済み・M1 ws-2) (6)Embedding失敗→バックフィル→embedding_completed復帰(実施済み・ws-2)。復旧後の再評価(FR-18): skipped保留候補がLLM障害回復後のMatch Eventで再評価対象になること | 10 §4.5 |
| 7 | K上限試験: 1イベントで一次候補が100件超になる状況(同一地域・時間帯・カテゴリに密集)を意図的に作り、Vector出力≦50・Cheap Judge判定≦20・Jev判定≦8回(1対1最低4回保証・グループ由来最大4回)・グループPool≦15が守られることをmatch_candidates・group_candidatesとJev実行回数の記録で検証。切り詰めの決定性は同一入力での2回実行が同一結果(スコア上位・同点はintent_id昇順) | 10 §4.6・06 §8 D-24 |
| 8 | Queue障害試験: 毒ペイロード→retry上限5回→失敗理由保持でquarantined・後続へ波及しない(実施済み・ws-1 test_6)。削除済みIntent参照Event→processed破棄(実施済み・test_5)。UNIQUE制約(idempotency key=(event_type, source_intent_id, version))による同一Event重複処理排除: 2回投入してもmatch_candidatesが二重生成しない | 10 §4.7・06 §9 |
| 9 | 02#5〜#12の検証方法と合格証拠(#5=処理件数・レイテンシがIntent数で急増しない/本格はload・#6=MatchCandidate生成記録・#7=更新後に候補の生成または消失・#8=時刻到来による候補生成・#9=Hard Constraint除外ペアが候補に現れない・#10=意味的近接ペアが候補生成・#11=Jev実行回数上限超えない・#12=A→B・B→A双方の判定値記録) | 02 §4・10 §3 |
| 10 | G2完了条件: ①02#5〜#12がci/stagingでグリーン(#9はHard Filter単体+staging E2E) ②K上限裏付け ③冪等性 ④縮退(breaker開放→半開) ⑤日本語評価(09 v0.5第4節) | 12 §3 M2 |
| 11 | 日本語評価: TypeSafe Jevの応答(would_* noul確率・5軸正規化値・confidence)へPrecision/Recall/ECE/Brier/Mutual Acceptance Precisionを算出。閾値は0.70/0.80/0.90の3点。フォールバックLLMも同一ゴールドセットで評価しproviderキーで分離。C=1・L=H×MutualScore×C。ハーネスがintentペアを正規化テキスト(07 §4形式。visibility/notification_levelを含めない)へ組み立て1ペア1リクエスト | 09 §4〜§4.2・goldset-plan §11 |
| 12 | ゴールドセット: 520ペア・304 Intent・status confirmed。intentsはstructured(アプリ層補完後)。pairsはkind(1to1/group)・segment・layer1_pass・expected{gold_mutual・would_* label+band・fit 0〜4・latent_yes band}。ng_unverifiableは正規化テキストで「(システムで判定不能)」付きsoft行 | g2-jev-goldset.yaml・goldset-plan §5・§7・07 §4 |
| 13 | コスト試算(実行前報告用): 両経路520ペア≈$4.75・再実行2回込み上限$15。1ペア=1リクエスト・入力約3k tok | goldset-plan §8 |
| 14 | #5の本格検証(1万→5万→10万)はload環境の負荷試験が担う。D-16上限到達・resume・Bucket遅延回収のフル試験・性能5目標・レイテンシ注入(p50/p95)はM4非機能試験 | 02 §4前置き・10 §3・§4.1〜§4.2・12 §3 M4スコープ4 |
| 15 | ci環境=最小構成(API 1・Worker 1・DB共用)。LLM呼び出しはLLM Gatewayのテストモード(スタブ・レイテンシ注入付き)で決定的にする。時刻参照はすべてClock経由(FakeClock注入で実時間待ちを排除) | 10 §1 |

### 1.3 既存実装資産との接続(すべてマージ済みmain)

1. **llm/gateway.py**(ws-5): judge_pairの第一候補→フォールバック切替実装済み。
   `_jev_call(fallback=False)` に「circuit breaker(ws-8)の注入ポイントは第一候補側。
   本単位では実装しない(design §2.10)」と明記済み。Timeoutsはコンストラクタ上書き式
   (envに出さない規律)——breakerのパラメータも同型にする
2. **worker/jev.py**(ws-5): LLMError→skipped(skip_reason=llm_failure)・JevOutputInvalidError→
   skipped(invalid_output)実装済み(引用#4・#6(2)の実体)。Guard契約・計上も不変
3. **worker/matching/layer4.py**(ws-5): select_jev_rowsの再選択規則で
   `RESELECT_ALWAYS=("llm_failure","invalid_output")` ——障害系skipは即時再選択対象。
   引用#6の復旧後再評価(「障害回復後のMatch Eventで再評価される」)はこの既存機構が担う。
   本単位は試験で実証するのみ(実装変更なし)
4. **llm/stub.py**: delay系統別注入・fail_jev(fail_parser/fail_embedding)あり。ただし失敗例外は
   LLMProviderError固定——429/529/timeout/接続障害の注入に例外種別指定が必要(§2.3)
5. **K上限の個別試験**(ws-3〜ws-7): test_2_kv_truncation_deterministic(Vector≤50・同点intent_id昇順)・
   test_2_kc_truncation_deterministic(Cheap≤20)・test_2_kj8_truncation_deterministic(Jev≤8・決定性)・
   test_2_pool_limit_15_recorded(Pool≤15・dense配置・`group pool built pool_size=`ログ)。
   引用#7の「単一シナリオで同時に」は未実施(§2.6)
6. **worker/matching/runner.py**: RetrievalOutcome(layer1_pass_count・pairs≤50・topkc≤20)が
   記録の供給源。LAYER1通過件数はLayer 2 SQLのCOUNT(*) OVER()で取得済み
7. **worker/reeval.py + test_10_reeval_runner_path**(ws-6): catch-up経由の再評価E2Eあり
   (run_once直接呼び出し流儀)。30分Bucket側の抽出(_SELECT_BUCKET_TARGETS)は実装済み・
   試験はcatch-up側のみ(§2.5で#8対応)
8. **worker/main.py**: WorkerのDI(jev=注入でrun()内再構築を回避)・`_run_post_retrieval`
   (group→jev→latch→finalize直列)。テストプロセス内Worker(worker_env)がE2Eの土台
9. **g1gate/**(M1 ws-6): harness書式の先行例(--assets/--out/--limit・起動検証fail-fast・
   results/へのYAML証拠・SHA256記録)。g2gateはこの流儀を踏襲(§2.8)
10. **events/pubsub_bus.py**: ensure(create_topic/create_subscription冪等)・delete_subscription。
    常設subscriptionは settings.pubsub_subscription_match_events(既定 match-events-sub)。
    worker起動時の `await bus.ensure()` がsubscription再作成を担う→対抗策(§2.9)の土台
11. **jev_smoke.py**: フォールバック直接呼び出しは `gateway._jev_fallback.judge`(プライベート属性
    アクセス)。g2gateで同等の直接呼び出しが必要→公開IFへ切り出す(§2.8・承認事項6)
12. **0001スキーマ**: group_candidates・latches は intents へFKなし(intent_ids uuid[])——
    テストteardownでintentsを消してもgroup_candidates/latches行は残る。孤立行問題の構造的原因

### 1.4 スコープ外(後続単位・トラックへ渡すもの。本単位では作らない)

1. **日本語評価の実行(実課金520ペア)と合格基準の確定** — G2時(オーナー確定・実行前の
   金額報告はスーパーバイザー)。本単位はハーネスと--limit部分実行による動作確認まで
2. **D-16上限到達・月次リセット・resume/Bucket遅延回収のフル試験** — M4非機能(12 M4スコープ4)。
   G2完了条件(引用#10)の文言に含まれないためws-8では実施しない(承認事項5)
3. **#5本格負荷試験(1万→5万→10万)・性能5目標・レイテンシ分布注入** — load環境・M4(引用#14)
4. **staging環境の構築** — M4-1(12 §3)。02#5〜#12の「ci/stagingでグリーン」は本単位では
   ci環境(compose常設・スタブLLM)で実施する(承認事項4)
5. **Worker並列化・同時実行数上限** — ws-5 §2.9の引継ぎ言及は「計測後に判断」が前提。
   計測はM4性能試験。本単位では並列化しない(単位表スコープにも不在)
6. **usageトークンのDB記録・コスト集計** — M4(送信記録は構造化ログで既存枠組み)
7. **ws-7 Minor引継ぎ(b)(c)(d)** — M3へ回す(§2.10で判断記録)
8. **alert媒体の外部化・ダッシュボード** — M4 Observability(80% alertはws-4実装済み・構造化ログ)

## 2. 実装方式の選択と推奨

### 2.1 circuit breakerの実装位置と状態保持 — 推奨: Gateway内・プロセス内メモリ(案A)

| 案 | 内容 | 評価 |
|---|---|---|
| **A(推奨)** | LLMGatewayがCircuitBreakerオブジェクトを持ち、judge_pairの第一候補側でallow/record。状態はWorkerプロセス内メモリ | 切替ロジック(try節)と一体で測定(Clock)もGateway内で完結。judge_pairの呼び出し側(JevWorker)は無変更。APIプロセスはJev系統を呼ばないため影響なし |
| B | Redisに状態を共有(複数Worker対応) | ciはWorker 1・本番もWorker構成はM4で確定する。共有化が要るのはその時。IF(brakerのallow/record契約)は案Aと不変なので移行は局所変更 |
| C | Gateway外にラッパー層(JevWorkerがbreakerを確認してからjudge_pair呼び分け) | 切替try節の内側(どの例外でフォールバックへ行くか)に介入できず、第一候補呼び出しの成否とレイテンシを二重管理する。ws-5が「Gateway内」と設計済みの注入ポイントを放棄することになる |

案Aを採用。プロセス再起動でbreakerは閉状態から出直す(窓データ消失)——測定窓1分に対し
再起動は稀で、再起動直後は第一候補を試すのが合理的(引用#1の半開の挙動と同型)ため許容する。
Worker複数インスタンス時の共有はM4で案Bへ移行(採用しないもの§2.11にも記録)。

### 2.2 状態機と判定規則(承認事項1・2を含む)

**BreakerParams**(`llm/breaker.py`。Timeoutsと同様のコンストラクタ上書き式・envには出さない):

```python
@dataclass(frozen=True)
class BreakerParams:
    window_s: float = 60.0            # 測定窓(引用#1)
    error_rate_threshold: float = 0.5 # 「50%超」→判定は >
    half_open_after_s: float = 60.0   # 開放→半開(引用#1)
    min_samples: int = 2              # エラー率判定の最小呼び出し数(承認事項2)
    # p95しきい値はGatewayが Timeouts.jev_s と同じ値を渡す(6秒・引用#2)
```

**状態**は closed / open / half_open の3値。窓データは `(timestamp, is_error, latency_s)` の
deque(Clock注入・実時間参照なし)。

- **record(outcome, latency_s)**: 呼び出し1回の記録。窓外(timestamp < now-window_s)を除去してから追記。
  closed中のrecord直後に開放判定を行う
  - エラー率判定: 窓内呼び出し数 N ≥ min_samples(=2) かつ error数/N > 0.5 で open へ。
    **N=1の失敗では開放しない**(引用#1「単発のtimeoutやレート制限のスパイクでは発動せず」の
    実装解釈——1失敗だけではエラー率100%になるため、最小サンプルを設ける。承認事項2)
  - p95判定: 窓内呼び出しのレイテンシ昇順ソートで p95位置 = ceil(0.95×N)−1(0-indexed)の値が
    **timeout_s 以上**なら open へ。timeout(asyncio.timeout打ち切り)呼び出しは打ち切り時点の
    timeout_s をレイテンシとして記録する(実レイテンシは6秒以上であるが計測不能のため。
    承認事項1)。この判定は「6秒超(打ち切り含む)呼び出しが窓内で5%超」相当——成功呼び出しの
    レイテンシはasyncio.timeoutにより必ず6秒未満に収まるため、成功のみの母集団では「p95>6秒」が
    成立せず、timeout呼び出しを母集団に入れることが仕様成立の前提になる(設計解釈として記録)
  - エラー計上の範囲: `_jev_call(fallback=False)` が送出する例外のうちLLMError系
    (Timeout/RateLimit/Overloaded/Connection/Provider)をエラーとして計上(呼び出し失敗)。
    **JevOutputInvalidErrorは呼び出し成功扱い**(応答は得られており、検証失敗はGatewayの
    validate_and_normalize層・07 §4「実装不整合」)。レイテンシはok/errorとも計上
- **allow(now) → bool**: closed は常にTrue。open は now − opened_at ≥ half_open_after_s なら
  half_open へ遷移しTrue(この呼び出しが半開の試験リクエスト)、未満ならFalse。half_open で
  前回の試験リクエストが未recordならFalse(防御。WorkerはEvent直列処理のため通常発生しない)
- **record時の遷移**: half_open 中の記録は試験リクエストの結果——ok なら closed へ遷移し窓を
  リセット、error なら open へ戻す(opened_at を更新。再び60秒後に半開を試みる。引用#1の
  「往復は何度でも」)

**Gateway.judge_pair への組み込み**(既存構造への最小差分):

```python
# 第一候補側(既存の try: envelope = await self._jev_call(..., fallback=False) を置換)
if self._breaker is not None and not self._breaker.allow(self._clock.now()):
    raise _FirstCandidateSkippedOpen()   # 内部送出→except節でフォールバックへ合流
t0 = self._clock.now()
try:
    envelope = await self._jev_call(..., fallback=False)
except LLMError as exc:
    self._breaker.record(error=True, latency_s=latency(exc, t0))  # timeoutはtimeout_s
    raise
self._breaker.record(error=False, latency_s=(self._clock.now()-t0).total_seconds())
```

- 開放中の第一候補スキップは既存の切替try/except構造へ「内部例外で合流」させる——
  フォールバック呼び出し・送信記録2件の既存動作をそのまま使うため。開放中の送信記録は
  第一候補分が減る(呼んでいないので記録も無い——正しい)
- レイテンシ計測は `_jev_call` の外側(judge_pair内)でClock差分。asyncio.timeoutは`_call`内で
  かかるため、timeout時のレイテンシは例外種別でtimeout_sに置換して記録
- breaker=None(既定)は従動作(APIプロセス・既存試験互換)。build_worker_gatewayの
  stub/real両構成でCircuitBreaker(clock・params既定)を生成して渡す
- **open中のフォールバック失敗**(双障害)は現行どおりLLMErrorとしてJevWorkerがskipped記録
  (引用#4)。breakerは第一候補の品質のみを見る(フォールバックの失敗でbreaker状態は変わらない)

### 2.3 StubLLMの障害注入拡張(10 §1「疑似イベント注入」の整備)

引用#6の注入(429・529・timeout・双方失敗)に必要なスタブ機能を追加する。

```python
# stub.py への追加コンストラクタ引数(既存fail_jev=TrueはLLMProviderErrorのまま下位互換)
fail_jev_exc: str | None = None
# None | "ratelimit" | "overloaded" | "timeout" | "connection"
# → LLMRateLimitError / LLMOverloadedError / LLMTimeoutError / LLMConnectionError を送出
```

- 第一候補とフォールバックで**別インスタンス**をLLMGatewayへ注入(jev=障害スタブ・
  jev_fallback=正常スタブ等)して片側だけの障害を作る。`_CountingStub`(judge呼び出し数計上・
  ws-5試験資産)を併用し「開放中は第一候補を呼ばない」ことを呼び出し数0で検証する
- p95条件の再現: 遅延スタブ(delay_jev_ms)+Timeouts上書き(timeout_sを例えば0.05秒へ短縮)で
  「必ずtimeoutする呼び出し」を高速に量産する。実6秒待ちを作らない(引用#15)
- delayの系統別既定値(compose環境変数 llm_stub_delay_*_ms)は既存のまま使い、試験コード側で
  インスタンス引数により上書きする

### 2.4 縮退E2Eの試験構成 — 推奨: JevWorker直接構築流儀を主・Worker経由1本(案A)

| 案 | 内容 | 評価 |
|---|---|---|
| **A(推奨)** | 縮退の各確認事項はtest_matching_jev流儀(実DB・実Redis・JevWorker直接構築・FakeClock・注入Gateway)で検証。最後にworker_env(テストプロセス内Worker+API実HTTP)で一気通貫1本 | 障害注入の制御(breakerのClock操作・スタブ差し替え)が確実。Worker直列キックの実証は1本で足りる |
| B | 全部worker_envでAPI→Event→Worker経由 | APIが発行するEventの処理待ちポーリングが各試験に入り、Clock操作(breakerの60秒・debounce)と実時間の折り合わせが複雑化。障害の注入ポイント(Worker内Gateway)への差し込みも遠い |

試験群(tests/integration/test_degraded_e2e.py・詳細なassertは計画書):

1. **切替(引用#6-1)**: 第一候補=LLMRateLimitError→フォールバック正常。jev_result.provider=
   "fallback_llm"・双方の送信記録(caplog)。429/529/timeout/接続障害の4種はunit(gateway)で
   網羅し、integrationでは429を代表1種
2. **双障害skipped+提案ゼロ(引用#6-2・3)**: 両スタブ失敗→status=skipped・skip_reason=
   llm_failure・latches行が生成されない(提案ゼロ)。jev_resultはNULLのまま
3. **breaker開放・エラー率(引用#6-4)**: 第一候補のみ継続失敗(429)を窓内で2回→開放。
   開放後のjudge_pairで第一候補スタブのjudge_callsが増えない・フォールバックで評価継続
   (provider=fallback_llm)。窓1分・しきい値はBreakerParams上書きで縮めて高速化しない
   (docs確定値の実証のため実値60秒窓のままで、呼び出し2回の失敗だけで開放する性質を利用。
   FakeClockで時刻は進めない=同一窓内に収める)
4. **半開・成功で閉じ(引用#6-4)**: 開放後FakeClock+60秒→次のjudge_pairが第一候補を
   1回だけ呼ぶ(judge_calls=+1)・成功でclosedへ→以降の呼び出しは第一候補(provider=typesafe_jev)
5. **半開・失敗で開放戻し**: 半開の試験リクエスト失敗→再び開放(第一候補不呼出)→
   FakeClock+60秒→再度半開(往復)
6. **p95条件単独開放**: timeout多数(遅延スタブ+timeout短縮)・エラー率50%以下の窓
   (例: 20呼び出し中2件timeout+18件成功→エラー率10%・p95位置がtimeout)で開放する
   ——エラー率条件と独立してp95条件が効くことを分離実証(承認事項1の裏付け)
7. **復旧後再評価(FR-18・引用#6)**: 双障害でskipped→フォールバックを回復→起点の次の
   Event処理(同一起点のhandle再実行=「障害回復後のMatch Event」相当)でskipped行が再選択
   (layer4.RESELECT_ALWAYS)→evaluated・提案が発生。時間経過を要する日次リセット系skip
   (intent_daily等)の回収はM4(引用#14)
8. **Worker一気通貫1本(worker_env)**: APIでIntent作成→Event→Worker(stage1→embedding→
   L1〜3→GroupEngine→JevWorker→LatchEngine)で、注入した障害スタブ(第一候補429)でも
   フォールバック経由でlatches提案まで到達。Worker DI(jev=注入JevWorker)は既存IFで可能

unit(tests/unit/llm/test_breaker.py)は状態機を純粋に網羅する(min_samples未満不開放・窓追い出し
による自然復帰判定の素材・p95位置の境界・half_open試験中フラグ・パラメータ既定値のピン)。
gateway連携(開放中スキップ・レイテンシ記録・JevOutputInvalidは成功扱い)もunitで先行。

### 2.5 02#5〜#12の対応表と新設(承認事項4を含む)

G2条件①(引用#10)の証拠構造を明示する。**既存資産を最大対応させ、新設は最小3本**。

| # | 検証方法(02 §4) | 対応する試験(新=ws-8新設) | 備考 |
|---|---|---|---|
| 5 | Active数を増やし1イベントあたりVector Retrieval件数とレイテンシの推移観察 | **新**K上限E2E(§2.6)のlayer1_pass_count・pairs件数・所要記録+既存test_2_kv | ci分は「K_v上限でIntent数と無関係に安定」の観察。段階増加(1万〜)はload/M4(引用#14) |
| 6 | 一致する既存Intentを配置し新規Intentを登録→MatchCandidate生成記録 | 既存多数(events test_1・retrieval test_1・degraded試験8) | E2Eは試験8が兼ねる |
| 7 | Intentの時間・場所を更新→候補の生成または消失の記録 | **新**(§2.5-A)更新E2E: worker_envで2 Intent→候補生成→PATCH(時間帯を交差しない値へ)→debounce窓解放(FakeClock)→再評価で旧ペアが新評価世代で再生成されない=消失 | 既存test_2_debounceはイベント統合まで・jev test_4はclose_broken_pairs単体。E2Eの繋ぎ込みが無い |
| 8 | 指定時刻をまたぐ2 Intent→時刻到来による候補生成 | 既存test_10(catch-up)+**新**(§2.5-B)Bucket再評価run_once試験 | ReevalRunnerの_SELECT_BUCKET_TARGETS側を明示試験(time_startを未来Bucketに置いた2 Intent→FakeClockでBucket境界を経過→run_onceで抽出→候補生成)。06 §9 FR-09の30分Bucket本体 |
| 9 | 予算・時間・距離・人数を外すペアが候補に現れない | 既存hardfilter単体(ci割り当て・ws-3実施済み)+K上限E2Eに除外ペア数組を混入(非生成確認) | 10 §3「#9は二段構成」のE2E側 |
| 10 | 語彙不一致の意味的近接ペア(焼肉/肉系)が候補生成 | 既存test_1_semantic_pair_generated | — |
| 11 | 1イベント大量一次候補→Jev実行回数が上限超えない | **新**K上限E2E(§2.6)が担う(Jev≤8を含む) | 10 §4.6と同一試験 |
| 12 | 候補1件の判定結果でA→B・B→A双方の判定値記録 | 既存test_1_evaluate_records_jev_result(would_a/would_b双方) | — |

### 2.6 K上限裏付けE2E(10 §4.6)+ 冪等性(10 §4.7)— 新設 test_k_limits_e2e.py

**試験1: K上限一気通貫(引用#7・#11・#5観察)**

- 配置: ユーザーU0(起点)+相手ユーザー101人(計102 Intent)。全Intent「meal・天文館・radius 1000m・
  同一時間帯(BASE_HOURS=120窓)・2〜2人・予算5000円」でHard Filter全通過+embedding同一ベクトル
  (同点→intent_id昇順の決定性検証に好都合)。うち10人程度は3〜4人志向(min=3)を混ぜ
  GroupEngineのPoolも同時に走らせる。#9用に除外ペア(時間交差なし・予算min<500)を数組混ぜる
- 実行: run_candidate_retrieval → GroupEngine.handle → JevWorker(stub) → LatchEngine →
  GroupEngine.finalize(§1.3-8の共通チェーン。worker_envの_run_post_retrieval相当を試験内で直列呼び出し)
- 検証: ①layer1_pass_count > 100(一次候補100件超の成立) ②pairs(=Layer2出力)≤50 かつ
  全候補同点でintent_id昇順上位50と一致 ③topkc(=Layer3出力・match_candidates生成)≤20 ④
  Jev実行回数(judge_calls)≤8・1対1最低4回保証 ⑤`group pool built pool_size=`ログでPool≤15
  ⑥latches生成がD-08上限内 ⑦除外ペアの非生成 ⑧**決定性**: 同一起点に対し処理チェーンを
  2回実行し、match_candidates行集合(ペア・status・cheap_judge_score)・Jev呼び出し順・
  group_candidates・latchesが同一(引用#7の「同一入力での2回実行が同一結果」)
- 記録: 試験内でlayer1_pass_count・各層件数・所要時間をassert対象にすることで
  「記録で守られた」の証拠とする(10 §4.6「match_candidates・group_candidatesとJev実行回数の記録」)

**試験2: 冪等性・同一Event2回投入(引用#8・G2条件③)**

- dense配置(§2.6試験1と共用可)で、API作成によるEvent発行後、同一ペイロードを
  bus.publish_rawで2回投入(worker_env)→Stage1のidempotency UNIQUE((event_type,
  source_intent_id, version))で2回目がduplicate→match_candidatesの行数・内容が不変
  (二重生成なし)。既存test_7_duplicate_delivery_processed_once(ws-1)はイベント単位の
  processed-once検証——本試験はdense配置で「match_candidatesが二重生成しない」を
  行数とペア集合で直接検証する(G2文言どおり)
- 毒ペイロード・削除済み参照Eventはws-1 test_5/6で実施済み(引用#8)——対応付けのみ

**試験3・4: #7更新E2E・#8 Bucket再評価**(§2.5表の新設2本。worker_env/ReevalRunner流儀)

### 2.7 対抗策: make test-ci の常設subscription掃除(引継ぎa)

**問題の構造**(STATUS ws-7運用メモ・§1.3-12): test-ci中は常設workerを停止しているが、APIが
publishしたEventは**常設worker用subscription(既定 match-events-sub)にも配信・滞留する**
(テストプロセス内Workerの試験専用subscriptionとは別)。`docker compose start worker` 後に
常設workerが滞留メッセージを一斉処理し、teardown済みのUUID(毎回新規)へgroup_candidates/
latchesを書いて孤立行を作る(group_candidates・latchesはintentsへFKなしのため残存)。

**対抗策(推奨: 案a)**: test-ciのpytest終了後・worker復帰前に、常設subscriptionを削除する。
Pub/Subはsubscription削除で未配信メッセージごと消える。worker復帰(run())時の
`await bus.ensure()`(create_subscription冪等)が再作成する——**滞留メッセージの全廃**が
構造的に達成され、workerは次の新規メッセージから処理を始める。

- 実装: `python -m latch.events purge-match-sub`(新設の小CLI。PubsubEventBusを設定の
  subscription名で構築しdelete_subscription。NotFoundは握ってexit 0——存在しない=
  滞留なしでも同じ結果)。Makefileのtest-ciへ挿入:

```makefile
test-ci:
	docker compose up -d --wait
	docker compose stop worker
	cd backend && uv run --group geo pytest; rc=$$?; \
	uv run python -m latch.events purge-match-sub; docker compose start worker; exit $$rc
```

- 併せて既存残存の孤立行を掃除する検証手順(報告書用・スーパーバイザー実施)を定義:
  `DELETE FROM group_candidates WHERE NOT (intent_ids <@ (SELECT array_agg(id) FROM intents))`
  相当の「intent_ids全要素がintentsに存在しない行」の削除SQL(latchesも同様)+test-ci後に
  孤立行0件を確認。teardown完全性の感度はws-3/ws-4で既に実証済みの対抗策(prefix掃除)が
  維持する
- 案b(検討・不採用): worker復帰後に孤立行を掃除SQLで消す——孤立の定義がレース後の
  状態依存になり、掃除漏れの判定が曖昧。案aは「そもそも処理させない」ため確実

### 2.8 G2日本語評価ハーネス — latch.g2gate + make g2-gate(goldset-plan §11の実体化)

g1gateの書式(§1.3-9)を踏襲した新パッケージ。**評価実行はG2時・本単位では--limit部分実行での
動作確認まで**(スコープ外1)。pytestには実API呼び出しが構造的に存在しない(markerによらない
構造的分離・g1gateと同一規律)。

**前提: Gatewayへ第一候補/フォールバックの公開直呼びIFを追加(承認事項6)**

現行jev_smokeは `gateway._jev_fallback.judge` のプライベート属性アクセス。G2評価は
「第一候補を直接520回」「フォールバックを直接520回」の別経路実行が必要(judge_pairだと
第一候補成功時にフォールバックが呼ばれない)ため、`_jev_call` を公開IFへ切り出す:

```python
async def call_jev_first(self, *, intent_a, intent_b, intent_ids) -> JevJudgment
async def call_jev_fallback(self, *, intent_a, intent_b, intent_ids) -> JevJudgment
```

- 送信記録・timeout・validate_and_normalize・JevJudgment組立はjudge_pairと同一経路
  (call_jev_firstはbreakerを**参照しない**——評価は経路品質の実測が目的で、開放状態の
  影響を受けない完全な直接呼び出し。judge_pairからも第一候補側の呼び出し部品として再利用し
  二重実装を避ける。§2.2のbreaker組み込みはjudge_pair側のラップとして残る)
- jev_smokeもこのIFへ置き換える(_jev_fallbackアクセス解消・振る舞い不変)

**パッケージ構成(latch/g2gate/)**:

- **cases.py**: g2-jev-goldset.yaml読込(yaml.safe_load)。intents→JevTextInput変換
  (category_primary・structured_data{location_name, soft_constraints}・participants_min/max・
  time_start/end・budget_max・geo_radius_m)。**ng_unverifiable→downgraded_from_ng=Trueの
  soft行**への変換(引用#12・07 §4正規化テキストの「(システムで判定不能)」行)。
  soft_constraintsは{Falseの通常行+ng_unverifiable由来行}の順。meta.status=confirmedの
  検証(fail-fast)。YAMLのSHA256を返す
- **runner.py**: 各ペア→build_jev_text(既存・07 §4形式)→route別にcall_jev_first /
  call_jev_fallback→JevJudgment蓄積。直列実行(g1流儀・レート制限余裕。引用#13で520×2は
  制約にならない規模)。usage合計(応答envelopeから)も蓄積。ClockはFakeClock(BASE=
  goldset meta.current_datetime)——正規化テキスト内時刻表記の基準。実施日時はSystemClock
  (arch test規律)。JevOutputInvalidError・LLMErrorは「そのペアは失敗」として記録し継続
  (再試行しない・精度計算から除外せず失敗数で報告)
- **compare.py**: 指標算出(引用#11)
  - MutualScore = min(would_a_accept_b, would_b_accept_a)・L = MutualScore(H=1・C=1。
    layer1_pass=falseの30件はJev単体検証(§goldset-plan §6)のためH=1で通す)
  - Precision/Recall: 提案=L ≥ 閾値(0.70/0.80/0.90の3点)・実YES=gold_mutual
  - ECE: MutualScoreを10分割ビン([0,0.1)…[0.9,1])・各ビン|平均予測−実YES率|の加重平均
  - Brier: (MutualScore − gold)^2 の平均
  - Mutual Acceptance Precision(分離度): gold true と false のMutualScore分布の分離度——
    Mann-Whitney U に基づくAUCで数値化(09 §4.2「分布の分離度」の実装解釈)
  - 診断: would_*のband(low/mid/high)一致率・帯ズレ集計・fit軸0〜4の±1以内一致率
    (参考値・正式な集計方法はG2実施時に確定=goldset-plan §5)
- **report.py**: docs/testassets/results/g2-jev-result-YYYYMMDD-HHMMSS.yaml。meta
  (実施日時・route・goldset SHA・JEV_QUESTIONS定数SHA・usage合計・実行ペア数・partial:
  --limit指定時true)・per_pair(id・route・would_*・MutualScore・L・gold・band判定)・metrics・
  失敗内訳。--limit時はレポート先頭に「部分実行=証拠外」を明記
- **__main__.py**: CLI(--assets 既定../docs/testassets・--out 既定<assets>/results・--limit・
  --route first|fallback|both 既定both)。起動検証(llm_mode=real+3鍵・fail-fast)
- **Makefile**: `g2-gate` ターゲット(uv run --env-file ../.env。g1-gateと同一書式)

合格基準との比較判定は組み込まない(基準はG2実施前にオーナー確定=スコープ外1。レポートは
指標値とgoldset-plan §11手順2の参考値「Precision 0.60以上@閾値0.80」の表示まで)。

### 2.9 ws-7 Minor引継ぎ4件の処置(判断)

| 引継ぎ | 内容 | 処置 |
|---|---|---|
| (a) | _SELECT_GROUP_PAIRSにORDER BYなし(同ペア複数バージョン行の選択がSQL意味論上不定) | **ws-8で拾う**。`ORDER BY intent_a_id, intent_b_id, GREATEST(updated_at)` 相当で新行優先を確定させる(現実の挙動と同一・決定性を明文化)。K上限E2E試験1の決定性検証の土台として意味がある。回帰は既存groupengine試験で網羅(挙動不変のため) |
| (b) | 世代リセット後のメンバー間ペア再生成は相手起点経由のみ | M3へ。仕様の空白に近く(06 §7〜§8に直接の規定なし)動作は妨げない。M3-1回答系設計時に持ち帰る |
| (c) | aggregate計算済み集合の早期continue(互換行列SQL・geo逆転を毎評価実行) | M3/M4へ。性能改善でci規模では不要。M4性能試験の計測結果で判断 |
| (d) | ON CONFLICT昇格でlatches.group_candidate_idが旧gidのまま残りうる | M3-1へ。参照不一致が顕在化するのは回答系(latches照会)の実装時。ws-7報告書がM3-1設計確認候補として記録済み |

(a)のみ本単位で修正する。理由: 「切り詰めが決定的」(引用#7)をdense配置の2回実行で証明する
本単位の主題と直結する一方、(b)(c)(d)は本単位の試験が依存しない。

### 2.10 G2完了条件の充足マップ(本単位の成果物→G2判定)

| G2条件(12 M2) | 担当 |
|---|---|
| ①02#5〜#12グリーン | §2.5対応表(既存+新設4本)。ci環境・スタブLLM(承認事項4) |
| ②K上限裏付け | §2.6試験1 |
| ③冪等性 | §2.6試験2(+既存ws-1 test_5/6/7) |
| ④縮退(breaker開放→半開) | §2.2実装+§2.4試験群 |
| ⑤日本語評価 | ハーネスは§2.8。**評価実行・合格判定はG2時(本単位外)**——STATUS「G2判定の待ち事項」1のとおり実行前金額報告(上限$15)→実施→オーナー判定 |

### 2.11 採用しないもの(YAGNIによる切り捨て一覧)

1. **breaker状態のRedis共有**(§2.1案B)——Worker複数構成が確定するM4で移行。IF不変
2. **フォールバック側の品質測定・フォールバック用breaker**——D-15はフォールバック失敗を
   skipped保留で受け止める設計(引用#1・#4)。フォールバックのbreakerは二段目の縮退を遅らせるだけ
3. **開放状態のDB・ログ永化**——状態遷移は構造化ログ(latch.breaker)で記録。永化が必要になるのは
   Observability(M4)
4. **Worker並列化・Jev評価の並行化**——ws-5 §2.10どおり計測後(M4)。breakerのレイテンシ測定は
   直列前提でも正しく機能する
5. **D-16上限到達試験**——M4(承認事項5)。Guard denyのskipはws-4試験済み
6. **p95/p50レイテンシ分布注入**(10 §4.1)——M4性能試験の条件
7. **g2gateでの再試行・失敗ペアの再実行**——07 §4「失敗は再試行せず」の規律を評価でも踏襲。
   失敗はレポートに記録し、実行のやり直し(--route限定等)は人間が判断
8. **評価のpytest自動化**——実API・実課金はCIに載せない(g1gateと同一規律)
9. **staging環境のセットアップ**——M4-1(承認事項4)
10. **K上限E2Eでの実API**——K上限は切り詰め機構の検証でスタブで十分(10 §1)。実API品質は⑤が担う

## 3. ファイル構成

### 3.1 作るもの(新規ファイル)

| ファイル | 内容 |
|---|---|
| backend/src/latch/llm/breaker.py | CircuitBreaker・BreakerParams(§2.2)。Clock注入・純部品 |
| backend/src/latch/g2gate/__init__.py | パッケージ公開IF(g1gate流儀) |
| backend/src/latch/g2gate/cases.py | goldset読込・JevTextInput変換・SHA(§2.8) |
| backend/src/latch/g2gate/runner.py | 両経路の評価実行(§2.8) |
| backend/src/latch/g2gate/compare.py | 指標算出(P/R/ECE/Brier/AUC・band診断)(§2.8) |
| backend/src/latch/g2gate/report.py | 証拠YAML生成(§2.8) |
| backend/src/latch/g2gate/__main__.py | CLI(--assets/--out/--limit/--route)(§2.8) |
| backend/tests/unit/llm/test_breaker.py | 状態機網羅(§4.1) |
| backend/tests/unit/g2gate/test_cases_compare.py | 変換・指標の既知値試験(§4.1) |
| backend/tests/integration/test_degraded_e2e.py | 縮退E2E 8試験(§2.4) |
| backend/tests/integration/test_k_limits_e2e.py | K上限・冪等・#7・#8の4試験(§2.6) |

### 3.2 触るもの(既存ファイルへの変更)

| ファイル | 変更 |
|---|---|
| backend/src/latch/llm/gateway.py | judge_pairへbreaker組み込み(§2.2)・call_jev_first/call_jev_fallback公開IF(§2.8)・build_worker_gatewayでbreaker生成 |
| backend/src/latch/llm/stub.py | fail_jev_exc引数の追加(§2.3) |
| backend/src/latch/worker/matching/group_engine.py | _SELECT_GROUP_PAIRSへORDER BY追加(§2.9a・挙動不変の決定性明文化) |
| backend/src/latch/llm/jev_smoke.py | _jev_fallback属性アクセスをcall_jev_fallbackへ置換(§2.8・振る舞い不変) |
| backend/src/latch/events/__main__.py または新CLI最小部 | `purge-match-sub` サブコマンド(§2.7。events/__main__.pyが無い場合は新設) |
| Makefile | test-ciへpurge挿入・g2-gateターゲット追加(§2.7・§2.8) |
| backend/tests/integration/test_events_pipeline.py | worker_envへJevWorker注入オプション(§2.4試験8用の最小拡張・既存8試験は無変更) |

### 3.3 触らないもの(明示)

- worker/jev.py・worker/main.py・worker/matching/layer4.py・layer1〜3・latch_engine・
  origin.py・candidates.py —— 縮退の再評価機構(RESELECT_ALWAYS)・キックチェーンは
  既存実装のままで試験のみ追加
- alembic/(マイグレーション追加なし。head=0005のまま)
- compose.yaml・docker/(環境構成不変)
- auth/users/intents/geo/ratelimit/g1gate —— 無関係
- docs/testassets/g2-jev-goldset.yaml(status=confirmed・評価資産は無変更)

## 4. テスト方針

### 4.1 unit(`make test`。外部プロセス不要・スタブで決定的)

- **test_breaker.py**(12件程度): closed不開放(1失敗のみ・min_samples)・エラー率>0.5で開放
  (2失敗/2呼=100%・3失敗/5呼=60%は開放、2失敗/5呼=40%は不開放)・窓追い出し(60秒前の失敗は
  判定から除外)・p95位置境界(N=20でtimeout 1件=5%は不開放・2件=10%は開放。timeout打ち切り=
  timeout_s記録)・half_open遷移(60秒前はallow False・60秒後True)・半開成功→closed+窓リセット・
  半開失敗→open戻し+60秒再延長・half_open試験中の防御・BreakerParams既定値ピン
  (60.0/0.5/60.0/2=docs確定値の機械検査)
- **gateway連携**(tests/unit/llm/既存ファイルへ追加): breaker込みjudge_pair(開放中は
  第一候補呼び出し0回でフォールバック・JevJudgment正常)・breaker=None従動作・
  call_jev_first/fallbackの戻り(provider・model)・fail_jev_excの4種例外マップ
- **g2gate部品**: cases変換(ng_unverifiable→downgraded_from_ng=True行・confirmed検査・SHA)・
  compare指標(合成データで既知値: P/R・ECE(手計算値)・Brier・AUC完全分離=1.0/完全逆転=0.0・
  band境界0.35/0.65)・report形式(--limit時partialフラグ)
- 既存試験の期待値追随は最小(breaker=None既定でgateway従動作のため基本なし・
  _SELECT_GROUP_PAIRSは挙動不変)

### 4.2 integration(`make test-ci`。compose常設DB・実Redis・スタブGateway)

- **test_degraded_e2e.py**(§2.4の8試験): ws-5流儀の対抗策(時間窓now+5日統一・subject prefix
  teardown・Redis prefix掃除)を踏襲。FakeClockでbreakerの60秒・debounceを進行
- **test_k_limits_e2e.py**(§2.6の4試験): dense配置はfixture直入れ(embedding直接UPDATE・
  ws-3流儀)。#7更新E2EはAPI実HTTP(worker_env)。#8はReevalRunner.run_once直接呼び出し
- 収集と実行の分離規律(ws-5): 実行はmake test-ciのみ。試験ファイルは実行不要の構成

### 4.3 検証手順(報告書への明記用)

1. `make lint && make test` — unit全件グリーン
2. `docker compose build api worker` — STATUS運用ルール4
3. `make test-ci` — 既存1125+新規12前後がグリーン・**worker復帰前にpurge-match-subが走ること**
   (Makefile出力で確認)
4. test-ci後の残存・孤立行確認: users/intents/match_candidates系は従来手順+group_candidates/
   latchesの孤立行(intent_idsがintentsに存在しない行)が**0件**——対抗策の実効性実証
   (§2.7。1度目は既存残存の掃除SQLを先行実行)
5. `make g2-gate -- --limit 2 --route both`(部分実行・実課金≈$0.02)でexit 0・
   results/へレポート生成・partial=true明記。**520全件実行はG2時(本単位では実施しない)**
6. lint再実行(test-ciにlintが含まれないため・ws-4運用メモ)
7. basename一意確認(運用ルール5)・alembic head=0005不変・時刻参照がclock.pyのみ(arch test)

## 5. 未解決の論点(supervisor承認事項)

設計判断のうち、仕様の空白・文言の読み替えにあたるもの。docs内の根拠から自分で答えたが、
G2判定の証拠に関わるため承認を求める(回答は設計書の該当節へ確定記録する)。

1. **p95条件の実装解釈**(§2.2): 「p95レイテンシがtimeout(6秒)超過で開放」——成功呼び出しは
   asyncio.timeout(6秒)により必ず6秒未満で完了するため、成功のみの母集団では「p95>6秒」は
   成立し得ない。よってtimeout呼び出しを「レイテンシ=打ち切り時点の6.0秒」として母集団へ入れ、
   「p95位置(昇順ceil(0.95N)−1)の値がtimeout_s以上」で開放する。これは「6秒超(打ち切り含む)
   呼び出しが窓内で5%超」相当。timeout呼び出しは同時にエラー率の分子にも入り、エラー率50%条件
   との二重カバーになる(どちらか先で開放)
2. **エラー率判定の最小サンプル=2**(§2.2): 06 D-15は「エラー率50%超」に最小呼び出し数を規定
   しない。N=1の失敗で開放すると「単発のtimeoutやスパイクでは発動しない」の趣旨(引用#1根拠節)
   に反するため、窓内呼び出し2回以上を判定条件に加える
3. **breaker状態はWorkerプロセス内メモリ**(§2.1案A): 再起動で閉状態から出直す。Worker複数
   インスタンス時の共有(Redis)はM4構成確定後に移行(IF不変)
4. **02#5〜#12はci環境(compose常設・スタブLLM)で実施してG2証拠とする**(§2.5): 10 §3は
   #5〜#12をstaging割り当てだが、staging構築はM4-1(12 §3)でありM2時点で存在しない。
   実プロバイダの品質は⑤日本語評価が独立に担うため、機能試験としてはスタブで十分。
   stagingでの再実行はM4-3(27項目フル実行)が担う
5. **D-16上限到達試験はws-8では実施しない**(§1.4-2): G2完了条件(引用#10)の文言に含まれず、
   12 M4スコープ4「縮退試験(D-16上限到達・resume再評価・catch-up回収)」が担う。
   Guard denyによるskipped自体はws-4試験済み。ただし復旧後再評価(FR-18)のLLM障害回復系
   (引用#6)はG2縮退文言の裏返しとして§2.4試験7で実施する
6. **Gatewayへcall_jev_first/call_jev_fallbackの公開IF追加**(§2.8): jev_smokeのプライベート
   属性アクセス(_jev_fallback)を解消しg2gateが両経路を直接実行するため。judge_pairの
   第一候補側からも再利用(二重実装なし)。call_jev_firstはbreakerを参照しない(評価は
   経路品質の実測が目的)
7. **ws-7 Minor 4件の処置**(§2.9): (a)ORDER BY追加のみws-8で拾う(決定性がK上限試験の主題と
   直結)。(b)世代リセット後のペア再生成経路・(c)aggregate早期continueはM3/M4・
   (d)latches.group_candidate_id旧gidはM3-1(参照不一致が顕在化する回答系の実装時)へ回す
8. **make test-ciへのpurge-match-sub挿入**(§2.7): test-ciの実行時間・並走への影響は軽微
   (subscription削除1回)だが、Makefileの検証フロー自体の変更のため承認を求める。
   効果実証は検証手順4(孤立行0件)

その他の設計判断(案A採用・g1gate流儀踏襲・新規試験4+8本の構成・マイグレーションなし)は
docs根拠と§2のトレードオフ記述のとおり。

## 6. スーパーバイザー承認記録(2026-09-30)

§5の8件をすべて承認した。承認に先立ち、引用の要(06 §8 D-15 FR-10・10 §3の
staging割り当て・12 M4-1/4-3・goldset-plan §11「ハーネス(ws-8)」明記)と
実装資産の主張(gateway `_jev_call` 注入ポイントコメント・`_SELECT_GROUP_PAIRS` の
ORDER BYなし・stub `fail_jev` のLLMProviderError固定・Makefile test-ci現状・
`RESELECT_ALWAYS`・jev_smoke.py:84のプライベートアクセス・09 §4.2の指標定義)を
独立突合し、すべて実態どおりであることを確認した。

- **1(p95条件の実装解釈)・2(min_samples=2): 承認**。FR-10の文言と根拠節
  「単発のtimeoutやレート制限のスパイクでは発動せず」との整合読みである。
  解釈記録としてG2時確認事項へ追加する
- **3(breakerはプロセス内メモリ): 承認**。ciはWorker 1(10 §1)。M4構成確定後の
  移行条件を§2.11-1が保持する
- **4(02#5〜#12はci環境で実施): 承認**。staging構築はM4-1(12 §3)でありM2時点に
  存在しない。G2条件①(12)「ci/staging」をci側で満たす以外に進みようがなく、
  stagingでの再実行はM4-3(27項目フル実行)が担う。ゲート証拠の読み替えに
  あたるため**G2時確認事項へ追加する**(ユーザーへの報告済み)
- **5(D-16上限到達試験はws-8では実施しない): 承認**。G2完了条件(12)の文言に
  含まれず、12 M4スコープ4「縮退試験(D-16上限到達・resume再評価・catch-up回収)」の
  範囲。LLM障害回復後の再評価(FR-18)は§2.4試験7で実施する
- **6(call_jev_first/call_jev_fallback公開IF): 承認**。jev_smoke.py:84の
  プライベート属性アクセス解消を兼ねる内部改善。judge_pairからの再利用で
  二重実装を避ける構成も妥当
- **7(Minor 4件の処置): 承認**。(a)のORDER BY追加はK上限試験の決定性検証の土台で
  あり本単位の主題と直結する。(b)(c)(d)をM3/M4へ回す判断も各引継ぎの性質と一致
- **8(test-ciへのpurge-match-sub挿入): 承認**。検証フローの変更だが影響は軽微で、
  worker復帰時のensure()による再作成と対になっており、ci環境外では実行されない。
  効果実証は検証手順4(孤立行0件)が担う

§5外の解釈で確認したもの: call_jev_firstがbreakerを参照しない(§2.8)は
評価=経路品質の実測という目的と整合。09 §4.2「分布の分離度」の
Mann-Whitney U AUCによる実装(§2.8 compare.py)はランク指標という指標定義と
整合しており、あわせて採用する(band診断の集計方法をG2実施時に確定する
留保もそのまま)。

本設計でユーザー持ち帰りを要する事項(仕様変更・契約・優先度)はなし。
