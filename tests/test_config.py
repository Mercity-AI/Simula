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
