"""契約ケースの実行基盤(T0)。

参照実装 = compose常設api(127.0.0.1:8000・実DB)。移行実装 = Workers版
(cloudflare/api)。どちらも同じ Transport プロトコルで流す。

ケースは cases.CONTRACT_CASES から importする(データは cases.py に集約)。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from contracts.contract import ContractCase  # noqa: E402

pytestmark = pytest.mark.integration

_VAR_RE = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_:]*)\}")


def _substitute(value, captures: dict[str, object], *, url: bool = False):
    """{var} を変数で置換する(json内の文字列値も対象)。

    url=True はURL(path・query)へ埋めるためISO 8601の + 等を
    percent-encodeする(query paramが崩れないように)。
    """

    if isinstance(value, str):
        from urllib.parse import quote

        def repl(m: re.Match) -> str:
            key = m.group(1)
            if key not in captures:
                raise KeyError(f"capture変数 {key} が未定義")
            resolved = str(captures[key])
            if url:
                return quote(resolved, safe=":$-._~/")
            return resolved

        return _VAR_RE.sub(repl, value)
    if isinstance(value, dict):
        return {k: _substitute(v, captures, url=url) for k, v in value.items()}
    if isinstance(value, list):
        return [_substitute(v, captures, url=url) for v in value]
    return value


class ReferenceTransport:
    """compose常設apiへのHTTP転送(参照実装)。"""

    def __init__(self, client) -> None:
        self._client = client

    async def send(self, method: str, path: str, headers: dict[str, str], **kwargs):
        return await self._client.request(method, path, headers=headers, **kwargs)


def _subset_match(expected: object, actual: object, path: str) -> list[str]:
    """expected は actual の部分一致(dictは部分集合・リストは完全一致・他は等値)。"""

    if isinstance(expected, dict):
        if not isinstance(actual, dict):
            return [f"{path}: 応答がobjectでない"]
        problems: list[str] = []
        for key, sub_expected in expected.items():
            if key not in actual:
                problems.append(f"{path}.{key}: 応答に無い")
                continue
            problems.extend(_subset_match(sub_expected, actual[key], f"{path}.{key}"))
        return problems
    if isinstance(expected, list):
        if not isinstance(actual, list) or actual != expected:
            return [f"{path}: 期待 {expected!r} / 実際 {actual!r}"]
        return []
    if actual != expected:
        return [f"{path}: 期待 {expected!r} / 実際 {actual!r}"]
    return []


def _match_expect(
    case: ContractCase, resp, captures: dict[str, object], variables: dict | None = None
) -> list[str]:
    problems: list[str] = []
    expect = case.expect
    if resp.status_code != expect.status:
        snippet = resp.text[:300]
        problems.append(
            f"status: 期待 {expect.status} / 実際 {resp.status_code} body={snippet}"
        )
        return problems
    if expect.error_code is not None:
        try:
            body = resp.json()
        except Exception:  # noqa: BLE001
            problems.append(f"error envelopeがJSONでない: {resp.text[:200]}")
            return problems
        code = (body.get("error") or {}).get("code")
        if code != expect.error_code:
            problems.append(f"error.code: 期待 {expect.error_code} / 実際 {code}")
    if expect.json_equals:
        try:
            body = resp.json()
        except Exception:  # noqa: BLE001
            problems.append("応答がJSONでない")
            return problems
        merged = {**(variables or {}), **captures}
        for key, expected_raw in expect.json_equals.items():
            expected = _substitute(expected_raw, merged)
            parts = key.split(".")
            actual = body
            try:
                for part in parts:
                    actual = (
                        actual[int(part)] if isinstance(actual, list) else actual[part]
                    )
            except (KeyError, IndexError, TypeError, ValueError):
                problems.append(f"json_equals: {key} が応答に無い")
                continue
            problems.extend(_subset_match(expected, actual, key))
    for key in expect.json_contains:
        try:
            body = resp.json()
        except Exception:  # noqa: BLE001
            problems.append("応答がJSONでない")
            return problems
        parts = key.split(".")
        actual = body
        try:
            for part in parts:
                actual = actual[int(part)] if isinstance(actual, list) else actual[part]
        except (KeyError, IndexError, TypeError, ValueError):
            problems.append(f"json_contains: {key} が応答に無い")
    if expect.capture:
        try:
            body = resp.json()
        except Exception:  # noqa: BLE001
            problems.append("capture対象だが応答がJSONでない")
            return problems
        for var, path in expect.capture.items():
            parts = path.split(".") if path else []
            actual = body
            try:
                for part in parts:
                    actual = (
                        actual[int(part)] if isinstance(actual, list) else actual[part]
                    )
            except (KeyError, IndexError, TypeError, ValueError):
                problems.append(f"capture: {path} が応答に無い")
                continue
            captures[var] = actual
    return problems


def collect_var_names(case: ContractCase) -> set[str]:
    """case内のsetup・本体に現れるテンプレート変数名をすべて集める。"""

    names: set[str] = set()

    def walk(value):
        if isinstance(value, str):
            names.update(m.group(1) for m in _VAR_RE.finditer(value))
        elif isinstance(value, dict):
            for v in value.values():
                walk(v)
        elif isinstance(value, list):
            for v in value:
                walk(v)

    for _, setup_req, _ in case.setup:
        walk(setup_req.path)
        walk(setup_req.json)
        if setup_req.raw_body:
            walk(setup_req.raw_body)
        walk(setup_req.headers)
    walk(case.request.path)
    walk(case.request.json)
    if case.request.raw_body:
        walk(case.request.raw_body)
    walk(case.request.headers)
    return names


async def run_case(
    case: ContractCase,
    transport,
    resolve_token,  # async (actor: str) -> str
    seed_fn=None,  # async (name: str, params: dict) -> dict
    variables: dict[str, object] | None = None,
) -> list[str]:
    """1ケースを実行し、問題リスト(空=合格)を返す。

    variables は harness由来のテンプレート変数(clock_vars・seed結果・
    actorのuser_id等)。{var} はこれらで解決する。
    """

    variables = dict(variables or {})
    if case.seed is not None and seed_fn is not None:
        seed_name, seed_params = case.seed
        seeded = await seed_fn(seed_name, seed_params)
        variables.update(seeded)
    captures: dict[str, object] = {}
    all_vars: dict[str, object] = {}

    def resolve(value, url: bool = False):
        return _substitute(value, all_vars, url=url)

    def refresh_vars():
        all_vars.clear()
        all_vars.update(variables)
        all_vars.update(captures)

    async def send(req_like):
        headers: dict[str, str] = {}
        if req_like.actor is not None:
            headers["Authorization"] = f"Bearer {await resolve_token(req_like.actor)}"
        headers.update(req_like.headers)
        kwargs: dict = {}
        if req_like.raw_body is not None:
            kwargs["content"] = req_like.raw_body
            headers.setdefault("Content-Type", "application/json")
        elif req_like.json is not None:
            kwargs["json"] = resolve(req_like.json)
        return await transport.send(
            req_like.method, resolve(req_like.path, url=True), headers, **kwargs
        )

    refresh_vars()
    for label, setup_req, setup_expect in case.setup:
        resp = await send(setup_req)
        sub_case = ContractCase(
            case_id=f"{case.case_id}#setup:{label}",
            area=case.area,
            summary=f"setup {label}",
            request=setup_req,
            expect=setup_expect,
        )
        problems = _match_expect(sub_case, resp, captures, all_vars)
        if problems:
            return [f"setup[{label}] 失敗: {p}" for p in problems]
        refresh_vars()
    resp = await send(case.request)
    return _match_expect(case, resp, captures, all_vars)


# -- actor provisioning(参照実装) --------------------------------------------
