// 実施自己申告(09 §2.2 D-09「実際に会いましたか?」)。completed詳細の2択。
// 旧attendance.js(素のDOM配線)をReactコンポーネントへ載せ替えたもの
// (UI v2 Phase 6・D-14の決定どおりv2部品=05のasideカード構成)。
// 未回答かどうかの判別フィールドがないため、質問を表示してPOSTし、409で表示を
// 切替える(窓判定・二重回答ともサーバが真実)。
import { useState } from "react";
import {
  ATTENDANCE_ALREADY_TEXT,
  ATTENDANCE_DONE_TEXT,
  ATTENDANCE_ERROR_TEXT,
  ATTENDANCE_NO_LABEL,
  ATTENDANCE_QUESTION,
  ATTENDANCE_YES_LABEL,
  RATE_LIMIT_TEXT,
} from "./texts.js";

export const AttendanceCard = ({ client, latchId }) => {
  const [asking, setAsking] = useState(true); // 質問を表示している間
  const [hidden, setHidden] = useState(false); // 3日窓超過は非表示化(§9-6①)
  const [note, setNote] = useState(null);

  const submit = async (attended) => {
    setAsking(false); // 2択を外す
    try {
      await client.call("POST", `/v1/latches/${latchId}/attendance`, {
        body: { attended },
      });
      setNote(ATTENDANCE_DONE_TEXT);
    } catch (err) {
      if (err?.code === "ATTENDANCE_ALREADY_SUBMITTED") {
        setNote(ATTENDANCE_ALREADY_TEXT); // 回答済み表示へ切替
        return;
      }
      if (err?.code === "ATTENDANCE_WINDOW_CLOSED") {
        setHidden(true); // 完全に非表示
        return;
      }
      setNote(err?.code === "RATE_LIMITED" ? RATE_LIMIT_TEXT : ATTENDANCE_ERROR_TEXT);
    }
  };

  return (
    <div className="lt-attendance" data-role="attendance">
      {hidden ? null : (
        <>
          {asking ? (
            <fieldset className="attendance-field">
              <legend className="attendance-legend">{ATTENDANCE_QUESTION}</legend>
              <div className="attendance-actions">
                <button type="button" className="cta attendance-yes" data-value="true" onClick={() => submit(true)}>
                  {ATTENDANCE_YES_LABEL}
                </button>
                <button type="button" className="ghost attendance-no" data-value="false" onClick={() => submit(false)}>
                  {ATTENDANCE_NO_LABEL}
                </button>
              </div>
            </fieldset>
          ) : null}
          <p className="attendance-note" hidden={!note}>{note}</p>
        </>
      )}
    </div>
  );
};
