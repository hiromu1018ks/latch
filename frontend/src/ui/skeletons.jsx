// 画面スケルトン(UI v2 Phase 6・移行期)。旧index.htmlに静的に書かれていた
// 各画面の骨組みをそのままJSXへ写したもの。Phase 6の各作業で中身を
// Reactコンポーネントへ置き換えていく(01・02は作業3、03/04/05は作業4、
// 横断は作業5)。クラス名・ID・文言・属性は現行実装から一切変えない。
import { Icon } from "../ui/Icon.jsx";

// --- 01 預ける(ホーム) — UI v2 Phase 2 ---------------------------------------
export const HomeScreenSkeleton = ({ homeRef }) => (
  <div className="page-main" id="homeScreen" ref={homeRef}>
    <div className="haze haze-home" aria-hidden="true"></div>

    <section className="compose-section" aria-labelledby="h-compose">
      <p className="standby-line" id="standbyLine" hidden>
        <span className="beacon" aria-hidden="true"></span>
        <span data-standby-count>0件の意思が待機中 — いまは何もしなくて大丈夫</span>
      </p>
      <h1 id="h-compose" className="hero-title">どんな条件なら、<br />動きますか。</h1>
      <p className="hero-lead">ひとこと預けるだけで、探す必要はありません。条件が揃った瞬間に、相手と機会のほうから届きます。</p>

      <div className="composer">
        <label htmlFor="intentText" className="composer-label">条件をひとことで</label>
        <textarea id="intentText" rows="3" maxLength={300} aria-describedby="readStatus" placeholder="例：今日20時以降、天文館で2〜4人、予算5,000円までなら軽く飲みたい"></textarea>

        <div className="read-section">
          <p className="read-head"><span className="read-dot" aria-hidden="true"></span>LATCHの読み取り（入力に合わせて更新）</p>
          <div id="readStatus" aria-live="polite" className="read-status">
            <div id="readChips" className="read-chips"></div>
            <span className="read-guide" data-guide hidden>時間・場所・人数・金額などを書くと、ここで読み取ります</span>
            <span className="read-noread" data-noread hidden>
              <Icon name="info" size={18} />
              <span>まだ条件を読み取れません。「今日20時以降」「天文館で」「3人」「5,000円まで」のように、時間・場所・人数・金額のどれかを足してみてください。</span>
            </span>
          </div>
          <p className="parse-error" id="parseError" role="alert" hidden></p>
        </div>

        <div className="composer-foot">
          <div className="composer-foot-left">
            <p className="lock-line">
              <Icon name="lock" size={16} />
              <span>成立するまで、この条件は誰にも見えません</span>
            </p>
            <button type="button" className="ghost draft-button" id="draftButton">下書きに保存</button>
          </div>
          <div className="cta-wrap">
            <button type="button" className="cta read-button" id="readButton" aria-describedby="ctaHelp">
              <span className="cta-label">読み解く</span>
            </button>
            <span className="cta-help" id="ctaHelp">条件を書くと、読み解けます</span>
          </div>
        </div>
      </div>

      <div className="examples">
        <p className="examples-label">こんな預け方もできます</p>
        <div className="example-chips" id="exampleChips"></div>
      </div>
    </section>

    <section className="wait-section" id="waitSection" aria-labelledby="h-wait">
      <div className="wait-head">
        <div className="wait-title">
          <h2 id="h-wait" className="wait-heading">待機中の意思</h2>
          <span className="wait-sub">あなたが預けて、まだ成立していない条件</span>
        </div>
        <a className="wait-all" href="#/standby">すべて見る
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M5 12h14"></path><path d="M13 6l6 6-6 6"></path></svg>
        </a>
      </div>
      <div className="intent-grid" id="intentCards"></div>
    </section>
  </div>
);

// --- 02 読み解き確認 — UI v2 Phase 2 -----------------------------------------
export const ConfirmScreenSkeleton = ({ confirmRef }) => (
  <section className="page-main page-main--confirm" id="confirmScreen" ref={confirmRef} hidden>
    <div className="haze haze-confirm" aria-hidden="true"></div>

    <div className="confirm-head">
      <ol className="steps" aria-label="預けるまでの手順">
        <li className="step done">
          <span className="step-check" aria-hidden="true">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round"><path d="M5 12.5l4.5 4.5L19 7"></path></svg>
          </span>
          <span>書く<span className="vh">（完了）</span></span>
        </li>
        <li className="step now" aria-current="step">
          <span className="step-line" aria-hidden="true"></span>
          <span className="step-no" aria-hidden="true">02</span>
          <span>読み解きを確認<span className="vh">（現在の手順）</span></span>
        </li>
        <li className="step todo">
          <span className="step-line" aria-hidden="true"></span>
          <span className="step-no" aria-hidden="true">03</span>
          <span>預ける</span>
        </li>
      </ol>
      <h1 className="confirm-title">こう読み解きました。</h1>
      <p className="confirm-lead">ずれていたら、ここで直してください。確認して預けたら、あとは待つだけです。</p>
    </div>

    <div className="confirm-grid">
      <div className="confirm-col">
        <section className="card words-card" aria-labelledby="h-words">
          <div className="card-head-row">
            <h2 id="h-words" className="card-title">あなたの言葉</h2>
            <a className="rewrite-link" href="#/" data-action="rewrite">
              <Icon name="pencil" size={15} />
              書き直す
            </a>
          </div>
          <p className="words-text" id="wordsText"></p>
          <p className="words-note">番号は、読み取った条件の番号に対応しています</p>
        </section>

        <section className="card privacy-card" aria-labelledby="h-privacy">
          <h2 id="h-privacy" className="card-title">公開範囲と通知</h2>
          <ul className="privacy-list">
            <li className="privacy-item">
              <span className="privacy-ic">
                <Icon name="eyeOff" size={19} />
              </span>
              <div className="privacy-body">
                <p className="privacy-t">成立するまで、誰にも表示されません</p>
                <p className="privacy-s">双方が「参加する」と答えたときにだけ、はじめてお互いが見えます。</p>
              </div>
            </li>
            <li className="privacy-item">
              <span className="privacy-ic">
                <Icon name="bell" size={19} />
              </span>
              <div className="privacy-body">
                <p className="privacy-t">通知は「LATCH候補があります」の一文だけ</p>
                <p className="privacy-s">ロック画面に「飲みたい」という意思が出ることはありません。</p>
              </div>
            </li>
            <li className="privacy-item">
              <span className="privacy-ic">
                <Icon name="clock" size={19} />
              </span>
              <div className="privacy-body">
                <p className="privacy-t"><span id="expiryLabel">—</span> に自動で取り下げ</p>
                <p className="privacy-s">時間を過ぎた条件は、静かに消えます。</p>
                <label htmlFor="expiry" className="expiry-label">取り下げる時刻
                  <select id="expiry" className="field" disabled></select>
                </label>
              </div>
            </li>
          </ul>
        </section>
      </div>

      <section className="card slots-card" aria-labelledby="h-slots">
        <div className="slots-head">
          <h2 id="h-slots" className="card-title">読み取った条件</h2>
          <span aria-live="polite" className="slot-summary"><span className="dot" id="slotSummaryDot" aria-hidden="true"></span><span id="slotSummary">5項目 · 確認が必要なもの 1件</span></span>
        </div>
        <div id="confirmRows"></div>
        <div className="extras">
          <p className="extras-label">足せる条件（任意）— 足すほど、届く候補は少なくなります</p>
          <div className="extra-chips" id="extraChips"></div>
        </div>
        <div className="confirm-actions">
          <button type="button" className="cta deposit-button" id="depositButton" aria-describedby="depositHelp">
            <span className="cta-label">この条件で預ける</span>
          </button>
          <a className="ghost rewrite-button" href="#/" data-action="rewrite">書き直す</a>
        </div>
        <p className="cta-help strong" id="depositHelp">「03 人数」の確認に答えると、預けられます。</p>
        <p className="form-error" id="confirmError" role="alert" hidden></p>
      </section>
    </div>
  </section>
);

// --- 03 待機中 — UI v2 Phase 3(プロトタイプ03が正源) --------------------------
export const StandbyScreenSkeleton = ({ standbyRef }) => (
  <div className="page-main page-main--standby" id="standbyScreen" ref={standbyRef} hidden>
    <section className="sb-hero" aria-labelledby="h-standby">
      <div className="sb-col">
        <p className="sb-badge"><span className="beacon" aria-hidden="true"></span><span data-sb-badge>待機中 · 0件</span></p>
        <h1 id="h-standby" className="sb-title"><span data-sb-title-a>条件が揃うのを、</span><br /><span data-sb-title-b>待っています。</span></h1>
        <p className="sb-lead" data-sb-lead>あなたが預けた条件です。別の誰かの条件と成立し得ると判断された瞬間にだけ、お知らせします。</p>
        <article className="sb-main" data-sb-main aria-label="預けた条件" hidden></article>
      </div>
      <div className="sb-orb" data-sb-orb aria-hidden="true" hidden></div>
    </section>

    <section className="sb-doing" aria-labelledby="h-doing">
      <h2 id="h-doing" className="sb-h2">いま、LATCHがしていること</h2>
      <div className="doing-grid">
        <div className="doing-card">
          <span aria-hidden="true" className="doing-no">01</span>
          <h3>照らし合わせ続ける</h3>
          <p>あなたの条件を、ほかの待機中の意思と照らし合わせ続けています。そのあいだ、あなたの条件は誰にも表示されません。</p>
        </div>
        <div className="doing-card">
          <span aria-hidden="true" className="doing-no">02</span>
          <h3>成立し得るかだけを見る</h3>
          <p>見るのは人柄や相性ではなく、その時・その条件で二つの意思が成立し得るか。片方だけが大満足する提案は出しません。</p>
        </div>
        <div className="doing-card">
          <span aria-hidden="true" className="doing-no">03</span>
          <h3>揃ったら、ひとことだけ</h3>
          <p>通知の本文はこの一文だけ。ロック画面に意思が漏れることはありません。</p>
          <div className="doing-notify" aria-hidden="true">
            <span className="doing-notify-ic">
              <svg width="18" height="18" viewBox="0 0 40 40" fill="none"><path d="M19 5 A15 15 0 0 0 19 35" style={{ stroke: "var(--t1)" }} strokeWidth="4" strokeLinecap="round"></path><path d="M22 5 A15 15 0 0 1 22 35" style={{ stroke: "var(--acc)" }} strokeWidth="4" strokeLinecap="round"></path></svg>
            </span>
            <span className="doing-notify-body"><span className="doing-notify-app">LATCH</span><span className="doing-notify-text">LATCH候補があります</span></span>
            <span className="doing-notify-time">今</span>
          </div>
        </div>
      </div>
    </section>

    <section className="sb-others-sec" aria-labelledby="h-others">
      <div className="sb-others-head">
        <h2 id="h-others" className="sb-h2">ほかに待機中の意思</h2>
        <a className="sb-new" href="#/">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M12 5v14M5 12h14"></path></svg>新しく預ける
        </a>
      </div>
      <ul className="sb-others" data-sb-others></ul>
    </section>

    {/* 下書き(D-13・UI v2 Phase 5)。1件もないときは画面側がsectionごと隠す */}
    <section className="sb-others-sec" data-sb-drafts-section aria-labelledby="h-drafts" hidden>
      <div className="sb-others-head">
        <h2 id="h-drafts" className="sb-h2">下書き</h2>
      </div>
      <ul className="sb-others" data-sb-drafts></ul>
    </section>
  </div>
);

// --- 候補・成立の簡易一覧(D-11・UI v2 Phase 5) --------------------------------
const ListScreenSkeleton = ({ id, listRef }) => (
  <div className="page-main page-main--lists" id={id} ref={listRef} hidden>
    <section className="list-hero">
      <p className="pv-badge" data-list-badge hidden><span className="pv-badge-dot" aria-hidden="true"></span><span data-list-badge-text>{id === "proposalsScreen" ? "LATCH候補 · 0件" : "成立 · 0件"}</span></p>
      <h1 className="list-title" data-list-title></h1>
    </section>
    <section className="sb-others-sec">
      <ul className="sb-others" data-list-rows></ul>
      <button className="ghost notice-more" data-list-more type="button" hidden>もっと見る</button>
    </section>
  </div>
);

export const ProposalsScreenSkeleton = ({ listRef }) => (
  <ListScreenSkeleton id="proposalsScreen" listRef={listRef} />
);
export const MatchedScreenSkeleton = ({ listRef }) => (
  <ListScreenSkeleton id="matchedScreen" listRef={listRef} />
);

// --- 設定(UI v2 Phase 5)とオンボーディング ------------------------------------
export const SettingsScreenSkeleton = ({ settingsRef }) => (
  <section className="page-main page-main--settings" id="settingsScreen" ref={settingsRef} hidden>
    <header className="settings-hero">
      <p className="section-kicker"><span></span>SETTINGS</p>
      <h1 className="settings-title">設定</h1>
    </header>
    <section className="card settings-card" aria-labelledby="notificationSettingsTitle">
      <h2 id="notificationSettingsTitle" className="card-title">通知</h2>
      <div id="permissionControl"></div>
    </section>
    <section className="card settings-card" aria-labelledby="blockSettingsTitle">
      <h2 id="blockSettingsTitle" className="card-title">ブロック管理</h2>
      <div className="block-list" id="blockList"></div>
      <button className="ghost notice-more" id="blockMoreButton" type="button" hidden>もっと見る</button>
    </section>
    <section className="card settings-card" aria-labelledby="consentSettingsTitle">
      <h2 id="consentSettingsTitle" className="card-title">LLM送信の同意</h2>
      <div id="consentControl"></div>
    </section>
  </section>
);

export const OnboardingScreenSkeleton = ({ onboardingRef }) => (
  <section className="page-main page-main--settings" id="onboardingScreen" ref={onboardingRef} hidden>
    <header className="settings-hero">
      <p className="section-kicker"><span></span>ONBOARDING</p>
      <h1 className="settings-title">LLM送信の同意</h1>
    </header>
    <div id="consentOnboarding"></div>
  </section>
);
