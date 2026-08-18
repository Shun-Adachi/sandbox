"""Agent に渡す 3 つのツール。

「ツール」とは、LLM が呼び出せる Python 関数のこと。@tool デコレータを付けると、
関数名・docstring・引数の型が「ツール定義」として LLM に渡り、LLM は
「この名前のツールをこの引数で実行して」という要求(tool_calls)を返せるようになる。
つまり **docstring は人間向けではなく LLM 向けの説明文**(いつ・どう使うかを書く)。

設計上のポイント:
- スキーマ全体をプロンプトに埋め込まず、Agent に必要な分だけ調査させる
- ツール内のエラーは例外にせず文字列で返す。エラーメッセージも LLM への
  フィードバックであり、Agent はそれを読んで SQL を直して再試行する
"""

from __future__ import annotations

import re
import threading

from langchain_core.tools import tool

from .config import Settings
from .db import connect, fetch, format_result
from .sql_guard import SqlGuardError, validate_and_limit

_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*$")


def make_tools(settings: Settings) -> list:
    """設定を閉じ込めたツール群を返す。接続は初回利用時に張って使い回す。

    関数の中で関数を定義しているのは、settings と DB 接続を各ツールに
    持たせるため(クロージャ)。ツールの引数は LLM が埋めるものだけにしたいので、
    設定類を引数で渡す代わりにこの形をとっている。
    """
    state: dict = {"conn": None}
    # ツールは並列に呼ばれ得る(LangGraph の ToolNode)ため、初期化はロックで直列化する
    lock = threading.Lock()

    def conn():
        with lock:
            if state["conn"] is None:
                state["conn"] = connect(settings)
            return state["conn"]

    @tool
    def list_tables() -> str:
        """対象スキーマのテーブル一覧と行数を返す。まずこれで全体像を掴むこと。"""
        sql = (
            f"SELECT table_name, row_count FROM {settings.database}.INFORMATION_SCHEMA.TABLES "
            "WHERE table_schema = %s AND table_type = 'BASE TABLE' ORDER BY table_name"
        )
        try:
            result = fetch(conn(), sql, settings.max_result_rows, (settings.schema,))
        except Exception as e:  # noqa: BLE001 - Agent へのフィードバックに変換
            return f"エラー: {e}"
        return format_result(result)

    @tool
    def get_table_schema(table_name: str) -> str:
        """指定テーブルのカラム定義(カラム名・型・コメント)を返す。

        Args:
            table_name: テーブル名(例: ORDERS)
        """
        if not _IDENTIFIER_RE.match(table_name):
            return f"エラー: 不正なテーブル名です: {table_name!r}"
        sql = (
            "SELECT column_name, data_type, is_nullable, comment "
            f"FROM {settings.database}.INFORMATION_SCHEMA.COLUMNS "
            "WHERE table_schema = %s AND table_name = %s ORDER BY ordinal_position"
        )
        try:
            result = fetch(
                conn(), sql, settings.max_result_rows, (settings.schema, table_name.upper())
            )
        except Exception as e:  # noqa: BLE001
            return f"エラー: {e}"
        if not result.rows:
            return f"エラー: テーブル {table_name} が見つかりません。list_tables で確認してください"
        return format_result(result)

    @tool
    def run_query(sql: str) -> str:
        """SELECT 文を実行し結果を返す。

        制約: SELECT 単文のみ。書き込み・DDL は拒否される。
        LIMIT 未指定時は自動で付与され、結果は最大行数で切り詰められる。

        Args:
            sql: 実行する SELECT 文
        """
        try:
            safe_sql = validate_and_limit(sql, settings.max_result_rows)
        except SqlGuardError as e:
            return f"SQL 検証エラー: {e}"
        try:
            result = fetch(conn(), safe_sql, settings.max_result_rows)
        except Exception as e:  # noqa: BLE001
            return f"実行エラー: {e}"
        return f"実行 SQL: {safe_sql}\n\n{format_result(result)}"

    return [list_tables, get_table_schema, run_query]
