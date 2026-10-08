"""契約ケース定義(T0)。

既存integration試験(test_*_api.py)が担保する挙動を、HTTPレベルの
データケースへ対応させたもの。期待値はすべて既存試験・routes・errors.py
から抽出している(source_test欄)。秘密情報・実利用者データは含まない。

ケースは参照実装(PostgreSQL)と移行実装(D1/Workers)の双方で実行する。
実装固有の事前状態は seed 名で参照し、各実装のharnessが用意する
(e.g. seed="pair_latch" → latches行1件)。

時刻は {now_iso}/{future_2h} 等のテンプレート変数で持つ(harnessがClock
由来で解決)。静的リテラルにすると長期運用でタイムボムになる
(M1 ws-2の教訓と同型)。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from contracts.contract import ContractCase, ContractRequest, Expect  # noqa: E402

# -- 共通body部品 --------------------------------------------------------------

INTENT_ACTIVE = {
    "raw_text": "今度の金曜の夜、天文館あたりで軽くごはん。",
    "status": "active",
    "structured_intent": {
        "category": {"primary": "meal", "secondary": None},
        "alcohol_involved": False,
        "time": {"start": "{future_2h}", "end": None},
        "location": {"name": "天文館"},
    },
}


def _intent_body(**overrides) -> dict:
    body = {
        "raw_text": INTENT_ACTIVE["raw_text"],
        "status": "active",
        "structured_intent": dict(INTENT_ACTIVE["structured_intent"]),
    }
    si_over = overrides.pop("structured_intent", None)
    si = body["structured_intent"]
    if si_over:
        for key, value in si_over.items():
            if isinstance(value, dict) and isinstance(si.get(key), dict):
                si[key] = {**si[key], **value}
            else:
                si[key] = value
    for key, value in overrides.items():
        body[key] = value
    return body


REGISTER_BODY = {
    "display_name": "契約たろう",
    "birth_date": "1995-06-15",
    "profile": {"bio": "よろしく"},
}

C = ContractCase
R = ContractRequest
E = Expect

# -- cases ----------------------------------------------------------------------

CASES: list[ContractCase] = [
    # ================= auth =================
    C(
        "auth_token_exchange_google",
        "auth",
        "google IdPトークン交換の応答形式",
        R(
            "POST",
            "/v1/auth/token",
            json={"provider": "google", "idp_token": "{idp_token:google}"},
        ),
        E(
            200,
            json_equals={"token_type": "Bearer", "expires_in": 3600},
            json_contains=("access_token", "refresh_token", "user"),
            capture={"refresh_token": "refresh_token"},
        ),
        effects="access JWT(HS256)発行・refresh family生成",
        source_test="test_auth_api.test_1_token_exchange_via_cli_for_both_providers",
    ),
    C(
        "auth_token_wrong_key",
        "auth",
        "未知の鍵で署名されたIdPトークンは401",
        R(
            "POST",
            "/v1/auth/token",
            json={"provider": "google", "idp_token": "{idp_token_bad}"},
        ),
        E(401, error_code="INVALID_IDP_TOKEN"),
        source_test="test_auth_api.test_3_wrong_key_idp_token_rejected",
    ),
    C(
        "auth_token_unknown_provider",
        "auth",
        "未対応providerは422(schemaのLiteral)",
        R("POST", "/v1/auth/token", json={"provider": "github", "idp_token": "x"}),
        E(422, error_code="VALIDATION_ERROR"),
        source_test="routes.pyのprovider Literal検証(実装挙動の固定)",
    ),
    C(
        "auth_refresh_unknown",
        "auth",
        "不明refreshトークンは401",
        R("POST", "/v1/auth/refresh", json={"refresh_token": "unknown-token-value"}),
        E(401, error_code="INVALID_REFRESH_TOKEN"),
        source_test="test_auth_api.test_6_unknown_refresh_token_rejected",
    ),
    C(
        "auth_refresh_rotation",
        "auth",
        "refresh回転は新refreshとaccessを返す",
        R("POST", "/v1/auth/refresh", json={"refresh_token": "{refresh_token}"}),
        E(
            200,
            json_equals={"token_type": "Bearer", "expires_in": 3600},
            json_contains=("access_token", "refresh_token"),
        ),
        setup=(
            (
                "exchange",
                R(
                    "POST",
                    "/v1/auth/token",
                    json={"provider": "google", "idp_token": "{idp_token:google}"},
                ),
                E(200, capture={"refresh_token": "refresh_token"}),
            ),
        ),
        effects="旧refresh失効・family維持",
        source_test="test_auth_api.test_4_refresh_rotation_and_reuse_revokes_family",
    ),
    C(
        "auth_refresh_reuse_revokes_family",
        "auth",
        "回転後の旧refresh再利用はfamily失効",
        R("POST", "/v1/auth/refresh", json={"refresh_token": "{old_refresh}"}),
        E(401, error_code="INVALID_REFRESH_TOKEN"),
        setup=(
            (
                "exchange",
                R(
                    "POST",
                    "/v1/auth/token",
                    json={"provider": "google", "idp_token": "{idp_token:google}"},
                ),
                E(200, capture={"old_refresh": "refresh_token"}),
            ),
            (
                "rotate",
                R("POST", "/v1/auth/refresh", json={"refresh_token": "{old_refresh}"}),
                E(200),
            ),
        ),
        effects="再利用検出→family全体失効(rotated新refreshも401)",
        source_test="test_auth_api.test_4_refresh_rotation_and_reuse_revokes_family",
    ),
    C(
        "auth_logout_no_auth",
        "auth",
        "logoutは認証必須",
        R("POST", "/v1/auth/logout", json={}),
        E(401, error_code="UNAUTHENTICATED"),
        source_test="test_auth_api.test_5(未認証経路)",
    ),
    C(
        "auth_logout_ok",
        "auth",
        "logoutは204・JWT失効",
        R("POST", "/v1/auth/logout", json={}, actor="u1"),
        E(204),
        effects="Redis auth:revoked:{jti}・family削除",
        source_test="test_auth_api.test_5_logout_revokes",
    ),
    C(
        "auth_logout_replay_401",
        "auth",
        "logout済みJWTの再利用は401",
        R("POST", "/v1/auth/logout", json={}, actor="u1_logged_out"),
        E(401),
        source_test="test_auth_api.test_5_logout_revokes",
    ),
    # ================= users =================
    C(
        "users_register_201",
        "users",
        "初回登録は201・user envelope",
        R("POST", "/v1/users", json=REGISTER_BODY, actor="fresh"),
        E(201, json_contains=("user",)),
        effects="users行作成",
        source_test="test_users_api.test_1_registration_full_flow",
    ),
    C(
        "users_me_200",
        "users",
        "GET /me は登録済みプロファイル",
        R("GET", "/v1/users/me", actor="u1"),
        E(200, json_contains=("id", "display_name", "profile_complete")),
        source_test="test_users_api.test_1",
    ),
    C(
        "users_under_age",
        "users",
        "18歳未満は422 UNDER_AGE",
        R(
            "POST",
            "/v1/users",
            json={**REGISTER_BODY, "birth_date": "{minor_birth_date}"},
            actor="fresh",
        ),
        E(422, error_code="UNDER_AGE"),
        effects="users行不作成",
        source_test="test_users_api.test_2_under_age_rejected_and_no_row",
    ),
    C(
        "users_missing_display_name",
        "users",
        "表示名欠落は422 VALIDATION_ERROR",
        R("POST", "/v1/users", json={"birth_date": "1995-06-15"}, actor="fresh"),
        E(422, error_code="VALIDATION_ERROR"),
        source_test="test_users_api.test_3",
    ),
    C(
        "users_bad_birth_date",
        "users",
        "birth_date形式不正は422",
        R(
            "POST",
            "/v1/users",
            json={**REGISTER_BODY, "birth_date": "1995/06/15"},
            actor="fresh",
        ),
        E(422, error_code="VALIDATION_ERROR"),
        source_test="test_users_api.test_3",
    ),
    C(
        "users_duplicate_409",
        "users",
        "同一subjectの再登録は409 USER_EXISTS",
        R("POST", "/v1/users", json=REGISTER_BODY, actor="u1"),
        E(409, error_code="USER_EXISTS"),
        source_test="test_users_api.test_4",
    ),
    C(
        "users_me_unregistered_404",
        "users",
        "未登録subjectの/meは404",
        R("GET", "/v1/users/me", actor="ghost"),
        E(404, error_code="NOT_FOUND"),
        source_test="test_users_api.test_5_me_without_user_row_404",
    ),
    C(
        "users_no_auth_401",
        "users",
        "認証なしは401",
        R("POST", "/v1/users", json=REGISTER_BODY),
        E(401, error_code="UNAUTHENTICATED"),
        source_test="test_users_api.test_6",
    ),
    C(
        "users_tampered_jwt_401",
        "users",
        "改変JWTは401",
        R("GET", "/v1/users/me", headers={"Authorization": "Bearer {tampered_token}"}),
        E(401, error_code="UNAUTHENTICATED"),
        source_test="test_users_api.test_6",
    ),
    # ================= consent =================
    C(
        "consent_get_initial_null",
        "consent",
        "未同意のGETはconsented_at=null",
        R("GET", "/v1/users/me/consent", actor="u1_noconsent"),
        E(200, json_equals={"consented_at": None}),
        source_test="test_consent_api.test_get_returns_null_then_post_records",
    ),
    C(
        "consent_post_records",
        "consent",
        "同意記録は200・consented_at設定",
        R("POST", "/v1/users/me/consent", json={}, actor="u1_noconsent"),
        E(200, json_contains=("consented_at",)),
        effects="users.consent_at更新(冪等・last-write-wins)",
        source_test="test_consent_api.test_get_returns_null_then_post_records",
    ),
    C(
        "consent_unregistered_404",
        "consent",
        "未登録は404",
        R("GET", "/v1/users/me/consent", actor="ghost"),
        E(404, error_code="NOT_FOUND"),
        source_test="test_consent_api.test_unregistered_consent_is_404",
    ),
    C(
        "consent_blocks_parse",
        "consent",
        "未同意のparseは422 CONSENT_REQUIRED",
        R(
            "POST",
            "/v1/intents/parse",
            json={"text": " tonight ramen"},
            actor="u1_noconsent",
        ),
        E(422, error_code="CONSENT_REQUIRED"),
        effects="LLM呼出しなし",
        source_test="test_consent_api.test_parse_blocked_without_consent_then_allowed",
    ),
    C(
        "consent_blocks_active_create",
        "consent",
        "未同意のactive保存は422",
        R("POST", "/v1/intents", json=_intent_body(), actor="u1_noconsent"),
        E(422, error_code="CONSENT_REQUIRED"),
        source_test="test_consent_api.test_active_create_blocked_without_consent_then_allowed",
    ),
    # ================= intents/parse =================
    C(
        "parse_happy",
        "intents.parse",
        "stub LLMの固定構造を返す",
        R(
            "POST",
            "/v1/intents/parse",
            json={"text": "今度の金曜の夜、天文館でごはん。"},
            actor="u1",
        ),
        E(
            200,
            json_equals={
                "structured_intent": {
                    "category": {"primary": "meal", "secondary": None},
                    "alcohol_involved": False,
                    "time": {
                        "start": "{stub_time_start}",
                        "end": None,
                        "flexibility_minutes": None,
                    },
                    "location": {
                        "name": "東京駅",
                        "radius_m": None,
                        "flexibility": None,
                    },
                    "budget": {"max": None, "currency": "JPY"},
                    "participants": {"min": None, "max": None},
                    "soft_constraints": [],
                    "negative_constraints": [],
                    "ng_unverifiable": [],
                },
                "warnings": [],
            },
            json_contains=("structured_intent",),
        ),
        effects="DB書込みゼロ(05 §5 SP-4)",
        source_test="test_intents_parse_api.test_1",
    ),
    C(
        "parse_300_ok",
        "intents.parse",
        "300字ちょうどは200",
        R("POST", "/v1/intents/parse", json={"text": "{text_300}"}, actor="u1"),
        E(200),
        source_test="test_intents_parse_api.test_2",
    ),
    C(
        "parse_301_over",
        "intents.parse",
        "301字は422(切詰めない)",
        R("POST", "/v1/intents/parse", json={"text": "{text_301}"}, actor="u1"),
        E(422, error_code="VALIDATION_ERROR"),
        source_test="test_intents_parse_api.test_2",
    ),
    C(
        "parse_missing_text",
        "intents.parse",
        "text欠落は422",
        R("POST", "/v1/intents/parse", json={}, actor="u1"),
        E(422, error_code="VALIDATION_ERROR"),
        source_test="test_intents_parse_api.test_3",
    ),
    C(
        "parse_malformed_json",
        "intents.parse",
        "JSON形式不正は400 MALFORMED_REQUEST",
        R("POST", "/v1/intents/parse", raw_body="{not valid json", actor="u1"),
        E(400, error_code="MALFORMED_REQUEST"),
        source_test="test_intents_parse_api.test_3",
    ),
    C(
        "parse_no_auth_401",
        "intents.parse",
        "認証なしは401",
        R("POST", "/v1/intents/parse", json={"text": "x"}),
        E(401, error_code="UNAUTHENTICATED"),
        source_test="test_intents_parse_api.test_4",
    ),
    C(
        "parse_unregistered_200",
        "intents.parse",
        "未登録subjectでもparseは200",
        R(
            "POST",
            "/v1/intents/parse",
            json={"text": "未登録でも解析できる"},
            actor="ghost",
        ),
        E(200, json_contains=("structured_intent",)),
        source_test="test_intents_parse_api.test_5_unregistered_subject_can_parse",
    ),
    # ================= intents CRUD =================
    C(
        "intent_create_active_201",
        "intents.crud",
        "active保存は201・version=1",
        R("POST", "/v1/intents", json=_intent_body(), actor="u1"),
        E(
            201,
            json_equals={"intent": {"status": "active", "version": 1}},
            json_contains=("intent", "intent.id", "intent.expires_at"),
            capture={"intent_id": "intent.id"},
        ),
        effects="geocoding・match_events(created/pending)発生",
        source_test="test_intents_crud_api.test_1_active_create_full_flow",
    ),
    C(
        "intent_get_200",
        "intents.crud",
        "取得は保存時の構造を返す",
        R("GET", "/v1/intents/{intent_id}", actor="u1"),
        E(
            200,
            json_equals={
                "intent": {"structured_intent": {"location": {"name": "天文館"}}}
            },
            json_contains=("intent.raw_text",),
        ),
        setup=(
            (
                "create",
                R("POST", "/v1/intents", json=_intent_body(), actor="u1"),
                E(201, capture={"intent_id": "intent.id"}),
            ),
        ),
        source_test="test_intents_crud_api.test_1",
    ),
    C(
        "intent_create_draft_201",
        "intents.crud",
        "draft保存は201・検証緩和",
        R("POST", "/v1/intents", json=_intent_body(status="draft"), actor="u1"),
        E(201, json_equals={"intent": {"status": "draft"}}),
        effects="geoなし・match_events 0件",
        source_test="test_intents_crud_api.test_2_draft_create",
    ),
    C(
        "intent_val_no_category",
        "intents.crud",
        "category欠落(active)は422",
        R(
            "POST",
            "/v1/intents",
            json=_intent_body(
                structured_intent={
                    "category": None,
                    "time": {"start": "{future_2h}"},
                    "location": {"name": "天文館"},
                }
            ),
            actor="u1",
        ),
        E(422, error_code="VALIDATION_ERROR"),
        source_test="test_intents_crud_api.test_4",
    ),
    C(
        "intent_val_past_start",
        "intents.crud",
        "過去time.start(active)は422",
        R(
            "POST",
            "/v1/intents",
            json=_intent_body(structured_intent={"time": {"start": "{past_1h}"}}),
            actor="u1",
        ),
        E(422, error_code="VALIDATION_ERROR"),
        source_test="test_intents_crud_api.test_4",
    ),
    C(
        "intent_val_start_gt_7d",
        "intents.crud",
        "+7日超過time.start(active)は422",
        R(
            "POST",
            "/v1/intents",
            json=_intent_body(structured_intent={"time": {"start": "{future_8d}"}}),
            actor="u1",
        ),
        E(422, error_code="VALIDATION_ERROR"),
        source_test="test_intents_crud_api.test_4",
    ),
    C(
        "intent_geocoding_failed",
        "intents.crud",
        "不明地名(active)は422 GEOCODING_FAILED",
        R(
            "POST",
            "/v1/intents",
            json=_intent_body(
                structured_intent={"location": {"name": "絶対に存在しない地名12345"}}
            ),
            actor="u1",
        ),
        E(422, error_code="GEOCODING_FAILED"),
        source_test="test_intents_crud_api.test_4",
    ),
    C(
        "intent_under_age_drinking",
        "intents.crud",
        "19歳の飲酒Intentは422 UNDER_AGE",
        R(
            "POST",
            "/v1/intents",
            json=_intent_body(structured_intent={"alcohol_involved": True}),
            actor="minor19",
        ),
        E(422, error_code="UNDER_AGE"),
        source_test="test_intents_crud_api.test_4b_under_age_drinking_rejected",
    ),
    C(
        "intent_patch_update_v2",
        "intents.crud",
        "active更新はversion=2",
        R(
            "PATCH",
            "/v1/intents/{intent_id}",
            json=_intent_body(
                structured_intent={
                    "location": {"name": "鹿児島市天文館一丁目", "radius_m": 1000}
                }
            ),
            actor="u1",
        ),
        E(200, json_equals={"intent": {"version": 2, "status": "active"}}),
        setup=(
            (
                "create",
                R("POST", "/v1/intents", json=_intent_body(), actor="u1"),
                E(201, capture={"intent_id": "intent.id"}),
            ),
        ),
        effects="再geocoding・match_events(updated)",
        source_test="test_intents_crud_api.test_5_patch_active_update",
    ),
    C(
        "intent_pause_200",
        "intents.crud",
        "pauseはpaused・version不変",
        R("POST", "/v1/intents/{intent_id}/pause", actor="u1"),
        E(200, json_equals={"intent": {"status": "paused"}}),
        setup=(
            (
                "create",
                R("POST", "/v1/intents", json=_intent_body(), actor="u1"),
                E(201, capture={"intent_id": "intent.id"}),
            ),
        ),
        effects="match_events追加なし",
        source_test="test_intents_crud_api.test_6_pause_resume",
    ),
    C(
        "intent_resume_200",
        "intents.crud",
        "resumeはactive・version=2",
        R("POST", "/v1/intents/{intent_id}/resume", actor="u1"),
        E(200, json_equals={"intent": {"status": "active", "version": 2}}),
        setup=(
            (
                "create",
                R("POST", "/v1/intents", json=_intent_body(), actor="u1"),
                E(201, capture={"intent_id": "intent.id"}),
            ),
            ("pause", R("POST", "/v1/intents/{intent_id}/pause", actor="u1"), E(200)),
        ),
        effects="match_events(updated)発生",
        source_test="test_intents_crud_api.test_6_pause_resume",
    ),
    C(
        "intent_delete_active_204",
        "intents.crud",
        "削除は204・cancelled",
        R("DELETE", "/v1/intents/{intent_id}", actor="u1"),
        E(204),
        setup=(
            (
                "create",
                R("POST", "/v1/intents", json=_intent_body(), actor="u1"),
                E(201, capture={"intent_id": "intent.id"}),
            ),
        ),
        effects="status=cancelled・deleted event",
        source_test="test_intents_crud_api.test_7_delete",
    ),
    C(
        "intent_delete_idempotent_204",
        "intents.crud",
        "2回目削除も204",
        R("DELETE", "/v1/intents/{intent_id}", actor="u1"),
        E(204),
        setup=(
            (
                "create",
                R("POST", "/v1/intents", json=_intent_body(), actor="u1"),
                E(201, capture={"intent_id": "intent.id"}),
            ),
            ("delete1", R("DELETE", "/v1/intents/{intent_id}", actor="u1"), E(204)),
        ),
        effects="deleted eventは1回のみ(ON CONFLICT)",
        source_test="test_deletion_api.test_4",
    ),
    C(
        "intent_other_user_403",
        "intents.crud",
        "他人のIntentは403",
        R("GET", "/v1/intents/{intent_id}", actor="u2"),
        E(403, error_code="FORBIDDEN"),
        setup=(
            (
                "create",
                R("POST", "/v1/intents", json=_intent_body(), actor="u1"),
                E(201, capture={"intent_id": "intent.id"}),
            ),
        ),
        source_test="test_intents_crud_api.test_8_authorization",
    ),
    C(
        "intent_missing_404",
        "intents.crud",
        "存在しないIDは404",
        R("GET", "/v1/intents/{uuid4}", actor="u1"),
        E(404, error_code="NOT_FOUND"),
        source_test="test_intents_crud_api.test_8",
    ),
    C(
        "intent_list_no_auth_401",
        "intents.crud",
        "一覧は認証必須",
        R("GET", "/v1/intents"),
        E(401, error_code="UNAUTHENTICATED"),
        source_test="test_intents_crud_api.test_8",
    ),
    C(
        "intent_list_pagination",
        "intents.crud",
        "limit/cursorページング",
        R("GET", "/v1/intents?limit=2", actor="u1"),
        E(200, json_contains=("items", "next_cursor")),
        setup=tuple(
            (
                f"create{i}",
                R(
                    "POST",
                    "/v1/intents",
                    json=_intent_body(raw_text=f"意図テキスト{i}", status="draft"),
                    actor="u1",
                ),
                E(201),
            )
            for i in range(3)
        ),
        source_test="test_intents_crud_api.test_9",
    ),
    C(
        "intent_list_status_filter",
        "intents.crud",
        "status絞り込み",
        R("GET", "/v1/intents?status=draft", actor="u1"),
        E(200, json_contains=("items",)),
        setup=(
            (
                "draft",
                R(
                    "POST",
                    "/v1/intents",
                    json=_intent_body(raw_text="下書きのみ", status="draft"),
                    actor="u1",
                ),
                E(201),
            ),
            (
                "active",
                R(
                    "POST",
                    "/v1/intents",
                    json=_intent_body(raw_text="actな意図"),
                    actor="u1",
                ),
                E(201),
            ),
        ),
        source_test="test_intents_crud_api.test_9",
    ),
    # ================= expiry-options =================
    C(
        "expiry_no_auth_401",
        "expiry",
        "認証なしは401",
        R("GET", "/v1/intents/expiry-options"),
        E(401, error_code="UNAUTHENTICATED"),
        source_test="test_expiry_options_api.test_1",
    ),
    C(
        "expiry_no_time_start",
        "expiry",
        "time_start無しはdefault_index=null",
        R("GET", "/v1/intents/expiry-options", actor="ghost"),
        E(200, json_equals={"default_index": None}, json_contains=("options",)),
        source_test="test_expiry_options_api.test_2",
    ),
    C(
        "expiry_with_time_start",
        "expiry",
        "time_start指定はdefault_index=整数",
        R("GET", "/v1/intents/expiry-options?time_start={future_2h}", actor="ghost"),
        E(200, json_contains=("options", "default_index")),
        source_test="test_expiry_options_api.test_3",
    ),
    # ================= latches =================
    C(
        "latch_yes_partial",
        "latches",
        "1人目YESはpartial_accept",
        R(
            "POST",
            "/v1/latches/{latch_id}/response",
            json={"response": "yes"},
            actor="u1",
        ),
        E(
            200,
            json_equals={
                "latch": {"status": "partial_accept", "remaining_responses": 1}
            },
        ),
        seed=("pair_latch", {}),
        effects="responses追記・latch_status_events(proposed→partial_accept)",
        source_test="test_latches_api.test_1_pair_double_yes",
    ),
    C(
        "latch_double_yes_matched",
        "latches",
        "2人目YESでmatched",
        R(
            "POST",
            "/v1/latches/{latch_id}/response",
            json={"response": "yes"},
            actor="u2",
        ),
        E(200, json_equals={"latch": {"status": "matched", "remaining_responses": 0}}),
        setup=(
            (
                "yes1",
                R(
                    "POST",
                    "/v1/latches/{latch_id}/response",
                    json={"response": "yes"},
                    actor="u1",
                ),
                E(200),
            ),
        ),
        seed=("pair_latch", {}),
        effects="双方のIntent→matched",
        source_test="test_latches_api.test_1",
    ),
    C(
        "latch_no_rejects",
        "latches",
        "NOはrejected",
        R(
            "POST",
            "/v1/latches/{latch_id}/response",
            json={"response": "no"},
            actor="u1",
        ),
        E(200, json_equals={"latch": {"status": "rejected"}}),
        seed=("pair_latch", {}),
        source_test="test_latches_api.test_2",
    ),
    C(
        "latch_defer_rejects",
        "latches",
        "DEFERもrejected(回答は保持)",
        R(
            "POST",
            "/v1/latches/{latch_id}/response",
            json={"response": "defer"},
            actor="u1",
        ),
        E(200, json_equals={"latch": {"status": "rejected"}}),
        seed=("pair_latch", {}),
        source_test="test_latches_api.test_2",
    ),
    C(
        "latch_double_answer_409",
        "latches",
        "二重回答は409 ALREADY_ANSWERED",
        R(
            "POST",
            "/v1/latches/{latch_id}/response",
            json={"response": "yes"},
            actor="u1",
        ),
        E(409, error_code="ALREADY_ANSWERED"),
        setup=(
            (
                "yes1",
                R(
                    "POST",
                    "/v1/latches/{latch_id}/response",
                    json={"response": "yes"},
                    actor="u1",
                ),
                E(200),
            ),
        ),
        seed=("pair_latch", {}),
        source_test="test_latches_api.test_3",
    ),
    C(
        "latch_expired_409",
        "latches",
        "期限切れ回答は409 LATCH_EXPIRED",
        R(
            "POST",
            "/v1/latches/{latch_id}/response",
            json={"response": "yes"},
            actor="u1",
        ),
        E(409, error_code="LATCH_EXPIRED"),
        seed=("pair_latch", {"deadline_hours": -1.0}),
        source_test="test_latches_api.test_4",
    ),
    C(
        "latch_cancelled_409",
        "latches",
        "cancelled状態の回答は409 LATCH_CLOSED",
        R(
            "POST",
            "/v1/latches/{latch_id}/response",
            json={"response": "yes"},
            actor="u1",
        ),
        E(409, error_code="LATCH_CLOSED"),
        seed=("pair_latch", {"status": "cancelled"}),
        source_test="test_latches_api.test_6",
    ),
    C(
        "latch_outsider_403",
        "latches",
        "非参加者の回答は403",
        R(
            "POST",
            "/v1/latches/{latch_id}/response",
            json={"response": "yes"},
            actor="outsider",
        ),
        E(403, error_code="FORBIDDEN"),
        seed=("pair_latch", {}),
        source_test="test_latches_api.test_7",
    ),
    C(
        "latch_missing_404",
        "latches",
        "存在しないlatchは404",
        R("POST", "/v1/latches/{uuid4}/response", json={"response": "yes"}, actor="u1"),
        E(404, error_code="NOT_FOUND"),
        source_test="test_latches_api.test_7",
    ),
    C(
        "latch_invalid_response_422",
        "latches",
        "response値域外は422",
        R(
            "POST",
            "/v1/latches/{latch_id}/response",
            json={"response": "maybe"},
            actor="u1",
        ),
        E(422, error_code="VALIDATION_ERROR"),
        seed=("pair_latch", {}),
        source_test="test_latches_api.test_8",
    ),
    C(
        "latch_group3_partial_then_matched",
        "latches",
        "3人組は全員YESでmatched",
        R(
            "POST",
            "/v1/latches/{latch_id}/response",
            json={"response": "yes"},
            actor="u3",
        ),
        E(200, json_equals={"latch": {"status": "matched", "remaining_responses": 0}}),
        setup=(
            (
                "yes1",
                R(
                    "POST",
                    "/v1/latches/{latch_id}/response",
                    json={"response": "yes"},
                    actor="u1",
                ),
                E(200),
            ),
            (
                "yes2",
                R(
                    "POST",
                    "/v1/latches/{latch_id}/response",
                    json={"response": "yes"},
                    actor="u2",
                ),
                E(
                    200,
                    json_equals={
                        "latch": {"status": "partial_accept", "remaining_responses": 1}
                    },
                ),
            ),
        ),
        seed=("group_latch", {"size": 3}),
        source_test="test_latches_api.test_9_group",
    ),
    C(
        "latch_group3_no_rejects",
        "latches",
        "3人組のNOはrejected(部分成立なし)",
        R(
            "POST",
            "/v1/latches/{latch_id}/response",
            json={"response": "no"},
            actor="u3",
        ),
        E(200, json_equals={"latch": {"status": "rejected"}}),
        setup=(
            (
                "yes1",
                R(
                    "POST",
                    "/v1/latches/{latch_id}/response",
                    json={"response": "yes"},
                    actor="u1",
                ),
                E(200),
            ),
            (
                "yes2",
                R(
                    "POST",
                    "/v1/latches/{latch_id}/response",
                    json={"response": "yes"},
                    actor="u2",
                ),
                E(200),
            ),
        ),
        seed=("group_latch", {"size": 3}),
        source_test="test_latches_api.test_9_group",
    ),
    C(
        "latch_list_shape",
        "latches",
        "一覧はitems+my_response(candidate除外)",
        R("GET", "/v1/latches?limit=1", actor="u1"),
        E(200, json_contains=("items", "next_cursor")),
        seed=("pair_latch", {}),
        effects="candidate状態は一覧に含まれない",
        source_test="test_latches_api.test_15",
    ),
    C(
        "latch_detail_pre_match_hidden",
        "latches",
        "成立前詳細は参加者非開示",
        R("GET", "/v1/latches/{latch_id}", actor="u1"),
        E(
            200,
            json_equals={
                "latch": {"participants": None, "time_summary": None, "area_name": None}
            },
        ),
        seed=("pair_latch", {}),
        source_test="test_latches_api.test_16",
    ),
    C(
        "latch_detail_matched_released",
        "latches",
        "成立後詳細は参加者開示",
        R("GET", "/v1/latches/{latch_id}", actor="u1"),
        E(
            200,
            json_contains=(
                "latch.participants",
                "latch.time_summary",
                "latch.area_name",
            ),
        ),
        seed=("pair_latch", {"status": "matched"}),
        source_test="test_latches_api.test_16",
    ),
    C(
        "latch_detail_outsider_403",
        "latches",
        "非参加者の詳細は403",
        R("GET", "/v1/latches/{latch_id}", actor="outsider"),
        E(403, error_code="FORBIDDEN"),
        seed=("pair_latch", {"status": "matched"}),
        source_test="test_latches_api.test_16",
    ),
    C(
        "latch_detail_missing_404",
        "latches",
        "存在しない詳細は404",
        R("GET", "/v1/latches/{uuid4}", actor="u1"),
        E(404, error_code="NOT_FOUND"),
        source_test="test_latches_api.test_16",
    ),
    # ================= chat / attendance =================
    C(
        "chat_send_matched_201",
        "chat",
        "成立後の送信は201",
        R(
            "POST",
            "/v1/latches/{latch_id}/messages",
            json={"body": "はじめまして"},
            actor="u1",
        ),
        E(201, json_contains=("message", "message.id", "message.body")),
        seed=("pair_latch", {"status": "matched"}),
        source_test="test_chat_attendance_api.test_1",
    ),
    C(
        "chat_list_asc",
        "chat",
        "一覧は昇順",
        R("GET", "/v1/latches/{latch_id}/messages", actor="u1"),
        E(200, json_contains=("items", "next_cursor")),
        setup=(
            (
                "send",
                R(
                    "POST",
                    "/v1/latches/{latch_id}/messages",
                    json={"body": "1通目"},
                    actor="u1",
                ),
                E(201),
            ),
        ),
        seed=("pair_latch", {"status": "matched"}),
        source_test="test_chat_attendance_api.test_1",
    ),
    C(
        "chat_before_match_409",
        "chat",
        "成立前の送信は409 CHAT_READONLY",
        R("POST", "/v1/latches/{latch_id}/messages", json={"body": "まだ"}, actor="u1"),
        E(409, error_code="CHAT_READONLY"),
        seed=("pair_latch", {}),
        source_test="test_chat_attendance_api.test_3",
    ),
    C(
        "chat_after_cancelled_409",
        "chat",
        "cancelled後の送信409・閲覧200",
        R(
            "POST",
            "/v1/latches/{latch_id}/messages",
            json={"body": "送れない"},
            actor="u1",
        ),
        E(409, error_code="CHAT_READONLY"),
        seed=("pair_latch", {"status": "cancelled"}),
        source_test="test_chat_attendance_api.test_5",
    ),
    C(
        "chat_outsider_403",
        "chat",
        "非参加者は403",
        R("GET", "/v1/latches/{latch_id}/messages", actor="outsider"),
        E(403, error_code="FORBIDDEN"),
        seed=("pair_latch", {"status": "matched"}),
        source_test="test_chat_attendance_api.test_7",
    ),
    C(
        "chat_missing_404",
        "chat",
        "存在しないlatchは404",
        R("GET", "/v1/latches/{uuid4}/messages", actor="u1"),
        E(404, error_code="NOT_FOUND"),
        source_test="test_chat_attendance_api.test_8",
    ),
    C(
        "chat_body_empty_422",
        "chat",
        "空本文は422",
        R("POST", "/v1/latches/{latch_id}/messages", json={"body": "  "}, actor="u1"),
        E(422, error_code="VALIDATION_ERROR"),
        seed=("pair_latch", {"status": "matched"}),
        source_test="test_chat_attendance_api.test_9",
    ),
    C(
        "chat_limit_over_422",
        "chat",
        "limit=101は422",
        R("GET", "/v1/latches/{latch_id}/messages?limit=101", actor="u1"),
        E(422, error_code="VALIDATION_ERROR"),
        seed=("pair_latch", {"status": "matched"}),
        source_test="test_chat_attendance_api.test_2",
    ),
    C(
        "attendance_true_200",
        "attendance",
        "実施申告trueは200",
        R(
            "POST",
            "/v1/latches/{latch_id}/attendance",
            json={"attended": True},
            actor="u1",
        ),
        E(200, json_equals={"latch_id": "{latch_id}", "actual_attended": True}),
        seed=("completed_latch", {"hours_ago": 1.0}),
        effects="calibration_records.actual_attended=true",
        source_test="test_chat_attendance_api.test_10",
    ),
    C(
        "attendance_second_user_409",
        "attendance",
        "2人目の申告は409",
        R(
            "POST",
            "/v1/latches/{latch_id}/attendance",
            json={"attended": True},
            actor="u2",
        ),
        E(409, error_code="ATTENDANCE_ALREADY_SUBMITTED"),
        setup=(
            (
                "first",
                R(
                    "POST",
                    "/v1/latches/{latch_id}/attendance",
                    json={"attended": True},
                    actor="u1",
                ),
                E(200),
            ),
        ),
        seed=("completed_latch", {"hours_ago": 1.0}),
        source_test="test_chat_attendance_api.test_12",
    ),
    C(
        "attendance_window_closed_409",
        "attendance",
        "72時間超過は409",
        R(
            "POST",
            "/v1/latches/{latch_id}/attendance",
            json={"attended": True},
            actor="u1",
        ),
        E(409, error_code="ATTENDANCE_WINDOW_CLOSED"),
        seed=("completed_latch", {"hours_ago": 73.0}),
        source_test="test_chat_attendance_api.test_13",
    ),
    C(
        "attendance_not_completed_409",
        "attendance",
        "matchedのままでは409 LATCH_CLOSED",
        R(
            "POST",
            "/v1/latches/{latch_id}/attendance",
            json={"attended": True},
            actor="u1",
        ),
        E(409, error_code="LATCH_CLOSED"),
        seed=("pair_latch", {"status": "matched"}),
        source_test="test_chat_attendance_api.test_14",
    ),
    C(
        "attendance_outsider_404",
        "attendance",
        "非参加者の申告は404(403でない)",
        R(
            "POST",
            "/v1/latches/{latch_id}/attendance",
            json={"attended": True},
            actor="outsider",
        ),
        E(404, error_code="NOT_FOUND"),
        seed=("completed_latch", {"hours_ago": 1.0}),
        source_test="test_chat_attendance_api.test_15",
    ),
    # ================= safety (blocks / reports) =================
    C(
        "block_register_201",
        "safety",
        "ブロック登録は201・冪等",
        R("POST", "/v1/users/{u2_id}/block", actor="u1"),
        E(201, json_equals={"blocked_id": "{u2_id}"}),
        effects="blocks行(重複なし)",
        source_test="test_safety_api.test_1",
    ),
    C(
        "block_register_idempotent_201",
        "safety",
        "再登録も201同body",
        R("POST", "/v1/users/{u2_id}/block", actor="u1"),
        E(201, json_equals={"blocked_id": "{u2_id}"}),
        setup=(("block1", R("POST", "/v1/users/{u2_id}/block", actor="u1"), E(201)),),
        source_test="test_safety_api.test_1",
    ),
    C(
        "block_list_shape",
        "safety",
        "一覧はitems降順",
        R("GET", "/v1/users/me/blocks", actor="u1"),
        E(200, json_contains=("items", "next_cursor")),
        setup=(("block", R("POST", "/v1/users/{u2_id}/block", actor="u1"), E(201)),),
        source_test="test_safety_api.test_1",
    ),
    C(
        "block_unblock_204",
        "safety",
        "解除は204",
        R("DELETE", "/v1/users/{u2_id}/block", actor="u1"),
        E(204),
        setup=(("block", R("POST", "/v1/users/{u2_id}/block", actor="u1"), E(201)),),
        source_test="test_safety_api.test_1",
    ),
    C(
        "block_unblock_again_404",
        "safety",
        "未ブロック解除は404",
        R("DELETE", "/v1/users/{u2_id}/block", actor="u1"),
        E(404, error_code="NOT_FOUND"),
        source_test="test_safety_api.test_1",
    ),
    C(
        "block_self_422",
        "safety",
        "自分へのブロックは422",
        R("POST", "/v1/users/{u1_id}/block", actor="u1"),
        E(422, error_code="VALIDATION_ERROR"),
        source_test="test_safety_api.test_2",
    ),
    C(
        "block_target_missing_404",
        "safety",
        "存在しない対象は404",
        R("POST", "/v1/users/{uuid4}/block", actor="u1"),
        E(404, error_code="NOT_FOUND"),
        source_test="test_safety_api.test_2",
    ),
    C(
        "block_bad_cursor_422",
        "safety",
        "不正cursorは422",
        R("GET", "/v1/users/me/blocks?cursor=%21%21%21", actor="u1"),
        E(422, error_code="VALIDATION_ERROR"),
        source_test="test_safety_api.test_3",
    ),
    C(
        "report_accepted_201",
        "safety",
        "通報は201(report_id)",
        R(
            "POST",
            "/v1/reports",
            json={"reportee_id": "{u2_id}", "reason": "inappropriate_content"},
            actor="u1",
        ),
        E(201, json_contains=("report_id",)),
        effects="reports行(status=pending)",
        source_test="test_safety_api.test_8",
    ),
    C(
        "report_self_422",
        "safety",
        "自分への通報は422",
        R(
            "POST",
            "/v1/reports",
            json={"reportee_id": "{u1_id}", "reason": "other"},
            actor="u1",
        ),
        E(422, error_code="VALIDATION_ERROR"),
        source_test="test_safety_api.test_8",
    ),
    C(
        "report_bad_reason_422",
        "safety",
        "規定外reasonは422",
        R(
            "POST",
            "/v1/reports",
            json={"reportee_id": "{u2_id}", "reason": "unknown"},
            actor="u1",
        ),
        E(422, error_code="VALIDATION_ERROR"),
        source_test="test_safety_api.test_8",
    ),
    C(
        "report_reportee_missing_404",
        "safety",
        "対象不在は404",
        R(
            "POST",
            "/v1/reports",
            json={"reportee_id": "{uuid4}", "reason": "other"},
            actor="u1",
        ),
        E(404, error_code="NOT_FOUND"),
        source_test="test_safety_api.test_8",
    ),
    C(
        "report_ghost_404",
        "safety",
        "未登録主体は404",
        R(
            "POST",
            "/v1/reports",
            json={"reportee_id": "{u2_id}", "reason": "other"},
            actor="ghost",
        ),
        E(404, error_code="NOT_FOUND"),
        source_test="test_safety_api.test_8",
    ),
    C(
        "report_both_participants_201",
        "safety",
        "latch参加者間の通報は201",
        R(
            "POST",
            "/v1/reports",
            json={
                "reportee_id": "{u2_id}",
                "latch_id": "{latch_id}",
                "reason": "suspected_impersonation",
            },
            actor="u1",
        ),
        E(201, json_contains=("report_id",)),
        seed=("pair_latch", {"status": "matched"}),
        source_test="test_safety_api.test_9",
    ),
    C(
        "report_reportee_not_in_latch_422",
        "safety",
        "latch非参加のreporteeは422",
        R(
            "POST",
            "/v1/reports",
            json={
                "reportee_id": "{u3_id}",
                "latch_id": "{latch_id}",
                "reason": "other",
            },
            actor="u1",
        ),
        E(422, error_code="VALIDATION_ERROR"),
        seed=("pair_latch", {"status": "matched"}),
        source_test="test_safety_api.test_9",
    ),
    C(
        "report_latch_missing_404",
        "safety",
        "latch不在は404",
        R(
            "POST",
            "/v1/reports",
            json={"reportee_id": "{u2_id}", "latch_id": "{uuid4}", "reason": "other"},
            actor="u1",
        ),
        E(404, error_code="NOT_FOUND"),
        source_test="test_safety_api.test_9",
    ),
    C(
        "report_reportee_omitted_pair_201",
        "safety",
        "1対1はreportee省略解決",
        R(
            "POST",
            "/v1/reports",
            json={"latch_id": "{latch_id}", "reason": "other"},
            actor="u1",
        ),
        E(201, json_contains=("report_id",)),
        seed=("pair_latch", {"status": "matched"}),
        source_test="test_safety_api.test_10",
    ),
    C(
        "report_both_omitted_422",
        "safety",
        "両方省略は422",
        R("POST", "/v1/reports", json={"reason": "other"}, actor="u1"),
        E(422, error_code="VALIDATION_ERROR"),
        source_test="test_safety_api.test_10",
    ),
    # ================= notifications =================
    C(
        "notif_empty_list",
        "notifications",
        "空一覧はitems+next_cursor",
        R("GET", "/v1/notifications", actor="u1"),
        E(200, json_contains=("items", "next_cursor")),
        source_test="test_notifications_api.test_1(空経路)",
    ),
    C(
        "notif_list_seeded",
        "notifications",
        "seeded通知が一覧に現れる",
        R("GET", "/v1/notifications", actor="u1"),
        E(200, json_contains=("items.0.type", "items.0.latch_id")),
        seed=("notification", {}),
        source_test="test_notifications_api.test_1",
    ),
    C(
        "notif_read_204",
        "notifications",
        "既読は204・冪等",
        R("POST", "/v1/notifications/{notification_id}/read", actor="u1"),
        E(204),
        seed=("notification", {}),
        source_test="test_notifications_api.test_5a",
    ),
    C(
        "notif_read_other_user_404",
        "notifications",
        "他人の通知は404",
        R("POST", "/v1/notifications/{notification_id}/read", actor="u2"),
        E(404, error_code="NOT_FOUND"),
        seed=("notification", {}),
        source_test="test_notifications_api.test_5b",
    ),
    C(
        "notif_read_missing_404",
        "notifications",
        "存在しない通知は404",
        R("POST", "/v1/notifications/{uuid4}/read", actor="u1"),
        E(404, error_code="NOT_FOUND"),
        source_test="test_notifications_api.test_5b",
    ),
    C(
        "notif_unauth_401",
        "notifications",
        "認証なしは401",
        R("GET", "/v1/notifications"),
        E(401, error_code="UNAUTHENTICATED"),
        source_test="test_notifications_api.test_8",
    ),
    # ================= account deletion =================
    C(
        "account_delete_204",
        "account",
        "退会は204",
        R("DELETE", "/v1/users/me", actor="u1_del"),
        E(204),
        effects="メッセージ/通知削除・blocks/reports保持・匿名化",
        source_test="test_account_api.test_1",
    ),
    C(
        "account_old_jwt_401",
        "account",
        "退会後の旧JWTは401",
        R("GET", "/v1/users/me", actor="u1_del"),
        E(401, error_code="UNAUTHENTICATED"),
        setup=(("del", R("DELETE", "/v1/users/me", actor="u1_del"), E(204)),),
        effects="session失効",
        source_test="test_account_api.test_4",
    ),
    C(
        "account_relogin_new_user",
        "account",
        "退会後の再ログインは新規扱い",
        R(
            "POST",
            "/v1/auth/token",
            json={"provider": "google", "idp_token": "{idp_token:u1_del}"},
        ),
        E(200, json_equals={"user": {"id": None, "profile_complete": False}}),
        setup=(("del", R("DELETE", "/v1/users/me", actor="u1_del"), E(204)),),
        source_test="test_account_api.test_3",
    ),
    C(
        "account_unregistered_404",
        "account",
        "未登録の退会は404",
        R("DELETE", "/v1/users/me", actor="ghost"),
        E(404, error_code="NOT_FOUND"),
        source_test="test_account_api.test_5",
    ),
    C(
        "account_no_auth_401",
        "account",
        "認証なし退会は401",
        R("DELETE", "/v1/users/me"),
        E(401, error_code="UNAUTHENTICATED"),
        source_test="test_account_api.test_6",
    ),
    # ================= rate limits =================
    C(
        "rl_create_daily_20",
        "ratelimit",
        "作成20件/日超過は429 RATE_LIMITED",
        R(
            "POST",
            "/v1/intents",
            json=_intent_body(raw_text="21件目", status="draft"),
            actor="u1",
        ),
        E(429, error_code="RATE_LIMITED"),
        setup=tuple(
            (
                f"create{i}",
                R(
                    "POST",
                    "/v1/intents",
                    json=_intent_body(raw_text=f"枠{i}", status="draft"),
                    actor="u1",
                ),
                E(201),
            )
            for i in range(20)
        ),
        source_test="test_ratelimit_api.test_1",
    ),
    C(
        "rl_update_hourly_6",
        "ratelimit",
        "更新6回/時超過は429",
        R("PATCH", "/v1/intents/{intent_id}", json=_intent_body(), actor="u1"),
        E(429, error_code="RATE_LIMITED"),
        setup=(
            (
                "create",
                R("POST", "/v1/intents", json=_intent_body(), actor="u1"),
                E(201, capture={"intent_id": "intent.id"}),
            ),
            *tuple(
                (
                    f"upd{i}",
                    R(
                        "PATCH",
                        "/v1/intents/{intent_id}",
                        json=_intent_body(raw_text=f"更新{i}"),
                        actor="u1",
                    ),
                    E(200),
                )
                for i in range(6)
            ),
        ),
        source_test="test_ratelimit_api.test_2",
    ),
    C(
        "rl_update_per_intent_unit",
        "ratelimit",
        "更新枠はIntent単位で隔離",
        R("PATCH", "/v1/intents/{intent_id2}", json=_intent_body(), actor="u1"),
        E(200),
        setup=(
            (
                "create1",
                R("POST", "/v1/intents", json=_intent_body(raw_text="1"), actor="u1"),
                E(201, capture={"intent_id": "intent.id"}),
            ),
            (
                "create2",
                R("POST", "/v1/intents", json=_intent_body(raw_text="2"), actor="u1"),
                E(201, capture={"intent_id2": "intent.id"}),
            ),
            *tuple(
                (
                    f"upd{i}",
                    R(
                        "PATCH",
                        "/v1/intents/{intent_id}",
                        json=_intent_body(raw_text=f"更新{i}"),
                        actor="u1",
                    ),
                    E(200),
                )
                for i in range(6)
            ),
        ),
        source_test="test_ratelimit_api.test_2",
    ),
    C(
        "rl_active_limit_422",
        "ratelimit",
        "active 5件超過は422 ACTIVE_INTENT_LIMIT",
        R("POST", "/v1/intents", json=_intent_body(raw_text="6本目"), actor="u1"),
        E(422, error_code="ACTIVE_INTENT_LIMIT"),
        setup=tuple(
            (
                f"create{i}",
                R(
                    "POST",
                    "/v1/intents",
                    json=_intent_body(raw_text=f"active{i}"),
                    actor="u1",
                ),
                E(201),
            )
            for i in range(5)
        ),
        source_test="test_ratelimit_api.test_4",
    ),
    C(
        "rl_401_priority",
        "ratelimit",
        "401はrate limit前に判定",
        R("GET", "/v1/intents", headers={"Authorization": "Bearer invalid.jwt.value"}),
        E(401, error_code="UNAUTHENTICATED"),
        source_test="test_ratelimit_api.test_3",
    ),
    C(
        "rl_health_exempt",
        "ratelimit",
        "/healthはrate limit対象外",
        R("GET", "/health"),
        E(200, json_equals={"status": "ok"}),
        source_test="test_ratelimit_api.test_9",
    ),
]

# -- 将来回帰ケース(T0: SQL fakeでは見逃す系統。バグを正解fixtureにしない) --------
#
# 参照実装では実行しない。該当タスクで実装・試験する。ここでは期待挙動を
# 固定しておく(計画§1 Review Focus・T0要件)。

PLANNED_REGRESSION_CASES: list[ContractCase] = [
    C(
        "regress_quarantine_arg_inversion",
        "regression",
        "隔離書込みの引数逆転: quarantined行のsource_intent_id/event_typeが逆転して"
        "保存されないこと(誤った引数順で正常系に見えないこと)",
        R("POST", "/internal/regression/quarantine"),
        E(501),
        regression="isolation_arg_inversion",
        planned_for="T5",
        effects="隔離診断は対象eventの特定に失敗しない",
    ),
    C(
        "regress_redelivery_idempotent",
        "regression",
        "同一仕事の再配送(at-least-once): 2回届いた同一event_id/command_idで"
        "二重書込みしない(候補・評価・通知の二重生成なし)",
        R("POST", "/internal/regression/redelivery"),
        E(501),
        regression="redelivery",
        planned_for="T5",
        effects="外部呼出しも2回目は行わない",
    ),
    C(
        "regress_fairness_due_recovery",
        "regression",
        "公平性: due仕事の回収が先頭N件の偏りで残件を starvation させない"
        "(cursor・上限付きbatchで全対象へ到達)",
        R("POST", "/internal/regression/fairness"),
        E(501),
        regression="fairness",
        planned_for="T8",
        effects="50件超のdueも世代欠落なく回収",
    ),
    C(
        "regress_redis_success_then_rollback",
        "regression",
        "Redis成功後rollback: 失効リスト書込み成功後にDB更新が失敗した際、"
        "失効だけが残りSession不整合とならない(原子command内へ)",
        R("POST", "/internal/regression/session-boundary"),
        E(501),
        regression="redis_success_then_rollback",
        planned_for="T3",
        effects="session状態はDB側のcommandと同じbatchで確定",
    ),
]


def all_cases() -> list[ContractCase]:
    return CASES + PLANNED_REGRESSION_CASES


CONTRACT_CASES: list[ContractCase] = CASES
