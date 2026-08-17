"""採点段: 保存済みの生応答を正解ラベルと突き合わせる。

採点は 3 層に分けている。それぞれ「何を保証したいか」が違う。

  1. 完全一致  … name / company / category / urgency。正解が一意に決まる項目
  2. ルール    … summary の 50 字以内・必須キーワードの包含。機械的に判定できる範囲
  3. LLM judge … summary の質(要件の取り違え・挨拶の混入など)。judge.py 参照

「全項目一致」は 1 の 4 項目がすべて正解のこと。summary は自由文で
完全一致の正解を持てないため、集計の主指標からは外し、別列で扱う。
"""

from __future__ import annotations

from .config import price_for
from .dataset import Case

EXACT_FIELDS = ("name", "company", "category", "urgency")
SUMMARY_MAX_LEN = 50
URGENCY_LEVELS = ("高", "中", "低")


def score_record(record: dict, case: Case) -> dict:
    """1 呼び出し分の採点。API エラーだった記録は全項目 False として数える。

    エラーを集計から除外すると「失敗の多い版ほど正解率が高く見える」という
    逆転が起きるため、エラー = その条件では正解を出せなかった、として扱う。
    """
    score = {
        "case_id": record["case_id"],
        "prompt_version": record["prompt_version"],
        "repeat": record.get("repeat", 0),
        "api_ok": record["ok"],
    }
    if not record["ok"]:
        score["field_ok"] = {f: False for f in EXACT_FIELDS}
        score["all_fields_ok"] = False
        score["summary_len_ok"] = False
        score["summary_keywords_ok"] = False
        score["actual"] = None
        return score

    data = record["response"]["data"]
    expected = case.expected
    field_ok = {f: data.get(f) == getattr(expected, f) for f in EXACT_FIELDS}
    summary = data.get("summary", "")

    score["field_ok"] = field_ok
    score["all_fields_ok"] = all(field_ok.values())
    score["summary_len_ok"] = len(summary) <= SUMMARY_MAX_LEN
    score["summary_keywords_ok"] = all(kw in summary for kw in expected.summary_keywords)
    score["actual"] = data
    return score


def score_all(records: list[dict], cases: list[Case]) -> list[dict]:
    case_by_id = {c.id: c for c in cases}
    return [score_record(r, case_by_id[r["case_id"]]) for r in records]


def aggregate(scored: list[dict], records: list[dict], cases: list[Case]) -> dict[str, dict]:
    """版ごとの集計。レポートの表 1 行分に相当する辞書を版名で引ける形にする。

    正解率のほかに 2 つの軸を持つ。
    - urgency 混同行列 … 「どちら向きに間違えたか」が改善方針を決める
      (高に寄るなら基準の明文化や否定形の条件、低に寄るなら影響の言語化を促す)
    - タグ別の一致率 … どの類型のケースで落ちているかを特定し、
      次のプロンプト修正とデータセット拡充の対象を絞る
    """
    case_by_id = {c.id: c for c in cases}
    rec_by_key = {
        (r["case_id"], r["prompt_version"], r.get("repeat", 0)): r for r in records
    }
    by_version: dict[str, dict] = {}

    for v in sorted({s["prompt_version"] for s in scored}):
        rows = [s for s in scored if s["prompt_version"] == v]
        n = len(rows)
        confusion = {e: {a: 0 for a in URGENCY_LEVELS} for e in URGENCY_LEVELS}
        tag_total: dict[str, int] = {}
        tag_ok: dict[str, int] = {}
        latencies: list[int] = []
        total_tokens = 0
        cost_usd = 0.0

        for s in rows:
            case = case_by_id[s["case_id"]]
            for tag in case.tags:
                tag_total[tag] = tag_total.get(tag, 0) + 1
                if s["all_fields_ok"]:
                    tag_ok[tag] = tag_ok.get(tag, 0) + 1
            if s["actual"] is not None:
                actual_urgency = s["actual"].get("urgency")
                if actual_urgency in URGENCY_LEVELS:
                    confusion[case.expected.urgency][actual_urgency] += 1
                res = rec_by_key[(s["case_id"], v, s["repeat"])]["response"]
                latencies.append(res.get("latency_ms", 0))
                usage = res.get("usage", {})
                total_tokens += usage.get("total_tokens", 0)
                price = price_for(res.get("model", ""))
                if price:
                    cost_usd += (
                        usage.get("prompt_tokens", 0) * price[0]
                        + usage.get("completion_tokens", 0) * price[1]
                    ) / 1_000_000

        by_version[v] = {
            "n": n,
            "api_errors": sum(1 for s in rows if not s["api_ok"]),
            "all_fields_ok": sum(1 for s in rows if s["all_fields_ok"]),
            "field_ok": {f: sum(1 for s in rows if s["field_ok"][f]) for f in EXACT_FIELDS},
            "summary_len_ok": sum(1 for s in rows if s["summary_len_ok"]),
            "summary_keywords_ok": sum(1 for s in rows if s["summary_keywords_ok"]),
            "urgency_confusion": confusion,
            "tags": {t: (tag_ok.get(t, 0), tag_total[t]) for t in sorted(tag_total)},
            "avg_latency_ms": round(sum(latencies) / len(latencies)) if latencies else 0,
            "max_latency_ms": max(latencies, default=0),
            "total_tokens": total_tokens,
            "cost_usd": cost_usd,
        }
    return by_version


def stability(scored: list[dict]) -> dict[str, tuple[int, int]]:
    """repeat > 1 のときの安定性: 4 項目の答えが全繰り返しで一致したケースの割合。

    temperature 0 でも出力は完全には固定されないので、
    版の比較で 1 件差を論じる前に、まず答えがブレていないかを見る。
    戻り値は 版 → (全繰り返しが一致したケース数, ケース数)。repeat=1 なら全一致扱い。
    """
    result: dict[str, tuple[int, int]] = {}
    for v in sorted({s["prompt_version"] for s in scored}):
        groups: dict[str, set[tuple]] = {}
        for s in (s for s in scored if s["prompt_version"] == v):
            answer = (
                tuple(s["actual"].get(f) for f in EXACT_FIELDS)
                if s["actual"] is not None
                else ("<error>",)
            )
            groups.setdefault(s["case_id"], set()).add(answer)
        stable = sum(1 for answers in groups.values() if len(answers) == 1)
        result[v] = (stable, len(groups))
    return result
