import pytest

from llm_api.config import settings
from llm_api.prompts import PromptNotFoundError, available_versions, load_prompt


def test_extract_versions_are_discovered():
    assert available_versions("extract") == ["v1", "v2", "v3"]


def test_load_prompt_reads_model_and_temperature():
    prompt = load_prompt("extract", "v1")
    assert prompt.model == "gpt-4o-mini"
    assert prompt.temperature == 0.0
    assert "category" in prompt.system


def test_qa_prompt_carries_fallback_answer():
    prompt = load_prompt("qa", "v1")
    assert prompt.fallback_answer is not None
    assert "FAQ に見つかりませんでした" in prompt.fallback_answer


def test_missing_version_raises_with_available_versions_listed():
    with pytest.raises(PromptNotFoundError, match="v1, v2, v3"):
        load_prompt("extract", "v99")


def test_missing_version_error_does_not_leak_the_filesystem_path():
    """このメッセージは 400 の detail としてクライアントに返るため。

    "extract/v99" のような用途と版の区切りは含まれてよい。
    漏らしてはいけないのはサーバー上の実際のパス。
    """
    with pytest.raises(PromptNotFoundError) as exc_info:
        load_prompt("extract", "v99")
    message = str(exc_info.value)
    assert str(settings.prompts_dir) not in message
    assert ".yaml" not in message


@pytest.mark.parametrize("version", ["../../etc/passwd", ".hidden", "a\\b"])
def test_version_cannot_escape_the_prompts_directory(version):
    with pytest.raises(PromptNotFoundError):
        load_prompt("extract", version)


def test_render_reports_the_missing_variable():
    prompt = load_prompt("qa", "v1")
    with pytest.raises(KeyError, match="context"):
        prompt.render_system()
