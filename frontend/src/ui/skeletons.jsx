// 画面スケルトン(UI v2 Phase 6・移行期)。旧index.htmlに静的に書かれていた
// 画面の骨組みのうち、まだ旧DOM配線モジュールが命を吹き込む画面
// (03待機中・D-11一覧・設定・オンボーディング)の内側をJSXへ写したもの。
// 01ホーム+02読み解き確認はすでにReactコンポーネント化済み(作業3)。
// 残りも作業4・5でコンポーネント化してこのファイルは消える。
// クラス名・ID・文言・属性は現行実装から一切変えない。

// --- 03 待機中 — UI v2 Phase 3(プロトタイプ03が正源)の内側 --------------------
export const StandbyScreenInner = () => (
  <>
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
  </>
);

// --- 候補・成立の簡易一覧(D-11・UI v2 Phase 5)の内側 --------------------------
export const ListScreenInner = ({ mode }) => (
  <>
    <section className="list-hero">
      <p className="pv-badge" data-list-badge hidden><span className="pv-badge-dot" aria-hidden="true"></span><span data-list-badge-text>{mode === "proposals" ? "LATCH候補 · 0件" : "成立 · 0件"}</span></p>
      <h1 className="list-title" data-list-title></h1>
    </section>
    <section className="sb-others-sec">
      <ul className="sb-others" data-list-rows></ul>
      <button className="ghost notice-more" data-list-more type="button" hidden>もっと見る</button>
    </section>
  </>
);

// --- 設定(UI v2 Phase 5)の内側 ------------------------------------------------
export const SettingsScreenInner = () => (
  <>
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
  </>
);

// --- オンボーディングの内側(文言はconsent.jsがtexts.jsの承認済み定数から描く) --
export const OnboardingScreenInner = () => (
  <>
    <header className="settings-hero">
      <p className="section-kicker"><span></span>ONBOARDING</p>
      <h1 className="settings-title">LLM送信の同意</h1>
    </header>
    <div id="consentOnboarding"></div>
  </>
);
