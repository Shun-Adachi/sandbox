# t2-retry-policy

LLM API 呼び出しのリトライ判断を、**待たずに**決める純粋なクラスを実装する。
実際の sleep は呼び出し側の責務で、ここでは「次に何秒待つか / もう諦めるか」だけを返す。

実装対象は `retry_policy.py` の 1 ファイルのみ。標準ライブラリだけを使う。

## 公開インターフェース

```python
@dataclass
class RetryPolicy:
    max_attempts: int = 3
    base_delay: float = 0.5
    multiplier: float = 2.0
    max_delay: float = 8.0

    def next_delay(
        self,
        attempt: int,
        *,
        status: int | None = None,
        error: str | None = None,
        retry_after: str | None = None,
        now: datetime | None = None,
        jitter: float = 1.0,
    ) -> float | None: ...
```

- `attempt` … いま失敗した試行の番号。**1 始まり**。
- `status` … 上流の HTTP ステータス。応答が得られなかった場合は `None`。
- `error` … `status` が `None` のときの失敗種別。`"timeout"` / `"connection"` など。
- `retry_after` … `Retry-After` ヘッダーの生の値。無ければ `None`。
- `now` … `retry_after` が HTTP-date のときの基準時刻。
- `jitter` … `0.0`〜`1.0` の乱数を呼び出し側が注入する。テスト可能にするための引数で、
  クラス内部で乱数を生成しないこと。
- 戻り値 … 次に待つ秒数（`float`）。リトライすべきでなければ `None`。

## 仕様

判定は次の順に行う。

### 1. 打ち切り

1. `attempt` が 1 未満なら `ValueError`。
2. `attempt >= max_attempts` なら `None`（リトライ済み回数が上限に達した）。

### 2. リトライ可否

3. `status` が `None` のとき … `error` が `"timeout"` または `"connection"` なら再試行可。
   それ以外（`None` を含む）は `None` を返す。
4. `status` が `408` または `429` … 再試行可。
5. `status` がその他の 4xx … `None`。
6. `status` が 5xx … 再試行可。
7. `status` が 2xx / 3xx … `None`。

### 3. Retry-After

再試行可のときだけ `retry_after` を見る。

8. ASCII 数字のみ（例 `"3"`）… その秒数をそのまま返す。
9. HTTP-date（例 `"Wed, 12 Aug 2026 09:00:30 GMT"`）… `now` との差分秒を返す。
   差分が負なら `0.0`。`now` が省略されている場合は `ValueError`。
   タイムゾーンを持たない日時は UTC とみなす。
10. どちらとしても解釈できない値（`"soon"`、`"-5"` など）… 無視して 4. のバックオフに進む。
11. `Retry-After` 由来の値には **jitter を掛けない**。`max_delay` でも頭打ちにしない。

### 4. 指数バックオフ

12. `min(base_delay * multiplier ** (attempt - 1), max_delay) * jitter` を返す。

## 例

```python
p = RetryPolicy()
p.next_delay(1, status=500)                      # → 0.5
p.next_delay(2, status=500)                      # → 1.0
p.next_delay(2, status=500, jitter=0.5)          # → 0.5
p.next_delay(3, status=500)                      # → None  (max_attempts=3)
p.next_delay(1, status=404)                      # → None
p.next_delay(1, status=429, retry_after="120")   # → 120.0  (max_delay で切らない)
p.next_delay(1, status=None, error="timeout")    # → 0.5
```

## 完了条件

`tests/test_retry_policy.py` が全件通ること。
（採点時にのみ実行する。作業中は見ない — `../../PROTOCOL.md` を参照）
