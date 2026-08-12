"""環境変数から読む設定。基盤層。

設定値をコードに散らさず 1 箇所に集めておくと、
「モデルを変えたい」「タイムアウトを伸ばしたい」が .env の編集だけで済む。
また、鍵のような秘密をソースに書かずに済む。
"""

from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# __file__ はこのファイル自身のパス。
# resolve() で絶対パスにし、parents[2] で 2 階層上("src/llm_api" の 2 つ上)を取る。
#   .../projects/llm-api/src/llm_api/config.py
#   parents[0] = .../src/llm_api   parents[1] = .../src   parents[2] = .../llm-api
# こうしておくと、どのディレクトリから起動してもパスが狂わない
PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    """`.env` と環境変数から読む。環境変数のほうが優先される。

    OPENAI_API_KEY だけは業界標準の名前をそのまま使い、
    このアプリ固有の設定には LLM_API_ を付けて区別する。

    BaseSettings は Pydantic の設定用クラスで、
    宣言した項目名に対応する環境変数を自動で探して型変換まで行う。
    例えば LLM_API_MAX_RETRIES=5 という文字列は int の 5 になる。
    """

    # extra="ignore" は、.env に知らない項目があっても無視する指定。
    # これが無いと、他用途の変数が 1 つ混ざっただけで起動に失敗する
    model_config = SettingsConfigDict(env_file=PROJECT_ROOT / ".env", extra="ignore")

    # alias で環境変数名を明示している。
    # 当初は env_prefix="LLM_API_" でまとめて前置しようとしたが、
    # それだと OPENAI_API_KEY まで LLM_API_OPENAI_API_KEY を探しに行ってしまい、
    # 標準的な名前で鍵を置けなくなる。項目ごとに指定するこの形に落ち着いた
    openai_api_key: str = Field(default="", alias="OPENAI_API_KEY")

    chat_model: str = Field(default="gpt-4o-mini", alias="LLM_API_CHAT_MODEL")
    embedding_model: str = Field(
        default="text-embedding-3-small", alias="LLM_API_EMBEDDING_MODEL"
    )

    request_timeout: float = Field(default=30.0, alias="LLM_API_REQUEST_TIMEOUT")
    max_retries: int = Field(default=3, alias="LLM_API_MAX_RETRIES")

    # 検索パラメータ。Dify 版(rag-qa)と同じ値に合わせてある
    retrieval_top_k: int = Field(default=4, alias="LLM_API_RETRIEVAL_TOP_K")
    retrieval_score_threshold: float = Field(
        default=0.3, alias="LLM_API_RETRIEVAL_SCORE_THRESHOLD"
    )

    # 以下は環境変数から変えない固定のパス。alias を付けていないのはそのため
    prompts_dir: Path = PROJECT_ROOT / "src" / "prompts"
    faq_path: Path = PROJECT_ROOT / "src" / "sample-data" / "faq-taskflow.md"
    cache_dir: Path = PROJECT_ROOT / ".cache"


# モジュール読み込み時に 1 回だけ作り、各所から `from .config import settings` で共有する。
# 各所で Settings() を呼ぶと .env を何度も読むことになるうえ、
# 途中で環境変数が変わったときに値がずれる恐れがある
settings = Settings()
