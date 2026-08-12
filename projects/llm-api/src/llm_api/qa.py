"""FAQ に対する RAG Q&A(Dify の rag-qa 相当)。サービス層。

RAG (Retrieval-Augmented Generation) は「検索してから生成する」方式のこと。
LLM は TaskFlow という架空製品を知らないので、まず FAQ から関連箇所を検索し、
それを読ませたうえで答えさせる。こうすると学習データに無い知識でも答えられるし、
出典を示せる。

Dify 版はコンテキストが空でもプロンプトの指示でフォールバックさせていたが、
ここでは閾値を超えるチャンクが 0 件なら LLM を呼ばずに定型文を返す。
コストがかからず、フォールバックが確実に出ることをコード側で保証できるため。
プロンプト側の同じ指示は、チャンクは引けたが答えが書かれていない場合の保険として残している。

このファイルには一括応答とストリーミング応答の 2 系統がある。
共通部分(_citations / _messages / _fallback)を先に定義し、
その後に answer(一括)と answer_stream(逐次)が続く構成。
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import AsyncIterator

from .llm import get_client, usage_from
from .prompts import Prompt, load_prompt
from .rag import Chunk, retrieve
from .schemas import Citation, QaResponse, Usage

# __name__ を渡すと "llm_api.qa" という名前のロガーになり、
# ログ出力にモジュール名が付いてどこから出たか分かる
logger = logging.getLogger(__name__)


def _citations(hits: list[tuple[Chunk, float]]) -> list[Citation]:
    """検索結果を、クライアントに返す出典情報に変換する。

    引数の型 `list[tuple[Chunk, float]]` は「(チャンク, スコア) の組のリスト」。
    rag.py の検索がこの形で返してくるので、それを Citation に詰め替えている。

    中身はリスト内包表記。`[式 for 変数 in リスト]` で
    「各要素を変換した新しいリスト」を作る書き方で、for 文で append するより短い。
    `for chunk, score in hits` は組を 2 つの変数に分解して受け取っている(アンパック)。
    """
    return [
        # スコアは小数が長く続くので、表示用に 4 桁で丸める
        Citation(chunk_id=chunk.id, score=round(score, 4), heading=chunk.heading)
        for chunk, score in hits
    ]


def _messages(prompt: Prompt, hits: list[tuple[Chunk, float]], question: str) -> list[dict]:
    """検索結果と質問から、LLM に送るメッセージ配列を組み立てる。

    一括応答とストリーミングの両方から呼ばれる共通部分。
    プロンプトの組み立てを 1 箇所にまとめておかないと、
    片方だけ直して挙動がずれる、という事故が起きる。
    """
    # 検索で当たったチャンクの本文を空行 2 つで連結してコンテキストにする。
    # `for chunk, _ in hits` の `_` は「この値は使わない」という慣習的な変数名(ここではスコア)
    context = "\n\n".join(chunk.text for chunk, _ in hits)
    return [
        # コンテキストは system 側に埋め込む。プロンプト YAML の {context} がここで埋まる
        {"role": "system", "content": prompt.render_system(context=context)},
        {"role": "user", "content": prompt.render_user(question=question)},
    ]


def _fallback(prompt: Prompt, started: float) -> QaResponse:
    """FAQ に該当が無かったときの定型応答を組み立てる。

    LLM を呼ばずに返すので usage は空(トークン消費ゼロ)。
    文言はプロンプト YAML の fallback_answer から取る。
    ここにベタ書きすると、プロンプト側の指示文と二重管理になるため。
    """
    return QaResponse(
        # `A or B` は「A が空や None なら B」という書き方。
        # fallback_answer が未設定の版でも落ちないようにする保険
        answer=prompt.fallback_answer or "回答できませんでした。",
        citations=[],
        prompt_version=prompt.version,
        # LLM を呼んでいないので応答から model 名を取れない。設定値をそのまま記録する
        model=prompt.model,
        usage=Usage(),
        latency_ms=int((time.perf_counter() - started) * 1000),
    )


async def answer(question: str, prompt_version: str = "v1") -> QaResponse:
    """質問に一括で答える(ストリーミングしない版)。

    流れは 3 段階。
      1. FAQ を検索する
      2. 該当が無ければ LLM を呼ばずに定型文を返す
      3. 該当があればコンテキストに載せて LLM に答えさせる
    """
    prompt = load_prompt("qa", prompt_version)
    started = time.perf_counter()

    # await が付いているのは、検索の中で埋め込み API を呼ぶ通信が発生するため
    hits = await retrieve(question)
    if not hits:
        # 空リストは False として扱われるので `if not hits` で「0 件なら」の意味になる
        return _fallback(prompt, started)

    # extract と違い response_format を指定しない。自由文で答えてほしいため
    completion = await get_client().chat.completions.create(
        model=prompt.model,
        temperature=prompt.temperature,
        messages=_messages(prompt, hits, question),
    )

    return QaResponse(
        # content は None になりうる型なので、`or ""` で必ず文字列にしておく
        answer=completion.choices[0].message.content or "",
        citations=_citations(hits),
        prompt_version=prompt.version,
        model=completion.model,
        usage=usage_from(completion.usage),
        latency_ms=int((time.perf_counter() - started) * 1000),
    )


def _sse(event: str, payload: object) -> str:
    """Server-Sent Events (SSE) の 1 イベント分の文字列を組み立てる。

    SSE は「サーバーが押し出し続ける」ための素朴な仕様で、書式はこれだけ。

        event: イベント名\\n
        data: 本文\\n
        \\n                 ← 空行が 1 イベントの終わり

    末尾の空行が無いとクライアントはイベントの終わりを認識できない。
    そのため f-string の最後が `\\n\\n` になっている。

    ensure_ascii=False を付けているのは、付けないと日本語が
    \\u30c6\\u30b9\\u30c8 のようにエスケープされて読みづらくなるため。
    """
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


async def answer_stream(question: str, prompt_version: str = "v1") -> AsyncIterator[str]:
    """SSE で返す。実処理は _stream_events に委ね、ここは失敗の受け止めだけを行う。

    ストリームを流し始めた後は HTTP ステータスを変えられないので、
    途中で失敗した場合は error イベントとして本文に流す。
    クライアントは done が来なければ異常終了と判断できる。

    戻り値の型 `AsyncIterator[str]` は「非同期に文字列を次々返すもの」。
    関数の中に yield があるので、呼んでもすぐには実行されず、
    受け取り側が 1 つずつ取り出すたびに続きが動く。
    """
    try:
        # `async for` は、非同期に届く要素を 1 つずつ取り出すループ。
        # 受け取った各イベントをそのまま外へ中継している
        async for event in _stream_events(question, prompt_version):
            yield event
    except Exception as exc:  # noqa: BLE001 - ストリーム内なので握って通知に変える
        # 通常なら例外は errors.py のハンドラに任せるが、
        # ここは応答本文を送り始めた後なので届かない。自前で通知に変換する
        logger.exception("SSE ストリームが失敗しました")
        # 例外の中身は外に出さず、クラス名と定型文だけを返す(内部情報を漏らさない)
        yield _sse("error", {"code": type(exc).__name__, "message": "回答の生成に失敗しました"})


async def _stream_events(question: str, prompt_version: str) -> AsyncIterator[str]:
    """引用は本文より先に送る。UI が回答を描き始める前に出典を出せるようにするため。

    送るイベントは 3 種類で、順序は citations → delta(複数) → done。
    done が最後に必ず来ることを約束にしているので、
    クライアントは done の到着で正常終了を判定できる。
    """
    prompt = load_prompt("qa", prompt_version)
    started = time.perf_counter()

    hits = await retrieve(question)
    # model_dump() は Pydantic モデルを辞書に変換するメソッド。JSON 化の前段として使う
    yield _sse("citations", [c.model_dump() for c in _citations(hits)])

    if not hits:
        # 該当なしでも形式は揃える。クライアントから見れば
        # 「短い回答が 1 回で届いた」だけで、特別扱いが要らない
        fallback = _fallback(prompt, started)
        yield _sse("delta", {"text": fallback.answer})
        yield _sse("done", {"usage": fallback.usage.model_dump(), "latency_ms": fallback.latency_ms})
        # 早期 return でこれ以上イベントを送らない。ジェネレータはここで終了する
        return

    stream = await get_client().chat.completions.create(
        model=prompt.model,
        temperature=prompt.temperature,
        messages=_messages(prompt, hits, question),
        stream=True,
        # 付けないとストリーミング時に使用トークン数が取れない。
        # 最後に usage だけを持つチャンクが 1 つ追加で届くようになる
        stream_options={"include_usage": True},
    )

    usage = Usage()
    async for chunk in stream:
        # usage は最後のチャンクにだけ入る。届いたら上書きして覚えておく
        if chunk.usage is not None:
            usage = usage_from(chunk.usage)
        for choice in chunk.choices:
            # delta は「前回からの差分」。1 チャンクが数文字ぶんの断片になる
            text = choice.delta.content
            # 空文字や None のチャンクも混ざるので、中身があるときだけ送る
            if text:
                yield _sse("delta", {"text": text})

    yield _sse(
        "done",
        {"usage": usage.model_dump(), "latency_ms": int((time.perf_counter() - started) * 1000)},
    )
