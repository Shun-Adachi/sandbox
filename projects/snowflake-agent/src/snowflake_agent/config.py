"""環境変数からの設定読み込み。

接続情報・API キーは .env(git 管理外)にのみ置く。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    model_id: str
    account: str
    user: str
    password: str
    authenticator: str | None
    warehouse: str
    database: str
    schema: str
    role: str | None
    max_result_rows: int
    query_timeout_seconds: int


def load_settings() -> Settings:
    # カレントではなくテーマディレクトリの .env を読む(どこから実行しても効くように)
    theme_root = Path(__file__).resolve().parents[2]
    load_dotenv(theme_root / ".env")
    load_dotenv()  # カレントの .env があればそちらも(既存値は上書きしない)

    missing = [
        name
        for name in ("SNOWFLAKE_ACCOUNT", "SNOWFLAKE_USER", "SNOWFLAKE_PASSWORD")
        if not os.getenv(name)
    ]
    if missing:
        raise SystemExit(
            f".env に {', '.join(missing)} を設定してください(.env.example 参照)"
        )

    return Settings(
        model_id=os.getenv("MODEL_ID", "claude-opus-5"),
        account=os.environ["SNOWFLAKE_ACCOUNT"],
        user=os.environ["SNOWFLAKE_USER"],
        password=os.environ["SNOWFLAKE_PASSWORD"],
        authenticator=os.getenv("SNOWFLAKE_AUTHENTICATOR") or None,
        warehouse=os.getenv("SNOWFLAKE_WAREHOUSE", "COMPUTE_WH"),
        database=os.getenv("SNOWFLAKE_DATABASE", "SNOWFLAKE_SAMPLE_DATA"),
        schema=os.getenv("SNOWFLAKE_SCHEMA", "TPCH_SF1"),
        role=os.getenv("SNOWFLAKE_ROLE") or None,
        max_result_rows=int(os.getenv("MAX_RESULT_ROWS", "100")),
        query_timeout_seconds=int(os.getenv("QUERY_TIMEOUT_SECONDS", "30")),
    )
