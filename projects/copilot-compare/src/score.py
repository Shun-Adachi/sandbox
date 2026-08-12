#!/usr/bin/env python3
"""runs/ 配下の成果物を採点し、比較表を出す。

    python src/score.py                 # runs/ 配下を全部
    python src/score.py --reference     # 参照実装も並べる(ハーネスの自己検証)
    python src/score.py --task t1-sse-parser
    python src/score.py --freeze        # 採点後、現在の実装を Phase A として凍結

Phase A(仕様書だけの一発勝負)を採点したら --freeze で凍結する。
以降は phase_a/ が Phase A の点、直下が Phase B(対話で直したあと)の点になる。

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

from bench import (
    ROOT,
    code_lines,
    freeze_phase_a,
    list_runs,
    list_tasks,
    phase_a_dir,
    read_meta,
    same_solution,
    task_dir,
    work_dir,
)

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


def collect(
    tasks: list[str], agents: set[str] | None, reference: bool, freeze: bool
) -> list[dict]:
    totals = {t: task_total(t) for t in tasks}
    targets: list[tuple[str, str, Path, bool]] = []

    if reference:
        targets += [("(参照実装)", t, task_dir(t) / "reference", False) for t in tasks]
    targets += [
        (agent, task, d, True)
        for agent, task, d in list_runs()
        if task in tasks and (agents is None or agent in agents)
    ]

    rows = []
    for agent, task, directory, is_run in targets:
        print(f"採点中: {agent} / {task}", file=sys.stderr)
        meta = read_meta(directory)
        frozen = phase_a_dir(directory)
        # 参照実装はディレクトリ直下、エージェントの成果物は work/ の中。
        current = work_dir(directory) if is_run else directory

        # phase_a/ が在れば、そちらが Phase A・work/ が Phase B。
        # 無ければ work/ がまだ Phase A で、Phase B は未実施。
        if frozen.is_dir():
            phase_a = grade(task, frozen, totals[task])
            # 凍結直後は実装が phase_a/ と同一。Phase B が実際に手を入れたか、
            # あるいは run.json に往復数が記録されるまでは「未実施」として扱う。
            started = meta["phase_b"]["turns"] is not None or not same_solution(
                current, frozen
            )
            phase_b = grade(task, current, totals[task]) if started else None
        else:
            phase_a = grade(task, current, totals[task])
            phase_b = None
            if is_run and freeze and freeze_phase_a(directory):
                print(f"  → Phase A として凍結: {frozen.relative_to(ROOT)}", file=sys.stderr)

        rows.append(
            {
                "agent": agent,
                "task": task,
                "phase_a": phase_a,
                "phase_b": phase_b,
                "lines": code_lines(current),
                "model": meta.get("model") or "",
                "mode": meta.get("mode") or "",
                "a_minutes": meta["phase_a"]["minutes"],
                "a_turns": meta["phase_a"]["turns"],
                "b_minutes": meta["phase_b"]["minutes"],
                "b_turns": meta["phase_b"]["turns"],
                "hint_level": meta["phase_b"]["hint_level"],
                "notes": meta.get("notes") or "",
            }
        )
    return rows


MARK = {"ok": "✅", "fail": "❌", "collect-error": "💥", "timeout": "⏱"}


def _score(phase: dict | None) -> str:
    if phase is None:
        return "-"
    return f"{MARK.get(phase['status'], '?')} {phase['passed']}/{phase['total']}"


def _pair(a, b) -> str:
    """Phase A / Phase B の実測値を "1 / +2" の形にまとめる。"""
    left = "-" if a is None else str(a)
    return left if b is None else f"{left} / +{b}"


def to_markdown(rows: list[dict]) -> str:
    out = [
        "# 採点結果",
        "",
        "`python src/score.py --reference` の出力。手で編集しない。",
        "",
        "- **Phase A** … 仕様書だけを渡した一発勝負",
        "- **Phase B** … 段階的にヒントを出して対話で直したあと",
        "- **ヒント** … Phase B で満点に届いたときの情報量(L1 件数のみ / L2 テスト名 / L3 pytest 出力)",
        "- 往復・分は `A / +B` の形。手入力の自己申告値",
        "",
        "| エージェント | 課題 | Phase A | Phase B | ヒント | 往復 | 分 | 行数 | モデル | 備考 |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for r in rows:
        hint = r["hint_level"]
        out.append(
            "| {agent} | {task} | {a} | {b} | {hint} | {turns} | {minutes} | {lines} "
            "| {model} | {notes} |".format(
                agent=r["agent"],
                task=r["task"],
                a=_score(r["phase_a"]),
                b=_score(r["phase_b"]),
                hint="-" if hint is None else f"L{hint}",
                turns=_pair(r["a_turns"], r["b_turns"]),
                minutes=_pair(r["a_minutes"], r["b_minutes"]),
                lines=r["lines"],
                model=r["model"],
                notes=r["notes"],
            )
        )

    agents = sorted({r["agent"] for r in rows})
    if len(agents) > 1:
        out += [
            "",
            "## エージェント別の合計",
            "",
            "| エージェント | Phase A | Phase B | 行数合計 |",
            "| --- | --- | --- | --- |",
        ]
        for a in agents:
            mine = [r for r in rows if r["agent"] == a]
            total = sum(r["phase_a"]["total"] for r in mine)
            got_a = sum(r["phase_a"]["passed"] for r in mine)
            # Phase B 未実施の課題は Phase A の点をそのまま持ち上げる。
            got_b = sum((r["phase_b"] or r["phase_a"])["passed"] for r in mine)
            done_b = any(r["phase_b"] for r in mine)
            out.append(
                f"| {a} | {got_a}/{total} | {f'{got_b}/{total}' if done_b else '-'} "
                f"| {sum(r['lines'] for r in mine)} |"
            )
    return "\n".join(out) + "\n"


def main() -> int:
    tasks = list_tasks()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task", action="append", choices=tasks, help="課題を絞る(複数可)")
    parser.add_argument("--agent", action="append", help="エージェントを絞る(複数可)")
    parser.add_argument("--reference", action="store_true", help="参照実装も採点して並べる")
    parser.add_argument(
        "--freeze",
        action="store_true",
        help="採点後、現在の実装を Phase A として phase_a/ に凍結する",
    )
    args = parser.parse_args()

    rows = collect(
        args.task or tasks,
        set(args.agent) if args.agent else None,
        args.reference,
        args.freeze,
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
