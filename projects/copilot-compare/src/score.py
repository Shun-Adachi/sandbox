#!/usr/bin/env python3
"""runs/ 配下の成果物を採点し、比較表を出す。

    python src/score.py                 # runs/ 配下を全部
    python src/score.py --reference     # 参照実装も並べる(ハーネスの自己検証)
    python src/score.py --task t1-sse-parser

結果を docs/results.md と docs/results.json に書き出す。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

from bench import ROOT, code_lines, list_runs, list_tasks, read_meta, task_dir

DOCS = ROOT / "docs"
TIMEOUT_SEC = 300


def _pytest(args: list[str], solution: Path) -> subprocess.CompletedProcess:
    env = dict(os.environ, PYTHONPATH=str(solution), PYTHONDONTWRITEBYTECODE="1")
    return subprocess.run(
        [sys.executable, "-m", "pytest", *args, "-p", "no:cacheprovider"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=TIMEOUT_SEC,
    )


def task_total(task: str) -> int:
    """課題のテスト総数。参照実装を通して数える。"""
    tests = task_dir(task) / "tests"
    proc = _pytest([str(tests), "--collect-only", "-q"], task_dir(task) / "reference")
    for line in reversed(proc.stdout.splitlines()):
        found = re.match(r"(\d+) tests? collected", line.strip())
        if found:
            return int(found.group(1))
    raise RuntimeError(f"{task}: テスト数を数えられませんでした\n{proc.stdout}{proc.stderr}")


def grade(task: str, solution: Path, total: int) -> dict:
    """1 つの成果物にテストを当てる。"""
    tests = task_dir(task) / "tests"
    with tempfile.TemporaryDirectory() as tmp:
        report = Path(tmp) / "report.xml"
        try:
            proc = _pytest([str(tests), f"--junit-xml={report}", "-q"], solution)
        except subprocess.TimeoutExpired:
            return {"passed": 0, "total": total, "status": "timeout"}

        if not report.is_file():
            return {"passed": 0, "total": total, "status": "collect-error"}

        suite = ET.parse(report).getroot().find("testsuite")
        if suite is None:
            return {"passed": 0, "total": total, "status": "collect-error"}

        ran = int(suite.get("tests", 0))
        bad = sum(int(suite.get(k, 0)) for k in ("failures", "errors", "skipped"))
        passed = max(0, ran - bad)

    status = "ok" if passed == total else "fail"
    if ran == 0 or proc.returncode not in (0, 1):
        status = "collect-error"
    return {"passed": passed, "total": total, "status": status}


def collect(tasks: list[str], agents: set[str] | None, reference: bool) -> list[dict]:
    totals = {t: task_total(t) for t in tasks}
    targets: list[tuple[str, str, Path]] = []

    if reference:
        targets += [("(参照実装)", t, task_dir(t) / "reference") for t in tasks]
    targets += [
        (agent, task, d)
        for agent, task, d in list_runs()
        if task in tasks and (agents is None or agent in agents)
    ]

    rows = []
    for agent, task, directory in targets:
        print(f"採点中: {agent} / {task}", file=sys.stderr)
        meta = read_meta(directory)
        rows.append(
            {
                "agent": agent,
                "task": task,
                **grade(task, directory, totals[task]),
                "lines": code_lines(directory),
                "model": meta.get("model") or "",
                "mode": meta.get("mode") or "",
                "minutes": meta.get("minutes"),
                "turns": meta.get("turns"),
                "notes": meta.get("notes") or "",
            }
        )
    return rows


MARK = {"ok": "✅", "fail": "❌", "collect-error": "💥", "timeout": "⏱"}


def to_markdown(rows: list[dict]) -> str:
    out = [
        "# 採点結果",
        "",
        "`python src/score.py --reference` の出力。手で編集しない。",
        "",
        "| エージェント | 課題 | 合格 | 行数 | モデル | モード | 分 | 往復 | 備考 |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for r in rows:
        cells = dict(
            r,
            mark=MARK.get(r["status"], "?"),
            minutes="-" if r["minutes"] is None else r["minutes"],
            turns="-" if r["turns"] is None else r["turns"],
        )
        out.append(
            "| {agent} | {task} | {mark} {passed}/{total} | {lines} | {model} | {mode} "
            "| {minutes} | {turns} | {notes} |".format(**cells)
        )

    agents = sorted({r["agent"] for r in rows})
    if len(agents) > 1:
        out += ["", "## エージェント別の合計", "", "| エージェント | 合格 | 行数合計 |", "| --- | --- | --- |"]
        for a in agents:
            mine = [r for r in rows if r["agent"] == a]
            out.append(
                f"| {a} | {sum(r['passed'] for r in mine)}/{sum(r['total'] for r in mine)} "
                f"| {sum(r['lines'] for r in mine)} |"
            )
    return "\n".join(out) + "\n"


def main() -> int:
    tasks = list_tasks()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task", action="append", choices=tasks, help="課題を絞る(複数可)")
    parser.add_argument("--agent", action="append", help="エージェントを絞る(複数可)")
    parser.add_argument("--reference", action="store_true", help="参照実装も採点して並べる")
    args = parser.parse_args()

    rows = collect(
        args.task or tasks,
        set(args.agent) if args.agent else None,
        args.reference,
    )
    if not rows:
        print("採点対象がありません。src/new_run.py で作業ディレクトリを作ってください。", file=sys.stderr)
        return 1

    markdown = to_markdown(rows)
    DOCS.mkdir(exist_ok=True)
    (DOCS / "results.md").write_text(markdown, encoding="utf-8")
    (DOCS / "results.json").write_text(
        json.dumps(rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print()
    print(markdown)
    print(f"→ {(DOCS / 'results.md').relative_to(ROOT)} / {(DOCS / 'results.json').relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
