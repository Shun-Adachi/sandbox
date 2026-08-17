"""人手評価段: レビューシートの生成と、人 vs judge の一致率の算出。

このハーネスの評価の正は人手評価であり、LLM-as-judge はそれを件数面で
代行するための道具にすぎない。judge の採点を信用してよいかは、
人の採点と突き合わせて(キャリブレーションして)初めて言える。
このモジュールはその突き合わせを行う。

  review-sheet … 要約をシャッフルした CSV に書き出す(人が採点する用)
  agreement    … 記入済み CSV を読み、judge との一致率・κ・不一致一覧を出す

シートには意図的にプロンプト版と judge のスコアを載せない。
「v3 の出力だから良いはず」「judge が 3 を付けたから 3」という
アンカリングを避け、人の採点を独立させるため。対応表は review-map.json に分離する。
"""

from __future__ import annotations

import csv
import json
import random
from pathlib import Path

SHEET_NAME = "review-sheet.csv"
MAP_NAME = "review-map.json"
AGREEMENT_NAME = "agreement.md"
SCORES = (1, 2, 3)


def _key(d: dict) -> tuple:
    return (d["case_id"], d["prompt_version"], d.get("repeat", 0))


def make_sheet(run_dir: Path, records: list[dict], cases: list) -> Path:
    """成功レコード全件の要約を、決定的にシャッフルして CSV に書き出す。

    シャッフルのシードは run_id。同じ run からは常に同じシートができるので、
    採点の途中でシートを作り直しても行がズレない。
    """
    case_by_id = {c.id: c for c in cases}
    rows = [r for r in records if r["ok"]]
    rng = random.Random(run_dir.name)
    rng.shuffle(rows)

    mapping: dict[str, dict] = {}
    sheet_path = run_dir / SHEET_NAME
    # utf-8-sig は Excel で開いたときの文字化け対策
    with sheet_path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["review_id", "問い合わせ原文", "要約", "human_score", "comment"])
        for i, record in enumerate(rows, start=1):
            review_id = f"r{i:03d}"
            mapping[review_id] = {
                "case_id": record["case_id"],
                "prompt_version": record["prompt_version"],
                "repeat": record.get("repeat", 0),
            }
            writer.writerow(
                [
                    review_id,
                    case_by_id[record["case_id"]].text,
                    record["response"]["data"].get("summary", ""),
                    "",
                    "",
                ]
            )

    (run_dir / MAP_NAME).write_text(
        json.dumps(mapping, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return sheet_path


def load_filled_sheet(run_dir: Path) -> dict[tuple, dict]:
    """記入済みシートを読み、(case_id, version, repeat) → 人の採点 に引き直す。

    human_score が空欄の行は「未採点」として黙って読み飛ばす。
    全件埋めなくても、埋めた分だけで一致率を出せるようにするため。
    1〜3 以外の値はタイプミスの可能性が高いので、行番号つきで拒否する。
    """
    mapping = json.loads((run_dir / MAP_NAME).read_text(encoding="utf-8"))
    result: dict[tuple, dict] = {}
    with (run_dir / SHEET_NAME).open(encoding="utf-8-sig", newline="") as f:
        for lineno, row in enumerate(csv.DictReader(f), start=2):
            raw = (row["human_score"] or "").strip()
            if not raw:
                continue
            if raw not in {"1", "2", "3"}:
                raise ValueError(
                    f"{SHEET_NAME}:{lineno} 行目 human_score が 1〜3 でない: {raw!r}"
                )
            m = mapping[row["review_id"]]
            result[(m["case_id"], m["prompt_version"], m["repeat"])] = {
                "score": int(raw),
                "comment": (row.get("comment") or "").strip(),
            }
    return result


def cohen_kappa(pairs: list[tuple[int, int]]) -> float | None:
    """Cohen's κ。単純一致率は「全部 3 を付けがち」なだけでも高く出るので、
    偶然の一致を差し引いた κ を併記する。分母が 0(全員同じ点しか付けない等)なら None。"""
    n = len(pairs)
    if n == 0:
        return None
    po = sum(1 for h, j in pairs if h == j) / n
    pe = sum(
        (sum(1 for h, _ in pairs if h == s) / n) * (sum(1 for _, j in pairs if j == s) / n)
        for s in SCORES
    )
    if pe == 1.0:
        return None
    return (po - pe) / (1 - pe)


def build_agreement(
    human: dict[tuple, dict], judge_results: list[dict], cases: list
) -> tuple[str, str]:
    """一致率レポート(Markdown)とコンソール要約を組み立てる。"""
    case_by_id = {c.id: c for c in cases}
    judge_by_key = {_key(j): j for j in judge_results}
    keys = [k for k in human if k in judge_by_key]
    pairs = [(human[k]["score"], judge_by_key[k]["score"]) for k in keys]
    n = len(pairs)
    if n == 0:
        raise ValueError("human_score が 1 件も記入されていない(または judge と突き合わせられない)")

    exact = sum(1 for h, j in pairs if h == j)
    kappa = cohen_kappa(pairs)
    lines: list[str] = []
    add = lines.append

    j0 = judge_results[0]
    add("# 人手評価と LLM-as-judge の一致")
    add("")
    add(f"- judge: {j0['judge_version']} / {j0['judge_model']}")
    add(f"- 採点済み: {n} 件(全 {len(judge_by_key)} 件中)")
    add(f"- 完全一致率: {exact / n:.0%} ({exact}/{n})")
    add(f"- Cohen's κ: {f'{kappa:.2f}' if kappa is not None else '算出不能(偶然一致率が 1)'}")
    add("")

    add("## 混同行列(行=人、列=judge)")
    add("")
    add("| 人＼judge | 1 | 2 | 3 |")
    add("| --- | --- | --- | --- |")
    for h in SCORES:
        cells = [str(sum(1 for hh, jj in pairs if hh == h and jj == j)) for j in SCORES]
        add(f"| {h} | " + " | ".join(cells) + " |")
    add("")

    add("## 版ごとの平均(人 vs judge)")
    add("")
    add("| 版 | 人の平均 | judge の平均 | 件数 |")
    add("| --- | --- | --- | --- |")
    for v in sorted({k[1] for k in keys}):
        vk = [k for k in keys if k[1] == v]
        h_avg = sum(human[k]["score"] for k in vk) / len(vk)
        j_avg = sum(judge_by_key[k]["score"] for k in vk) / len(vk)
        add(f"| {v} | {h_avg:.2f} | {j_avg:.2f} | {len(vk)} |")
    add("")

    add("## 不一致の一覧")
    add("")
    disagreements = [k for k in keys if human[k]["score"] != judge_by_key[k]["score"]]
    if not disagreements:
        add("なし")
    for k in disagreements:
        j = judge_by_key[k]
        add(f"- **{k[0]} / {k[1]}**: 人 {human[k]['score']} / judge {j['score']} — {j['summary']}")
        add(f"  - judge の理由: {j['reason']}")
        if human[k]["comment"]:
            add(f"  - 人のコメント: {human[k]['comment']}")
    add("")

    console = (
        f"採点済み {n} 件: 完全一致 {exact / n:.0%} ({exact}/{n})"
        f" / κ {f'{kappa:.2f}' if kappa is not None else '-'}"
        f" / 不一致 {len(disagreements)} 件"
    )
    return "\n".join(lines) + "\n", console
