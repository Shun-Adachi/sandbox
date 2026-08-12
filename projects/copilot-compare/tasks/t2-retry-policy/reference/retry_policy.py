"""LLM API 呼び出しのリトライ判断 — 参照実装。

ハーネスの自己検証用。エージェントに解かせる際は見せないこと。
仕様は ../spec.md を参照。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

RETRYABLE_ERRORS = frozenset({"timeout", "connection"})


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
        if attempt < 1:
            raise ValueError("attempt は 1 以上")
        if attempt >= self.max_attempts:
            return None
        if not self._retryable(status, error):
            return None

        if retry_after is not None:
            seconds = self._parse_retry_after(retry_after, now)
            if seconds is not None:
                return seconds

        delay = min(self.base_delay * self.multiplier ** (attempt - 1), self.max_delay)
        return delay * jitter

    @staticmethod
    def _retryable(status: int | None, error: str | None) -> bool:
        if status is None:
            return error in RETRYABLE_ERRORS
        if status in (408, 429):
            return True
        return 500 <= status < 600

    @staticmethod
    def _parse_retry_after(value: str, now: datetime | None) -> float | None:
        # 仕様は「ASCII 数字のみ」。前後の空白を取り除くとは書いていないので、
        # "  3  " は数字として扱わず、HTTP-date としても解釈できずバックオフに落ちる。
        if value.isascii() and value.isdigit():
            return float(value)

        try:
            when = parsedate_to_datetime(value)
        except (TypeError, ValueError):
            return None
        if when is None:
            return None

        if now is None:
            raise ValueError("HTTP-date 形式の retry_after には now が必要")
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        return max(0.0, (when - now).total_seconds())
