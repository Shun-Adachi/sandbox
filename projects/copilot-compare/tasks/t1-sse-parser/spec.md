# t1-sse-parser

`llm-api` の `/v1/qa` が返す Server-Sent Events を、逐次デコードするパーサを実装する。

実装対象は `sse_parser.py` の 1 ファイルのみ。標準ライブラリだけを使う。

## 公開インターフェース

```python
@dataclass
class SSEEvent:
    event: str          # イベント種別
    data: str           # データ本体
    id: str | None      # dispatch 時点の Last-Event-ID

class SSEParser:
    last_event_id: str | None   # 初期値 None
    retry: int | None           # 初期値 None

    def feed(self, chunk: str) -> list[SSEEvent]: ...
    def close(self) -> list[SSEEvent]: ...
```

`SSEEvent` は `==` で比較できること（`@dataclass` で十分）。

## 仕様

### 行の切り出し

1. `\r\n` / `\n` / `\r` のいずれも、1 つの改行として扱う。
2. ストリーム全体の先頭に BOM (`﻿`) が 1 つあれば取り除く。2 つ目以降は通常の文字として扱う。
3. `feed("")` は状態を一切変えない。BOM の判定位置もずらさない。
4. `feed()` は行の途中で切れたチャンクを受け取れること。行が完成するまでイベントは出さない。
   特に、あるチャンクが `\r` で終わり次のチャンクが `\n` で始まる場合、両者を 1 つの改行として扱う。

### 行の解釈

5. 空行 → **dispatch**（後述）。
6. `:` で始まる行 → コメント。無視する。
7. `:` を含む行 → 最初の `:` までがフィールド名、以降が値。
   値の先頭に空白が 1 個あれば、**1 個だけ**取り除く。
8. `:` を含まない行 → 行全体がフィールド名、値は空文字列。

### フィールドの処理

| フィールド | 処理 |
| --- | --- |
| `event` | イベント種別バッファに値を設定する |
| `data` | データバッファに `値 + "\n"` を追加する |
| `id` | 値に NUL (`\0`) が含まれなければ `last_event_id` に設定する。含まれれば無視 |
| `retry` | 値が ASCII 数字のみなら `int` にして `retry` に設定する。それ以外は無視 |
| その他 | 無視する |

### dispatch

9. データバッファが空文字列なら、イベントを生成せずイベント種別バッファだけを空に戻す。
10. そうでなければ、データバッファ末尾の `"\n"` を **1 個だけ**取り除く。
11. イベント種別バッファが空なら `"message"` を使う。
12. `SSEEvent(event=..., data=..., id=last_event_id)` を生成する。
13. データバッファとイベント種別バッファを空に戻す。`last_event_id` と `retry` は保持する。

### 終了

14. `close()` はストリーム終了を表す。未完了の行・未 dispatch のブロックは破棄し、常に空リストを返す。

## 例

```python
p = SSEParser()
p.feed("event: citations\ndata: [1]\n\n")
# → [SSEEvent(event="citations", data="[1]", id=None)]

p.feed("data: a\ndata: b\n\n")
# → [SSEEvent(event="message", data="a\nb", id=None)]

p.feed(":keep-alive\n\n")
# → []   コメントのみ。データバッファが空なので dispatch されない
```

## 完了条件

`tests/test_sse_parser.py` が全件通ること。
（採点時にのみ実行する。作業中は見ない — `../../PROTOCOL.md` を参照）
