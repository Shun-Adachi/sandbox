"""評価データセットの読み込みと検証。

正解ラベル(expected)の正しさはハーネスでは保証できないので、
せめて「形の壊れたケースが混ざって集計を狂わせる」ことだけは
読み込み時点で防ぐ。ラベルの列挙値は評価対象 API のスキーマ
(llm-api の Category / Urgency)と同じ集合に閉じている。
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field


class Expected(BaseModel):
    """1 ケースの正解ラベル。

    name / company の正解が空文字なのは「不明」ではなく
    「本文から判断できないので空が正しい(推測したら誤り)」という意味。
    summary は自由文なので完全一致の正解を持たず、
    「これが入っていなければ要件を外している」というキーワードだけを持つ。
    """

    name: str
    company: str
    category: Literal["質問", "不具合", "解約", "その他"]
    urgency: Literal["高", "中", "低"]
    summary_keywords: list[str] = Field(min_length=1)


class Case(BaseModel):
    id: str = Field(min_length=1)
    text: str = Field(min_length=1)
    expected: Expected
    # タグは「どの類型のケースで落ちているか」をレポートで切るための軸
    tags: list[str] = Field(default_factory=list)
    # ラベルの根拠。ラベルに異議が出たときに議論の起点になるので書き残す
    note: str = ""


def load_cases(path: Path) -> list[Case]:
    cases: list[Case] = []
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            cases.append(Case.model_validate_json(line))
        except ValueError as exc:
            raise ValueError(f"{path.name}:{lineno} 行目が不正: {exc}") from exc

    ids = [c.id for c in cases]
    dup = {i for i in ids if ids.count(i) > 1}
    if dup:
        raise ValueError(f"case id が重複している: {sorted(dup)}")
    return cases


def dataset_sha256(path: Path) -> str:
    """データセットの指紋。run の meta.json に残し、
    「どのデータで測ったスコアか」を後から突き合わせられるようにする。"""
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def dump_case_index(cases: list[Case]) -> str:
    """run ディレクトリに同梱するケース一覧(JSONL)。
    データセット本体が後で書き換わっても、run 側だけで採点を再現できるようにする。"""
    return "\n".join(json.dumps(c.model_dump(), ensure_ascii=False) for c in cases) + "\n"
