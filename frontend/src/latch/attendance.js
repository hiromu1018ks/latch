// 実施自己申告(09 §2.2 D-09「実際に会いましたか?」)。completed詳細の2択。
// UI v2 Phase 4でD-14の決定どおりv2部品(05のasideカード構成)へ再様式化した
// (プロトタイプに画面はないため部品の踏襲が正・画面構成の裁量は Phase 4で最小限)。
// 未回答かどうかの判別フィールドがないため、質問を表示してPOSTし、409で表示を
// 切替える(窓判定・二重回答ともサーバが真実)。
import {
  ATTENDANCE_ALREADY_TEXT,
  ATTENDANCE_DONE_TEXT,
  ATTENDANCE_ERROR_TEXT,
  ATTENDANCE_NO_LABEL,
  ATTENDANCE_QUESTION,
  ATTENDANCE_YES_LABEL,
  RATE_LIMIT_TEXT,
} from "./texts.js";
import { escapeHtml } from "./view.js";

export const createAttendanceFlow = ({ client, latchId, mount }) => {
  const noteEl = document.createElement("p");
  noteEl.className = "attendance-note";
  noteEl.hidden = true;

  const showNote = (text) => {
    noteEl.textContent = text;
    noteEl.hidden = false;
  };

  const submit = async (attended) => {
    mount.querySelector(".attendance-field")?.remove(); // 2択を外す
    try {
      await client.call("POST", `/v1/latches/${latchId}/attendance`, {
        body: { attended },
      });
      showNote(ATTENDANCE_DONE_TEXT);
    } catch (err) {
      if (err?.code === "ATTENDANCE_ALREADY_SUBMITTED") {
        showNote(ATTENDANCE_ALREADY_TEXT); // 回答済み表示へ切替
        return;
      }
      if (err?.code === "ATTENDANCE_WINDOW_CLOSED") {
        mount.replaceChildren(); // 3日窓超過は非表示化
        return;
      }
      showNote(err?.code === "RATE_LIMITED" ? RATE_LIMIT_TEXT : ATTENDANCE_ERROR_TEXT);
    }
  };

  const renderQuestion = () => {
    const field = document.createElement("fieldset");
    field.className = "attendance-field";
    const legend = document.createElement("legend");
    legend.className = "attendance-legend";
    legend.textContent = ATTENDANCE_QUESTION;
    field.append(legend);
    const actions = document.createElement("div");
    actions.className = "attendance-actions";
    for (const [value, label, cls] of [
      ["true", ATTENDANCE_YES_LABEL, "cta attendance-yes"],
      ["false", ATTENDANCE_NO_LABEL, "ghost attendance-no"],
    ]) {
      const button = document.createElement("button");
      button.type = "button";
      button.dataset.value = value;
      button.className = cls;
      button.textContent = label;
      button.addEventListener("click", () => submit(value === "true"));
      actions.append(button);
    }
    field.append(actions);
    mount.replaceChildren(field, noteEl);
    mount.hidden = false;
  };

  return { renderQuestion, submit };
};
