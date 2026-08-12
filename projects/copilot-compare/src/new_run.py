#!/usr/bin/env python3
"""エージェント 1 回分の作業ディレクトリを用意する。

    python src/new_run.py --agent copilot --task t1-sse-parser

`runs/<agent>/<task>/` に starter とメタデータの雛形を置く。
エージェントにはこのディレクトリと `tasks/<task>/spec.md` だけを渡す。
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys

from bench import ROOT, list_tasks, run_dir, task_dir, work_dir

META_TEMPLATE = {
    "agent": "",
    "model": "",
    "mode": "",
    "used_spec_only": True,
    "phase_a": {"minutes": None, "turns": None},
    "phase_b": {"minutes": None, "turns": None, "hint_level": None},
    "notes": "",
}


def main() -> int:
    tasks = list_tasks()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--agent", required=True, help="エージェント名 (例: copilot, claude-code)")
    parser.add_argument("--task", required=True, choices=tasks, help="課題名")
    parser.add_argument("--force", action="store_true", help="既存の作業ディレクトリを作り直す")
    args = parser.parse_args()

    dest = run_dir(args.agent, args.task)
    if dest.exists():
        if not args.force:
            print(f"既に存在します: {dest.relative_to(ROOT)}", file=sys.stderr)
            print("作り直すなら --force を付けてください。", file=sys.stderr)
            return 1
        shutil.rmtree(dest)

    starter = task_dir(args.task) / "starter"
    shutil.copytree(starter, work_dir(dest))

    meta = dict(META_TEMPLATE, agent=args.agent)
    (dest / "run.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    rel = dest.relative_to(ROOT)
    print(f"作成しました: {rel}")
    print()
    print("エージェントに渡すもの:")
    print(f"  - 仕様      tasks/{args.task}/spec.md")
    print(f"  - 作業場所  {rel}/work")
    print("  - 進め方    PROTOCOL.md")
    print()
    print(f"run.json と phase_a/ は work/ の外に置いてある。")
    print(f"採点側が書いた内容がエージェントの context に入らないようにするため、")
    print(f"エージェントには {rel}/work だけを渡すこと。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
