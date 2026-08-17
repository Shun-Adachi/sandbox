"""環境変数と定数。パスの基準はすべてこのテーマのルート(projects/prompt-eval)。"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env")

# 評価対象の API。別ポートで動かした llm-api やステージング環境に向け替えられる
TARGET_URL = os.environ.get("PROMPT_EVAL_TARGET_URL", "http://localhost:8100")
REQUEST_TIMEOUT = float(os.environ.get("PROMPT_EVAL_TIMEOUT", "60"))

DATASET_PATH = PROJECT_ROOT / "src" / "dataset" / "extract-cases.jsonl"
JUDGE_PROMPT_DIR = PROJECT_ROOT / "src" / "prompts" / "judge"
JUDGE_PROMPT_PATH = JUDGE_PROMPT_DIR / "v1.yaml"
RUNS_DIR = PROJECT_ROOT / "runs"

# モデル料金(USD / 100万トークン、(入力, 出力))。2026-08 時点の公表価格。
# レポートのコスト欄はここから概算する。料金改定時はここだけ直せばよい。
PRICES_USD_PER_1M: dict[str, tuple[float, float]] = {
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4o": (2.50, 10.00),
}


def price_for(model: str) -> tuple[float, float] | None:
    """モデル名から料金を引く。応答の model は "gpt-4o-mini-2024-07-18" のように
    日付サフィックスが付くので、前方一致で照合する(長い名前を優先)。"""
    for name in sorted(PRICES_USD_PER_1M, key=len, reverse=True):
        if model.startswith(name):
            return PRICES_USD_PER_1M[name]
    return None
