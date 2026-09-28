// parse連携の送信制御(design §2.4・告白4承認済み: debounce 1秒)。
// - テキスト変更後1秒のdebounceで自動送信(03 §3「記入↓構造化」)
// - 同一テキストの再送なし(直近成功テキストと比較・60req/分と07 §1のコスト配慮)
// - in-flight中の再入力は古いリクエストをabortして最新で再送(AbortController)
// - 空テキスト・変更なしは送信しない・自動再試行なし(07 §1)

export const createParseFlow = ({ debounceMs = 1000, send, onResult, onError }) => {
  let timer = null;
  let controller = null;
  let lastText = null;
  let pending = null;

  const flush = async () => {
    timer = null;
    const text = pending;
    pending = null;
    if (!text || !text.trim() || text === lastText) return;

    controller?.abort();
    const current = new AbortController();
    controller = current;
    try {
      const data = await send(text, { signal: current.signal });
      if (current !== controller || current.signal.aborted) return; // 古い応答は破棄
      lastText = text;
      onResult(data);
    } catch (err) {
      if (current !== controller || current.signal.aborted) return;
      if (err?.name === "AbortError") return;
      onError(err);
    }
  };

  return {
    input: (text) => {
      pending = text;
      if (timer) clearTimeout(timer);
      timer = setTimeout(flush, debounceMs);
    },
    retry: () => {
      // flush後はpendingが空のため、直近テキスト(lastText)を再送対象へ戻す
      pending = pending ?? lastText;
      lastText = null;
      if (timer) clearTimeout(timer);
      timer = setTimeout(flush, 0);
    },
    dispose: () => {
      if (timer) clearTimeout(timer);
      timer = null;
      controller?.abort();
      controller = null;
      pending = null;
    },
  };
};
