// 有効期限の選択肢構成(design §2.6)。
// 4選択肢の絶対時刻・既定選択・selectableはすべてGET /v1/intents/expiry-options
// (completion.pyの単一実装)の応答から組み立てる — フロントで計算しない(07 §2)。
export const fetchExpiryOptions = (client, timeStartIso) => {
  const query = timeStartIso
    ? `?time_start=${encodeURIComponent(timeStartIso)}`
    : "";
  return client.call("GET", `/v1/intents/expiry-options${query}`);
};

export const applyExpiryOptions = (selectEl, data) => {
  selectEl.replaceChildren();
  for (const option of data.options) {
    const el = document.createElement("option");
    el.textContent = option.label;
    el.value = option.expires_at; // クライアントは絶対時刻を送る(03 §3)
    el.disabled = !option.selectable; // 過ぎた選択肢(確定値6)
    selectEl.append(el);
  }
  const fallback = data.options.findIndex((o) => o.selectable);
  const idx = data.default_index ?? fallback;
  if (idx >= 0) selectEl.selectedIndex = idx;
  return idx;
};

export const selectedExpiry = (selectEl) => {
  const option = selectEl.selectedOptions[0];
  if (!option) return { label: null, expiresAt: null };
  return { label: option.textContent, expiresAt: option.value };
};
