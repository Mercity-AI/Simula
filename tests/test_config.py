from pathlib import Path

import pytest
import yaml

from syndata.config import load_config, validate_schema_subset


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
    from syndata.config import resolve_api_key

    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("SYNDATA_TEST_ENV_KEY_XYZ=secret-123\n", encoding="utf-8")
    assert resolve_api_key("SYNDATA_TEST_ENV_KEY_XYZ") == "secret-123"
    # A shell-exported variable is deliberately ignored: only the project-root .env file is read.
    monkeypatch.setenv("SYNDATA_NOT_IN_ENV_FILE", "from-shell")
    assert resolve_api_key("SYNDATA_NOT_IN_ENV_FILE") is None


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
            {"provider": {"base_url": "https://example", "api_key_env": "SYNDATA_DEFINITELY_MISSING_KEY"}, "models": real_models},
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
    rendered = cfg.prompts.strategy_prompt(cfg.description, {"factors": []}, cfg.data["strategy"].get("guidance"))
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
    rendered = cfg.prompts.strategy_prompt(cfg.description, {"factors": []}, cfg.data["strategy"].get("guidance"))
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
                "def strategy_prompt(description, taxonomy, guidance=None):",
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
    assert cfg.prompts.strategy_prompt("NER", {"factors": [{}, {}]}) == "custom strategy for NER: 2"
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
