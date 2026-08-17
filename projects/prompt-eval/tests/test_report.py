"""レポート生成の検査。数値の埋め込みと表の整合だけを見る(体裁は問わない)。"""

from conftest import CORRECT_DATA, make_case, make_record

from prompt_eval.report import build_report, console_summary
from prompt_eval.scoring import aggregate, score_all, stability


def build_fixture():
    cases = [make_case("c01", tags=["基本"]), make_case("c02", tags=["緊急度境界"], urgency="中")]
    records = [
        make_record("c01", version="v1"),
        make_record("c02", version="v1", data=CORRECT_DATA | {"urgency": "高"}),
        make_record("c01", version="v2"),
        make_record("c02", version="v2", data=CORRECT_DATA | {"urgency": "中"}),
    ]
    meta = {
        "run_id": "20260814-000000",
        "created_at": "2026-08-14T00:00:00+09:00",
        "target_url": "http://testserver",
        "versions": ["v1", "v2"],
        "repeat": 1,
        "cases_file": "cases.jsonl",
        "cases_sha256": "abc",
        "num_cases": 2,
    }
    scored = score_all(records, cases)
    return meta, cases, records, scored, aggregate(scored, records, cases)


def test_版ごとの正解率が載る():
    meta, cases, records, scored, agg = build_fixture()
    report = build_report(meta, cases, records, scored, agg, stability(scored))
    assert "| v1 | 50% (1/2) |" in report
    assert "| v2 | 100% (2/2) |" in report


def test_誤答はケースと項目つきで載る():
    meta, cases, records, scored, agg = build_fixture()
    report = build_report(meta, cases, records, scored, agg, stability(scored))
    # v1 は c02 の urgency を「中→高」と誤答している
    assert "| c02 | urgency | 中 | 高 |" in report
    assert "× urgency" in report


def test_judge結果があれば平均と低評価が載る():
    meta, cases, records, scored, agg = build_fixture()
    judge = [
        {"case_id": "c01", "prompt_version": "v1", "repeat": 0, "score": 3,
         "reason": "問題なし", "summary": "s", "judge_version": "v1", "judge_model": "gpt-4o-mini"},
        {"case_id": "c02", "prompt_version": "v1", "repeat": 0, "score": 1,
         "reason": "要件を取り違えている", "summary": "誤った要約", "judge_version": "v1",
         "judge_model": "gpt-4o-mini"},
    ]
    report = build_report(meta, cases, records, scored, agg, stability(scored), judge)
    assert "judge平均" in report
    assert "2.00" in report  # (3+1)/2
    assert "要件を取り違えている" in report


def test_コンソール要約は1版1行():
    meta, cases, records, scored, agg = build_fixture()
    out = console_summary(meta, agg, None)
    assert len(out.splitlines()) == 2
    assert out.splitlines()[0].startswith("v1: ")
