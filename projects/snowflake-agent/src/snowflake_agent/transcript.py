"""実行トランスクリプトの保存。

AI とのやり取り全文(システムプロンプト、ツール呼び出しと引数、ツール結果、
各ターンのトークン使用量)を runs/(git 管理外)に Markdown で書き出す。
デモの証跡・デバッグ用。代表的なものは docs/ に転記して公開する。
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from langchain_core.messages import AIMessage, BaseMessage, ToolMessage


def _render_ai_message(message: AIMessage, turn: int) -> list[str]:
    lines = [f"## ターン {turn}: assistant"]
    content = message.content
    blocks = content if isinstance(content, list) else [{"type": "text", "text": content}]
    for block in blocks:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "text" and block.get("text"):
            lines += ["", block["text"]]
    for call in message.tool_calls or []:
        lines += ["", f"### ツール呼び出し: `{call['name']}`", ""]
        for key, value in call["args"].items():
            lines += [f"**{key}:**", "", "```sql" if key == "sql" else "```", str(value), "```"]
    usage = message.usage_metadata
    if usage:
        lines += [
            "",
            f"*tokens: in={usage.get('input_tokens', '?')} out={usage.get('output_tokens', '?')}*",
        ]
    return lines


def render_transcript(
    *,
    question: str,
    system_prompt: str,
    messages: list[BaseMessage],
    model_id: str,
    started_at: datetime,
) -> str:
    lines = [
        "# 実行トランスクリプト",
        "",
        f"- 日時: {started_at:%Y-%m-%d %H:%M:%S}",
        f"- モデル: {model_id}",
        "",
        "## システムプロンプト",
        "",
        "```",
        system_prompt.rstrip(),
        "```",
        "",
        "## 質問",
        "",
        question,
    ]
    turn = 0
    total_in = total_out = 0
    for message in messages:
        if isinstance(message, AIMessage):
            turn += 1
            lines += [""] + _render_ai_message(message, turn)
            usage = message.usage_metadata or {}
            total_in += usage.get("input_tokens", 0)
            total_out += usage.get("output_tokens", 0)
        elif isinstance(message, ToolMessage):
            lines += [
                "",
                f"### ツール結果 ({message.name})",
                "",
                "```",
                str(message.content).rstrip(),
                "```",
            ]
    lines += [
        "",
        "---",
        "",
        f"合計トークン: input={total_in} / output={total_out}({turn} ターン)",
        "",
    ]
    return "\n".join(lines)


def save_transcript(text: str, started_at: datetime) -> Path:
    runs_dir = Path(__file__).resolve().parents[2] / "runs"
    runs_dir.mkdir(exist_ok=True)
    path = runs_dir / f"{started_at:%Y%m%d-%H%M%S}.md"
    path.write_text(text, encoding="utf-8")
    return path
