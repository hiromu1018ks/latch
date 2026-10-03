"""専用シナリオ作成(M4 ws-3・設計§2.2対抗策(b)/§2.3隔離方式)。

シード母集合の提案群はD-08保留で揺れるため、提案の成否・状態遷移・通知内容
に依存する検査はHS-xxx専用ユーザー+早朝時刻(D+1〜D+7の06:30)のIntentで
行う(§2.3一覧どおり)。2回目以降の呼び出しはキャッシュで即返す(部分実行
はstaging-resetからのやり直しを前提とする・冪等な再入は行わない)。
"""

from __future__ import annotations

from conftest import auth_header

_SCENARIOS: dict[str, dict] = {}


async def _register(api, subject: str, birth_date: str) -> str:  # noqa: ANN001
    r = await api.post(
        "/v1/users",
        headers=auth_header(subject),
        json={"display_name": f"ハーネス{subject}", "birth_date": birth_date},
    )
    assert r.status_code == 201, (
        f"scenario users失敗 {subject}: {r.status_code} {r.text}"
    )
    user_id = r.json()["user"]["id"]
    rc = await api.post("/v1/users/me/consent", headers=auth_header(subject))
    assert rc.status_code == 200, f"scenario consent失敗 {subject}: {rc.status_code}"
    return user_id


async def _create_intent(  # noqa: ANN001
    api,
    subject: str,
    *,
    when: str,
    category: str = "activity",
    secondary: str = "散策",
    visibility: str = "summary_only",
    notification_level: str = "proposals_only",
    participants: tuple[int, int] = (2, 2),
    location: str = "天文館",
    alcohol_involved: bool = False,
    budget_max: int | None = 3000,
    raw_text: str | None = None,
    expires_at: str | None = None,
) -> str:
    """シナリオIntent 1件を作る。

    expires_at: 明示期限(API契約のstructured_intent.expires_at・03 §3
    FR-13)。既定のnearest_expires_atは作成時の仮想時刻から72時間以内の
    候補しか選ばないため、Clock前進後まで存活が必要なIntent
    (#17用ペア・#20用ペア)は呼び出し側で明示する。
    """
    structured: dict = {
        "category": {"primary": category, "secondary": secondary},
        "alcohol_involved": alcohol_involved,
        "time": {"start": when},
        "location": {"name": location, "radius_m": 1000},
        "budget": {"max": budget_max} if budget_max is not None else None,
        "participants": {"min": participants[0], "max": participants[1]},
        "soft_constraints": [],
        "visibility": visibility,
        "notification_level": notification_level,
    }
    if expires_at is not None:
        structured["expires_at"] = expires_at
    body = {
        "raw_text": raw_text
        or f"{when}に{location}で{secondary}。よろしくお願いします。",
        "status": "active",
        "structured_intent": structured,
    }
    r = await api.post("/v1/intents", headers=auth_header(subject), json=body)
    assert r.status_code == 201, (
        f"scenario intents失敗 {subject}: {r.status_code} {r.text}"
    )
    return r.json()["intent"]["id"]


async def make_scenario_pair(
    api,
    db,
    *,
    key: str,
    when: str,
    name: str,
    **kwargs,  # noqa: ANN003
) -> dict:
    """2人のユーザーを登録し各1件のIntentを作る(キャッシュつき)。"""
    global _SCENARIOS
    if key in _SCENARIOS:
        return _SCENARIOS[key]
    birth = kwargs.pop("birth_date", "1995-04-01")
    s1 = f"HS-{name}-1"
    s2 = f"HS-{name}-2"
    u1 = await _register(api, s1, birth)
    u2 = await _register(api, s2, birth)
    i1 = await _create_intent(api, s1, when=when, **kwargs)
    i2 = await _create_intent(api, s2, when=when, **kwargs)
    _SCENARIOS[key] = {
        "key": key,
        "subjects": [s1, s2],
        "user_ids": [u1, u2],
        "intent_ids": [i1, i2],
    }
    return _SCENARIOS[key]


async def make_scenario_group(
    api,
    db,
    *,
    key: str,
    when: str,
    name: str,
    members: int = 4,
    min_participants: int = 3,
    **kwargs,  # noqa: ANN003
) -> dict:
    """members人のユーザー×1件ずつ(集合クラスタ・min_participants≥3)。"""
    global _SCENARIOS
    if key in _SCENARIOS:
        return _SCENARIOS[key]
    birth = kwargs.pop("birth_date", "1995-04-01")
    subjects = [f"HS-{name}-{n}" for n in range(1, members + 1)]
    user_ids = []
    for s in subjects:
        user_ids.append(await _register(api, s, birth))
    intent_ids = []
    for s in subjects:
        intent_ids.append(
            await _create_intent(
                api, s, when=when, participants=(min_participants, members), **kwargs
            )
        )
    _SCENARIOS[key] = {
        "key": key,
        "subjects": subjects,
        "user_ids": user_ids,
        "intent_ids": intent_ids,
    }
    return _SCENARIOS[key]


async def my_latch(api, subject: str, *, status: str = "proposed") -> dict:  # noqa: ANN001
    """自分(status指定)のlatchを1件引き出す(ハーネス観測用)。"""
    r = await api.get("/v1/latches", headers=auth_header(subject))
    assert r.status_code == 200, f"latches一覧失敗 {subject}: {r.status_code}"
    hits = [i for i in r.json()["items"] if i["status"] == status]
    assert hits, f"{subject}にstatus={status}のlatchがない"
    return hits[0]


async def wait_for_latch(
    api, subject: str, *, status: str = "proposed", timeout_s: float = 180.0
) -> dict:
    """statusのlatchが現れるまで待つ(シナリオIntent作成直後の昇格待ち)。

    シナリオIntentは検査内で作成するため、提案昇格(debounce10秒+tick≤60秒)
    を待ってから観察する(計画コードからの適応・報告書に記録)。
    """
    from helpers_wait import wait_until

    found: dict = {}

    async def appeared() -> bool:
        try:
            found.clear()
            found.update(await my_latch(api, subject, status=status))
        except AssertionError:
            return False
        return True

    await wait_until(
        appeared,
        timeout_s=timeout_s,
        interval_s=10.0,
        what=f"{subject}のlatch status={status}の出現(昇格待ち・tickの2〜3倍)",
    )
    return dict(found)


async def wait_embeddings(
    db, intent_ids: list[str], *, timeout_s: float = 120.0
) -> None:
    """全Intentのembedding完備を待つ(グループPool検索の前提)。

    Pool検索はembedding NULL行を拾わないため、作成直後の同時処理では
    他メンバーが未embeddingでPool空になる競合がある(実測・報告書に記録)。
    """
    from helpers_wait import wait_until

    async def all_embedded() -> bool:
        n = await db.fetchval(
            "SELECT count(*) FROM intents"
            " WHERE id = ANY($1::uuid[]) AND embedding IS NOT NULL",
            intent_ids,
        )
        return n == len(intent_ids)

    await wait_until(
        all_embedded,
        timeout_s=timeout_s,
        interval_s=10,
        what=f"embedding完備({len(intent_ids)}件)",
    )
