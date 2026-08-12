"""SSE (Server-Sent Events) の逐次パーサ。

仕様は ../spec.md を参照。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class SSEEvent:
    event: str
    data: str
    id: str | None = None


class SSEParser:
    def __init__(self) -> None:
        self.last_event_id: str | None = None
        self.retry: int | None = None
        self._buffer: str = ""
        self._event_type: str = ""
        self._data: str = ""
        self._bom_checked: bool = False

    def feed(self, chunk: str) -> list[SSEEvent]:
        if chunk == "":
            return []

        if not self._bom_checked:
            self._bom_checked = True
            if chunk.startswith("\ufeff"):
                chunk = chunk[1:]

        self._buffer += chunk
        events: list[SSEEvent] = []

        while True:
            line, delim, rest = self._next_line(self._buffer)
            if line is None:
                break
            self._buffer = rest
            events.extend(self._process_line(line))

        return events

    def close(self) -> list[SSEEvent]:
        self._buffer = ""
        self._event_type = ""
        self._data = ""
        self._pending_cr = False
        return []

    def _next_line(self, text: str) -> tuple[str | None, str, str]:
        length = len(text)
        i = 0
        while i < length:
            ch = text[i]
            if ch == "\n":
                return text[:i], "\n", text[i + 1 :]
            if ch == "\r":
                if i + 1 < length:
                    if text[i + 1] == "\n":
                        return text[:i], "\r\n", text[i + 2 :]
                    return text[:i], "\r", text[i + 1 :]
                return text[:i], "\r", ""
            i += 1
        return None, "", text

    def _process_line(self, line: str) -> list[SSEEvent]:
        if line == "":
            return self._dispatch()
        if line.startswith(":"):
            return []

        field, value = self._parse_field(line)
        if field == "event":
            self._event_type = value
        elif field == "data":
            self._data += value + "\n"
        elif field == "id":
            if "\0" not in value:
                self.last_event_id = value
        elif field == "retry":
            if value.isascii() and value.isdigit():
                self.retry = int(value)
        return []

    def _parse_field(self, line: str) -> tuple[str, str]:
        if ":" in line:
            name, value = line.split(":", 1)
            if value.startswith(" "):
                value = value[1:]
            return name, value
        return line, ""

    def _dispatch(self) -> list[SSEEvent]:
        if self._data == "":
            self._event_type = ""
            return []

        data = self._data[:-1] if self._data.endswith("\n") else self._data
        event = self._event_type or "message"
        message = SSEEvent(event=event, data=data, id=self.last_event_id)
        self._data = ""
        self._event_type = ""
        return [message]
