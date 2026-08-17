"""CLI の score コマンドを、作り置きの run ディレクトリに対して通しで動かす。

ネットワークも OpenAI も使わない。run コマンド側の中身(collect)は
test_collect.py で検査済みなので、ここでは score → report.md の配線を見る。
"""

import json

from conftest import CORRECT_DATA, make_case, make_record

from prompt_eval.cli import main
from prompt_eval.dataset import dump_case_index


def make_run_dir(tmp_path):
    run_dir = tmp_path / "20260814-120000"
    run_dir.mkdir()
    cases = [make_case("c01"), make_case("c02", urgency="中")]
    records = [
        make_record("c01", version="v1"),
        make_record("c02", version="v1", data=CORRECT_DATA | {"urgency": "高"}),
    ]
    meta = {
        "run_id": "20260814-120000",
        "created_at": "2026-08-14T12:00:00+09:00",
        "target_url": "http://testserver",
        "versions": ["v1"],
        "repeat": 1,
        "cases_file": "extract-cases.jsonl",
        "cases_sha256": "abc",
        "num_cases": 2,
    }
    (run_dir / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    (run_dir / "cases.jsonl").write_text(dump_case_index(cases), encoding="utf-8")
    (run_dir / "records.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in records) + "\n",
        encoding="utf-8",
    )
    return run_dir


def test_scoreがレポートを書く(tmp_path, capsys):
    run_dir = make_run_dir(tmp_path)
    main(["score", str(run_dir)])

    report = (run_dir / "report.md").read_text(encoding="utf-8")
    assert "| v1 | 50% (1/2) |" in report

    out = capsys.readouterr().out
    assert "v1: 全項目一致 50% (1/2)" in out
    assert "report.md" in out
