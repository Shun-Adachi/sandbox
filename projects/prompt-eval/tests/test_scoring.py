"""採点ロジックの検査。境界(50 字ちょうど・空文字の正解)を重点的に。"""

from conftest import CORRECT_DATA, make_case, make_record

from prompt_eval.scoring import aggregate, score_all, score_record, stability


def test_全項目正解(case):
    s = score_record(make_record(), case)
    assert s["all_fields_ok"]
    assert s["summary_len_ok"]
    assert s["summary_keywords_ok"]


def test_urgencyだけ不正解(case):
    data = CORRECT_DATA | {"urgency": "中"}
    s = score_record(make_record(data=data), case)
    assert not s["all_fields_ok"]
    assert s["field_ok"]["urgency"] is False
    assert s["field_ok"]["category"] is True


def test_正解が空文字の項目は推測すると不正解():
    # name/company の空文字は「判断できないので空が正しい」の意味。
    # ドメイン等から推測して埋めた出力は誤りとして数えられること
    case = make_case(name="", company="")
    data = CORRECT_DATA | {"name": "鈴木", "company": "みどり製作所"}
    s = score_record(make_record(data=data), case)
    assert s["field_ok"]["name"] is False
    assert s["field_ok"]["company"] is False


def test_要約50字ちょうどはOKで51字はNG(case):
    ok = score_record(make_record(data=CORRECT_DATA | {"summary": "ログイン" + "あ" * 46}), case)
    ng = score_record(make_record(data=CORRECT_DATA | {"summary": "ログイン" + "あ" * 47}), case)
    assert ok["summary_len_ok"]
    assert not ng["summary_len_ok"]


def test_キーワードは全部含んで初めてOK():
    case = make_case(summary_keywords=["ログイン", "停止"])
    s = score_record(make_record(data=CORRECT_DATA | {"summary": "ログインできない"}), case)
    assert not s["summary_keywords_ok"]


def test_APIエラーは全項目不正解として数える(case):
    s = score_record(make_record(ok=False), case)
    assert not s["api_ok"]
    assert not s["all_fields_ok"]
    assert s["actual"] is None


def test_集計_混同行列とタグとコスト():
    cases = [
        make_case("c01", tags=["基本"]),
        make_case("c02", tags=["緊急度境界"], urgency="中"),
    ]
    records = [
        make_record("c01"),  # 正解(高→高)
        make_record("c02", data=CORRECT_DATA | {"urgency": "高"}),  # 中の正解を高と誤答
    ]
    agg = aggregate(score_all(records, cases), records, cases)["v1"]

    assert agg["n"] == 2
    assert agg["all_fields_ok"] == 1
    assert agg["urgency_confusion"]["高"]["高"] == 1
    assert agg["urgency_confusion"]["中"]["高"] == 1
    assert agg["urgency_confusion"]["中"]["中"] == 0
    assert agg["tags"]["基本"] == (1, 1)
    assert agg["tags"]["緊急度境界"] == (0, 1)
    # 640 トークン × 2 件が gpt-4o-mini 料金で概算されている(ゼロや異常値でない)
    assert agg["total_tokens"] == 1280
    assert 0 < agg["cost_usd"] < 0.01


def test_集計_エラーの多い版が有利にならない():
    # v1: 1 勝 1 エラー、v2: 1 勝 1 敗。エラーを分母から外すと v1 が 100% に
    # 見えてしまう。両者とも 1/2 になることを保証する
    cases = [make_case("c01"), make_case("c02")]
    records = [
        make_record("c01", version="v1"),
        make_record("c02", version="v1", ok=False),
        make_record("c01", version="v2"),
        make_record("c02", version="v2", data=CORRECT_DATA | {"category": "質問"}),
    ]
    agg = aggregate(score_all(records, cases), records, cases)
    assert agg["v1"]["all_fields_ok"] == 1 and agg["v1"]["n"] == 2
    assert agg["v2"]["all_fields_ok"] == 1 and agg["v2"]["n"] == 2


def test_安定性_繰り返しでブレたケースを検出():
    cases = [make_case("c01"), make_case("c02")]
    records = [
        make_record("c01", repeat=0),
        make_record("c01", repeat=1),  # c01 は 2 回とも同じ答え
        make_record("c02", repeat=0),
        make_record("c02", repeat=1, data=CORRECT_DATA | {"urgency": "中"}),  # c02 はブレ
    ]
    st = stability(score_all(records, cases))
    assert st["v1"] == (1, 2)
