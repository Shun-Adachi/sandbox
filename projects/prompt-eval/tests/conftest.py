"""テスト用のヘルパ。ケースと記録(record)を最小の記述で組み立てる。"""

from __future__ import annotations

import pytest

from prompt_eval.dataset import Case

CORRECT_DATA = {
    "name": "田中",
    "company": "株式会社サンプル商事",
    "category": "不具合",
    "urgency": "高",
    "summary": "ログインできず業務が停止している",
}


def make_case(case_id: str = "c01", tags: list[str] | None = None, **expected_over) -> Case:
    expected = {
        "name": "田中",
        "company": "株式会社サンプル商事",
        "category": "不具合",
        "urgency": "高",
        "summary_keywords": ["ログイン"],
    } | expected_over
    return Case(
        id=case_id,
        text="株式会社サンプル商事の田中です。ログインできません。",
        expected=expected,
        tags=tags if tags is not None else ["基本"],
    )


def make_record(
    case_id: str = "c01",
    version: str = "v1",
    repeat: int = 0,
    ok: bool = True,
    data: dict | None = None,
    latency_ms: int = 1000,
    model: str = "gpt-4o-mini-2024-07-18",
) -> dict:
    if not ok:
        return {
            "case_id": case_id,
            "prompt_version": version,
            "repeat": repeat,
            "ok": False,
            "error": {"status_code": 429, "body": {"ok": False}},
        }
    return {
        "case_id": case_id,
        "prompt_version": version,
        "repeat": repeat,
        "ok": True,
        "response": {
            "ok": True,
            "data": data if data is not None else dict(CORRECT_DATA),
            "warnings": [],
            "prompt_version": version,
            "model": model,
            "usage": {"prompt_tokens": 600, "completion_tokens": 40, "total_tokens": 640},
            "latency_ms": latency_ms,
        },
    }


@pytest.fixture
def case():
    return make_case()
