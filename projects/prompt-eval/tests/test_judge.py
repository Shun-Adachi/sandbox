"""judge プロンプトの読み込みと形の検査。OpenAI は呼ばない。"""

import pytest

from prompt_eval.judge import JudgeVerdict, load_judge_prompt


def test_同梱のjudgeプロンプトが全版読み込める():
    from prompt_eval.config import JUDGE_PROMPT_DIR

    paths = sorted(JUDGE_PROMPT_DIR.glob("*.yaml"))
    assert len(paths) >= 2  # v1, v2
    for path in paths:
        prompt = load_judge_prompt(path)
        assert prompt["version"] == path.stem
        assert prompt["model"]
        assert "{text}" in prompt["user_template"]
        assert "{summary}" in prompt["user_template"]


def test_user_templateに原文と要約が埋まる():
    prompt = load_judge_prompt()
    rendered = prompt["user_template"].format(text="原文です", summary="要約です")
    assert "原文です" in rendered
    assert "要約です" in rendered


def test_必須キーが欠けたプロンプトは拒否される(tmp_path):
    p = tmp_path / "broken.yaml"
    p.write_text("model: gpt-4o-mini\nsystem: s\n", encoding="utf-8")
    with pytest.raises(ValueError, match="user_template"):
        load_judge_prompt(p)


def test_judgeの出力スキーマは1から3に閉じている():
    assert JudgeVerdict(reason="r", score=3).score == 3
    with pytest.raises(ValueError):
        JudgeVerdict(reason="r", score=4)
    with pytest.raises(ValueError):
        JudgeVerdict(reason="r", score=0)
