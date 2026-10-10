// parse連携の送信制御(design §2.4・告白4承認済み: debounce 1秒)。
// - テキスト変更後1秒のdebounceで自動送信(03 §3「入力↓構造化」)
// - 同一テキストの再送なし(直近成功と比較・60req/分と07 §1のコスト配慮)
//   ※同一判定は「同じテキストかつ同じJST基準日」(2026-10-08移行計画§5.5:
//     入力欄を開いたままJST日を跨いだら同文でも再送する — 相対日時の
//     「明日」「今夜」を古い基準日の結果で使い回さない)
// - in-flight中の再入力は古いリクエストをabortして最新で再送(AbortController)
// - 空テキスト・変更なしは送信しない・自動再試行なし(07 §1)

export const jstDateString = (now = new Date()) => {
  // JST(UTC+9)の暦日付。実行環境TZに依存しない計算のみで導出。
  const shifted = new Date(now.getTime() + 9 * 60 * 60 * 1000);
  return shifted.toISOString().slice(0, 10);
};

export const createParseFlow = ({ debounceMs = 1000, send, onResult, onError }) => {
  let timer = null;
  let controller = null;
  let lastText = null;
  let lastBasisDate = null;
  let pending = null;

  const sameAsLastParse = (text) =>
    text === lastText && lastBasisDate === jstDateString();

  const flush = async () => {
    timer = null;
    const text = pending;
    pending = null;
    if (!text || !text.trim() || sameAsLastParse(text)) return;

    controller?.abort();
    const current = new AbortController();
    controller = current;
    try {
      const data = await send(text, { signal: current.signal });
      if (current !== controller || current.signal.aborted) return; // 古い応答は破棄
      lastText = text;
      lastBasisDate = jstDateString(); // 解析基準日を記録(§5.5)
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
    // debounceを打ち切って即時送信(01のCTA「読み解く」— 入力直後の遷移で
    // 古いparse結果を使わないために待つ)。同一テキストなら送信せず即完了
    commit: () => {
      if (timer) clearTimeout(timer);
      timer = null;
      return flush();
    },
    retry: () => {
      // flush後はpendingが空のため、直近テキスト(lastText)を再送対象へ戻す
      pending = pending ?? lastText;
      lastText = null;
      lastBasisDate = null;
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
