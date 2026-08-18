"""CLI エントリポイント。

使い方:
    snowflake-agent "顧客セグメント別の売上上位5件を分析して"
    snowflake-agent --check          # Snowflake 疎通チェックのみ
"""

from __future__ import annotations

import argparse
import sys

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage


def _text_of(message: AIMessage) -> str:
    """AIMessage.content(文字列 or ブロックのリスト)からテキストを取り出す。"""
    content = message.content
    if isinstance(content, str):
        return content
    parts = [b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"]
    return "\n".join(p for p in parts if p)


def check_connection() -> int:
    from .config import load_settings
    from .db import connect, fetch, format_result

    settings = load_settings()
    print(f"接続先: {settings.account} / {settings.database}.{settings.schema}")
    conn = connect(settings)
    version = fetch(conn, "SELECT CURRENT_VERSION()", 1)
    print(f"Snowflake バージョン: {version.rows[0][0]}")
    tables = fetch(
        conn,
        f"SELECT table_name, row_count FROM {settings.database}.INFORMATION_SCHEMA.TABLES "
        "WHERE table_schema = %s AND table_type = 'BASE TABLE' ORDER BY table_name",
        50,
        (settings.schema,),
    )
    print(f"テーブル数: {len(tables.rows)}")
    print(format_result(tables))
    print("\n疎通 OK")
    return 0


def run_agent(question: str, max_steps: int, log: bool = True) -> int:
    from datetime import datetime

    from .agent import SYSTEM_PROMPT, build_agent
    from .config import load_settings
    from .transcript import render_transcript, save_transcript

    settings = load_settings()
    app = build_agent(settings)
    started_at = datetime.now()

    print(f"[質問] {question}\n")
    final: AIMessage | None = None
    history: list = []
    try:
        # app.stream() はグラフを実行しながら進捗を逐次返す。
        # - 初期状態としてユーザーの質問 1 件を渡すと、agent ⇄ tools のループが回り始める
        # - stream_mode="updates" は「ノードが実行されるたびに、そのノードが
        #   追加したメッセージだけを受け取る」モード(進捗表示に向く)
        # - recursion_limit はループの上限。Claude がツールを使い続けて
        #   無限ループになるのを防ぐ安全装置
        for update in app.stream(
            {"messages": [HumanMessage(question)]},
            config={"recursion_limit": max_steps},
            stream_mode="updates",
        ):
            for payload in update.values():
                for message in payload.get("messages", []):
                    history.append(message)
                    if isinstance(message, AIMessage):
                        final = message
                        for call in message.tool_calls or []:
                            args = call["args"]
                            detail = args.get("sql") or args.get("table_name") or ""
                            print(f"[tool] {call['name']} {detail}".rstrip())
                    elif isinstance(message, ToolMessage):
                        text = str(message.content)
                        head = text.splitlines()[0] if text else ""
                        print(f"       → {head[:100]}")
    finally:
        # 途中で失敗してもそこまでのやり取りは記録する
        if log and history:
            path = save_transcript(
                render_transcript(
                    question=question,
                    system_prompt=SYSTEM_PROMPT.format(
                        database=settings.database, schema=settings.schema
                    ),
                    messages=history,
                    model_id=settings.model_id,
                    started_at=started_at,
                ),
                started_at,
            )
            print(f"\n[log] やり取りの全文: {path}")

    if final is None:
        print("回答を得られませんでした", file=sys.stderr)
        return 1
    print("\n===== 回答 =====")
    print(_text_of(final))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="snowflake-agent",
        description="自然言語の質問から Snowflake に SQL を発行し分析結果を返す Agent",
    )
    parser.add_argument("question", nargs="?", help="質問(日本語可)")
    parser.add_argument("--check", action="store_true", help="Snowflake 疎通チェックのみ実行")
    parser.add_argument("--max-steps", type=int, default=30, help="グラフの最大ステップ数")
    parser.add_argument(
        "--no-log", action="store_true", help="runs/ へのトランスクリプト保存を無効化"
    )
    args = parser.parse_args()

    if args.check:
        return check_connection()
    if not args.question:
        parser.error("質問を指定するか --check を使ってください")
    return run_agent(args.question, args.max_steps, log=not args.no_log)


if __name__ == "__main__":
    sys.exit(main())
