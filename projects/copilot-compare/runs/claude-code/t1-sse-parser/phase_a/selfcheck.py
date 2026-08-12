"""spec.md に載っている挙動だけを自分で確認するスクリプト。

採点用テストとは無関係。`python selfcheck.py` で走らせる。
"""

from sse_parser import SSEEvent, SSEParser

fails = []


def check(label, got, want):
    if got != want:
        fails.append(f"{label}\n  got : {got!r}\n  want: {want!r}")


# spec の例
p = SSEParser()
check(
    "example 1",
    p.feed("event: citations\ndata: [1]\n\n"),
    [SSEEvent(event="citations", data="[1]", id=None)],
)
check(
    "example 2",
    p.feed("data: a\ndata: b\n\n"),
    [SSEEvent(event="message", data="a\nb", id=None)],
)
check("example 3", p.feed(":keep-alive\n\n"), [])

# 1. 3 種類の改行
p = SSEParser()
check(
    "newline variants",
    p.feed("data: a\r\ndata: b\rdata: c\n\r\n"),
    [SSEEvent(event="message", data="a\nb\nc", id=None)],
)

# 2. 先頭 BOM は 1 つだけ落ちる
p = SSEParser()
check("bom", p.feed("﻿data: x\n\n"), [SSEEvent(event="message", data="x", id=None)])
p = SSEParser()
# 2 つ目の BOM は通常の文字。フィールド名が "﻿data" になり無視される
check("bom twice", p.feed("﻿﻿data: x\n\n"), [])

# 3. feed("") は状態を変えない(BOM 判定位置も含む)
p = SSEParser()
check("empty feed", p.feed(""), [])
check(
    "empty feed keeps bom slot",
    p.feed("﻿data: x\n\n"),
    [SSEEvent(event="message", data="x", id=None)],
)

# 4. チャンク境界の \r\n
p = SSEParser()
check("split crlf part1", p.feed("data: a\r"), [])
check("split crlf part2", p.feed("\n\n"), [SSEEvent(event="message", data="a", id=None)])

# \r の次が \n でない場合
p = SSEParser()
check("split cr part1", p.feed("data: a\r"), [])
check("split cr part2", p.feed("data: b\n\n"), [SSEEvent(event="message", data="a\nb", id=None)])

# 7. 値の先頭空白は 1 個だけ
p = SSEParser()
check("one leading space", p.feed("data:  x\n\n"), [SSEEvent(event="message", data=" x", id=None)])
p = SSEParser()
check("no leading space", p.feed("data:x\n\n"), [SSEEvent(event="message", data="x", id=None)])

# 8. コロンなしの行はフィールド名のみ、値は空
p = SSEParser()
check("bare data field", p.feed("data\n\n"), [SSEEvent(event="message", data="", id=None)])

# id / retry
p = SSEParser()
check("id set", p.feed("id: 7\ndata: x\n\n"), [SSEEvent(event="message", data="x", id="7")])
check("last_event_id", p.last_event_id, "7")
p.feed("id: a\0b\ndata: y\n\n")
check("id with NUL ignored", p.last_event_id, "7")
p.feed("retry: 3000\n")
check("retry", p.retry, 3000)
p.feed("retry: 30x\n")
check("retry non-digit ignored", p.retry, 3000)
p.feed("retry: １２３\n")
check("retry non-ascii digit ignored", p.retry, 3000)

# 9. データバッファが空なら dispatch しない。event バッファは戻る
p = SSEParser()
check("event only no dispatch", p.feed("event: ping\n\n"), [])
check("event buffer reset", p.feed("data: x\n\n"), [SSEEvent(event="message", data="x", id=None)])

# 10. 末尾の \n は 1 個だけ落ちる
p = SSEParser()
check(
    "trailing blank data line",
    p.feed("data: a\ndata\n\n"),
    [SSEEvent(event="message", data="a\n", id=None)],
)

# 13. last_event_id は dispatch 後も保持される
p = SSEParser()
p.feed("id: 1\ndata: a\n\n")
check("id persists", p.feed("data: b\n\n"), [SSEEvent(event="message", data="b", id="1")])

# 14. close は未完了分を捨てて空リスト
p = SSEParser()
p.feed("data: a\n")
check("close returns empty", p.close(), [])

# 1 回の feed で複数イベント
p = SSEParser()
check(
    "multiple events in one feed",
    p.feed("data: a\n\ndata: b\n\n"),
    [SSEEvent(event="message", data="a", id=None), SSEEvent(event="message", data="b", id=None)],
)

if fails:
    print(f"NG: {len(fails)}")
    for f in fails:
        print(f)
else:
    print("OK")
