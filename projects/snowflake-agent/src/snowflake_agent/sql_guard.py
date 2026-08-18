"""Agent が生成した SQL の安全弁。

方針: 許可リスト方式。sqlglot でパースし、
- 単文であること
- トップレベルが SELECT(または UNION 等の集合演算)であること
- 木のどこにも書き込み系ノードが含まれないこと
を確認した上で、LIMIT を強制する(未指定なら付与、上限超過なら丸める)。
パースできない SQL は実行しない。
"""

from __future__ import annotations

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError


class SqlGuardError(ValueError):
    """検証に落ちた SQL。メッセージは Agent へのフィードバックとして使う。"""


def _types(*names: str) -> tuple[type, ...]:
    # sqlglot のバージョン差でクラスが無い場合に備えて getattr で拾う
    return tuple(t for t in (getattr(exp, n, None) for n in names) if t is not None)


_ALLOWED_TOP_LEVEL = _types("Select", "Union", "Except", "Intersect")

_FORBIDDEN = _types(
    "Insert", "Update", "Delete", "Merge",
    "Create", "Drop", "Alter", "TruncateTable",
    "Copy", "Grant", "Revoke", "Use", "Set", "Transaction", "Commit", "Rollback",
    # sqlglot が構文として解釈できない文(CALL, PUT 等)は Command になる
    "Command",
)


def validate_and_limit(sql: str, max_rows: int) -> str:
    """検証済み SQL(LIMIT 強制付き)を返す。不正なら SqlGuardError。"""
    if not sql or not sql.strip():
        raise SqlGuardError("SQL が空です")

    try:
        statements = [s for s in sqlglot.parse(sql, read="snowflake") if s is not None]
    except ParseError as e:
        raise SqlGuardError(f"SQL をパースできません: {e}") from None

    if len(statements) != 1:
        raise SqlGuardError("複文は実行できません。SELECT 文を 1 つだけ渡してください")

    stmt = statements[0]

    if not isinstance(stmt, _ALLOWED_TOP_LEVEL):
        raise SqlGuardError(
            f"SELECT 文のみ実行できます(受け取った文: {type(stmt).__name__})"
        )

    forbidden = next(iter(stmt.find_all(*_FORBIDDEN)), None)
    if forbidden is not None:
        raise SqlGuardError(
            f"書き込み・DDL 等は実行できません({type(forbidden).__name__} を検出)"
        )

    if stmt.args.get("into") is not None:
        raise SqlGuardError("SELECT INTO は実行できません")

    stmt = _enforce_limit(stmt, max_rows)
    return stmt.sql(dialect="snowflake")


def _enforce_limit(stmt: exp.Expression, max_rows: int) -> exp.Expression:
    limit = stmt.args.get("limit")
    if limit is None:
        return stmt.limit(max_rows)

    value = limit.expression
    if isinstance(value, exp.Literal) and value.is_int and int(value.this) <= max_rows:
        return stmt
    # 上限超過、または LIMIT がリテラルでない場合は上限に丸める
    return stmt.limit(max_rows)
