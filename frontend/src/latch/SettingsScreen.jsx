// 設定画面(M3 ws-8 design §2.4・M4 ws-5・UI v2 Phase 6でReact化)。旧settings.js
// (3セクションのマウント指示)+permission.js/blocks.js/consent.jsの配線を
// 1つの画面コンポーネントへ載せ替えたもの。3セクション(通知許可・ブロック管理・
// LLM送信の同意)。規定のない要素(プロフィール編集・ログアウト・テーマ等)は
// 置かない(design §1.4)。通知許可(D-18)とLLM送信同意(D-14)は根拠も効果も
// 別物のため区分表示を維持する。
import { PermissionControl } from "./PermissionControl.jsx";
import { BlocksSection } from "./BlocksSection.jsx";
import { ConsentCard } from "./ConsentCard.jsx";

export const SettingsScreen = ({ client }) => (
  <section className="page-main page-main--settings" id="settingsScreen">
    <header className="settings-hero">
      <p className="section-kicker"><span></span>SETTINGS</p>
      <h1 className="settings-title">設定</h1>
    </header>
    <section className="card settings-card" aria-labelledby="notificationSettingsTitle">
      <h2 id="notificationSettingsTitle" className="card-title">通知</h2>
      <PermissionControl
        notificationApi={typeof Notification !== "undefined" ? Notification : undefined}
      />
    </section>
    <section className="card settings-card" aria-labelledby="blockSettingsTitle">
      <h2 id="blockSettingsTitle" className="card-title">ブロック管理</h2>
      <BlocksSection client={client} />
    </section>
    <section className="card settings-card" aria-labelledby="consentSettingsTitle">
      <h2 id="consentSettingsTitle" className="card-title">LLM送信の同意</h2>
      <ConsentCard client={client} interactive={false} />
    </section>
  </section>
);

// オンボーディング画面(文言はtexts.jsの承認済み定数から描く)
export const OnboardingScreen = ({ client }) => (
  <section className="page-main page-main--settings" id="onboardingScreen">
    <header className="settings-hero">
      <p className="section-kicker"><span></span>ONBOARDING</p>
      <h1 className="settings-title">LLM送信の同意</h1>
    </header>
    <ConsentCard
      client={client}
      interactive
      onAgreed={() => {
        location.hash = "#/"; // 同意済み → ホームへ戻す
      }}
    />
  </section>
);
