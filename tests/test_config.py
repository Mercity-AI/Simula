from pathlib import Path

import pytest
import yaml

from simula.config import load_config, validate_schema_subset


def _fake_models() -> dict:
    return {
        "strategic": {"model": "fake"},
        "bulk": {"model": "fake"},
        "critic": {"model": "fake"},
    }


def _write_config(tmp_path: Path, extra: dict | None = None) -> Path:
    data = {
        "description": "Generate examples.",
        "project": {"output_dir": str(tmp_path / "run")},
        "models": _fake_models(),
    }
    if extra:
        data.update(extra)
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return path


def test_env_key_read_from_project_root_only(tmp_path: Path, monkeypatch) -> None:
    from simula.config import resolve_api_key

    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("SIMULA_TEST_ENV_KEY_XYZ=secret-123\n", encoding="utf-8")
    assert resolve_api_key("SIMULA_TEST_ENV_KEY_XYZ") == "secret-123"
    # A shell-exported variable is deliberately ignored: only the project-root .env file is read.
    monkeypatch.setenv("SIMULA_NOT_IN_ENV_FILE", "from-shell")
    assert resolve_api_key("SIMULA_NOT_IN_ENV_FILE") is None


def test_null_section_falls_back_to_defaults(tmp_path: Path) -> None:
    cfg = load_config(_write_config(tmp_path, {"evaluation": None}))
    assert cfg.evaluation.coverage_mode == "lineage"
    assert cfg.evaluation.dedupe is True


def test_rejects_nonpositive_target_size(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="target_size"):
        load_config(_write_config(tmp_path, {"generation": {"target_size": 0}}))


def test_rejects_overgenerate_ratio_below_one(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="overgenerate_ratio"):
        load_config(_write_config(tmp_path, {"generation": {"overgenerate_ratio": 0.5}}))


def test_missing_api_key_warns_but_does_not_fail(tmp_path: Path, capsys, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)  # no .env here, so the provider key cannot resolve
    real_models = {role: {"model": "vendor/real-model"} for role in ("strategic", "bulk", "critic")}
    cfg = load_config(
        _write_config(
            tmp_path,
            {"provider": {"base_url": "https://example", "api_key_env": "SIMULA_DEFINITELY_MISSING_KEY"}, "models": real_models},
        )
    )  # must not raise
    assert cfg.data["models"]["bulk"]["model"] == "vendor/real-model"
    assert "no API key resolved" in capsys.readouterr().err


def test_config_defaults(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "description": "Generate examples.",
                "project": {"output_dir": str(tmp_path / "run")},
                "models": {
                    "strategic": {"base_url": "fake", "model": "fake"},
                    "bulk": {"base_url": "fake", "model": "fake"},
                    "critic": {"base_url": "fake", "model": "fake"},
                },
            }
        )
    )
    cfg = load_config(path)
    assert cfg.data["generation"]["target_size"] == 50
    assert cfg.schema is None
    assert cfg.is_schema_free is True
    assert cfg.output_format == "text"


def test_config_schema_mode(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "description": "Generate examples.",
                "project": {"output_dir": str(tmp_path / "run")},
                "schema": {
                    "type": "object",
                    "required": ["input", "output"],
                    "properties": {"input": {"type": "string"}, "output": {"type": "string"}},
                },
                "models": {
                    "strategic": {"base_url": "fake", "model": "fake"},
                    "bulk": {"base_url": "fake", "model": "fake"},
                    "critic": {"base_url": "fake", "model": "fake"},
                },
            }
        )
    )
    cfg = load_config(path)
    assert cfg.schema is not None
    assert cfg.schema["required"] == ["input", "output"]
    assert cfg.output_format == "json"


def test_diversity_defaults(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "description": "Generate examples.",
                "project": {"output_dir": str(tmp_path / "run")},
                "models": {
                    "strategic": {"base_url": "fake", "model": "fake"},
                    "bulk": {"base_url": "fake", "model": "fake"},
                    "critic": {"base_url": "fake", "model": "fake"},
                },
            }
        )
    )
    cfg = load_config(path)
    assert cfg.data["evaluation"]["diversity"]["sample_cap"] == 1000
    assert cfg.data["evaluation"]["diversity"]["text_field"] is None


def test_strategy_guidance_defaults_to_none_and_omits_block(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "description": "Generate examples.",
                "project": {"output_dir": str(tmp_path / "run")},
                "models": {
                    "strategic": {"base_url": "fake", "model": "fake"},
                    "bulk": {"base_url": "fake", "model": "fake"},
                    "critic": {"base_url": "fake", "model": "fake"},
                },
            }
        )
    )
    cfg = load_config(path)
    assert cfg.data["strategy"]["guidance"] is None
    rendered = cfg.prompts.strategy_prompt(cfg.description, {"factors": []}, [], cfg.data["strategy"].get("guidance"))
    assert "User guidance" not in rendered


def test_strategy_guidance_is_woven_into_prompt(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "description": "Generate examples.",
                "project": {"output_dir": str(tmp_path / "run")},
                "strategy": {"guidance": "Make billing the most common branch."},
                "models": {
                    "strategic": {"base_url": "fake", "model": "fake"},
                    "bulk": {"base_url": "fake", "model": "fake"},
                    "critic": {"base_url": "fake", "model": "fake"},
                },
            }
        )
    )
    cfg = load_config(path)
    rendered = cfg.prompts.strategy_prompt(cfg.description, {"factors": []}, [], cfg.data["strategy"].get("guidance"))
    assert "User guidance" in rendered
    assert "Make billing the most common branch." in rendered


def test_strategy_guidance_rejects_non_string(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "description": "Generate examples.",
                "project": {"output_dir": str(tmp_path / "run")},
                "strategy": {"guidance": 123},
                "models": {
                    "strategic": {"base_url": "fake", "model": "fake"},
                    "bulk": {"base_url": "fake", "model": "fake"},
                    "critic": {"base_url": "fake", "model": "fake"},
                },
            }
        )
    )
    with pytest.raises(ValueError, match="strategy.guidance must be a string"):
        load_config(path)


def _sampling_config(tmp_path: Path, sampling: dict) -> Path:
    path = tmp_path / "config.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "description": "Generate examples.",
                "project": {"output_dir": str(tmp_path / "run")},
                "sampling": sampling,
                "models": {
                    "strategic": {"base_url": "fake", "model": "fake"},
                    "bulk": {"base_url": "fake", "model": "fake"},
                    "critic": {"base_url": "fake", "model": "fake"},
                },
            }
        )
    )
    return path


def test_sampling_tasks_valid_config_loads(tmp_path: Path) -> None:
    cfg = load_config(_sampling_config(tmp_path, {"tasks": {"generate": {"temperature": 1.1, "min_p": 0.05}}}))
    assert cfg.data["sampling"]["tasks"]["generate"]["temperature"] == 1.1


def test_sampling_rejects_unknown_task(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="unknown task"):
        load_config(_sampling_config(tmp_path, {"tasks": {"generation": {"temperature": 1.1}}}))


def test_sampling_rejects_non_mapping_params(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="must be a mapping"):
        load_config(_sampling_config(tmp_path, {"tasks": {"generate": 1.1}}))


def test_sampling_rejects_non_numeric_temperature(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="must be a number"):
        load_config(_sampling_config(tmp_path, {"tasks": {"generate": {"temperature": "hot"}}}))


def test_schema_subset_rejects_missing_array_items() -> None:
    with pytest.raises(ValueError):
        validate_schema_subset({"type": "array"})


def test_prompt_module_overrides_subset_and_falls_back(tmp_path: Path) -> None:
    prompt_dir = tmp_path / "config"
    prompt_dir.mkdir()
    (prompt_dir / "prompts.py").write_text(
        "\n".join(
            [
                'SYSTEM_JSON = "custom system"',
                "",
                "def strategy_prompt(description, taxonomy, valid_paths, guidance=None, count=None):",
                '    return f"custom strategy for {description}: {len(taxonomy[\'factors\'])}"',
            ]
        )
    )
    path = tmp_path / "config.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "description": "Generate examples.",
                "project": {"output_dir": str(tmp_path / "run")},
                "prompts": {"module": "config/prompts.py"},
                "models": {
                    "strategic": {"base_url": "fake", "model": "fake"},
                    "bulk": {"base_url": "fake", "model": "fake"},
                    "critic": {"base_url": "fake", "model": "fake"},
                },
            }
        )
    )
    cfg = load_config(path)
    assert cfg.prompts.SYSTEM_JSON == "custom system"
    assert cfg.prompts.strategy_prompt("NER", {"factors": [{}, {}]}, []) == "custom strategy for NER: 2"
    assert "Dataset description:" in cfg.prompts.factor_prompt("NER")


def test_prompt_module_string_shorthand_is_supported(tmp_path: Path) -> None:
    (tmp_path / "prompts.py").write_text(
        "\n".join(
            [
                "def generate_text_prompt(description, meta_prompt):",
                '    return f"{description}::{meta_prompt}"',
            ]
        )
    )
    path = tmp_path / "config.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "description": "Generate examples.",
                "project": {"output_dir": str(tmp_path / "run")},
                "prompts": "prompts.py",
                "models": {
                    "strategic": {"base_url": "fake", "model": "fake"},
                    "bulk": {"base_url": "fake", "model": "fake"},
                    "critic": {"base_url": "fake", "model": "fake"},
                },
            }
        )
    )
    cfg = load_config(path)
    assert cfg.prompts.generate_text_prompt("desc", "meta") == "desc::meta"


def test_prompt_module_rejects_bad_signature(tmp_path: Path) -> None:
    (tmp_path / "prompts.py").write_text(
        "\n".join(
            [
                "def strategy_prompt(description):",
                '    return "bad"',
            ]
        )
    )
    path = tmp_path / "config.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "description": "Generate examples.",
                "project": {"output_dir": str(tmp_path / "run")},
                "prompts": {"module": "prompts.py"},
                "models": {
                    "strategic": {"base_url": "fake", "model": "fake"},
                    "bulk": {"base_url": "fake", "model": "fake"},
                    "critic": {"base_url": "fake", "model": "fake"},
                },
            }
        )
    )
    with pytest.raises(ValueError, match="strategy_prompt must accept parameters"):
        load_config(path)


def test_strategy_count_bounds(tmp_path: Path) -> None:
    assert load_config(_write_config(tmp_path, {"strategy": {"count": 6}})).strategy.count == 6
    assert load_config(_write_config(tmp_path)).strategy.count is None
    with pytest.raises(ValueError):
        load_config(_write_config(tmp_path, {"strategy": {"count": 0}}))
    with pytest.raises(ValueError):
        load_config(_write_config(tmp_path, {"strategy": {"count": 13}}))


def test_optimization_defaults(tmp_path: Path) -> None:
    cfg = load_config(_write_config(tmp_path))
    assert cfg.taxonomy.best_of_n == 1
    assert cfg.generation.meta_prompts_per_mix == 1
    assert cfg.generation.meta_prompts is True and cfg.generation.record_batch_size == 1 and cfg.generation.meta_batch_size == 1
    assert cfg.generation.critic is True


def test_meta_prompt_knobs_require_the_meta_prompt_step(tmp_path: Path) -> None:
    for knob in ("meta_prompts_per_mix", "meta_batch_size"):
        with pytest.raises(ValueError, match=knob):
            load_config(_write_config(tmp_path, {"generation": {"meta_prompts": False, knob: 2}}))
    # record_batch_size batches the record step on either path (mode B with meta prompts, mode C without).
    assert load_config(_write_config(tmp_path, {"generation": {"record_batch_size": 4}})).generation.record_batch_size == 4
    assert load_config(_write_config(tmp_path, {"generation": {"meta_prompts": False, "record_batch_size": 4}})).generation.record_batch_size == 4


def test_model_picked_needs_the_combined_path(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="model_picked requires"):
        load_config(_write_config(tmp_path, {"generation": {"model_picked": {"topic": 1}}}))
    with pytest.raises(ValueError, match="levels must be"):
        load_config(_write_config(tmp_path, {"generation": {"meta_prompts": False, "model_picked": {"topic": 0}}}))
    cfg = load_config(_write_config(tmp_path, {"generation": {"meta_prompts": False, "model_picked": {"topic": 2}}}))
    assert cfg.generation.model_picked == {"topic": 2}


def test_unknown_or_renamed_keys_are_errors(tmp_path: Path) -> None:
    # A typo or an old knob name must not be silently ignored.
    for extra in ({"generation": {"scenarios_per_mix": 3}}, {"generation": {"batch_size": 5}}, {"taxonomy": {"depht": 3}}, {"genration": {}}):
        with pytest.raises(ValueError, match="Extra inputs are not permitted"):
            load_config(_write_config(tmp_path, extra))
    # Role-level decoding params stay open (they pass through to the provider).
    models = {**_fake_models(), "bulk": {"model": "fake", "min_p": 0.05}}
    assert load_config(_write_config(tmp_path, {"models": models})).models.bulk.model == "fake"


def test_taxonomy_critic_off_needs_a_single_proposal(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="taxonomy.critic: false requires"):
        load_config(_write_config(tmp_path, {"taxonomy": {"critic": False, "best_of_n": 2}}))
    assert load_config(_write_config(tmp_path, {"taxonomy": {"critic": False}})).taxonomy.critic is False


def test_stop_after_meta_prompts_needs_the_meta_prompt_step(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="stop_after: meta_prompts requires"):
        load_config(_write_config(tmp_path, {"generation": {"meta_prompts": False, "stop_after": "meta_prompts"}}))
    # The CLI flag is applied after load and must be rejected the same way.
    from simula.cli import main

    path = _write_config(tmp_path, {"generation": {"meta_prompts": False, "record_batch_size": 2}})
    assert main(["generate", str(path), "--stop-after", "meta_prompts", "--quiet"]) == 1


def test_code_level_is_validated(tmp_path: Path) -> None:
    assert load_config(_write_config(tmp_path, {"generation": {"code_level": {"topic": 1}}})).generation.code_level == {"topic": 1}
    with pytest.raises(ValueError, match="code_level levels must be"):
        load_config(_write_config(tmp_path, {"generation": {"code_level": {"topic": 0}}}))
    with pytest.raises(ValueError, match="picked by one or the other"):
        load_config(_write_config(tmp_path, {"generation": {"meta_prompts": False, "code_level": {"topic": 1}, "model_picked": {"topic": 1}}}))
