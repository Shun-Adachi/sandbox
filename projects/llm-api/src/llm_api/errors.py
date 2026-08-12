"""例外を構造化エラーレスポンスに変換する。横断的な部品。

上流(OpenAI)の失敗をそのまま 500 で潰すと、呼び出し側が
「待って再試行すべき」のか「入力を直すべき」のか判断できない。
リトライ可能性が伝わるステータスコードに割り当てる。

エラー処理をここ 1 箇所に集めているおかげで、
main.py にも extract.py にも qa.py にも try/except が一つも無い。
各関数は「失敗したら例外を投げる」とだけ考えればよく、
HTTP でどう見せるかを気にしなくて済む。
"""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AuthenticationError,
    BadRequestError,
    RateLimitError,
)

from .llm import MissingApiKeyError
from .prompts import PromptNotFoundError
from .schemas import ErrorBody, ErrorResponse

logger = logging.getLogger(__name__)

# 例外 -> (HTTP ステータス, エラーコード, 呼び出し側に見せる説明)
#
# **並び順に意味がある。** 上から順に isinstance で照合し、最初に一致したものを採用する。
# RateLimitError などはすべて APIStatusError の子クラスなので、
# 親である APIStatusError を先に置くと全部そこで捕まってしまう。
# 具体的なものを先、包括的なものを後、が鉄則。
_MAPPING: list[tuple[type[Exception], int, str, str]] = [
    # 400: 呼び出し側が版名を直せば解決する
    (PromptNotFoundError, 400, "prompt_not_found", "指定されたプロンプト版がありません"),
    # 503: サーバー側の設定ミス。呼び出し側には直せない
    (MissingApiKeyError, 503, "api_key_missing", "サーバー側の API キーが未設定です"),
    (AuthenticationError, 503, "upstream_auth_failed", "上流 API の認証に失敗しました"),
    # 429: 時間を置けば直る。Retry-After を付ける
    (RateLimitError, 429, "upstream_rate_limited", "上流 API のレート制限に達しました"),
    # 504: 上流が時間内に応答しなかった
    (APITimeoutError, 504, "upstream_timeout", "上流 API がタイムアウトしました"),
    # 502: 上流に届かない / 上流が異常を返した
    (APIConnectionError, 502, "upstream_unreachable", "上流 API に接続できませんでした"),
    (BadRequestError, 502, "upstream_bad_request", "上流 API がリクエストを拒否しました"),
    # 包括的な受け皿なので必ず最後に置く
    (APIStatusError, 502, "upstream_error", "上流 API がエラーを返しました"),
]


def _classify(exc: Exception) -> tuple[int, str, str]:
    """例外を (ステータス, コード, 説明) に振り分ける。未知の例外は 500 扱い。

    タプルを返しているので、呼び出し側は
    `status, code, message = _classify(exc)` と 3 変数に展開して受け取れる。
    """
    for exc_type, status, code, message in _MAPPING:
        # isinstance は子クラスも True になる。だから並び順が効いてくる
        if isinstance(exc, exc_type):
            return status, code, message
    # 表に無い例外は想定外の不具合。中身を見せずに 500 にする
    return 500, "internal_error", "サーバー内部エラー"


def install_error_handlers(app: FastAPI) -> None:
    """アプリに例外ハンドラを取り付ける。main.py から 1 回だけ呼ばれる。

    関数の中で関数を定義してデコレータを付ける、という形になっている。
    こうすると「アプリを引数で受け取ってから登録する」ことができ、
    テスト用に別のアプリを作る場合にも同じ関数を使い回せる。
    """

    # このデコレータは「処理されなかった例外が出たらこの関数を呼べ」という登録。
    # Exception を指定しているので、あらゆる例外がここに来る
    @app.exception_handler(Exception)
    async def _handle(request: Request, exc: Exception) -> JSONResponse:
        status, code, message = _classify(exc)

        # ログの出し分け。5xx はこちらの不具合なので、原因追跡のため
        # スタックトレース付きで残す(logger.exception がそれをやる)。
        # 4xx は呼び出し側の入力ミスで、トレースを出すとログが無駄に膨らむので 1 行だけ
        if status >= 500:
            logger.exception("%s %s failed: %s", request.method, request.url.path, code)
        else:
            logger.warning("%s %s -> %s: %s", request.method, request.url.path, code, exc)

        # 5xx の detail は上流のエラー本文を含みうるのでクライアントには返さず、ログだけに残す
        detail = str(exc) if status < 500 else None
        body = ErrorResponse(error=ErrorBody(code=code, message=message, detail=detail))

        # 時間を置けば直る種類のエラーには、目安の秒数をヘッダーで伝える。
        # 行儀のよいクライアントはこれを見て待ち時間を決める
        headers = {"Retry-After": "5"} if status in (429, 503, 504) else None

        # ここだけは Pydantic モデルを直接 return できない
        # (例外ハンドラの戻り値は Response でなければならない)。
        # model_dump() で辞書にしてから JSONResponse に包む
        return JSONResponse(status_code=status, content=body.model_dump(), headers=headers)
