"""API レベルのテスト。OpenAI は呼ばずにサービス層を差し替える。"""

import pytest
from fastapi.testclient import TestClient

from llm_api import qa as qa_service
from llm_api.config import settings
from llm_api.extract import SUMMARY_MAX_CHARS, _check_business_rules
from llm_api.main import app
from llm_api.prompts import PromptNotFoundError
from llm_api.schemas import Category, Inquiry, Urgency

client = TestClient(app, raise_server_exceptions=False)


def _inquiry(summary: str = "ログインできない") -> Inquiry:
    return Inquiry(
        name="田中",
        company="サンプル商事",
        category=Category.不具合,
        urgency=Urgency.高,
        summary=summary,
    )


def test_healthz_does_not_need_an_api_key():
    body = client.get("/healthz").json()
    assert body["status"] == "ok"
    assert "chat_model" in body


def test_prompts_endpoint_lists_versions():
    assert client.get("/v1/prompts").json()["extract"] == ["v1", "v2", "v3"]


def test_extract_rejects_empty_text():
    assert client.post("/v1/extract", json={"text": ""}).status_code == 422


def test_unknown_prompt_version_is_a_400_not_a_500():
    response = client.post("/v1/extract", json={"text": "本文", "prompt_version": "v99"})
    assert response.status_code == 400
    body = response.json()
    assert body["ok"] is False
    assert body["error"]["code"] == "prompt_not_found"
    # 4xx は原因が呼び出し側にあるので detail を返す
    assert "v99" in body["error"]["detail"]
    # ただしサーバーのディレクトリ構成は漏らさない
    assert str(settings.prompts_dir) not in body["error"]["detail"]
    assert ".yaml" not in body["error"]["detail"]
    assert "利用可能な版" in body["error"]["detail"]


def test_unexpected_errors_are_500_without_leaking_detail(monkeypatch):
    async def boom(*args, **kwargs):
        raise RuntimeError("接続文字列 postgres://user:pw@host が壊れています")

    monkeypatch.setattr("llm_api.main.extract_service", boom)
    response = client.post("/v1/extract", json={"text": "本文"})
    assert response.status_code == 500
    assert response.json()["error"]["detail"] is None


def test_prompt_not_found_maps_before_the_generic_handler(monkeypatch):
    async def missing(*args, **kwargs):
        raise PromptNotFoundError("そんな版はない")

    monkeypatch.setattr("llm_api.main.extract_service", missing)
    assert client.post("/v1/extract", json={"text": "本文"}).status_code == 400


# --- 業務ルール検証 ---


def test_short_summary_produces_no_warning():
    assert _check_business_rules(_inquiry()) == []


def test_long_summary_is_a_warning_not_an_error():
    warnings = _check_business_rules(_inquiry("あ" * (SUMMARY_MAX_CHARS + 1)))
    assert len(warnings) == 1
    assert "summary" in warnings[0]


def test_blank_summary_is_reported():
    assert any("空" in w for w in _check_business_rules(_inquiry("   ")))


# --- フォールバックとストリーミング(検索結果ゼロ件を強制する) ---


@pytest.fixture
def no_hits(monkeypatch):
    async def empty(question):
        return []

    monkeypatch.setattr("llm_api.qa.retrieve", empty)


def test_qa_falls_back_without_calling_the_llm(no_hits):
    body = client.post("/v1/qa", json={"question": "FAQ に無い質問"}).json()
    assert "FAQ に見つかりませんでした" in body["answer"]
    assert body["citations"] == []
    # LLM を呼んでいないのでトークン消費はゼロ
    assert body["usage"]["total_tokens"] == 0


def test_stream_emits_citations_then_deltas_then_done(no_hits):
    with client.stream("POST", "/v1/qa", json={"question": "q", "stream": True}) as response:
        assert response.headers["content-type"].startswith("text/event-stream")
        events = [
            line.removeprefix("event: ")
            for line in response.iter_lines()
            if line.startswith("event: ")
        ]
    assert events == ["citations", "delta", "done"]


def test_stream_reports_failures_as_an_error_event(monkeypatch):
    async def boom(question):
        raise RuntimeError("検索に失敗")

    monkeypatch.setattr("llm_api.qa.retrieve", boom)
    with client.stream("POST", "/v1/qa", json={"question": "q", "stream": True}) as response:
        # ストリーム開始後の失敗なのでステータスは 200 のまま
        assert response.status_code == 200
        text = "".join(response.iter_text())
    assert "event: error" in text
    assert "検索に失敗" not in text  # 内部メッセージは漏らさない


def test_sse_payload_is_not_ascii_escaped(no_hits):
    """SSE は JSON を手で組み立てているので、日本語がそのまま乗ることを固定する。"""
    with client.stream("POST", "/v1/qa", json={"question": "q", "stream": True}) as response:
        text = "".join(response.iter_text())
    assert "申し訳ありません" in text
    assert qa_service._sse("x", {"a": "あ"}) == 'event: x\ndata: {"a": "あ"}\n\n'
