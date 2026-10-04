"""負荷注入のHTTPクライアント(M4 ws-6)。

宛先はload環境のnginx入口(既定 http://127.0.0.1:28000)。IdPトークンは
latch.auth.tools.issue_idp_token(既存関数の直接呼出し・鍵=.staging/keys/
idp_test_private.pem・load composeが同一keysをroマウント=ws-1)で発行し・
POST /v1/auth/tokenでaccess_tokenへ回す(auth/routes.py TokenRequest契約・
stagingハーネスと同一経路)。クライアント側応答時間は参考値(測定の主系統
はサーバ側Prometheusヒストグラム・design §2.1案A)。
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import httpx

from latch.auth.tools import issue_idp_token
from latch.testassets.vocab import LOCATIONS

DEFAULT_BASE_URL = "http://127.0.0.1:28000"
DEFAULT_KEYS_DIR = ".staging/keys"
DEFAULT_DSN = "postgresql://latch:latch@127.0.0.1:25432/latch"
LOAD_USER_SUBJECT = "load-u{nnnn:04d}"

# perf・総当たりの注入ペイロードの決定的語彙(10 §2の分散に寄せる)
_CATEGORIES = ("drinking", "meal", "activity")


def perf_intent_payload(seq: int, *, started_at: datetime) -> dict:
    """perf区間・総当たりのPOST /v1/intentsペイロード(決定的)。

    time_start=実行時刻+3時間+seq分(05 §5の7日上限内・try_promoteの
    75分ルールを満たす)。地名・カテゴリはseqから決定的選択(7地名分散・
    stub遅延のハッシュ入力を多様化)。alcohol_involvedはdrinkingのみTrue
    (負荷ユーザーは全員20歳以上で作成可)。raw_textは300字上限内。
    """
    places = list(LOCATIONS)
    place = places[seq % len(places)]
    category = _CATEGORIES[seq % len(_CATEGORIES)]
    start = started_at + timedelta(hours=3, minutes=seq % 60)
    end = start + timedelta(minutes=120)
    return {
        "raw_text": f"負荷試験{seq:05d}: {place}あたりで{category}の集まりを始めます",
        "status": "active",
        "structured_intent": {
            "category": {"primary": category, "secondary": None},
            "alcohol_involved": category == "drinking",
            "time": {"start": start.isoformat(), "end": end.isoformat()},
            "location": {"name": place},
            "budget": {"max": 3000},
            "participants": {"min": 2, "max": 4},
            "soft_constraints": [],
            "visibility": "hidden_until_match",
            "notification_level": "proposals_only",
        },
    }


def parse_text(seq: int) -> str:
    """parse相の入力テキスト(決定的・300字上限内)。"""
    places = list(LOCATIONS)
    place = places[seq % len(places)]
    return f"{seq % 7 + 1}日の夜に{place}あたりで{seq % 3 + 2}人ほど話したい"


def user_subject(seq: int) -> str:
    """注入のラウンドロビン先ユーザー(perf側・Active余裕は§2.5)。

    総当たり側は段階ごとにCLI引数(--first-user)で範囲を変える(Task 8)。
    """
    return LOAD_USER_SUBJECT.format(nnnn=(seq % 1000) + 1)


class LoadClient:
    """注入用の非同期クライアント(subject→access_tokenをキャッシュ)。"""

    def __init__(
        self,
        *,
        base_url: str = DEFAULT_BASE_URL,
        keys_dir: str | Path = DEFAULT_KEYS_DIR,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._private_key = (Path(keys_dir) / "idp_test_private.pem").read_bytes()
        self._tokens: dict[str, str] = {}

    async def _ensure_token(self, http: httpx.AsyncClient, subject: str) -> None:
        if subject in self._tokens:
            return
        idp_token = issue_idp_token(
            provider="google", subject=subject, private_key_pem=self._private_key
        )
        r = await http.post(
            "/v1/auth/token",
            json={"provider": "google", "idp_token": idp_token},
        )
        r.raise_for_status()
        self._tokens[subject] = r.json()["access_token"]

    def _headers(self, subject: str) -> dict:
        return {"Authorization": f"Bearer {self._tokens[subject]}"}

    async def create_user(
        self,
        http: httpx.AsyncClient,
        subject: str,
        display_name: str,
        birth_date,
        bio: str,
    ) -> int:
        """POST /v1/users。戻り値201=作成・409=既存(再実行べき等)・他は例外。"""
        await self._ensure_token(http, subject)
        r = await http.post(
            "/v1/users",
            headers=self._headers(subject),
            json={
                "display_name": display_name,
                "birth_date": birth_date.isoformat(),
                "profile": {"bio": bio},
            },
        )
        if r.status_code not in (201, 409):
            raise RuntimeError(f"users {subject}: {r.status_code} {r.text[:200]}")
        return r.status_code

    async def consent(self, http: httpx.AsyncClient, subject: str) -> None:
        await self._ensure_token(http, subject)
        r = await http.post("/v1/users/me/consent", headers=self._headers(subject))
        if r.status_code != 200:
            raise RuntimeError(f"consent {subject}: {r.status_code} {r.text[:200]}")

    async def create_intent(
        self, http: httpx.AsyncClient, subject: str, payload: dict
    ) -> tuple[int, str]:
        """POST /v1/intents。201時は(201, intent_id)を返す。"""
        await self._ensure_token(http, subject)
        r = await http.post(
            "/v1/intents", headers=self._headers(subject), json=payload
        )
        if r.status_code != 201:
            raise RuntimeError(
                f"intent create {subject}: {r.status_code} {r.text[:200]}"
            )
        return 201, str(r.json()["intent"]["id"])

    async def draft_activate(
        self, http: httpx.AsyncClient, subject: str, payload: dict
    ) -> int:
        """draft作成→同内容でPATCH active化(design §2.5のactivate経路)。

        E2Eの起点はactive化コミット時刻(match_events.created_at・route=
        activate)。draft作成のevent発行はないため計画数の1計画で2リクエスト。
        """
        await self._ensure_token(http, subject)
        draft_payload = dict(payload)
        draft_payload["status"] = "draft"
        r = await http.post(
            "/v1/intents", headers=self._headers(subject), json=draft_payload
        )
        if r.status_code != 201:
            raise RuntimeError(f"draft create {subject}: {r.status_code} {r.text}")
        intent_id = str(r.json()["intent"]["id"])
        # PATCH契約(IntentPatchRequest・intent_input.py): raw_text必須・
        # status active・structured_intentで全置換
        r2 = await http.patch(
            f"/v1/intents/{intent_id}",
            headers=self._headers(subject),
            json={
                "raw_text": payload["raw_text"],
                "status": "active",
                "structured_intent": payload["structured_intent"],
            },
        )
        if r2.status_code != 200:
            raise RuntimeError(
                f"draft activate {subject}: {r2.status_code} {r2.text[:200]}"
            )
        return 200

    async def parse_intent(
        self, http: httpx.AsyncClient, subject: str, text: str
    ) -> int:
        """POST /v1/intents/parse(目標②の測定対象・DB書込みなし)。"""
        await self._ensure_token(http, subject)
        r = await http.post(
            "/v1/intents/parse",
            headers=self._headers(subject),
            json={"text": text},
        )
        if r.status_code != 200:
            raise RuntimeError(f"parse {subject}: {r.status_code} {r.text[:200]}")
        return 200
