// 通報・ブロックの統合dialog(UI v2 Phase 4・D-09・プロトタイプ05が正源)。
// 旧safety.js(素のDOM配線)をReactコンポーネントへ載せ替えたもの(UI v2 Phase 6)。
// 成立済み詳細の「安全のために」ボタンと提案詳細の通報導線から開く。
// 2ステップ: ①対象メンバーのラジオ+「ブロックする」/「通報する」
// ②「通報する」押下時に理由4択のステップをdialog内に挿入する(D-09決定。
// reports APIのreasonは必須のため・プロトタイプは作り込みの省略)。
// 完了はdialog内のrole=status表示(プロトタイプのdone状態)+ブロック成功時は
// onBlocked(詳細再取得)で収束する。提案1対1(participants=null)は対象ラジオも
// ブロックボタンも出さず、reportee_id省略body(案X)。
import { useEffect, useMemo, useRef, useState } from "react";
import {
  RATE_LIMIT_TEXT,
  REPORT_REASON_OPTIONS,
  SAFETY_BACK_LABEL,
  SAFETY_BLOCK_LABEL,
  SAFETY_CANCEL_LABEL,
  SAFETY_CLOSE_LABEL,
  SAFETY_DIALOG_TITLE,
  SAFETY_ERROR_TEXT,
  SAFETY_NOTE_TEXT,
  SAFETY_REASON_ERROR_TEXT,
  SAFETY_REASON_LEGEND,
  SAFETY_REASON_NOTE,
  SAFETY_REPORT_LABEL,
  SAFETY_TARGET_ERROR_TEXT,
  SAFETY_TARGET_LABEL,
} from "./texts.js";

export const SafetyDialog = ({ client, open, onClose, onBlocked, latchId, participants, meId }) => {
  const ref = useRef(null);
  const [step, setStep] = useState("target"); // target | reason | done
  const [selectedId, setSelectedId] = useState(null);
  const [doneMessage, setDoneMessage] = useState("");
  const [errorText, setErrorText] = useState(null);
  const [reasonPicked, setReasonPicked] = useState(false);

  // 対象メンバー(自分を除く)。提案1対1(participants=null)は対象非開示(案X)
  const omitReportee = participants == null;
  const candidates = useMemo(
    () =>
      (participants ?? [])
        .filter((p) => p.user_id !== meId)
        .map((p) => ({ id: p.user_id, name: p.display_name })),
    [participants, meId],
  );
  // 1対1: あらかじめ選択済みで出す
  const fixedId = candidates.length === 1 ? candidates[0].id : null;

  // 開くたびに初期化(旧open()と同じ)
  useEffect(() => {
    if (!open) return;
    setStep("target");
    setSelectedId(fixedId);
    setDoneMessage("");
    setErrorText(null);
    setReasonPicked(false);
    if (ref.current && !ref.current.open) ref.current.showModal();
  }, [open, latchId, fixedId]);

  // dialogが閉じたら(Esc含む)呼び出し側へ返す
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const onClosed = () => onClose?.();
    el.addEventListener("close", onClosed);
    return () => el.removeEventListener("close", onClosed);
  }, [onClose]);

  if (!open) return <dialog className="dlg" id="safetyDialog" aria-label="通報・ブロック" ref={ref}></dialog>;

  const close = () => {
    if (ref.current?.open) ref.current.close();
  };

  const showError = (text) => setErrorText(text);

  const reporteeName = () => candidates.find((c) => c.id === selectedId)?.name ?? "";

  const submitBlock = async () => {
    const targetId = selectedId;
    if (!targetId) {
      showError(SAFETY_TARGET_ERROR_TEXT);
      return;
    }
    try {
      await client.call("POST", `/v1/users/${targetId}/block`);
      setStep("done");
      setDoneMessage(`${reporteeName()}さんをブロックしました。今後この相手とLATCHが成立することはありません。`);
      await onBlocked?.(); // 詳細再取得(D-23の表示収束)
    } catch (err) {
      showError(err?.code === "RATE_LIMITED" ? RATE_LIMIT_TEXT : SAFETY_ERROR_TEXT);
    }
  };

  const submitReport = async (reason) => {
    if (!reason) {
      showError(SAFETY_REASON_ERROR_TEXT);
      return;
    }
    const body = { latch_id: latchId, reason };
    if (fixedId) body.reportee_id = fixedId;
    else if (selectedId) body.reportee_id = selectedId;
    // omitReportee(提案1対1)はreportee_idなしのまま送る(案X)
    try {
      await client.call("POST", "/v1/reports", { body });
      const name = reporteeName();
      setStep("done");
      setDoneMessage(`${name ? `${name}さんについての` : ""}通報を受け付けました。運営が内容を確認します。`);
    } catch (err) {
      showError(err?.code === "RATE_LIMITED" ? RATE_LIMIT_TEXT : SAFETY_ERROR_TEXT);
    }
  };

  // backdropクリックで閉じる(Escは<dialog>標準)
  const onBackdropClick = (event) => {
    if (event.target === ref.current && ref.current.open) close();
  };

  const h2 = <h2>{SAFETY_DIALOG_TITLE}</h2>;

  if (step === "done") {
    return (
      <dialog className="dlg" id="safetyDialog" aria-label="通報・ブロック" ref={ref} onClick={onBackdropClick}>
        <div className="safety-body">
          {h2}
          <p role="status" className="safety-done">{doneMessage}</p>
          <div className="safety-actions">
            <button type="button" className="ghost safety-btn" data-role="safety-close" onClick={close}>
              {SAFETY_CLOSE_LABEL}
            </button>
          </div>
        </div>
      </dialog>
    );
  }

  if (step === "reason") {
    return (
      <dialog className="dlg" id="safetyDialog" aria-label="通報・ブロック" ref={ref} onClick={onBackdropClick}>
        <div className="safety-body">
          {h2}
          <fieldset className="safety-targets safety-reasons">
            <legend>{SAFETY_REASON_LEGEND}</legend>
            <span className="safety-reason-note">{SAFETY_REASON_NOTE}</span>
            {REPORT_REASON_OPTIONS.map(([code, label]) => (
              <label key={code} className="safety-opt">
                {/* happy-domはラジオの排他(uncheck)を実装しないため、試験からも
                    全解除→選択で扱う(旧latch-report試験の手法を踏襲) */}
                <input
                  type="radio"
                  name="safety-reason"
                  value={code}
                  onChange={() => {
                    setReasonPicked(true);
                    setErrorText(null);
                  }}
                />{" "}
                {label}
              </label>
            ))}
          </fieldset>
          <p className="safety-error" data-role="safety-error" role="alert" hidden={!errorText}>{errorText}</p>
          <div className="safety-actions">
            <button type="button" className="ghost safety-btn" data-role="safety-back" onClick={() => setStep("target")}>
              {SAFETY_BACK_LABEL}
            </button>
            <button
              type="button"
              className="safety-btn safety-solid"
              data-role="safety-report-submit"
              disabled={!reasonPicked}
              onClick={(event) => {
                const reason = ref.current?.querySelector('input[name="safety-reason"]:checked')?.value;
                if (!reason) {
                  showError(SAFETY_REASON_ERROR_TEXT);
                  return;
                }
                void submitReport(reason);
              }}
            >
              {SAFETY_REPORT_LABEL}
            </button>
          </div>
        </div>
      </dialog>
    );
  }

  // target(1ステップ目)
  return (
    <dialog className="dlg" id="safetyDialog" aria-label="通報・ブロック" ref={ref} onClick={onBackdropClick}>
      <div className="safety-body">
        {h2}
        {omitReportee ? null : (
          <fieldset className="safety-targets">
            <legend>{SAFETY_TARGET_LABEL}</legend>
            {candidates.map((c) => {
              const checked = fixedId === c.id || selectedId === c.id;
              return (
                <label key={c.id} className={`safety-opt${checked ? " on" : ""}`}>
                  <input
                    type="radio"
                    name="safety-who"
                    value={c.id}
                    checked={checked}
                    onChange={(event) => {
                      if (event.target.checked) {
                        setSelectedId(c.id);
                        setErrorText(null);
                      }
                    }}
                  />{" "}
                  {c.name}
                </label>
              );
            })}
          </fieldset>
        )}
        <p className="safety-note">{SAFETY_NOTE_TEXT}</p>
        <p className="safety-error" data-role="safety-error" role="alert" hidden={!errorText}>{errorText}</p>
        <div className="safety-actions">
          <button type="button" className="ghost safety-btn" data-role="safety-close" onClick={close}>
            {SAFETY_CANCEL_LABEL}
          </button>
          {omitReportee ? null : (
            <button
              type="button"
              className="ghost safety-btn safety-dng"
              data-role="safety-block"
              disabled={!selectedId}
              onClick={submitBlock}
            >
              {SAFETY_BLOCK_LABEL}
            </button>
          )}
          <button
            type="button"
            className="safety-btn safety-solid"
            data-role="safety-report"
            disabled={!(selectedId || omitReportee)}
            onClick={() => {
              if (!selectedId && !omitReportee) {
                showError(SAFETY_TARGET_ERROR_TEXT);
                return;
              }
              setStep("reason");
            }}
          >
            {SAFETY_REPORT_LABEL}
          </button>
        </div>
      </div>
    </dialog>
  );
};
