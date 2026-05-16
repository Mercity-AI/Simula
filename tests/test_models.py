from syndata.models import _reasoning_extras


def test_reasoning_extras_for_reasoning_models() -> None:
    assert _reasoning_extras("deepseek/deepseek-r1") == {"reasoning": {"effort": "low", "exclude": True}}
    assert _reasoning_extras("openai/o3-mini") == {"reasoning": {"effort": "low", "exclude": True}}


def test_reasoning_extras_skips_non_reasoning_models() -> None:
    assert _reasoning_extras("google/gemini-3-flash-preview") == {}
