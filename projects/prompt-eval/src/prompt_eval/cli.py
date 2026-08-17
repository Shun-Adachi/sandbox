"""CLI。run(収集)と score(採点)の 2 コマンド。

  python -m prompt_eval run --versions v1 v2 v3
  python -m prompt_eval score latest --judge

run と score を分けている理由は collect.py / scoring.py の冒頭を参照。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import collect as collect_mod
from .config import DATASET_PATH, RUNS_DIR, TARGET_URL
from .dataset import load_cases
from .judge import judge_run, load_judge_results
from .report import build_report, console_summary
from .review import AGREEMENT_NAME, SHEET_NAME, build_agreement, load_filled_sheet, make_sheet
from .scoring import aggregate, score_all, stability


def _resolve_run_dir(arg: str) -> Path:
    """"latest" なら runs/ 直下の最新 run を選ぶ。run_id はソート可能な時刻形式。"""
    if arg != "latest":
        return Path(arg)
    if not RUNS_DIR.exists():
        sys.exit(f"runs が空です。先に run を実行してください: {RUNS_DIR}")
    candidates = sorted(d for d in RUNS_DIR.iterdir() if (d / "meta.json").exists())
    if not candidates:
        sys.exit(f"runs が空です。先に run を実行してください: {RUNS_DIR}")
    return candidates[-1]


def cmd_run(args: argparse.Namespace) -> None:
    collect_mod.run_eval(
        target_url=args.target,
        versions=args.versions,
        cases_path=Path(args.cases),
        repeat=args.repeat,
        concurrency=args.concurrency,
    )


def _load_run(run_dir: Path):
    meta = json.loads((run_dir / "meta.json").read_text(encoding="utf-8"))
    # 採点はデータセット本体ではなく run に同梱したスナップショットに対して行う。
    # run の後にデータセットを編集しても、この run の採点結果は変わらない
    cases = load_cases(run_dir / "cases.jsonl")
    records = [
        json.loads(line)
        for line in (run_dir / "records.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    return meta, cases, records


def cmd_score(args: argparse.Namespace) -> None:
    run_dir = _resolve_run_dir(args.run_dir)
    meta, cases, records = _load_run(run_dir)

    judge_results = load_judge_results(run_dir)
    if args.judge and judge_results is None:
        from openai import OpenAI  # キー未設定でも run/score 単体は動くよう、ここで import

        from .config import JUDGE_PROMPT_DIR

        print(f"judge({args.judge_prompt}): summary を採点中")
        prompt_path = JUDGE_PROMPT_DIR / f"{args.judge_prompt}.yaml"
        # judge は OpenAI を直接呼ぶ(llm-api を経由しない)ので、リトライはここで持つ。
        # gpt-4o の TPM 制限に 72 連続呼び出しが当たりうるため、既定の 2 回では足りない
        judge_results = judge_run(run_dir, records, cases, OpenAI(max_retries=6), prompt_path)
    elif args.judge:
        print("judge: 保存済みの judge.jsonl を再利用(採点し直すなら退避か削除してから)")

    scored = score_all(records, cases)
    agg = aggregate(scored, records, cases)
    report = build_report(
        meta, cases, records, scored, agg, stability(scored), judge_results
    )
    report_path = run_dir / "report.md"
    report_path.write_text(report, encoding="utf-8")

    print(console_summary(meta, agg, judge_results))
    print(f"レポート: {report_path}")


def cmd_review_sheet(args: argparse.Namespace) -> None:
    run_dir = _resolve_run_dir(args.run_dir)
    _, cases, records = _load_run(run_dir)
    sheet = make_sheet(run_dir, records, cases)
    print(f"レビューシート: {sheet}")
    print("human_score 列を 1〜3 で記入したら `prompt-eval agreement` で judge と突き合わせます")
    print("(1=使えない / 2=要手直し / 3=このまま使える。judge と同じ基準)")


def cmd_agreement(args: argparse.Namespace) -> None:
    run_dir = _resolve_run_dir(args.run_dir)
    _, cases, _ = _load_run(run_dir)
    if not (run_dir / SHEET_NAME).exists():
        sys.exit(f"{SHEET_NAME} が無い。先に review-sheet を実行してください")
    judge_results = load_judge_results(run_dir)
    if judge_results is None:
        sys.exit("judge.jsonl が無い。先に score --judge を実行してください")

    report, console = build_agreement(load_filled_sheet(run_dir), judge_results, cases)
    path = run_dir / AGREEMENT_NAME
    path.write_text(report, encoding="utf-8")
    print(console)
    print(f"レポート: {path}")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="prompt_eval")
    sub = parser.add_subparsers(dest="command", required=True)

    p_run = sub.add_parser("run", help="評価対象 API を呼び、生の応答を runs/ に保存する")
    p_run.add_argument("--versions", nargs="+", default=["v1", "v2", "v3"])
    p_run.add_argument("--cases", default=str(DATASET_PATH))
    p_run.add_argument("--target", default=TARGET_URL)
    p_run.add_argument("--repeat", type=int, default=1)
    p_run.add_argument("--concurrency", type=int, default=4)
    p_run.set_defaults(func=cmd_run)

    p_score = sub.add_parser("score", help="保存済みの run を採点してレポートを書く")
    p_score.add_argument("run_dir", help="run ディレクトリのパス、または latest")
    p_score.add_argument("--judge", action="store_true", help="summary を LLM-as-judge で採点する")
    p_score.add_argument(
        "--judge-prompt", default="v1", help="judge プロンプトの版(src/prompts/judge/ 内)"
    )
    p_score.set_defaults(func=cmd_score)

    p_sheet = sub.add_parser(
        "review-sheet", help="人手評価用の CSV を書き出す(版は伏せてシャッフル)"
    )
    p_sheet.add_argument("run_dir", help="run ディレクトリのパス、または latest")
    p_sheet.set_defaults(func=cmd_review_sheet)

    p_agree = sub.add_parser(
        "agreement", help="記入済みシートを読み、人と judge の一致率を出す"
    )
    p_agree.add_argument("run_dir", help="run ディレクトリのパス、または latest")
    p_agree.set_defaults(func=cmd_agreement)

    args = parser.parse_args(argv)
    args.func(args)
