"""CLI エントリポイント。

使い方:
    snowflake-agent "顧客セグメント別の売上上位5件を分析して"
    snowflake-agent --approve "..."      # SQL 実行前に人間の承認を挟む
    snowflake-agent --database MY_DB --schema PUBLIC "..."   # 対象 DB を切り替え
    snowflake-agent --check              # Snowflake 疎通チェックのみ

回答の後は追質問を受け付ける(会話はチェックポイントに保存されているので
文脈が通じる)。Enter だけ、または exit で終了する。
"""

from __future__ import annotations

import argparse
import dataclasses
import sys

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from .config import Settings


def _text_of(message: AIMessage) -> str:
    """AIMessage.content(文字列 or ブロックのリスト)からテキストを取り出す。"""
    content = message.content
    if isinstance(content, str):
        return content
    parts = [b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"]
    return "\n".join(p for p in parts if p)


def check_connection(settings: Settings) -> int:
    from .db import connect, fetch, format_result

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


def show_thread(thread_id: str) -> int:
    """保存済みの会話を復元して表示する(data/conversations.sqlite から)。"""
    from .store import open_checkpointer

    saver = open_checkpointer()
    tuple_ = saver.get_tuple({"configurable": {"thread_id": thread_id}})
    if tuple_ is None:
        print(f"会話 '{thread_id}' は見つかりません(--threads で一覧を確認)", file=sys.stderr)
        return 1
    messages = tuple_.checkpoint["channel_values"].get("messages", [])
    print(f"会話 '{thread_id}': {len(messages)} メッセージ\n")
    for i, m in enumerate(messages, 1):
        if isinstance(m, HumanMessage):
            print(f"{i:3}. [ユーザー] {m.content}")
        elif isinstance(m, AIMessage):
            text = _text_of(m).replace("\n", " ")
            calls = ", ".join(c["name"] for c in (m.tool_calls or []))
            suffix = f"  →ツール要求: {calls}" if calls else ""
            print(f"{i:3}. [AI] {text[:100]}{' …' if len(text) > 100 else ''}{suffix}")
        elif isinstance(m, ToolMessage):
            head = str(m.content).splitlines()[0][:100] if str(m.content) else ""
            print(f"{i:3}. [ツール結果:{m.name}] {head} …")
    return 0


def _ask_approval(interrupt_value: dict):
    """承認待ちで一時停止したグラフに対し、人間の判断を Command(resume=...) で返す。"""
    from langgraph.types import Command

    print("\n[承認] Agent が以下の SQL の実行許可を求めています:")
    for i, sql in enumerate(interrupt_value.get("sqls", []), 1):
        print(f"--- SQL {i} ---\n{sql}")
    try:
        answer = input(
            "\n実行しますか? [y = 実行 / それ以外の入力 = 拒否理由として Agent に送信] > "
        ).strip()
    except EOFError:
        # パイプ実行など、キーボード入力を受け付けられない環境。
        # エラーで落とさず「自動拒否」として Agent に伝え、回答まで進ませる
        print("(標準入力が閉じているため自動拒否します。対話端末から実行すると承認できます)")
        return Command(
            resume={
                "approved": False,
                "reason": "承認入力ができない環境で実行されているため許可できません。"
                "SQL を実行せずに、答えられる範囲で回答してください",
            }
        )
    if answer.lower() == "y":
        return Command(resume={"approved": True})
    return Command(resume={"approved": False, "reason": answer})


def _ask_followup() -> str | None:
    """次の質問を受け付ける。空入力・exit・EOF(パイプ実行)なら終了。"""
    try:
        text = input("\n追質問があれば入力してください(Enter または exit で終了)> ").strip()
    except EOFError:
        return None
    if not text or text.lower() in {"exit", "quit"}:
        return None
    return text


def run_agent(
    settings: Settings,
    question: str,
    max_steps: int,
    log: bool,
    approve: bool,
    thread: str | None = None,
) -> int:
    from datetime import datetime

    from .agent import SYSTEM_PROMPT, build_agent
    from .store import open_checkpointer
    from .transcript import render_transcript, save_transcript

    # 会話状態は SQLite に永続化する。プロセスが終わっても消えず、
    # --thread <ID> を指定すれば続きから再開できる
    app = build_agent(settings, approval=approve, checkpointer=open_checkpointer())
    started_at = datetime.now()

    # thread_id は「どの会話か」を示す ID。チェックポインタはこの ID ごとに
    # 状態(会話履歴)を保存・復元する
    thread_id = thread or f"cli-{started_at:%Y%m%d-%H%M%S}"
    config: dict = {
        "recursion_limit": max_steps,
        "configurable": {"thread_id": thread_id},
    }

    # 既存スレッドの再開なら、保存済みの会話がどこまで進んでいたかを表示する
    saved = app.get_state(config).values.get("messages", [])
    if saved:
        last_human = next(
            (m.content for m in reversed(saved) if isinstance(m, HumanMessage)), "?"
        )
        print(f"[再開] 会話 '{thread_id}'(これまで {len(saved)} メッセージ。直前の質問: {last_human})\n")

    print(f"[質問] {question}\n")
    final: AIMessage | None = None
    answered_any = False
    history: list = []
    # 最初の入力は質問。以降は
    # - 承認で一時停止 → Command(resume=...) を入力にして再開
    # - 回答が出た → 追質問を新しい HumanMessage として同じ thread に投入
    pending = {"messages": [HumanMessage(question)]}
    try:
        while pending is not None:
            interrupted = None
            for update in app.stream(pending, config=config, stream_mode="updates"):
                # interrupt() で一時停止すると "__interrupt__" という特別な更新が届く
                if "__interrupt__" in update:
                    interrupted = update["__interrupt__"][0]
                    continue
                for payload in update.values():
                    # 状態を更新しないノード(承認して素通りした approve など)の
                    # payload は None になるため読み飛ばす
                    if not isinstance(payload, dict):
                        continue
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

            if interrupted is not None:
                # 承認待ち: 人間の判断を得て同じターンを再開する
                pending = _ask_approval(interrupted.value)
                continue

            # ターン完了 → 回答を表示し、続けるかどうかは人間が決める
            if final is not None:
                answered_any = True
                print("\n===== 回答 =====")
                print(_text_of(final))
            next_question = _ask_followup()
            if next_question is None:
                pending = None
            else:
                print(f"\n[質問] {next_question}\n")
                history.append(HumanMessage(next_question))
                final = None
                pending = {"messages": [HumanMessage(next_question)]}
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
        print(f"[thread] この会話の ID: {thread_id}(--thread {thread_id} で続きから再開できます)")

    if not answered_any:
        print("回答を得られませんでした", file=sys.stderr)
        return 1
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
    parser.add_argument(
        "--approve",
        action="store_true",
        help="SQL(run_query)の実行前に人間の承認を求める(human-in-the-loop)",
    )
    parser.add_argument("--database", help="対象データベース(.env の値を上書き)")
    parser.add_argument("--schema", help="対象スキーマ(.env の値を上書き)")
    parser.add_argument(
        "--thread", help="会話 ID。過去の会話 ID を指定すると続きから再開する"
    )
    parser.add_argument(
        "--threads", action="store_true", help="保存済みの会話一覧を表示して終了"
    )
    parser.add_argument(
        "--show", metavar="会話ID", help="保存済みの会話の中身を表示して終了"
    )
    args = parser.parse_args()

    if args.show:
        return show_thread(args.show)
    if args.threads:
        from .store import list_threads

        rows = list_threads()
        if not rows:
            print("保存済みの会話はありません")
        for thread_id, steps in rows:
            print(f"{thread_id}  (保存ステップ数: {steps})")
        return 0

    from .config import load_settings

    settings = load_settings()
    # CLI オプションで対象 DB / スキーマを差し替えられる(自作 DB の分析などに使う)。
    # Settings は不変(frozen)なので、書き換えではなく差し替えたコピーを作る
    overrides = {
        key: value.upper()
        for key, value in (("database", args.database), ("schema", args.schema))
        if value
    }
    if overrides:
        settings = dataclasses.replace(settings, **overrides)

    if args.check:
        return check_connection(settings)
    if not args.question:
        parser.error("質問を指定するか --check を使ってください")
    return run_agent(
        settings,
        args.question,
        args.max_steps,
        log=not args.no_log,
        approve=args.approve,
        thread=args.thread,
    )


if __name__ == "__main__":
    sys.exit(main())
