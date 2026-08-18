"""会話履歴の永続化。

LangGraph のチェックポインタを SQLite(data/conversations.sqlite)にすることで、
会話の状態がプロセス終了後も残り、`--thread <ID>` で続きから再開できる。

グラフ側(agent.py)は保存先を知らない — compile() に渡すオブジェクトを
差し替えるだけで「メモリ → ファイル → 本番なら Postgres」と切り替わる。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from langgraph.checkpoint.sqlite import SqliteSaver

# テーマディレクトリ直下の data/ に置く(git 管理外)
DB_PATH = Path(__file__).resolve().parents[2] / "data" / "conversations.sqlite"


def open_checkpointer() -> SqliteSaver:
    DB_PATH.parent.mkdir(exist_ok=True)
    # グラフ実行は複数スレッドを使うことがあるため check_same_thread=False
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    return SqliteSaver(conn)


def list_threads() -> list[tuple[str, int]]:
    """保存済みの会話 ID と保存ステップ数を、更新が新しい順で返す。

    注: checkpoints テーブルは SqliteSaver の内部スキーマであり、
    一覧取得の公式 API がまだ無いため直接読んでいる(PoC の割り切り)。
    """
    if not DB_PATH.exists():
        return []
    conn = sqlite3.connect(DB_PATH)
    try:
        rows = conn.execute(
            "SELECT thread_id, COUNT(*) FROM checkpoints "
            "GROUP BY thread_id ORDER BY MAX(rowid) DESC"
        ).fetchall()
    except sqlite3.OperationalError:
        return []
    finally:
        conn.close()
    return rows
