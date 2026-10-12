import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, waitFor } from "@testing-library/react";
import { ApiError } from "../src/api/session.js";
import {
  SURVEY_CONVENIENCE_QUESTION,
  SURVEY_CONTINUATION_QUESTION,
  SURVEY_DONE_TEXT,
  SURVEY_ERROR_TEXT,
  SURVEY_FREE_COMMENT_LABEL,
  SURVEY_ONBOARDING_TITLE,
  SURVEY_WEEK4_TITLE,
} from "../src/latch/texts.js";
import { SurveyCard } from "../src/latch/SurveyCard.jsx";

// 体感アンケート配信カード(V1-P3・05 v0.7)のRTL試験。意図=due判定による
// 描画/非描画・onboarding 1問とweek4 3問の設問構成・送信ボディ・
// 回答済み表示・エラー文言・通知・モーダルを使わない受動カードであること。
const apiError = (status, code) => new ApiError(status, code, "msg");

const statusBody = (onboarding, week4) => ({
  onboarding: { status: onboarding, available_at: null },
  week4: { status: week4, available_at: null },
});

const mountCard = (callImpl) => {
  const client = { call: vi.fn(callImpl) };
  render(<SurveyCard client={client} />);
  return { client };
};

afterEach(() => {
  cleanup();
});

describe("survey配信カード(05 v0.7)", () => {
  it("onboarding dueは1問(便利さ)だけを描画する", async () => {
    mountCard(async (method, path) => {
      if (method === "GET") return statusBody("due", "not_due");
      return {};
    });
    await waitFor(() => {
      expect(document.querySelector('[data-role="survey"]')).not.toBeNull();
    });
    const card = document.querySelector('[data-role="survey"]');
    expect(card.dataset.svRound).toBe("onboarding");
    expect(card.textContent).toContain(SURVEY_ONBOARDING_TITLE);
    expect(card.textContent).toContain(SURVEY_CONVENIENCE_QUESTION);
    expect(card.textContent).not.toContain(SURVEY_CONTINUATION_QUESTION);
    expect(card.textContent).not.toContain(SURVEY_FREE_COMMENT_LABEL);
  });

  it("week4 dueは3問(便利さ+継続意向+任意自由記述)を描画する", async () => {
    mountCard(async (method) => {
      if (method === "GET") return statusBody("answered", "due");
      return {};
    });
    await waitFor(() => {
      expect(document.querySelector('[data-role="survey"]')).not.toBeNull();
    });
    const card = document.querySelector('[data-role="survey"]');
    expect(card.dataset.svRound).toBe("week4");
    expect(card.textContent).toContain(SURVEY_WEEK4_TITLE);
    expect(card.textContent).toContain(SURVEY_CONVENIENCE_QUESTION);
    expect(card.textContent).toContain(SURVEY_CONTINUATION_QUESTION);
    expect(card.querySelector("[data-sv-comment]")).not.toBeNull();
  });

  it("not_due/answeredの両ラウンドは何も描画しない(単発)", async () => {
    mountCard(async () => statusBody("answered", "not_due"));
    await waitFor(() => {
      expect(document.querySelector('[data-role="survey"]')).toBeNull();
    });
  });

  it("GET失敗は静かに何も描画しない(体験を妨げない)", async () => {
    const { client } = mountCard(async () => {
      throw apiError(503, "DEPENDENCY_UNAVAILABLE");
    });
    await waitFor(() => {
      expect(client.call).toHaveBeenCalled();
    });
    expect(document.querySelector('[data-role="survey"]')).toBeNull();
  });

  it("未選択では送信不可・選択後に{round,answers}を送り回答済み表示へ", async () => {
    const { client } = mountCard(async (method) => {
      if (method === "GET") return statusBody("due", "not_due");
      return { survey: { round: "onboarding", answered_at: "2026-10-12T09:00:00Z" } };
    });
    await waitFor(() => {
      expect(document.querySelector("[data-sv-submit]").disabled).toBe(true);
    });
    fireEvent.click(document.querySelector('[data-sv-option="4"]'));
    expect(document.querySelector("[data-sv-submit]").disabled).toBe(false);
    fireEvent.click(document.querySelector("[data-sv-submit]"));
    await waitFor(() => {
      expect(client.call).toHaveBeenCalledWith("POST", "/v1/users/me/survey", {
        body: { round: "onboarding", answers: { convenience: 4 } },
      });
    });
    await waitFor(() => {
      expect(document.querySelector('[data-role="survey"]').textContent).toContain(
        SURVEY_DONE_TEXT,
      );
    });
    expect(document.querySelector("[data-sv-submit]")).toBeNull();
  });

  it("week4は両方の選択が必須・自由記述(空白のみ)は送らない", async () => {
    const { client } = mountCard(async (method) => {
      if (method === "GET") return statusBody("answered", "due");
      return { survey: { round: "week4", answered_at: "2026-11-09T09:00:00Z" } };
    });
    await waitFor(() => {
      expect(document.querySelector("[data-sv-submit]").disabled).toBe(true);
    });
    fireEvent.click(document.querySelector('[name="sv-convenience"][value="4"]'));
    expect(document.querySelector("[data-sv-submit]").disabled).toBe(true); // continuation未選択
    fireEvent.click(document.querySelector('[name="sv-continuation"][value="5"]'));
    expect(document.querySelector("[data-sv-submit]").disabled).toBe(false);
    fireEvent.change(document.querySelector("[data-sv-comment]"), {
      target: { value: "   " },
    });
    fireEvent.click(document.querySelector("[data-sv-submit]"));
    await waitFor(() => {
      expect(client.call).toHaveBeenCalledWith("POST", "/v1/users/me/survey", {
        body: { round: "week4", answers: { convenience: 4, continuation: 5 } },
      });
    });
  });

  it("week4の自由記述(入力あり)はfree_commentとして送る", async () => {
    const { client } = mountCard(async (method) => {
      if (method === "GET") return statusBody("answered", "due");
      return { survey: { round: "week4", answered_at: "2026-11-09T09:00:00Z" } };
    });
    await waitFor(() => {
      expect(document.querySelector('[name="sv-convenience"]')).not.toBeNull();
    });
    fireEvent.click(document.querySelector('[name="sv-convenience"][value="5"]'));
    fireEvent.click(document.querySelector('[name="sv-continuation"][value="4"]'));
    fireEvent.change(document.querySelector("[data-sv-comment]"), {
      target: { value: "待っているだけで届くのが楽しい" },
    });
    fireEvent.click(document.querySelector("[data-sv-submit]"));
    await waitFor(() => {
      expect(client.call).toHaveBeenCalledWith("POST", "/v1/users/me/survey", {
        body: {
          round: "week4",
          answers: { convenience: 5, continuation: 4 },
          free_comment: "待っているだけで届くのが楽しい",
        },
      });
    });
  });

  it("送信失敗はエラー文言(フォーム維持)・409 ALREADYは回答済み表示へ", async () => {
    const calls = {
      GET: () => statusBody("due", "not_due"),
      POST: () => {
        throw apiError(503, "DEPENDENCY_UNAVAILABLE");
      },
    };
    const client = { call: vi.fn(async (m, p, o) => calls[m](p, o)) };
    render(<SurveyCard client={client} />);
    await waitFor(() => {
      expect(document.querySelector('[data-sv-option="3"]')).not.toBeNull();
    });
    fireEvent.click(document.querySelector('[data-sv-option="3"]'));
    fireEvent.click(document.querySelector("[data-sv-submit]"));
    await waitFor(() => {
      expect(document.querySelector('[data-sv-error]').textContent).toContain(SURVEY_ERROR_TEXT);
    });
    expect(document.querySelector("[data-sv-submit]")).not.toBeNull(); // 再送可能

    cleanup();
    const calls2 = {
      GET: () => statusBody("due", "not_due"),
      POST: () => {
        throw apiError(409, "SURVEY_ALREADY_ANSWERED");
      },
    };
    const client2 = { call: vi.fn(async (m, p, o) => calls2[m](p, o)) };
    render(<SurveyCard client={client2} />);
    await waitFor(() => {
      expect(document.querySelector('[data-sv-option="3"]')).not.toBeNull();
    });
    fireEvent.click(document.querySelector('[data-sv-option="3"]'));
    fireEvent.click(document.querySelector("[data-sv-submit]"));
    await waitFor(() => {
      expect(document.querySelector('[data-sv-done]').textContent).toContain(SURVEY_DONE_TEXT);
    });
  });
});
