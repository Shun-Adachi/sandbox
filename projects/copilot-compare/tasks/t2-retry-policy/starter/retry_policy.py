"""LLM API 呼び出しのリトライ判断。

仕様は ../spec.md を参照。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


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
    ) -> float | None:
        raise NotImplementedError
