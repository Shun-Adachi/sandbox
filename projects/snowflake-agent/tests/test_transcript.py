from datetime import datetime

from langchain_core.messages import AIMessage, ToolMessage

from snowflake_agent.transcript import render_transcript


def test_render_transcript_contains_full_exchange():
    messages = [
        AIMessage(
            content=[{"type": "text", "text": "テーブルを調べます"}],
            tool_calls=[
                {"id": "t1", "name": "run_query", "args": {"sql": "SELECT 1 AS x"}}
            ],
            usage_metadata={"input_tokens": 100, "output_tokens": 20, "total_tokens": 120},
        ),
        ToolMessage(content="X\n-\n1", name="run_query", tool_call_id="t1"),
        AIMessage(
            content="結果は 1 です",
            usage_metadata={"input_tokens": 150, "output_tokens": 30, "total_tokens": 180},
        ),
    ]
    text = render_transcript(
        question="1 を返して",
        system_prompt="あなたはアナリストです",
        messages=messages,
        model_id="claude-opus-5",
        started_at=datetime(2026, 8, 18, 12, 0, 0),
    )
    # システムプロンプト・質問・SQL 全文・ツール結果・最終回答・トークン合計が全部入ること
    for expected in (
        "あなたはアナリストです",
        "1 を返して",
        "SELECT 1 AS x",
        "ツール結果 (run_query)",
        "結果は 1 です",
        "input=250 / output=50(2 ターン)",
    ):
        assert expected in text
