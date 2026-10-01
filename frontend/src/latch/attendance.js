// 実施自己申告(D-09・M3 ws-7 design §2.7)。completed詳細の2択。
// 未回答かどうかの判別フィールドがないため、質問を表示してPOSTし、
// 409で表示を切替える(窓判定・二重回答ともサーバが真実)。
import {
  ATTENDANCE_ALREADY_TEXT,
  ATTENDANCE_DONE_TEXT,
  ATTENDANCE_ERROR_TEXT,
  ATTENDANCE_QUESTION,
  RATE_LIMIT_TEXT,
} from "./texts.js";

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
    legend.textContent = ATTENDANCE_QUESTION;
    field.append(legend);
    for (const [value, label] of [["true", "会いました"], ["false", "会えていません"]]) {
      const button = document.createElement("button");
      button.type = "button";
      button.dataset.value = value;
      button.textContent = label;
      button.addEventListener("click", () => submit(value === "true"));
      field.append(button);
    }
    mount.replaceChildren(field, noteEl);
  };

  return { renderQuestion, submit };
};
