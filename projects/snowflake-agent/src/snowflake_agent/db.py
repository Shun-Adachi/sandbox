"""Snowflake 接続と読み取り実行。

接続は 1 プロセス 1 本を使い回す(PoC の CLI 用途では十分)。
タイムアウトはセッションパラメータ STATEMENT_TIMEOUT_IN_SECONDS で DB 側に強制する。
"""

from __future__ import annotations

from dataclasses import dataclass

import snowflake.connector

from .config import Settings


@dataclass
class QueryResult:
    columns: list[str]
    rows: list[tuple]
    truncated: bool


def connect(settings: Settings) -> snowflake.connector.SnowflakeConnection:
    kwargs: dict = dict(
        account=settings.account,
        user=settings.user,
        password=settings.password,
        warehouse=settings.warehouse,
        database=settings.database,
        schema=settings.schema,
        session_parameters={
            "STATEMENT_TIMEOUT_IN_SECONDS": settings.query_timeout_seconds,
        },
    )
    if settings.role:
        kwargs["role"] = settings.role
    if settings.authenticator:
        kwargs["authenticator"] = settings.authenticator
    return snowflake.connector.connect(**kwargs)


def fetch(
    conn: snowflake.connector.SnowflakeConnection,
    sql: str,
    max_rows: int,
    params: tuple | None = None,
) -> QueryResult:
    """max_rows + 1 件まで取得し、切り詰めたかどうかも返す。"""
    with conn.cursor() as cur:
        cur.execute(sql, params)
        rows = cur.fetchmany(max_rows + 1)
        columns = [c[0] for c in cur.description]
    truncated = len(rows) > max_rows
    return QueryResult(columns=columns, rows=rows[:max_rows], truncated=truncated)


def format_result(result: QueryResult, max_cell_width: int = 60) -> str:
    """ツール結果として LLM に返す簡易テーブル表現。"""
    if not result.rows:
        return "(0 行)"

    def cell(v: object) -> str:
        s = "" if v is None else str(v)
        return s if len(s) <= max_cell_width else s[: max_cell_width - 1] + "…"

    lines = [" | ".join(result.columns)]
    lines.append("-" * min(len(lines[0]), 120))
    lines.extend(" | ".join(cell(v) for v in row) for row in result.rows)
    if result.truncated:
        lines.append(f"(注: 結果は先頭 {len(result.rows)} 行に切り詰められています)")
    return "\n".join(lines)
