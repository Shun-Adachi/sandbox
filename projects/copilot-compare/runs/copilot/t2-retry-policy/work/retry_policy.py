"""LLM API 呼び出しのリトライ判断。

仕様は ../spec.md を参照。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime


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
            raise ValueError("attempt must be 1 or greater")

        if attempt >= self.max_attempts:
            return None

        if not self._should_retry(status, error):
            return None

        if retry_after is not None:
            retry_delay = self._parse_retry_after(retry_after, now)
            if retry_delay is not None:
                return retry_delay

        return self._backoff_delay(attempt, jitter)

    def _should_retry(self, status: int | None, error: str | None) -> bool:
        if status is None:
            return error in ("timeout", "connection")

        if status == 408 or status == 429:
            return True

        if 400 <= status < 500:
            return False

        if 500 <= status < 600:
            return True

        return False

    def _parse_retry_after(self, retry_after: str, now: datetime | None) -> float | None:
        if retry_after and retry_after.isascii() and retry_after.isdigit():
            return float(retry_after)

        try:
            parsed = parsedate_to_datetime(retry_after)
        except (TypeError, ValueError, IndexError, OverflowError):
            return None

        if parsed is None:
            return None

        if now is None:
            raise ValueError("now is required for HTTP-date retry_after values")

        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        else:
            parsed = parsed.astimezone(timezone.utc)

        if now.tzinfo is None:
            now_dt = now.replace(tzinfo=timezone.utc)
        else:
            now_dt = now.astimezone(timezone.utc)

        delay = (parsed - now_dt).total_seconds()
        return 0.0 if delay < 0.0 else float(delay)

    def _backoff_delay(self, attempt: int, jitter: float) -> float:
        delay = self.base_delay * self.multiplier ** (attempt - 1)
        delay = min(delay, self.max_delay)
        return delay * jitter
