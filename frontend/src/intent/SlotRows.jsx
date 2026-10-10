// 02「読み取った条件」5行のReactコンポーネント(UI v2 Phase 6)。旧conditions.js
// のrenderRows/beginRowEdit/peopleFieldset/ngNoteBox(素のDOM組立)を置き換える。
// 値・文言・a11y属性(aria-label/aria-expanded/fieldset+legend・vh)は旧実装と
// 同じ。行編集はテキスト1行input+保存/キャンセルで、確定時にconditions.jsの
// editors.commitをコピーstateへ適用する。
import { useEffect, useRef, useState } from "react";
import { EXTRAS, NG_NOTE_TEXT, editors, rowModels } from "./conditions.js";
import { MISSING_TEXT, formatParticipants } from "./format.js";
import { Icon } from "../ui/Icon.jsx";

export const SlotRows = ({
  state,
  editing, // {key} | null
  onBeginEdit, // (row) => void
  onCommitEdit, // (key, text) => void
  onCancelEdit, // () => void
  onPickIncludesOwner, // ("in"|"out") => void
  errorTarget, // "time"|"location"|null(保存エラーの行強調)
}) => {
  const nowIso = new Date().toISOString();
  const rows = rowModels(state, nowIso);
  return (
    <>
      {rows.map((row) => (
        <SlotRow
          key={row.key}
          row={row}
          state={state}
          editing={editing}
          onBeginEdit={onBeginEdit}
          onCommitEdit={onCommitEdit}
          onCancelEdit={onCancelEdit}
          onPickIncludesOwner={onPickIncludesOwner}
          errorTarget={errorTarget}
        />
      ))}
      <NgNoteBox state={state} />
    </>
  );
};

const SlotRow = ({
  row,
  state,
  editing,
  onBeginEdit,
  onCommitEdit,
  onCancelEdit,
  onPickIncludesOwner,
  errorTarget,
}) => {
  const isEditing = editing === row.key;
  const className = `slot-row${errorTarget === row.key ? " slot-error" : ""}`;
  return (
    <div
      className={className}
      data-missing={row.missing ? "true" : undefined}
      data-key={row.key}
      data-label={row.label}
    >
      <span className="slot-ic">
        <Icon name={row.icon} size={19} />
      </span>
      <div className="slot-body">
        <span className="slot-line">
          <span className="slot-no">{row.no}</span>
          {row.label}
        </span>
        {isEditing ? (
          <SlotEditForm row={row} state={state} onCommit={onCommitEdit} onCancel={onCancelEdit} />
        ) : (
          <>
            <span className="slot-value">{row.value}</span>
            {row.sub ? <span className="slot-sub">{row.sub}</span> : null}
            {row.key === "participants" ? (
              <PeopleFieldset state={state} onPick={onPickIncludesOwner} />
            ) : null}
          </>
        )}
      </div>
      <button
        className={`edit-button${isEditing ? " editing" : ""}`}
        type="button"
        data-action="edit-row"
        aria-label={`${row.label}を編集`}
        aria-expanded={isEditing ? "true" : "false"}
        onClick={() => {
          if (!isEditing) onBeginEdit(row);
        }}
      >
        <Icon name="pencil" size={17} />
      </button>
    </div>
  );
};

// 行編集フォーム(テキスト1行input・プロトタイプ02)。シードはeditors.seed・
// 確定はonCommit(editors.commitをコピーstateへ適用するのは呼び出し側)。
// Escapeで取り消し(state不変)は旧実装どおり。
const SlotEditForm = ({ row, state, onCommit, onCancel }) => {
  const nowIso = new Date().toISOString();
  const [value, setValue] = useState(() => editors[row.key].seed(state, nowIso));
  const inputRef = useRef(null);

  useEffect(() => {
    inputRef.current?.focus();
    inputRef.current?.select();
  }, []);

  return (
    <form
      className="slot-edit"
      onSubmit={(event) => {
        event.preventDefault();
        onCommit(row.key, value);
      }}
      onKeyDown={(event) => {
        if (event.key === "Escape") onCancel(); // 取り消し(state不変)
      }}
    >
      <label className="vh" htmlFor={`edit-${row.key}`}>{row.label}を編集</label>
      <input
        ref={inputRef}
        className="field"
        type="text"
        id={`edit-${row.key}`}
        name="value"
        autoComplete="off"
        value={value}
        onChange={(event) => setValue(event.target.value)}
      />
      <span className="slot-edit-note">保存すると、この条件で照らし合わせ直します</span>
      <div className="slot-edit-actions">
        <button type="submit">保存</button>
        <button type="button" className="ghost" data-action="cancel-edit" onClick={onCancel}>
          キャンセル
        </button>
      </div>
    </form>
  );
};

// 人数確認seg(D-04・プロトタイプ02のfieldset)。未回答=要確認(acc)。
// ラジオのcheckedはstateから派生(旧実装と同じ排他表示)。
const PeopleFieldset = ({ state, onPick }) => {
  const inc = state.participantsIncludesOwner;
  const answered = inc != null;
  const base = formatParticipants(state.structured.participants);
  return (
    <fieldset className={answered ? "people-box ok" : "people-box need"}>
      <legend className="vh">あなたを人数に含めるか</legend>
      <p className="people-q">
        <span className="people-tag">{answered ? "確認済み" : "要確認"}</span>
        {` 「${base === MISSING_TEXT ? "人数" : base}」に、あなたは含まれますか？`}
      </p>
      <div className="seg">
        {[["in", "含む"], ["out", "含まない"]].map(([value, label]) => {
          const checked = (value === "in" && inc === true) || (value === "out" && inc === false);
          return (
            <label key={value} className={checked ? "on" : ""}>
              <input
                className="vh"
                type="radio"
                name="includes-owner"
                value={value}
                checked={checked}
                onChange={() => onPick(value)}
                data-action="pick-includes-owner"
              />
              {label}
            </label>
          );
        })}
      </div>
    </fieldset>
  );
};

// 参考条件(判定不能NG=02 D-04・parse由来soft=D-19補足)— 02の5行の下に
// stb情報箱で出す。いずれもsoft_constraints/ng_unverifiableとして送信される
// ため、表示から取り零さない(プロトタイプ02に相当行はない=本実装の補完)
const NgNoteBox = ({ state }) => {
  if (state.softRows.length === 0 && state.ngRows.length === 0) return null;
  return (
    <div className="ng-box">
      {state.softRows.map((row) => (
        <p key={row.id}>{`「${row.text}」— 参考条件として扱います`}</p>
      ))}
      {state.ngRows.map((row) => (
        <p key={row.id}>{`「${row.text}」— ${NG_NOTE_TEXT}`}</p>
      ))}
    </div>
  );
};

// 02「足せる条件」トグルchips(旧renderExtrasのReact版)
export const ExtraChips = ({ picked, onToggle }) => (
  <>
    {EXTRAS.map((extra) => {
      const on = picked.includes(extra.label);
      return (
        <button
          key={extra.id}
          type="button"
          className="extra-chip ghost"
          data-action="toggle-extra"
          data-label={extra.label}
          aria-pressed={String(on)}
          onClick={() => onToggle(extra.label)}
        >
          <Icon name={on ? "check" : "plus"} size={15} />
          {extra.label}
        </button>
      );
    })}
  </>
);
