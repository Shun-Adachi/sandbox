"""人手評価(レビューシート / 一致率)の検査。"""

import csv
import json

import pytest
from conftest import CORRECT_DATA, make_case, make_record

from prompt_eval.review import (
    build_agreement,
    cohen_kappa,
    load_filled_sheet,
    make_sheet,
)


def make_run(tmp_path, n_cases=4):
    run_dir = tmp_path / "20260814-120000"
    run_dir.mkdir()
    cases = [make_case(f"c{i:02d}") for i in range(1, n_cases + 1)]
    records = [
        make_record(c.id, version=v, data=CORRECT_DATA | {"summary": f"{c.id}-{v} の要約"})
        for c in cases
        for v in ("v1", "v2")
    ]
    return run_dir, cases, records


def fill_sheet(run_dir, scores: dict[str, str]):
    """review_id → 記入値 でシートの human_score を埋めて書き戻す。"""
    path = run_dir / "review-sheet.csv"
    with path.open(encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    for row in rows:
        row["human_score"] = scores.get(row["review_id"], "")
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)


def test_シートは版を伏せてシャッフルされ対応表と往復できる(tmp_path):
    run_dir, cases, records = make_run(tmp_path)
    sheet = make_sheet(run_dir, records, cases)

    text = sheet.read_text(encoding="utf-8-sig")
    assert "prompt_version" not in text  # 版はシートに載せない(バイアス防止)
    assert "v1" not in text.replace("v1 の要約", "")  # 要約本文以外に版名が漏れていない

    mapping = json.loads((run_dir / "review-map.json").read_text(encoding="utf-8"))
    assert len(mapping) == len(records)
    # 対応表で全レコードに引き直せる
    keys = {(m["case_id"], m["prompt_version"], m["repeat"]) for m in mapping.values()}
    assert keys == {(r["case_id"], r["prompt_version"], r["repeat"]) for r in records}

    # 同じ run からは同じ並びのシートができる(採点途中で作り直してもズレない)
    assert make_sheet(run_dir, records, cases).read_text(encoding="utf-8-sig") == text


def test_未記入行は読み飛ばし不正値は行番号つきで拒否(tmp_path):
    run_dir, cases, records = make_run(tmp_path)
    make_sheet(run_dir, records, cases)

    fill_sheet(run_dir, {"r001": "3", "r002": ""})
    assert len(load_filled_sheet(run_dir)) == 1

    fill_sheet(run_dir, {"r001": "5"})
    with pytest.raises(ValueError, match="human_score"):
        load_filled_sheet(run_dir)


def make_judge(key, score):
    case_id, version = key
    return {
        "case_id": case_id, "prompt_version": version, "repeat": 0,
        "score": score, "reason": "理由", "summary": f"{case_id}-{version} の要約",
        "judge_version": "v1", "judge_model": "gpt-4o-mini",
    }


def test_一致率と不一致一覧(tmp_path):
    cases = [make_case("c01"), make_case("c02")]
    human = {
        ("c01", "v1", 0): {"score": 3, "comment": ""},
        ("c02", "v1", 0): {"score": 1, "comment": "要件を外している"},
    }
    judge = [make_judge(("c01", "v1"), 3), make_judge(("c02", "v1"), 3)]

    report, console = build_agreement(human, judge, cases)
    assert "50% (1/2)" in report
    assert "c02 / v1" in report  # 不一致として列挙
    assert "人のコメント: 要件を外している" in report
    assert "不一致 1 件" in console


def test_全件未記入はエラー():
    with pytest.raises(ValueError, match="記入されていない"):
        build_agreement({}, [make_judge(("c01", "v1"), 3)], [make_case("c01")])


def test_kappaは偶然一致を差し引く():
    # 完全一致なら 1.0
    assert cohen_kappa([(1, 1), (2, 2), (3, 3)]) == pytest.approx(1.0)
    # 両者が常に同じ 1 点しか付けない場合、偶然一致率 1 で算出不能
    assert cohen_kappa([(3, 3), (3, 3)]) is None
    assert cohen_kappa([]) is None
    # 一致率 50% でも分布が偏っていれば κ は低くなる
    kappa = cohen_kappa([(3, 3), (3, 3), (3, 2), (2, 3)])
    assert kappa is not None and kappa < 0.5