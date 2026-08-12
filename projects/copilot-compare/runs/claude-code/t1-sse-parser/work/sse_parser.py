"""SSE (Server-Sent Events) の逐次パーサ。

仕様は ../spec.md を参照。
"""

from __future__ import annotations

from dataclasses import dataclass

BOM = "﻿"


@dataclass
class SSEEvent:
    event: str
    data: str
    id: str | None = None


class SSEParser:
    def __init__(self) -> None:
        self.last_event_id: str | None = None
        self.retry: int | None = None
        # 行が完成していない部分。
        self._buf: str = ""
        # 直前に消費した改行が "\r" だった。続く "\n" は同じ 1 つの改行の
        # 一部として読み飛ばす。チャンクをまたいでも成立させるため状態で持つ。
        self._skip_lf: bool = False
        self._data: str = ""
        self._event_type: str = ""
        # BOM の判定はストリーム先頭の 1 回だけ。空チャンクでは消費しない。
        self._bom_checked: bool = False

    def feed(self, chunk: str) -> list[SSEEvent]:
        if not chunk:
            return []

        if not self._bom_checked:
            self._bom_checked = True
            if chunk.startswith(BOM):
                chunk = chunk[1:]

        self._buf += chunk

        events: list[SSEEvent] = []
        while True:
            line = self._take_line()
            if line is None:
                break
            self._handle_line(line, events)
        return events

    def close(self) -> list[SSEEvent]:
        # 未完了の行と未 dispatch のブロックは捨てる。
        self._buf = ""
        self._data = ""
        self._event_type = ""
        self._skip_lf = False
        return []

    # --- 行の切り出し ---------------------------------------------------

    def _take_line(self) -> str | None:
        """完成した行を 1 本取り出す。まだ無ければ None。"""
        if self._skip_lf:
            if not self._buf:
                # 続きが "\n" かどうかまだ分からない。フラグは持ち越す。
                return None
            if self._buf[0] == "\n":
                self._buf = self._buf[1:]
            self._skip_lf = False

        cr = self._buf.find("\r")
        lf = self._buf.find("\n")
        if cr == -1 and lf == -1:
            return None

        if cr == -1 or (lf != -1 and lf < cr):
            line, self._buf = self._buf[:lf], self._buf[lf + 1 :]
            return line

        # "\r" で行は完成している。次が "\n" ならそれも同じ改行の一部だが、
        # 次のチャンクに回るかもしれないので、行はここで確定させて先送りしない。
        line, self._buf = self._buf[:cr], self._buf[cr + 1 :]
        self._skip_lf = True
        return line

    # --- 行の解釈 -------------------------------------------------------

    def _handle_line(self, line: str, events: list[SSEEvent]) -> None:
        if line == "":
            self._dispatch(events)
            return
        if line.startswith(":"):
            return

        name, sep, value = line.partition(":")
        if sep and value.startswith(" "):
            value = value[1:]

        if name == "event":
            self._event_type = value
        elif name == "data":
            self._data += value + "\n"
        elif name == "id":
            if "\0" not in value:
                self.last_event_id = value
        elif name == "retry":
            if value.isascii() and value.isdigit():
                self.retry = int(value)

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
