"""収集段: 評価対象 API を呼び、生の応答をそのまま runs/<run_id>/ に保存する。

採点(scoring.py)と分けているのは、ここだけが課金と時間のかかる段だから。
生の応答を残しておけば、採点基準を直したくなったとき・レポートの体裁を
変えたくなったとき、API を呼び直さずに score だけ何度でもやり直せる。
実務では「採点ロジックの試行錯誤 >> 収集のやり直し」なので、この境界が効く。

保存するもの:
  meta.json     … いつ・どこに向けて・どの版とデータで測ったか(再現条件)
  cases.jsonl   … 使ったケースのスナップショット(データセットが後で変わっても採点を再現できる)
  records.jsonl … 1 行 = 1 呼び出しの生の応答(エラーもそのまま記録)
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import os
from pathlib import Path

import httpx

from .config import PROJECT_ROOT, REQUEST_TIMEOUT, RUNS_DIR
from .dataset import Case, dataset_sha256, dump_case_index, load_cases


async def _call_extract(
    client: httpx.AsyncClient, case: Case, version: str, repeat: int
) -> dict:
    """1 ケース × 1 版の呼び出し。失敗しても例外を投げず、記録として返す。

    評価の途中で 1 件失敗しただけで run 全体が無駄になるのが最悪なので、
    失敗も「その条件で失敗した」という結果として残し、集計側で件数に出す。
    """
    record = {"case_id": case.id, "prompt_version": version, "repeat": repeat}
    try:
        res = await client.post(
            "/v1/extract",
            json={"text": case.text, "prompt_version": version},
        )
        body = res.json()
        if res.status_code == 200 and body.get("ok"):
            record["ok"] = True
            record["response"] = body
        else:
            record["ok"] = False
            record["error"] = {"status_code": res.status_code, "body": body}
    except httpx.HTTPError as exc:
        record["ok"] = False
        record["error"] = {"status_code": None, "body": f"{type(exc).__name__}: {exc}"}
    return record


async def collect(
    cases: list[Case],
    versions: list[str],
    *,
    client: httpx.AsyncClient,
    repeat: int = 1,
    concurrency: int = 4,
    on_progress=None,
) -> list[dict]:
    """ケース × 版 × 繰り返しの全組み合わせを呼ぶ。

    並列数は控えめの既定値にしてある。評価対象(の先の OpenAI)のレート制限を
    評価ハーネス自身が食い潰すと、本番トラフィック側に跳ねるため。
    """
    sem = asyncio.Semaphore(concurrency)
    done = 0
    total = len(cases) * len(versions) * repeat

    async def bounded(case: Case, version: str, i: int) -> dict:
        nonlocal done
        async with sem:
            record = await _call_extract(client, case, version, i)
        done += 1
        if on_progress:
            on_progress(done, total, record)
        return record

    tasks = [
        bounded(case, version, i)
        for version in versions
        for case in cases
        for i in range(repeat)
    ]
    return await asyncio.gather(*tasks)


def run_eval(
    *,
    target_url: str,
    versions: list[str],
    cases_path: Path,
    repeat: int = 1,
    concurrency: int = 4,
    runs_dir: Path = RUNS_DIR,
) -> Path:
    """収集を実行し、run ディレクトリのパスを返す。CLI の `run` コマンドの本体。"""
    cases = load_cases(cases_path)
    run_id = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir = runs_dir / run_id
    run_dir.mkdir(parents=True)

    def progress(done: int, total: int, record: dict) -> None:
        mark = "." if record["ok"] else "E"
        print(mark, end="", flush=True)
        if done == total:
            print()

    async def main() -> list[dict]:
        async with httpx.AsyncClient(
            base_url=target_url, timeout=REQUEST_TIMEOUT
        ) as client:
            return await collect(
                cases,
                versions,
                client=client,
                repeat=repeat,
                concurrency=concurrency,
                on_progress=progress,
            )

    print(f"run {run_id}: {len(cases)} ケース × {versions} × {repeat} 回 → {target_url}")
    records = asyncio.run(main())

    meta = {
        "run_id": run_id,
        "created_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "target_url": target_url,
        "versions": versions,
        "repeat": repeat,
        # レポートに載るので、環境依存の絶対パスではなく相対パスで記録する
        "cases_file": os.path.relpath(cases_path, PROJECT_ROOT),
        "cases_sha256": dataset_sha256(cases_path),
        "num_cases": len(cases),
    }
    (run_dir / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (run_dir / "cases.jsonl").write_text(dump_case_index(cases), encoding="utf-8")
    (run_dir / "records.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in records) + "\n",
        encoding="utf-8",
    )

    errors = sum(1 for r in records if not r["ok"])
    print(f"保存: {run_dir}(エラー {errors} / {len(records)} 件)")
    return run_dir
