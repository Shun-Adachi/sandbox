"""FAQ を対象にしたインメモリの検索インデックス。基盤層。

Dify 版(rag-qa)は Weaviate を使うが、このテーマの完成条件は
「クローン後 README のコマンドだけで動くこと」なので、ベクトル DB を立てずに
numpy のコサイン類似度で済ませている。FAQ 13 件程度ならこれで十分で、
チャンク設計・スコア閾値・フォールバックという RAG の要点は同じように示せる。

埋め込みは起動のたびに取り直すと API コストがかかるため、
FAQ の内容とモデル名のハッシュをキーにディスクへキャッシュする。

--- ベクトル検索の考え方 ---
文章を「埋め込みモデル」に通すと、意味を表す数百次元の数値ベクトルになる。
意味が近い文章どうしはベクトルの向きが揃うので、向きの近さ(コサイン類似度)を
測れば「意味が似ている文章」を探せる。キーワードが一致していなくても当たるのが利点。
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .config import settings
from .llm import get_client

logger = logging.getLogger(__name__)

# 行頭の "### " を見出しとみなす正規表現。
# re.MULTILINE を付けると ^ が「文字列の先頭」ではなく「各行の先頭」を意味するようになる。
# 使い回すので、モジュール読み込み時に一度だけコンパイルしておく
_HEADING = re.compile(r"^###\s+(.*)$", re.MULTILINE)


# frozen=True にすると生成後に属性を変更できなくなる(c.text = "..." が例外になる)。
# 検索対象は読むだけのデータなので、うっかり書き換える事故を型で防いでいる。
# @dataclass を付けると __init__ や __repr__ が自動生成されるので、
# 属性を並べるだけでクラスが作れる
@dataclass(frozen=True)
class Chunk:
    """FAQ を分割した 1 かたまり。1 チャンク = 1 つの Q&A。"""

    id: int  # 何番目のチャンクか。引用の識別子として返す
    heading: str  # 見出し行(FAQ の質問文)
    text: str  # 見出しを含むチャンク全体の本文


def load_chunks(path: Path) -> list[Chunk]:
    """`###` 見出しで 1 チャンク = 1 Q&A に切る。

    チャンク境界を見出しに合わせるのが Dify 版と同じ設計判断。固定長で切ると
    質問と回答が別チャンクに割れて検索がヒットしなくなる。

    RAG の精度はチャンクの切り方でかなり変わる。
    「意味のまとまり 1 つ = チャンク 1 つ」になるよう文書構造に合わせるのが基本。
    """
    content = path.read_text(encoding="utf-8")
    # finditer は全ての一致箇所を順に返す。list() で一覧にして位置を前後参照できるようにする
    matches = list(_HEADING.finditer(content))
    if not matches:
        # 区切りが無いまま 1 個の巨大チャンクにするより、設定ミスとして早く落とすほうがよい
        raise ValueError(f"{path} に '### ' 見出しがありません")

    chunks: list[Chunk] = []
    # enumerate は (連番, 要素) を返す。ここでは連番をチャンク ID に使う
    for i, match in enumerate(matches):
        # 各チャンクは「自分の見出しの先頭」から「次の見出しの直前」まで。
        # 最後のチャンクだけは次が無いので文末までとする
        end = matches[i + 1].start() if i + 1 < len(matches) else len(content)
        body = content[match.start() : end].strip()
        # group(1) は正規表現の丸括弧に対応する部分、つまり "### " を除いた見出し文字列
        chunks.append(Chunk(id=i, heading=match.group(1).strip(), text=body))
    return chunks


def _cache_path(chunks: list[Chunk]) -> Path:
    """キャッシュファイルの置き場所を決める。

    ファイル名に内容のハッシュを入れているのがポイント。
    FAQ を 1 文字でも書き換えるとハッシュが変わって別ファイル名になるので、
    「古い埋め込みを使い続けてしまう」事故が起きない。
    埋め込みモデルを変えたときも同様に別扱いになるよう、モデル名も混ぜている。
    """
    digest = hashlib.sha256(settings.embedding_model.encode())
    for chunk in chunks:
        # update を繰り返すと、連結した文字列を一度に渡したのと同じハッシュになる
        digest.update(chunk.text.encode())
    # 64 桁は長すぎるので先頭 16 桁だけ使う。衝突の心配は実用上ない
    return settings.cache_dir / f"embeddings-{digest.hexdigest()[:16]}.npy"


class Index:
    """チャンクとその埋め込みベクトルを保持し、類似検索を提供する。"""

    def __init__(self, chunks: list[Chunk], matrix: np.ndarray) -> None:
        self.chunks = chunks
        self.matrix = matrix  # (チャンク数, 次元数) を行ごとに L2 正規化したもの

    def search(
        self, query_vector: np.ndarray, top_k: int, threshold: float
    ) -> list[tuple[Chunk, float]]:
        """質問ベクトルに近いチャンクを、スコアの高い順に返す。

        引数:
            query_vector: 質問の埋め込み(正規化済み)
            top_k: 最大何件返すか
            threshold: この値未満のスコアは捨てる

        戻り値:
            (チャンク, スコア) の組のリスト。該当が無ければ空リスト
        """
        # @ は行列の掛け算。ループを書かずに全チャンクとの類似度を一度に計算する。
        # 両辺とも正規化済みなので、内積の値がそのままコサイン類似度になる
        scores = self.matrix @ query_vector
        # argsort は「小さい順に並べたときの添字」を返す。
        # -scores にすると符号が反転して大きい順になる。[:top_k] で上位だけ取る
        order = np.argsort(-scores)[:top_k]
        return [
            (self.chunks[i], float(scores[i])) for i in order if scores[i] >= threshold
        ]


def _normalize(matrix: np.ndarray) -> np.ndarray:
    """各行の長さを 1 に揃える(L2 正規化)。

    これをやっておくと、検索時に内積を計算するだけでコサイン類似度が得られる。
    毎回ベクトルの長さで割る手間が省け、行列積 1 回で全件比較できる。
    """
    # axis=1 で行ごとの長さを計算。keepdims=True は割り算のために形を保つ指定
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    # 長さ 0 のベクトルでゼロ除算しないよう、下限を極小値で押さえる
    return matrix / np.maximum(norms, 1e-12)


async def _embed(texts: list[str]) -> np.ndarray:
    """文字列のリストを埋め込みベクトルの行列に変換する。

    複数をまとめて 1 回の API 呼び出しで送っている。
    1 件ずつ送ると往復回数ぶん遅く、レート制限にも当たりやすい。
    """
    response = await get_client().embeddings.create(
        model=settings.embedding_model, input=texts
    )
    # float32 にしているのはメモリ節約のため。この用途で float64 の精度は要らない
    return np.array([item.embedding for item in response.data], dtype=np.float32)


# モジュール全体で 1 つだけ持つインデックス。最初のリクエストで作って以降は使い回す。
# 先頭の _ は外から触らせない意図。読み書きは get_index() 経由に限る
_index: Index | None = None
# 同時アクセスの制御に使う鍵。async 用なので通常の threading.Lock ではなく asyncio.Lock
_lock = asyncio.Lock()


async def get_index() -> Index:
    """インデックスを遅延構築する。同時リクエストで二重に埋め込まないようロックする。

    「遅延構築」は、起動時ではなく最初に必要になった時点で作ること。
    起動を速く保て、埋め込み API を使わないテストでは一度も作られない。

    if 判定が 2 回あるのは意図的で、「ダブルチェックロッキング」と呼ばれる定石。
      1 回目 … 既にあるならロックを取らずに即返す(毎回ロックすると遅い)
      2 回目 … ロック待ちの間に他のリクエストが作り終えていた場合を弾く
    これが無いと、同時に来た最初の 2 件が両方とも埋め込み API を叩いてしまう。
    """
    # global 宣言は「この関数内の代入をモジュール変数への代入として扱う」指定。
    # 書かないと _index = ... が関数内の別変数を作るだけで終わる
    global _index
    if _index is not None:
        return _index

    # async with は、ブロックを抜けるとき自動でロックを解放する書き方。
    # 途中で例外が出ても解放されるので、ロックの取りっぱなしが起きない
    async with _lock:
        if _index is not None:
            return _index

        chunks = load_chunks(settings.faq_path)
        cache = _cache_path(chunks)

        if cache.is_file():
            matrix = np.load(cache)
            logger.info("埋め込みキャッシュを読み込みました: %s", cache)
        else:
            logger.info("FAQ %d チャンクの埋め込みを取得します", len(chunks))
            matrix = _normalize(await _embed([c.text for c in chunks]))
            # parents=True で親ディレクトリごと、exist_ok=True で既にあってもエラーにしない
            cache.parent.mkdir(parents=True, exist_ok=True)
            np.save(cache, matrix)

        _index = Index(chunks, matrix)
        return _index


async def retrieve(question: str) -> list[tuple[Chunk, float]]:
    """質問に関連する FAQ チャンクを検索する。qa.py から呼ばれる唯一の入口。

    質問側も同じ埋め込みモデル・同じ正規化を通すことが重要。
    片方だけ処理が違うと、ベクトルが同じ空間に乗らず類似度が意味を持たなくなる。
    """
    index = await get_index()
    # _embed はリストを受けてリストを返すので、1 件だけ渡して [0] で取り出す
    query_vector = _normalize(await _embed([question]))[0]
    return index.search(
        query_vector, settings.retrieval_top_k, settings.retrieval_score_threshold
    )
