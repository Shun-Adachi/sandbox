"""OpenAI クライアント。基盤層。

リトライは SDK 内蔵のものを使う(429 / 5xx / 接続断に対する指数バックオフで、
`Retry-After` ヘッダーも尊重される)。自前で書くとヘッダー無視や
ジッター無しの実装になりやすく、レート制限をかえって悪化させるため。
このモジュールが足すのは、タイムアウト・リトライ回数の一元管理と使用量の取り出しだけ。

「指数バックオフ」は再試行の待ち時間を 1 秒 → 2 秒 → 4 秒と倍にしていく方式。
「ジッター」はそこに乱数を混ぜること。全クライアントが同じ間隔で再試行すると
復旧の瞬間にアクセスが集中して再び落ちるため、意図的にばらけさせる。
"""

from __future__ import annotations

from functools import lru_cache

from openai import AsyncOpenAI

from .config import settings
from .schemas import Usage


class MissingApiKeyError(RuntimeError):
    """OPENAI_API_KEY が未設定。

    鍵が無いのはサーバー側の設定ミスであって呼び出し側の責任ではない。
    だから errors.py では 4xx ではなく 503(サービス利用不可)に割り当てている。
    """


# maxsize=1 は「1 個だけ覚える」。この関数は引数を取らないので実質シングルトンになる。
# クライアントを毎回作ると HTTP のコネクションプールが作り直され、
# 接続の再利用ができず遅くなる。1 つを使い回すのが正しい使い方
@lru_cache(maxsize=1)
def get_client() -> AsyncOpenAI:
    """OpenAI の非同期クライアントを返す(初回だけ生成)。

    AsyncOpenAI(同期版の OpenAI ではない)を使うのは、
    このアプリ全体が async で動いているため。同期版を使うと
    応答待ちの間サーバー全体が止まり、並行処理の利点が消える。
    """
    if not settings.openai_api_key:
        # 鍵が無いまま呼ぶと分かりにくい認証エラーになるので、手前で明示的に落とす
        raise MissingApiKeyError(
            "OPENAI_API_KEY が設定されていません。"
            " projects/llm-api/.env に設定してください(.env.example 参照)。"
        )
    return AsyncOpenAI(
        api_key=settings.openai_api_key,
        # 1 リクエストがこの秒数を超えたら打ち切る。
        # 指定しないと応答が返らないとき延々待ち続ける
        timeout=settings.request_timeout,
        # SDK が内部で再試行する回数。ここを 0 にすると再試行しなくなる
        max_retries=settings.max_retries,
    )


def usage_from(raw: object) -> Usage:
    """SDK のレスポンスから使用量を取り出す。ストリーミングでは None のことがある。

    引数の型が具体的な型でなく object なのは、
    chat / embeddings / ストリーミングで微妙に別の型が来るため。
    どれも同じ属性名を持つので、型を固定せず属性の有無だけを見る
    (ダックタイピングという考え方)。

    getattr(オブジェクト, "名前", 既定値) は、属性が無ければ既定値を返す取得方法。
    さらに `or 0` を重ねているのは、属性はあるが値が None のケースに備えるため。
    """
    if raw is None:
        return Usage()
    return Usage(
        prompt_tokens=getattr(raw, "prompt_tokens", 0) or 0,
        completion_tokens=getattr(raw, "completion_tokens", 0) or 0,
        total_tokens=getattr(raw, "total_tokens", 0) or 0,
    )
