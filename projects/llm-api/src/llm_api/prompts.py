"""バージョン付きプロンプトのローダー。基盤層。

プロンプトはコードに埋め込まず `src/prompts/<name>/<version>.yaml` に置く。
API はリクエストごとに版を選べる。prompt-eval テーマから同じローダーを使い、
版 × 評価ケースのマトリクスを回せるようにするための構造。

外に出しているのは system / user_template / model / temperature の 4 つだけで、
「検索するか」「構造化出力を強制するか」といった処理の骨格はコード側にある。
プロンプトが担当するのは LLM への言い方だけ、という切り分け。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml

from .config import settings

logger = logging.getLogger(__name__)


class PromptNotFoundError(LookupError):
    """指定された名前 / 版のプロンプトが無い。

    組み込みの LookupError を継承しているのは意味的な分類のため
    (「探したが見つからない」系の例外)。
    errors.py がこの型を見て HTTP 400 に変換する。
    """


@dataclass(frozen=True)
class Prompt:
    """YAML 1 ファイルをそのまま表したもの。読み取り専用。"""

    name: str  # 用途。"extract" か "qa"
    version: str  # 版。"v1" など
    model: str  # このプロンプトで使うモデル。版ごとに変えられる
    temperature: float  # 0 に近いほど毎回同じ結果になる
    system: str  # 役割と指示
    user_template: str  # 処理対象の埋め込み方
    # コンテキストが引けなかったときの定型文(qa のみ)。
    # コード側にも同じ文言を持つと二重管理になるのでプロンプト側を唯一の出所にする。
    # `= None` があるので、この項目だけ YAML に無くてもよい
    fallback_answer: str | None = None

    def render_system(self, **kwargs: object) -> str:
        """system 文の `{変数}` を埋める。qa では context を渡す。"""
        return self._render(self.system, **kwargs)

    def render_user(self, **kwargs: object) -> str:
        """user 文の `{変数}` を埋める。extract では text、qa では question を渡す。"""
        return self._render(self.user_template, **kwargs)

    def _render(self, template: str, **kwargs: object) -> str:
        """`{変数名}` を埋める。リテラルの波括弧は YAML 側で `{{` `}}` と書く。

        `**kwargs` は「名前付き引数を何個でも受け取って辞書にまとめる」書き方。
        render_user(text="...") と呼ぶと kwargs は {"text": "..."} になる。

        テンプレートが要求する変数が足りないと str.format が KeyError を投げる。
        そのままだと「'context'」としか出ずどのプロンプトの話か分からないので、
        名前と版を添えて投げ直している。
        """
        try:
            return template.format(**kwargs)
        except KeyError as exc:
            # `raise ... from exc` は元の例外を原因として保持する書き方。
            # トレースバックに両方が出るので、原因を追える
            raise KeyError(
                f"プロンプト {self.name}/{self.version} の描画に必要な変数がありません: {exc}"
            ) from exc


def _prompt_path(name: str, version: str) -> Path:
    """名前と版から YAML のパスを組み立てる。"""
    # 版名は API 経由で任意の文字列が来る。"../../etc/passwd" のような値で
    # プロンプトディレクトリの外を読まれないよう、区切り文字を弾く
    # (パストラバーサル対策。ユーザー入力をパスに使うときの定石)
    if "/" in version or "\\" in version or version.startswith("."):
        # !r は repr 形式での埋め込み。値が引用符付きで出るので空文字なども判別できる
        raise PromptNotFoundError(f"不正な版名です: {version!r}")
    # Path 同士は / で連結できる。OS ごとの区切り文字を気にしなくてよい
    return settings.prompts_dir / name / f"{version}.yaml"


# 同じ (name, version) で呼ばれたら前回の結果を返す。
# リクエストのたびにファイルを読み直さないための最適化。
# 副作用が無く、同じ入力に同じ出力を返す関数だから安全に使える。
# 注意: YAML を編集したらサーバーを再起動しないと反映されない
@lru_cache(maxsize=None)
def load_prompt(name: str, version: str) -> Prompt:
    """プロンプト YAML を読み込んで Prompt にする。

    例外:
        PromptNotFoundError: ファイルが無い、または版名が不正
        ValueError: 必須キーが欠けている
    """
    path = _prompt_path(name, version)
    if not path.is_file():
        # このメッセージは 400 応答の detail としてクライアントに返る(errors.py)。
        # サーバーのディレクトリ構成を外に見せないよう、パスはログにだけ残し、
        # 呼び出し側には「何を指定すべきか」だけを伝える
        logger.warning("プロンプトが見つかりません: %s", path)
        raise PromptNotFoundError(
            f"プロンプト {name}/{version} が見つかりません。"
            f" 利用可能な版: {', '.join(available_versions(name)) or 'なし'}"
        )

    # safe_load は YAML の中に書かれた任意の Python オブジェクトを生成しない安全な版。
    # 単に load を使うと細工されたファイルで任意コードを実行されうるので、常にこちらを使う。
    # 空ファイルだと None が返るので `or {}` で辞書に寄せておく
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}

    # 集合の引き算で「必須キーのうち存在しないもの」を求める。
    # raw.keys() も集合として扱えるのでそのまま引ける
    missing = {"system", "user_template"} - raw.keys()
    if missing:
        # 起動後に初めて気付くと厄介なので、読み込み時点で落とす
        raise ValueError(f"{path} に必須キーがありません: {sorted(missing)}")

    return Prompt(
        name=name,
        version=version,
        # get(キー, 既定値) は、キーが無ければ既定値を返す。
        # model と temperature は YAML で省略でき、その場合は設定の既定値になる
        model=raw.get("model", settings.chat_model),
        temperature=float(raw.get("temperature", 0.0)),
        # こちらは必須なので [] で取る。無ければ上の検査で既に落ちている
        system=raw["system"],
        user_template=raw["user_template"],
        fallback_answer=raw.get("fallback_answer"),
    )


def available_versions(name: str) -> list[str]:
    """その用途で使える版の一覧を返す。無ければ空リスト。

    ファイル一覧から動的に作っているので、YAML を 1 つ置けば
    コードを変えずに新しい版が使えるようになる。
    """
    directory = settings.prompts_dir / name
    if not directory.is_dir():
        return []
    # glob はパターンに一致するファイルを列挙する。stem は拡張子を除いたファイル名
    # ("v1.yaml" → "v1")。順序が環境依存にならないよう sorted で固定する
    return sorted(p.stem for p in directory.glob("*.yaml"))
