"""レポート段: 採点結果を Markdown にまとめる。

レポートの構成は「意思決定に近い順」。まず版の優劣(サマリ)、
次にどこで差がついたか(混同行列・タグ別)、最後に個別の誤答。
数字は必ず割合と件数を併記する。母数の小さい評価で割合だけ見せると
1 件差が大きく見えてしまうため。
"""

from __future__ import annotations

from .dataset import Case
from .scoring import EXACT_FIELDS, SUMMARY_MAX_LEN, URGENCY_LEVELS


def _rate(ok: int, n: int) -> str:
    if n == 0:
        return "-"
    return f"{ok / n:.0%} ({ok}/{n})"


def _judge_stats(judge_results: list[dict] | None) -> dict[str, dict]:
    if not judge_results:
        return {}
    stats: dict[str, dict] = {}
    for v in sorted({j["prompt_version"] for j in judge_results}):
        rows = [j for j in judge_results if j["prompt_version"] == v]
        scores = [j["score"] for j in rows]
        stats[v] = {
            "avg": sum(scores) / len(scores),
            "dist": {s: scores.count(s) for s in (3, 2, 1)},
            "low": [j for j in rows if j["score"] <= 2],
        }
    return stats


def build_report(
    meta: dict,
    cases: list[Case],
    records: list[dict],
    scored: list[dict],
    agg: dict[str, dict],
    stability: dict[str, tuple[int, int]],
    judge_results: list[dict] | None = None,
) -> str:
    versions = meta["versions"]
    case_by_id = {c.id: c for c in cases}
    judge = _judge_stats(judge_results)
    lines: list[str] = []
    add = lines.append

    add(f"# 評価レポート {meta['run_id']}")
    add("")
    add("## 実行条件")
    add("")
    add("| 項目 | 値 |")
    add("| --- | --- |")
    add(f"| 実行日時 | {meta['created_at']} |")
    add(f"| 評価対象 | {meta['target_url']} POST /v1/extract |")
    add(f"| プロンプト版 | {', '.join(versions)} |")
    add(f"| ケース数 | {meta['num_cases']}(繰り返し {meta['repeat']} 回) |")
    add(f"| データセット | {meta['cases_file']} (sha256: {meta['cases_sha256']}) |")
    if judge_results:
        j0 = judge_results[0]
        add(f"| judge | {j0['judge_version']} / {j0['judge_model']} |")
    add("")

    add("## 版ごとのサマリ")
    add("")
    header = ["版", "全項目一致"] + list(EXACT_FIELDS) + [
        f"要約≤{SUMMARY_MAX_LEN}字",
        "要約KW",
    ]
    if judge:
        header.append("judge平均(1-3)")
    header += ["平均レイテンシ", "コスト"]
    add("| " + " | ".join(header) + " |")
    add("|" + " --- |" * len(header))
    error_notes = []
    for v in versions:
        a = agg[v]
        row = [v, _rate(a["all_fields_ok"], a["n"])]
        row += [_rate(a["field_ok"][f], a["n"]) for f in EXACT_FIELDS]
        row += [_rate(a["summary_len_ok"], a["n"]), _rate(a["summary_keywords_ok"], a["n"])]
        if judge:
            row.append(f"{judge[v]['avg']:.2f}" if v in judge else "-")
        row += [f"{a['avg_latency_ms']} ms", f"${a['cost_usd']:.4f}"]
        add("| " + " | ".join(row) + " |")
        if a["api_errors"]:
            error_notes.append(f"※ {v} は API エラー {a['api_errors']} 件を不正解として含む")
    add("")
    for note in error_notes:
        add(note)
    if error_notes:
        add("")

    if meta["repeat"] > 1:
        add("## 安定性(繰り返し間で 4 項目の答えが一致したケース)")
        add("")
        add("| 版 | 一致 |")
        add("| --- | --- |")
        for v in versions:
            st, n = stability[v]
            add(f"| {v} | {_rate(st, n)} |")
        add("")

    add("## urgency 混同行列(行=正解、列=出力)")
    add("")
    for v in versions:
        conf = agg[v]["urgency_confusion"]
        add(f"### {v}")
        add("")
        add("| 正解＼出力 | " + " | ".join(URGENCY_LEVELS) + " |")
        add("|" + " --- |" * (len(URGENCY_LEVELS) + 1))
        for e in URGENCY_LEVELS:
            cells = [
                f"**{conf[e][a]}**" if e == a and conf[e][a] else str(conf[e][a])
                for a in URGENCY_LEVELS
            ]
            add(f"| {e} | " + " | ".join(cells) + " |")
        add("")

    add("## タグ別の全項目一致率")
    add("")
    tags = sorted({t for c in cases for t in c.tags})
    add("| タグ | " + " | ".join(versions) + " |")
    add("|" + " --- |" * (len(versions) + 1))
    for t in tags:
        cells = [_rate(*agg[v]["tags"][t]) if t in agg[v]["tags"] else "-" for v in versions]
        add(f"| {t} | " + " | ".join(cells) + " |")
    add("")

    add("## ケース × 版")
    add("")
    add("○ = 4 項目一致。× の後は不一致だった項目。繰り返しがある場合は一致した回数。")
    add("")
    add("| ケース | タグ | " + " | ".join(versions) + " |")
    add("|" + " --- |" * (len(versions) + 2))
    for c in cases:
        cells = []
        for v in versions:
            rows = [
                s for s in scored
                if s["case_id"] == c.id and s["prompt_version"] == v
            ]
            if not rows:
                cells.append("-")
            elif meta["repeat"] > 1:
                ok = sum(1 for s in rows if s["all_fields_ok"])
                cells.append(f"{ok}/{len(rows)}")
            elif rows[0]["all_fields_ok"]:
                cells.append("○")
            elif not rows[0]["api_ok"]:
                cells.append("× (APIエラー)")
            else:
                bad = [f for f in EXACT_FIELDS if not rows[0]["field_ok"][f]]
                cells.append("× " + ",".join(bad))
        add(f"| {c.id} | {', '.join(c.tags)} | " + " | ".join(cells) + " |")
    add("")

    add("## 誤答の詳細")
    add("")
    for v in versions:
        wrong = [
            s for s in scored
            if s["prompt_version"] == v and not s["all_fields_ok"] and s["actual"] is not None
        ]
        add(f"### {v}({len(wrong)} 件)")
        add("")
        if not wrong:
            add("なし")
            add("")
            continue
        add("| ケース | 項目 | 正解 | 出力 |")
        add("| --- | --- | --- | --- |")
        for s in wrong:
            case = case_by_id[s["case_id"]]
            for f in EXACT_FIELDS:
                if not s["field_ok"][f]:
                    exp = getattr(case.expected, f) or "(空)"
                    act = s["actual"].get(f) or "(空)"
                    add(f"| {s['case_id']} | {f} | {exp} | {act} |")
        add("")

    if judge:
        add("## judge が低評価(score ≤ 2)にした要約")
        add("")
        any_low = False
        for v in versions:
            if v not in judge:
                continue
            for j in judge[v]["low"]:
                any_low = True
                add(f"- **{j['case_id']} / {v}**(score {j['score']}): {j['summary']}")
                add(f"  - 理由: {j['reason']}")
        if not any_low:
            add("なし")
        add("")

    return "\n".join(lines) + "\n"


def console_summary(meta: dict, agg: dict[str, dict], judge_results: list[dict] | None) -> str:
    """score コマンドが標準出力に出す 1 版 1 行の要約。詳細はレポート側で見る。"""
    judge = _judge_stats(judge_results)
    lines = []
    for v in meta["versions"]:
        a = agg[v]
        line = (
            f"{v}: 全項目一致 {_rate(a['all_fields_ok'], a['n'])}"
            f" / urgency {_rate(a['field_ok']['urgency'], a['n'])}"
        )
        if v in judge:
            line += f" / judge平均 {judge[v]['avg']:.2f}"
        line += f" / ${a['cost_usd']:.4f}"
        lines.append(line)
    return "\n".join(lines)
