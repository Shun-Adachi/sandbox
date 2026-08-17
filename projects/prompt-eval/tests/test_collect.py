"""収集段の検査。httpx の MockTransport で API を差し替え、ネットワークを使わない。"""

import asyncio
import json

import httpx
from conftest import CORRECT_DATA, make_case

from prompt_eval.collect import collect


def make_mock_client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url="http://testserver", transport=httpx.MockTransport(handler)
    )


def ok_response(request: httpx.Request) -> httpx.Response:
    body = json.loads(request.content)
    return httpx.Response(
        200,
        json={
            "ok": True,
            "data": dict(CORRECT_DATA),
            "warnings": [],
            "prompt_version": body["prompt_version"],
            "model": "gpt-4o-mini-2024-07-18",
            "usage": {"prompt_tokens": 600, "completion_tokens": 40, "total_tokens": 640},
            "latency_ms": 1200,
        },
    )


def test_ケースと版の全組み合わせを呼ぶ():
    cases = [make_case("c01"), make_case("c02")]

    async def main():
        async with make_mock_client(ok_response) as client:
            return await collect(cases, ["v1", "v2"], client=client, repeat=2)

    records = asyncio.run(main())
    assert len(records) == 2 * 2 * 2
    keys = {(r["case_id"], r["prompt_version"], r["repeat"]) for r in records}
    assert len(keys) == 8  # 重複なく全組み合わせ
    assert all(r["ok"] for r in records)
    # 応答にはどの版へのリクエストだったかがそのまま残る
    assert records[0]["response"]["prompt_version"] in ("v1", "v2")


def test_一部が失敗しても残りは収集される():
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if body["prompt_version"] == "v2":
            return httpx.Response(
                429, json={"ok": False, "error": {"code": "upstream_rate_limited"}}
            )
        return ok_response(request)

    async def main():
        async with make_mock_client(handler) as client:
            return await collect([make_case("c01")], ["v1", "v2"], client=client)

    records = asyncio.run(main())
    by_version = {r["prompt_version"]: r for r in records}
    assert by_version["v1"]["ok"]
    assert not by_version["v2"]["ok"]
    assert by_version["v2"]["error"]["status_code"] == 429


def test_接続エラーも記録として残る():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    async def main():
        async with make_mock_client(handler) as client:
            return await collect([make_case("c01")], ["v1"], client=client)

    records = asyncio.run(main())
    assert records[0]["ok"] is False
    assert "ConnectError" in records[0]["error"]["body"]
