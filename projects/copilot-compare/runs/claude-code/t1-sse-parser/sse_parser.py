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

    def feed(self, chunk: str) -> list[SSEEvent]:
        raise NotImplementedError

    def close(self) -> list[SSEEvent]:
        raise NotImplementedError
