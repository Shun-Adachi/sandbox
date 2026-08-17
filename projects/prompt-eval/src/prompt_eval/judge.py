"""LLM-as-judge 段: summary(自由文)の質を LLM に採点させる。

完全一致・ルールの 2 層で判定できない「要件を取り違えていないか」だけを
judge に任せる。機械的に判定できるもの(文字数など)まで judge に聞くと、
判定がブレる上に、何が原因で減点されたのか追えなくなるため。

judge の結果は run ディレクトリの judge.jsonl に追記保存する。
collect と同じく「課金の発生する処理の結果は必ずディスクに残す」方針。
"""

from __future__ import annotations

import json
from pathlib import Path

import yaml
from openai import OpenAI
from pydantic import BaseModel, Field

from .config import JUDGE_PROMPT_PATH


class JudgeVerdict(BaseModel):
    """judge の出力スキーマ。structured outputs で形を保証する。

    reason を score より前に定義しているのは意図的。生成は定義順に走るので、
    理由を書いてから点を付けさせる(逆順だと点に理由を後付けしがち)。
    """

    reason: str
    score: int = Field(ge=1, le=3)


def load_judge_prompt(path: Path = JUDGE_PROMPT_PATH) -> dict:
    prompt = yaml.safe_load(path.read_text(encoding="utf-8"))
    for key in ("model", "system", "user_template"):
        if key not in prompt:
            raise ValueError(f"judge プロンプトに {key} が無い: {path}")
    prompt["version"] = path.stem  # v1.yaml → v1
    return prompt


def judge_one(client: OpenAI, prompt: dict, text: str, summary: str) -> JudgeVerdict:
    completion = client.beta.chat.completions.parse(
        model=prompt["model"],
        temperature=prompt.get("temperature", 0.0),
        messages=[
            {"role": "system", "content": prompt["system"]},
            {
                "role": "user",
                "content": prompt["user_template"].format(text=text, summary=summary),
            },
        ],
        response_format=JudgeVerdict,
    )
    return completion.choices[0].message.parsed


def judge_run(
    run_dir: Path,
    records: list[dict],
    cases: list,
    client: OpenAI,
    prompt_path: Path = JUDGE_PROMPT_PATH,
) -> list[dict]:
    """run 内の成功レコード全件の summary を採点し、judge.jsonl に保存して返す。"""
    case_by_id = {c.id: c for c in cases}
    prompt = load_judge_prompt(prompt_path)
    results: list[dict] = []

    targets = [r for r in records if r["ok"]]
    for i, record in enumerate(targets, start=1):
        case = case_by_id[record["case_id"]]
        summary = record["response"]["data"].get("summary", "")
        verdict = judge_one(client, prompt, case.text, summary)
        results.append(
            {
                "case_id": record["case_id"],
                "prompt_version": record["prompt_version"],
                "repeat": record.get("repeat", 0),
                "summary": summary,
                "score": verdict.score,
                "reason": verdict.reason,
                "judge_version": prompt["version"],
                "judge_model": prompt["model"],
            }
        )
        print(".", end="", flush=True)
        if i == len(targets):
            print()

    (run_dir / "judge.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in results) + "\n",
        encoding="utf-8",
    )
    return results


def load_judge_results(run_dir: Path) -> list[dict] | None:
    path = run_dir / "judge.jsonl"
    if not path.exists():
        return None
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
