// 体感アンケートの配信カード(V1-P3・05 v0.7・09 §2.3)。03待機中への受動表示:
// モーダル・通知・バッジを使わない(押しつけにならない単発の配信)。
// GET /v1/users/me/surveyのdue判定で描画し、回答済みラウンドは二度と出ない
// (サーバ=真実・UNIQUE(user_id, round))。onboarding=便利さ1問・
// week4=便利さ+継続意向+任意の自由記述の3問。
import { useEffect, useState } from "react";
import {
  RATE_LIMIT_TEXT,
  SURVEY_CONTINUATION_QUESTION,
  SURVEY_CONVENIENCE_QUESTION,
  SURVEY_DONE_TEXT,
  SURVEY_ERROR_TEXT,
  SURVEY_FREE_COMMENT_LABEL,
  SURVEY_FREE_COMMENT_PLACEHOLDER,
  SURVEY_NOTE_TEXT,
  SURVEY_ONBOARDING_TITLE,
  SURVEY_SCALE_OPTIONS,
  SURVEY_SUBMIT_LABEL,
  SURVEY_WEEK4_TITLE,
} from "./texts.js";

// 5段階1問(fieldset+legend・選択肢はpill)
const QuestionFieldset = ({ name, question, value, onChange }) => (
  <fieldset className="sv-q">
    <legend className="sv-legend">{question}</legend>
    <div className="sv-scale" role="radiogroup" aria-label={question}>
      {SURVEY_SCALE_OPTIONS.map(([v, label]) => (
        <label key={v} className="sv-opt" data-checked={value === v ? "true" : undefined}>
          <input
            type="radio"
            name={name}
            value={v}
            checked={value === v}
            onChange={() => onChange(v)}
            data-sv-option={v}
          />
          <span className="sv-opt-num" aria-hidden="true">{v}</span>
          <span className="sv-opt-label">{label}</span>
        </label>
      ))}
    </div>
  </fieldset>
);

export const SurveyCard = ({ client }) => {
  const [status, setStatus] = useState(null); // GET応答(null=未取得/失敗)
  const [convenience, setConvenience] = useState(null);
  const [continuation, setContinuation] = useState(null);
  const [comment, setComment] = useState("");
  const [done, setDone] = useState(false); // 送信済み(このマウント内)
  const [error, setError] = useState(null);

  useEffect(() => {
    let alive = true;
    client
      .call("GET", "/v1/users/me/survey")
      .then((data) => {
        if (alive) setStatus(data);
      })
      .catch(() => {}); // 取得失敗は表示なし(アンケートは体験を妨げない)
    return () => {
      alive = false;
    };
  }, [client]);

  if (status == null) return null;
  const round =
    status.onboarding?.status === "due"
      ? "onboarding"
      : status.week4?.status === "due"
        ? "week4"
        : null;
  if (round == null) return null; // not_due/answeredは描画しない

  const isWeek4 = round === "week4";
  const canSubmit = convenience != null && (!isWeek4 || continuation != null);

  const submit = async () => {
    const answers = { convenience: Number(convenience) };
    if (isWeek4) answers.continuation = Number(continuation);
    const body = { round, answers };
    if (isWeek4 && comment.trim()) body.free_comment = comment.trim();
    try {
      await client.call("POST", "/v1/users/me/survey", { body });
      setDone(true);
    } catch (err) {
      if (err?.code === "SURVEY_ALREADY_ANSWERED") {
        setDone(true); // 他タブ回答済みも回答済み扱い(単発)
        return;
      }
      setError(err?.code === "RATE_LIMITED" ? RATE_LIMIT_TEXT : SURVEY_ERROR_TEXT);
    }
  };

  return (
    <section className="sv-sec" data-role="survey" data-sv-round={round} aria-labelledby="h-survey">
      <div className="sv-card">
        <h2 id="h-survey" className="sv-title">
          {isWeek4 ? SURVEY_WEEK4_TITLE : SURVEY_ONBOARDING_TITLE}
        </h2>
        {done ? (
          <p className="sv-done" data-sv-done>{SURVEY_DONE_TEXT}</p>
        ) : (
          <>
            <QuestionFieldset
              name="sv-convenience"
              question={SURVEY_CONVENIENCE_QUESTION}
              value={convenience}
              onChange={(v) => {
                setConvenience(v);
                setError(null);
              }}
            />
            {isWeek4 ? (
              <>
                <QuestionFieldset
                  name="sv-continuation"
                  question={SURVEY_CONTINUATION_QUESTION}
                  value={continuation}
                  onChange={(v) => {
                    setContinuation(v);
                    setError(null);
                  }}
                />
                <label className="sv-comment">
                  <span className="sv-comment-label">{SURVEY_FREE_COMMENT_LABEL}</span>
                  <textarea
                    rows={3}
                    maxLength={2000}
                    placeholder={SURVEY_FREE_COMMENT_PLACEHOLDER}
                    value={comment}
                    onChange={(e) => setComment(e.target.value)}
                    data-sv-comment
                  />
                </label>
              </>
            ) : null}
            <div className="sv-foot">
              <button
                type="button"
                className="cta sv-submit"
                disabled={!canSubmit}
                data-sv-submit
                onClick={submit}
              >
                {SURVEY_SUBMIT_LABEL}
              </button>
              <p className="sv-privacy">{SURVEY_NOTE_TEXT}</p>
            </div>
            <p className="sv-error" hidden={!error} data-sv-error>{error}</p>
          </>
        )}
      </div>
    </section>
  );
};
