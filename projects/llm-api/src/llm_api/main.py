"""FastAPI アプリ本体。入口層。

このファイルの責務は「どの URL が来たらどの関数を呼ぶか」を決めることだけで、
業務ロジックは一切書かない。だから 60 行程度に収まっている。
実際の処理は extract.py と qa.py にある。

読むときのポイントは、各関数が驚くほど短いこと。
JSON のパースもバリデーションも変換もエラー処理も書いていないのは、
それらを FastAPI と Pydantic と errors.py が肩代わりしているため。
"""

from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.responses import StreamingResponse

from . import qa as qa_service
from .config import settings
from .errors import install_error_handlers
from .extract import extract as extract_service
from .prompts import available_versions
from .schemas import ExtractRequest, ExtractResponse, QaRequest, QaResponse

# ログの出力先と書式を決める。これを呼ばないと logger.info() が画面に出ない
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

# ここで指定した title / description / version は
# http://localhost:8100/docs の自動生成ドキュメントにそのまま表示される
app = FastAPI(
    title="llm-api",
    description="OpenAI API を使った AI 連携 API。Dify で組んだ 2 つのワークフローをコードで再現したもの。",
    version="0.1.0",
)

# 例外 → HTTP ステータスの変換規則をアプリに取り付ける。
# これがあるおかげで、以降の各エンドポイントに try/except を一つも書かなくて済む
install_error_handlers(app)


# @app.get(...) は「デコレータ」。直下の関数を FastAPI に登録する目印で、
# 「GET /healthz が来たらこの関数を呼べ」という意味になる。
# 関数名(healthz)は URL とは無関係で、自由に付けてよい。
@app.get("/healthz")
async def healthz() -> dict:
    """疎通確認。OpenAI は呼ばないので鍵が無くても 200 を返す。

    死活監視用のエンドポイントで外部 API を叩くと、
    上流が不調なだけで自分まで「異常」と判定されてしまう。だからここでは呼ばない。
    鍵が設定されているかどうかだけは真偽値で返し、設定ミスに気付けるようにしている。
    """
    return {
        "status": "ok",
        "chat_model": settings.chat_model,
        "embedding_model": settings.embedding_model,
        # 鍵そのものは絶対に返さない。設定済みかどうかだけを bool にして返す
        "openai_api_key_configured": bool(settings.openai_api_key),
    }


@app.get("/v1/prompts")
async def list_prompts() -> dict:
    """利用可能なプロンプト版の一覧。prompt-eval から版を列挙するのに使う。

    戻り値は {"extract": ["v1", "v2"], "qa": ["v1"]} の形。
    評価スクリプトが版を決め打ちせず、実在する版だけを回せるようにするため。
    """
    return {name: available_versions(name) for name in ("extract", "qa")}


# response_model を指定すると、返した値がこの型に沿っているか検証したうえで
# JSON に変換される。/docs のドキュメントにもこの型が載る
@app.post("/v1/extract", response_model=ExtractResponse)
async def post_extract(request: ExtractRequest) -> ExtractResponse:
    """問い合わせテキストを構造化データに変換する。

    引数に `request: ExtractRequest` と書くだけで、FastAPI が
    受信 JSON をこの型に変換し、検証まで済ませてから呼んでくれる。
    だからこの関数がやるのはサービス層への受け渡しだけになる。

    `await` を付けているのは extract() が async 関数だから。
    OpenAI の応答を待つ数秒の間、サーバーは他のリクエストを処理できる。
    """
    return await extract_service(request.text, request.prompt_version)


# response_model=None にしているのは、戻り値が 2 種類(JSON か SSE ストリーム)あり、
# 単一の型では表せないため。型が決まらないので FastAPI の自動検証は無効になる
@app.post("/v1/qa", response_model=None)
async def post_qa(request: QaRequest) -> QaResponse | StreamingResponse:
    """FAQ に基づいて質問に答える。stream の値で応答形式が変わる。"""
    if not request.stream:
        # 一括応答。完成した QaResponse を返せば FastAPI が JSON にする
        return await qa_service.answer(request.question, request.prompt_version)

    # ストリーミング応答。ここで渡している answer_stream(...) は
    # 「呼び出した瞬間には何も実行されない非同期ジェネレータ」で、
    # StreamingResponse が少しずつ取り出しながらクライアントへ流す。
    # await を付けていないのはそのため(付けると生成器自体を待とうとして壊れる)
    return StreamingResponse(
        qa_service.answer_stream(request.question, request.prompt_version),
        media_type="text/event-stream",
        # nginx 等を挟んだときにバッファされてストリーミングにならないのを防ぐ
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
