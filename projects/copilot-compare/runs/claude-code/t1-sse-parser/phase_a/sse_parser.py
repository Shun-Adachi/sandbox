"""SSE (Server-Sent Events) の逐次パーサ。

仕様は ../spec.md を参照。
"""

from __future__ import annotations

from dataclasses import dataclass

_BOM = "﻿"
_DIGITS = "0123456789"


@dataclass
class SSEEvent:
    event: str
    data: str
    id: str | None = None


class SSEParser:
    def __init__(self) -> None:
        self.last_event_id: str | None = None
        self.retry: int | None = None
        # 未完了の行。改行が来るまでここに溜める。
        self._line = ""
        # 直前のチャンクが "\r" で終わった状態。次が "\n" なら 1 つの改行として扱う。
        self._pending_cr = False
        # BOM の判定はストリーム先頭の 1 回だけ。feed("") では消費しない。
        self._bom_checked = False
        # dispatch までのバッファ
        self._data = ""
        self._event_type = ""

    def feed(self, chunk: str) -> list[SSEEvent]:
        if not chunk:
            return []

        if not self._bom_checked:
            self._bom_checked = True
            if chunk.startswith(_BOM):
                chunk = chunk[1:]

        events: list[SSEEvent] = []
        for ch in chunk:
            if self._pending_cr:
                self._pending_cr = False
                if ch == "\n":
                    # 直前の "\r" と合わせて 1 つの改行。行はすでに処理済み。
                    continue
            if ch == "\n":
                self._handle_line(self._line, events)
                self._line = ""
            elif ch == "\r":
                # "\r" 単体でも行の終わり。後続が "\n" かどうかは次の文字で判断する。
                self._pending_cr = True
                self._handle_line(self._line, events)
                self._line = ""
            else:
                self._line += ch
        return events

    def close(self) -> list[SSEEvent]:
        # 未完了の行と未 dispatch のブロックは破棄する。
        # last_event_id / retry はストリームの状態なので残す。
        self._line = ""
        self._pending_cr = False
        self._data = ""
        self._event_type = ""
        return []

    def _handle_line(self, line: str, events: list[SSEEvent]) -> None:
        if line == "":
            self._dispatch(events)
            return
        if line.startswith(":"):
            return  # コメント

        name, sep, value = line.partition(":")
        if sep and value.startswith(" "):
            value = value[1:]  # 先頭の空白は 1 個だけ取り除く

        if name == "event":
            self._event_type = value
        elif name == "data":
            self._data += value + "\n"
        elif name == "id":
            if "\0" not in value:
                self.last_event_id = value
        elif name == "retry":
            if value and all(c in _DIGITS for c in value):
                self.retry = int(value)
        # その他のフィールドは無視する

    def _dispatch(self, events: list[SSEEvent]) -> None:
        if self._data == "":
            self._event_type = ""
            return

        data = self._data[:-1] if self._data.endswith("\n") else self._data
        events.append(
            SSEEvent(
                event=self._event_type or "message",
                data=data,
                id=self.last_event_id,
            )
        )
        self._data = ""
        self._event_type = ""
