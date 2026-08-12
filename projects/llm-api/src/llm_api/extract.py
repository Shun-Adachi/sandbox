"""問い合わせテキストの構造化抽出(Dify の doc-extract 相当)。サービス層。

Dify 版は「LLM に JSON を書かせる → コードノードでキーと列挙値を検証 → IF/ELSE で分岐」
という作りだった。ここでは OpenAI の structured outputs を使うので、
キーの欠落と列挙値の逸脱はそもそも起きない。代わりに、形では表せない業務ルール
(要約の長さなど)を検証して警告として返す。

このファイルは HTTP を一切知らない。FastAPI も Request も import していないので、
サーバーを立てずに `await extract("本文")` と直接呼べる。
バッチ処理やテストから使いやすいのはこの分離のおかげ。
"""

from __future__ import annotations

import time

from .llm import get_client, usage_from
from .prompts import load_prompt
from .schemas import ExtractResponse, Inquiry

# 業務ルールの閾値。マジックナンバーを埋め込まず定数にしておくと、
# テストから同じ値を参照できて「50」の二重管理を避けられる
SUMMARY_MAX_CHARS = 50


class ExtractionRefusedError(RuntimeError):
    """モデルが安全上の理由で応答を拒否した。

    独自の例外クラスを定義するのは、呼び出し側が except で選り分けられるようにするため。
    クラス本体は docstring だけで中身が要らない(pass の代わりに docstring を置いている)。
    """


def _check_business_rules(data: Inquiry) -> list[str]:
    """形では表せない業務ルールを検証し、違反を文字列のリストで返す。

    例外を投げず、戻り値で違反を伝えているのが設計上の判断。
    要約が 1 字長いだけで抽出結果を丸ごと捨てるのは惜しいので、
    「使えるが要確認」として呼び出し側に判断を委ねる。

    先頭の `_` は「このモジュール内部用」という慣習的な目印。
    Python に private の仕組みは無いので、名前で意図を示す。
    """
    warnings: list[str] = []
    if len(data.summary) > SUMMARY_MAX_CHARS:
        warnings.append(
            f"summary が {SUMMARY_MAX_CHARS} 字を超えています({len(data.summary)} 字)"
        )
    if not data.summary.strip():
        # strip() で空白を除いてから判定。空白だけの要約も「空」とみなす
        warnings.append("summary が空です")
    return warnings


async def extract(text: str, prompt_version: str = "v1") -> ExtractResponse:
    """テキストから問い合わせ情報を抽出する。この API の中核。

    引数:
        text: 解析対象の問い合わせ本文
        prompt_version: 使うプロンプトの版。既定は v1

    戻り値:
        抽出結果に、警告・使用トークン・所要時間を添えたもの

    例外:
        PromptNotFoundError: 指定の版が存在しない(errors.py が 400 に変換)
        ExtractionRefusedError: モデルが応答を拒否した
        openai の各種例外: 上流の失敗(errors.py が 429/502/504 等に変換)
    """
    # 版に対応する YAML を読む。2 回目以降はキャッシュから返るのでファイル I/O は起きない
    prompt = load_prompt("extract", prompt_version)

    # perf_counter は「時刻」ではなく「経過時間の計測」専用の時計。
    # システム時刻の変更や NTP 同期の影響を受けないので、所要時間の測定にはこちらを使う
    started = time.perf_counter()

    # ここが山場。response_format に Pydantic モデルを渡すと、SDK が
    # Inquiry から JSON Schema を生成して送り、OpenAI 側はその形でしか返せなくなる。
    # 戻り値の message.parsed には、パース済みの Inquiry インスタンスが入る。
    # 「JSON 文字列を受け取って json.loads して検証する」工程が丸ごと不要になる
    completion = await get_client().beta.chat.completions.parse(
        model=prompt.model,
        temperature=prompt.temperature,  # 0 にして毎回同じ結果が出るようにしている
        messages=[
            # LLM への入力は role 付きのメッセージの配列。
            # system が役割と指示、user が処理対象のデータ、という分担にしている
            {"role": "system", "content": prompt.render_system()},
            {"role": "user", "content": prompt.render_user(text=text)},
        ],
        response_format=Inquiry,
    )

    # choices は候補の配列。n を指定していないので候補は常に 1 つで、先頭だけ見ればよい
    message = completion.choices[0].message

    # structured outputs でも、モデルが応答を拒否する可能性は残る。
    # その場合 parsed は None で refusal に理由が入るので、先に潰しておく
    if message.refusal:
        raise ExtractionRefusedError(message.refusal)
    if message.parsed is None:
        raise RuntimeError("structured outputs のパース結果が空でした")

    return ExtractResponse(
        data=message.parsed,
        warnings=_check_business_rules(message.parsed),
        prompt_version=prompt.version,
        # 要求した model 名ではなく応答が申告した model を記録する。
        # "gpt-4o-mini" と頼んでも実体は "gpt-4o-mini-2024-07-18" のように
        # スナップショットが返るので、後から結果を再現するにはこちらが要る
        model=completion.model,
        usage=usage_from(completion.usage),
        latency_ms=int((time.perf_counter() - started) * 1000),
    )
