"""データセットの読み込みと、同梱している実データの健全性の検査。"""

import pytest

from prompt_eval.config import DATASET_PATH
from prompt_eval.dataset import dataset_sha256, dump_case_index, load_cases


def test_同梱データセットが読み込める():
    cases = load_cases(DATASET_PATH)
    assert len(cases) >= 20
    assert len({c.id for c in cases}) == len(cases)


def test_同梱データセットは全ケースにタグと根拠がある():
    # タグはレポートの分析軸、note はラベルの根拠。どちらも無いケースは
    # 「なぜこの正解なのか」を後から説明できなくなるので、データ側の規約として検査する
    for c in load_cases(DATASET_PATH):
        assert c.tags, f"{c.id}: tags が空"
        assert c.note, f"{c.id}: note が空"


def test_壊れた行は行番号つきで拒否される(tmp_path):
    p = tmp_path / "bad.jsonl"
    p.write_text(
        '{"id": "a", "text": "t", "expected": {"name": "", "company": "", '
        '"category": "質問", "urgency": "低", "summary_keywords": ["x"]}}\n'
        '{"id": "b", "text": "t", "expected": {"category": "存在しない種別"}}\n',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="2 行目"):
        load_cases(p)


def test_id重複は拒否される(tmp_path):
    line = (
        '{"id": "dup", "text": "t", "expected": {"name": "", "company": "", '
        '"category": "質問", "urgency": "低", "summary_keywords": ["x"]}}\n'
    )
    p = tmp_path / "dup.jsonl"
    p.write_text(line + line, encoding="utf-8")
    with pytest.raises(ValueError, match="dup"):
        load_cases(p)


def test_スナップショットを再読み込みできる(tmp_path):
    cases = load_cases(DATASET_PATH)
    p = tmp_path / "cases.jsonl"
    p.write_text(dump_case_index(cases), encoding="utf-8")
    assert load_cases(p) == cases


def test_sha256は内容が変わると変わる(tmp_path):
    a = tmp_path / "a.jsonl"
    b = tmp_path / "b.jsonl"
    a.write_text("x", encoding="utf-8")
    b.write_text("y", encoding="utf-8")
    assert dataset_sha256(a) != dataset_sha256(b)
