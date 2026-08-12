"""copilot-compare ハーネスの共通部品。"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TASKS = ROOT / "tasks"
RUNS = ROOT / "runs"

# runs/<エージェント>/<課題>/ の中身。
#   work/     … エージェントに渡す唯一のパス。実装以外を置かない
#   phase_a/  … Phase A 終了時のスナップショット。Phase B で上書きされても点が残る
#   run.json  … 計測メタデータ
# work/ の外に記録を置くのは、採点側が書いた内容がエージェントの context に
# 入るのを防ぐため。run.json を work/ の中に置いていたとき、そこに書いた
# 失敗原因の分析が Phase B の開始前にエージェントへ渡ってしまい、
# ヒント段階の計測が 1 件無効になった。
WORK = "work"
PHASE_A = "phase_a"


def list_tasks() -> list[str]:
    """課題名を昇順で返す。"""
    return sorted(p.name for p in TASKS.iterdir() if (p / "spec.md").is_file())


def task_dir(task: str) -> Path:
    return TASKS / task


def run_dir(agent: str, task: str) -> Path:
    return RUNS / agent / task


def work_dir(directory: Path) -> Path:
    return directory / WORK


def list_runs() -> list[tuple[str, str, Path]]:
    """(エージェント名, 課題名, ディレクトリ) を昇順で返す。"""
    if not RUNS.is_dir():
        return []
    tasks = set(list_tasks())
    found = []
    for agent_dir in sorted(p for p in RUNS.iterdir() if p.is_dir()):
        for d in sorted(p for p in agent_dir.iterdir() if p.is_dir()):
            if d.name in tasks and work_dir(d).is_dir():
                found.append((agent_dir.name, d.name, d))
    return found


def read_meta(directory: Path) -> dict:
    """run.json を読み、phase_a / phase_b のキーが必ず在る形に整えて返す。"""
    path = directory / "run.json"
    raw: dict = {}
    if path.is_file():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            raw = {}

    phase_a = dict(raw.get("phase_a") or {})
    # Phase B 導入前の run.json は minutes / turns が直下にあった。
    for key in ("minutes", "turns"):
        if key in raw and key not in phase_a:
            phase_a[key] = raw[key]

    return {
        **raw,
        "phase_a": {"minutes": phase_a.get("minutes"), "turns": phase_a.get("turns")},
        "phase_b": {
            "minutes": (raw.get("phase_b") or {}).get("minutes"),
            "turns": (raw.get("phase_b") or {}).get("turns"),
            "hint_level": (raw.get("phase_b") or {}).get("hint_level"),
        },
    }


def phase_a_dir(directory: Path) -> Path:
    return directory / PHASE_A


def same_solution(left: Path, right: Path) -> bool:
    """2 つのディレクトリの .py が同一かどうか。Phase B が実装に触ったかの判定に使う。"""

    def snapshot(d: Path) -> dict[str, str]:
        return {p.name: p.read_text(encoding="utf-8") for p in d.glob("*.py")}

    return snapshot(left) == snapshot(right)


def freeze_phase_a(directory: Path) -> bool:
    """work/ の現状を Phase A として凍結する。既に凍結済みなら何もしない。"""
    dest = phase_a_dir(directory)
    if dest.exists():
        return False
    dest.mkdir()
    for path in sorted(work_dir(directory).glob("*.py")):
        shutil.copy2(path, dest / path.name)
    return True


def code_lines(directory: Path) -> int:
    """空行と行頭コメントを除いた .py の行数。実装量のごく粗い目安。"""
    total = 0
    for path in sorted(directory.glob("*.py")):
        for line in path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith("#"):
                total += 1
    return total
