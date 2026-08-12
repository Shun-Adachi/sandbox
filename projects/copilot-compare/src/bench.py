"""copilot-compare ハーネスの共通部品。"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TASKS = ROOT / "tasks"
RUNS = ROOT / "runs"


def list_tasks() -> list[str]:
    """課題名を昇順で返す。"""
    return sorted(p.name for p in TASKS.iterdir() if (p / "spec.md").is_file())


def task_dir(task: str) -> Path:
    return TASKS / task


def run_dir(agent: str, task: str) -> Path:
    return RUNS / agent / task


def list_runs() -> list[tuple[str, str, Path]]:
    """(エージェント名, 課題名, ディレクトリ) を昇順で返す。"""
    if not RUNS.is_dir():
        return []
    tasks = set(list_tasks())
    found = []
    for agent_dir in sorted(p for p in RUNS.iterdir() if p.is_dir()):
        for d in sorted(p for p in agent_dir.iterdir() if p.is_dir()):
            if d.name in tasks:
                found.append((agent_dir.name, d.name, d))
    return found


def read_meta(directory: Path) -> dict:
    path = directory / "run.json"
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def code_lines(directory: Path) -> int:
    """空行と行頭コメントを除いた .py の行数。実装量のごく粗い目安。"""
    total = 0
    for path in sorted(directory.glob("*.py")):
        for line in path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith("#"):
                total += 1
    return total
