"""t2-retry-policy の採点用テスト。仕様は ../spec.md。"""

from datetime import datetime, timedelta, timezone

import pytest

from retry_policy import RetryPolicy

NOW = datetime(2026, 8, 12, 9, 0, 0, tzinfo=timezone.utc)


# --- 打ち切り -------------------------------------------------------------


def test_attempt_below_one_raises():
    with pytest.raises(ValueError):
        RetryPolicy().next_delay(0, status=500)


def test_gives_up_at_max_attempts():
    assert RetryPolicy(max_attempts=3).next_delay(3, status=500) is None


def test_gives_up_beyond_max_attempts():
    assert RetryPolicy(max_attempts=3).next_delay(4, status=500) is None


def test_max_attempts_one_never_retries():
    assert RetryPolicy(max_attempts=1).next_delay(1, status=500) is None


# --- リトライ可否 ---------------------------------------------------------


@pytest.mark.parametrize("status", [408, 429, 500, 502, 503, 504, 599])
def test_retryable_statuses(status):
    assert RetryPolicy().next_delay(1, status=status) == pytest.approx(0.5)


@pytest.mark.parametrize("status", [200, 204, 301, 400, 401, 403, 404, 409, 422])
def test_non_retryable_statuses(status):
    assert RetryPolicy().next_delay(1, status=status) is None


@pytest.mark.parametrize("error", ["timeout", "connection"])
def test_retryable_transport_errors(error):
    assert RetryPolicy().next_delay(1, error=error) == pytest.approx(0.5)


@pytest.mark.parametrize("error", [None, "other", "ssl", ""])
def test_non_retryable_transport_errors(error):
    assert RetryPolicy().next_delay(1, error=error) is None


def test_error_is_ignored_when_status_present():
    # status があるときは error を見ない
    assert RetryPolicy().next_delay(1, status=404, error="timeout") is None


# --- 指数バックオフ -------------------------------------------------------


def test_backoff_grows_exponentially():
    p = RetryPolicy(max_attempts=5)
    assert p.next_delay(1, status=500) == pytest.approx(0.5)
    assert p.next_delay(2, status=500) == pytest.approx(1.0)
    assert p.next_delay(3, status=500) == pytest.approx(2.0)
    assert p.next_delay(4, status=500) == pytest.approx(4.0)


def test_backoff_is_capped_by_max_delay():
    p = RetryPolicy(max_attempts=9, base_delay=1.0, multiplier=10.0, max_delay=8.0)
    assert p.next_delay(3, status=500) == pytest.approx(8.0)


def test_jitter_scales_backoff():
    p = RetryPolicy(max_attempts=5)
    assert p.next_delay(2, status=500, jitter=0.5) == pytest.approx(0.5)
    assert p.next_delay(2, status=500, jitter=0.0) == pytest.approx(0.0)


def test_custom_parameters():
    p = RetryPolicy(max_attempts=4, base_delay=2.0, multiplier=3.0, max_delay=100.0)
    assert p.next_delay(3, status=503) == pytest.approx(18.0)


# --- Retry-After: 秒数 ----------------------------------------------------


def test_retry_after_seconds_wins_over_backoff():
    assert RetryPolicy().next_delay(1, status=429, retry_after="3") == pytest.approx(3.0)


def test_retry_after_is_not_jittered():
    got = RetryPolicy().next_delay(1, status=429, retry_after="3", jitter=0.5)
    assert got == pytest.approx(3.0)


def test_retry_after_is_not_capped_by_max_delay():
    got = RetryPolicy().next_delay(1, status=429, retry_after="120")
    assert got == pytest.approx(120.0)


def test_retry_after_zero():
    assert RetryPolicy().next_delay(1, status=429, retry_after="0") == pytest.approx(0.0)


def test_retry_after_ignored_on_non_retryable_status():
    assert RetryPolicy().next_delay(1, status=404, retry_after="3") is None


def test_retry_after_ignored_past_max_attempts():
    assert RetryPolicy(max_attempts=2).next_delay(2, status=429, retry_after="3") is None


# --- Retry-After: HTTP-date ----------------------------------------------


def test_retry_after_http_date_future():
    got = RetryPolicy().next_delay(
        1, status=503, retry_after="Wed, 12 Aug 2026 09:00:30 GMT", now=NOW
    )
    assert got == pytest.approx(30.0)


def test_retry_after_http_date_in_the_past_clamps_to_zero():
    got = RetryPolicy().next_delay(
        1, status=503, retry_after="Wed, 12 Aug 2026 08:59:00 GMT", now=NOW
    )
    assert got == pytest.approx(0.0)


def test_retry_after_http_date_with_naive_now_treated_as_utc():
    got = RetryPolicy().next_delay(
        1,
        status=503,
        retry_after="Wed, 12 Aug 2026 09:00:10 GMT",
        now=datetime(2026, 8, 12, 9, 0, 0),
    )
    assert got == pytest.approx(10.0)


def test_retry_after_http_date_with_offset():
    got = RetryPolicy().next_delay(
        1,
        status=503,
        retry_after="Wed, 12 Aug 2026 10:00:20 +0100",
        now=NOW,
    )
    assert got == pytest.approx(20.0)


def test_retry_after_http_date_without_now_raises():
    with pytest.raises(ValueError):
        RetryPolicy().next_delay(
            1, status=503, retry_after="Wed, 12 Aug 2026 09:00:30 GMT"
        )


def test_retry_after_http_date_is_not_capped():
    future = NOW + timedelta(minutes=10)
    stamp = future.strftime("%a, %d %b %Y %H:%M:%S GMT")
    got = RetryPolicy().next_delay(1, status=503, retry_after=stamp, now=NOW)
    assert got == pytest.approx(600.0)


# --- Retry-After: 不正値 --------------------------------------------------


@pytest.mark.parametrize("value", ["soon", "-5", "3.5", "", "１０"])
def test_unparsable_retry_after_falls_back_to_backoff(value):
    got = RetryPolicy(max_attempts=5).next_delay(2, status=503, retry_after=value)
    assert got == pytest.approx(1.0)


def test_unparsable_retry_after_does_not_raise_without_now():
    got = RetryPolicy().next_delay(1, status=503, retry_after="soon")
    assert got == pytest.approx(0.5)


def test_retry_after_with_surrounding_whitespace():
    got = RetryPolicy().next_delay(1, status=429, retry_after="  3  ")
    assert got == pytest.approx(3.0)
