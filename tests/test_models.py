from syndata.models import _reasoning_extras, resolve_sampling


def _config(role_cfg: dict, sampling: dict | None = None) -> dict:
    config = {"models": {"bulk": {"base_url": "x", "model": "test-model", **role_cfg}}}
    if sampling is not None:
        config["sampling"] = sampling
    return config


def test_sampling_defaults_when_nothing_configured() -> None:
    call_params, extra_body = resolve_sampling(_config({}), "bulk", "generate")
    assert call_params == {"temperature": 0.7, "max_tokens": 32768}
    assert extra_body == {}


def test_sampling_role_static_params_override_defaults() -> None:
    call_params, _ = resolve_sampling(_config({"temperature": 0.3, "max_tokens": 1000}), "bulk", "generate")
    assert call_params["temperature"] == 0.3
    assert call_params["max_tokens"] == 1000


def test_sampling_task_override_wins_over_role_static() -> None:
    config = _config({"temperature": 0.3}, {"tasks": {"generate": {"temperature": 1.2}}})
    generate_params, _ = resolve_sampling(config, "bulk", "generate")
    repair_params, _ = resolve_sampling(config, "bulk", "repair")
    assert generate_params["temperature"] == 1.2  # task override applies
    assert repair_params["temperature"] == 0.3  # untargeted task keeps role static


def test_sampling_known_params_top_level_unknowns_ride_extra_body() -> None:
    config = _config({}, {"tasks": {"generate": {"top_p": 0.9, "min_p": 0.05, "repetition_penalty": 1.1}}})
    call_params, extra_body = resolve_sampling(config, "bulk", "generate")
    assert call_params["top_p"] == 0.9
    assert "min_p" not in call_params and "repetition_penalty" not in call_params
    assert extra_body == {"min_p": 0.05, "repetition_penalty": 1.1}


def test_sampling_task_extras_merge_on_top_of_role_extra_body() -> None:
    config = _config({"extra_body": {"foo": 1}}, {"tasks": {"generate": {"min_p": 0.05}}})
    _, extra_body = resolve_sampling(config, "bulk", "generate")
    assert extra_body == {"foo": 1, "min_p": 0.05}


def test_reasoning_extras_for_reasoning_models() -> None:
    assert _reasoning_extras("deepseek/deepseek-r1") == {"reasoning": {"effort": "low", "exclude": True}}
    assert _reasoning_extras("openai/o3-mini") == {"reasoning": {"effort": "low", "exclude": True}}


def test_reasoning_extras_skips_non_reasoning_models() -> None:
    assert _reasoning_extras("google/gemini-3-flash-preview") == {}
