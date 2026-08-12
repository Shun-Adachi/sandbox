"""リクエスト / レスポンスの型。抽出結果のスキーマがこの API の契約そのもの。

このファイルには処理が一行も無く、「何を受け取って何を返すか」だけが書いてある。
ここを読めば API の全体像が分かるので、最初に読むべきファイル。

Pydantic の BaseModel を継承したクラスは、単なるデータ入れ物ではなく次の 3 役を兼ねる。

1. 入力の検証   … FastAPI が受信 JSON をこの型に流し込み、違反があれば 422 を自動で返す
2. 出力の整形   … 関数がこの型を return すると FastAPI が JSON に変換する
3. LLM への指示 … Inquiry は OpenAI の structured outputs にそのまま渡され、
                  「この形でしか返すな」という JSON Schema に変換される

つまり型注釈が実行時に意味を持つ。ここが普通の Python の型ヒントと違うところ。
"""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


class Category(str, Enum):
    """問い合わせの種別。取りうる値をここで閉じている。

    `str` と `Enum` を両方継承しているのは、文字列としても列挙型としても扱えるようにするため。
    `Category.質問 == "質問"` が True になり、JSON に出すときもそのまま文字列になる。
    素の Enum だと文字列比較が False になって扱いづらい。
    """

    質問 = "質問"
    不具合 = "不具合"
    解約 = "解約"
    その他 = "その他"


class Urgency(str, Enum):
    """緊急度。判断基準はプロンプト側(src/prompts/extract/*.yaml)に書いてある。"""

    高 = "高"
    中 = "中"
    低 = "低"


class Inquiry(BaseModel):
    """問い合わせテキストから抽出する構造。Dify の doc-extract と同じ項目。

    このクラスが extract 機能の心臓部。OpenAI にこの型を渡すと、
    キーの欠落も列挙値の逸脱も起こらないことが構造的に保証される。

    `Field(description=...)` の説明文は飾りではなく、JSON Schema に含まれて
    OpenAI 側に送られる。つまり **各項目の説明もプロンプトの一部**として働く。
    """

    name: str = Field(description="差出人の氏名。不明なら空文字")
    company: str = Field(description="会社名。不明なら空文字")
    # Category / Urgency 型にしておくと、取りうる値の一覧が JSON Schema に載る
    category: Category
    urgency: Urgency
    summary: str = Field(description="要件の 1 文要約(日本語、50 字以内)")


class Usage(BaseModel):
    """LLM が消費したトークン数。extract と qa の両方で使う。

    既定値を 0 にしてあるのは、LLM を呼ばずに返す経路(qa のフォールバック)で
    `Usage()` と引数なしで作れるようにするため。
    """

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class ExtractRequest(BaseModel):
    """POST /v1/extract のリクエストボディ。

    `Field(min_length=1, max_length=20_000)` がバリデーションそのもの。
    条件を書くだけで、違反時の 422 応答は FastAPI が自動生成する。
    上限を設けているのは、巨大な本文でトークン費用が青天井にならないようにするため。
    """

    text: str = Field(min_length=1, max_length=20_000)
    # 既定値があるフィールドは省略可能になる。省略すれば v1 が使われる
    prompt_version: str = "v1"


class ExtractResponse(BaseModel):
    """POST /v1/extract の成功レスポンス。"""

    # Literal[True] は「true という値しか取らない型」。
    # 成功なら必ず ok:true、失敗なら ErrorResponse の ok:false になるので、
    # クライアントは ok を見るだけで成否を分岐できる(判別可能ユニオンという書き方)
    ok: Literal[True] = True
    data: Inquiry
    # 形は structured outputs が保証するが、業務ルール(要約の長さ等)の違反はここに出す。
    # 後段のバッチ処理が「使えるが要確認」を仕分けできるようにするため、
    # エラーにして落とすのではなく警告として返す。
    #
    # default_factory=list なのは、可変オブジェクトを既定値にしてはいけないため。
    # `warnings: list[str] = []` と書くと全インスタンスで同じリストを共有してしまう
    # (Python の有名な落とし穴)。呼ばれるたびに新しいリストを作る関数を渡して回避する。
    warnings: list[str] = Field(default_factory=list)
    # 以下 4 つは結果そのものではなく「どういう条件で出た結果か」の記録。
    # プロンプト版を変えて精度を比較するとき、これが無いと条件が追えなくなる
    prompt_version: str
    model: str
    usage: Usage
    latency_ms: int


class Citation(BaseModel):
    """qa の回答が、FAQ のどのチャンクに基づいているかを示す出典情報。"""

    chunk_id: int
    score: float  # 質問とチャンクのコサイン類似度。1.0 に近いほど似ている
    heading: str  # チャンクの見出し(FAQ の質問文)


class QaRequest(BaseModel):
    """POST /v1/qa のリクエストボディ。"""

    question: str = Field(min_length=1, max_length=2_000)
    prompt_version: str = "v1"
    # true にすると SSE でストリーミング応答になる。既定は一括応答
    stream: bool = False


class QaResponse(BaseModel):
    """POST /v1/qa の成功レスポンス(ストリーミングでないとき)。

    ExtractResponse と違って ok フィールドが無いのは、
    qa には「形は正しいが業務的に怪しい」という中間状態が無いため。
    """

    answer: str
    citations: list[Citation]
    prompt_version: str
    model: str
    usage: Usage
    latency_ms: int


class ErrorBody(BaseModel):
    """エラーの中身。"""

    code: str  # 機械が分岐するための識別子(例 upstream_rate_limited)
    message: str  # 人が読む説明
    # `str | None` は「文字列か None のどちらか」という型。
    # 5xx では上流のエラー本文が混ざる恐れがあるので None にして返さない(errors.py 参照)
    detail: str | None = None


class ErrorResponse(BaseModel):
    """すべてのエラー応答の共通形。errors.py のハンドラだけがこれを組み立てる。"""

    ok: Literal[False] = False
    error: ErrorBody
