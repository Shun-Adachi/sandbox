"""SSE (Server-Sent Events) の逐次パーサ — 参照実装。

ハーネスの自己検証用。エージェントに解かせる際は見せないこと。
仕様は ../spec.md を参照。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class SSEEvent:
    event: str
    data: str
    id: str | None = None


def _find_newline(s: str) -> tuple[int, int]:
    """最初の改行の (位置, 長さ) を返す。無ければ (-1, 0)。"""
    i_n = s.find("\n")
    i_r = s.find("\r")
    if i_r == -1:
        return (i_n, 1) if i_n != -1 else (-1, 0)
    if i_n == -1:
        return i_r, 1
    if i_r < i_n:
        return (i_r, 2) if i_n == i_r + 1 else (i_r, 1)
    return i_n, 1


class SSEParser:
    def __init__(self) -> None:
        self.last_event_id: str | None = None
        self.retry: int | None = None
        self._buf = ""
        self._data = ""
        self._type = ""
        self._bom_checked = False
        self._pending_cr = False

    def feed(self, chunk: str) -> list[SSEEvent]:
        if not chunk:
            return []

        if not self._bom_checked:
            self._bom_checked = True
            if chunk.startswith("﻿"):
                chunk = chunk[1:]

        # 直前のチャンクが \r で終わっていた場合、続く \n は同じ改行の一部。
        if self._pending_cr:
            self._pending_cr = False
            if chunk.startswith("\n"):
                chunk = chunk[1:]

        self._buf += chunk
        events: list[SSEEvent] = []
        while True:
            idx, width = _find_newline(self._buf)
            if idx < 0:
                break
            # 末尾の孤立した \r は、次のチャンクの \n と対になる可能性がある。
            self._pending_cr = (
                width == 1 and self._buf[idx] == "\r" and idx == len(self._buf) - 1
            )
            line = self._buf[:idx]
            self._buf = self._buf[idx + width :]
            event = self._process(line)
            if event is not None:
                events.append(event)
        return events

    def close(self) -> list[SSEEvent]:
        self._buf = ""
        self._data = ""
        self._type = ""
        self._pending_cr = False
        return []

    def _process(self, line: str) -> SSEEvent | None:
        if line == "":
            return self._dispatch()
        if line.startswith(":"):
            return None

        if ":" in line:
            field, _, value = line.partition(":")
            if value.startswith(" "):
                value = value[1:]
        else:
            field, value = line, ""

        if field == "event":
            self._type = value
        elif field == "data":
            self._data += value + "\n"
        elif field == "id":
            if "\0" not in value:
                self.last_event_id = value
        elif field == "retry":
            if value.isascii() and value.isdigit():
                self.retry = int(value)
        return None

    def _dispatch(self) -> SSEEvent | None:
        if self._data == "":
            self._type = ""
            return None
        data = self._data[:-1] if self._data.endswith("\n") else self._data
        event = SSEEvent(event=self._type or "message", data=data, id=self.last_event_id)
        self._data = ""
        self._type = ""
        return event
