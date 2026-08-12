"""t1-sse-parser の採点用テスト。仕様は ../spec.md。"""

from sse_parser import SSEEvent, SSEParser


def parse(*chunks: str) -> list[SSEEvent]:
    p = SSEParser()
    out: list[SSEEvent] = []
    for c in chunks:
        out.extend(p.feed(c))
    out.extend(p.close())
    return out


# --- 基本 -----------------------------------------------------------------


def test_single_event_defaults_to_message():
    assert parse("data: hello\n\n") == [SSEEvent(event="message", data="hello", id=None)]


def test_explicit_event_type():
    assert parse("event: citations\ndata: [1]\n\n") == [
        SSEEvent(event="citations", data="[1]", id=None)
    ]


def test_multiple_data_lines_joined_with_newline():
    assert parse("data: a\ndata: b\n\n") == [SSEEvent("message", "a\nb", None)]


def test_multiple_events_in_one_chunk():
    assert parse("data: a\n\ndata: b\n\n") == [
        SSEEvent("message", "a", None),
        SSEEvent("message", "b", None),
    ]


def test_incomplete_block_yields_nothing():
    p = SSEParser()
    assert p.feed("data: a\n") == []


# --- 行の解釈 -------------------------------------------------------------


def test_comment_line_is_ignored():
    assert parse(":keep-alive\n\n") == []


def test_comment_does_not_break_surrounding_event():
    assert parse("data: a\n:ping\ndata: b\n\n") == [SSEEvent("message", "a\nb", None)]


def test_no_space_after_colon():
    assert parse("data:x\n\n") == [SSEEvent("message", "x", None)]


def test_only_one_leading_space_is_stripped():
    assert parse("data:  x\n\n") == [SSEEvent("message", " x", None)]


def test_field_without_colon_appends_empty_value():
    assert parse("data: a\ndata\ndata: b\n\n") == [SSEEvent("message", "a\n\nb", None)]


def test_unknown_field_is_ignored():
    assert parse("foo: bar\ndata: a\n\n") == [SSEEvent("message", "a", None)]


def test_empty_data_value_still_dispatches():
    assert parse("data:\n\n") == [SSEEvent("message", "", None)]


def test_trailing_newline_in_data_is_preserved():
    # データバッファは "a\n" + "\n" → 末尾 1 個だけ落として "a\n"
    assert parse("data: a\ndata\n\n") == [SSEEvent("message", "a\n", None)]


def test_blank_block_resets_event_type_without_dispatching():
    assert parse("event: x\n\ndata: a\n\n") == [SSEEvent("message", "a", None)]


def test_event_type_resets_after_dispatch():
    assert parse("event: x\ndata: a\n\ndata: b\n\n") == [
        SSEEvent("x", "a", None),
        SSEEvent("message", "b", None),
    ]


# --- id / retry -----------------------------------------------------------


def test_id_persists_across_events():
    assert parse("id: 1\ndata: a\n\ndata: b\n\n") == [
        SSEEvent("message", "a", "1"),
        SSEEvent("message", "b", "1"),
    ]


def test_id_containing_nul_is_ignored():
    assert parse("id: 1\ndata: a\n\nid: 2\x003\ndata: b\n\n") == [
        SSEEvent("message", "a", "1"),
        SSEEvent("message", "b", "1"),
    ]


def test_retry_is_parsed():
    p = SSEParser()
    p.feed("retry: 3000\ndata: a\n\n")
    assert p.retry == 3000


def test_retry_non_digit_is_ignored():
    p = SSEParser()
    p.feed("retry: 3000\n\nretry: 5s\ndata: a\n\n")
    assert p.retry == 3000


def test_retry_non_ascii_digit_is_ignored():
    p = SSEParser()
    p.feed("retry: ３０\ndata: a\n\n")
    assert p.retry is None


# --- 改行コード -----------------------------------------------------------


def test_crlf_line_endings():
    assert parse("event: x\r\ndata: a\r\n\r\n") == [SSEEvent("x", "a", None)]


def test_cr_only_line_endings():
    assert parse("event: x\rdata: a\r\r") == [SSEEvent("x", "a", None)]


def test_mixed_line_endings():
    assert parse("data: a\r\ndata: b\rdata: c\n\n") == [
        SSEEvent("message", "a\nb\nc", None)
    ]


# --- チャンク境界 ---------------------------------------------------------


def test_chunk_split_mid_line():
    assert parse("data: he", "llo\n\n") == [SSEEvent("message", "hello", None)]


def test_chunk_split_mid_field_name():
    assert parse("ev", "ent: x\nda", "ta: a\n\n") == [SSEEvent("x", "a", None)]


def test_chunk_split_between_cr_and_lf():
    assert parse("data: a\r", "\n\r\n") == [SSEEvent("message", "a", None)]


def test_chunk_split_between_cr_and_lf_on_blank_line():
    assert parse("data: a\r\n\r", "\n") == [SSEEvent("message", "a", None)]


def test_byte_at_a_time():
    stream = "event: x\r\ndata: a\ndata: b\r\r"
    assert parse(*stream) == [SSEEvent("x", "a\nb", None)]


# --- BOM ------------------------------------------------------------------


def test_leading_bom_is_stripped():
    assert parse("﻿data: a\n\n") == [SSEEvent("message", "a", None)]


def test_second_bom_is_kept_as_content():
    assert parse("﻿data: ﻿a\n\n") == [SSEEvent("message", "﻿a", None)]


def test_empty_feed_does_not_consume_bom_position():
    p = SSEParser()
    assert p.feed("") == []
    assert p.feed("﻿data: a\n\n") == [SSEEvent("message", "a", None)]


# --- 終了 -----------------------------------------------------------------


def test_close_discards_incomplete_block():
    p = SSEParser()
    p.feed("data: a\n")
    assert p.close() == []


def test_close_returns_empty_list():
    p = SSEParser()
    p.feed("data: a\n\n")
    assert p.close() == []
