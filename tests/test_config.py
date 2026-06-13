from pathlib import Path

import pytest
import yaml

from syndata.config import load_config, validate_schema_subset


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
                "def strategy_prompt(description, taxonomy):",
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
