from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft202012Validator

from .utils import ensure_dir


DEFAULT_SCHEMA = None


@dataclass
class Config:
    path: Path
    data: dict[str, Any]

    @property
    def output_dir(self) -> Path:
        return Path(self.data["project"]["output_dir"])

    @property
    def description(self) -> str:
        return self.data["description"]

    @property
    def schema(self) -> dict[str, Any] | None:
        return self.data["schema"]

    @property
    def is_schema_free(self) -> bool:
        return self.schema is None

    @property
    def output_format(self) -> str:
        return "text" if self.is_schema_free else "json"


def _deep_merge(defaults: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(defaults)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def default_config() -> dict[str, Any]:
    # Defaults define the public config contract; YAML files can override any nested key.
    return {
        "project": {"name": "pilot", "output_dir": "runs/pilot", "seed": 42},
        "description": "Describe the dataset to generate.",
        "schema": DEFAULT_SCHEMA,
        "models": {
            "strategic": {"base_url": "https://openrouter.ai/api/v1", "api_key_env": "OPENROUTER_API_KEY", "model": ""},
            "bulk": {"base_url": "https://openrouter.ai/api/v1", "api_key_env": "OPENROUTER_API_KEY", "model": ""},
            "critic": {"base_url": "https://openrouter.ai/api/v1", "api_key_env": "OPENROUTER_API_KEY", "model": ""},
        },
        "taxonomy": {"depth": 2, "factors": None, "best_of_n": 2, "review_mode": "auto_accept", "children_per_node": 4},
        "generation": {
            "target_size": 50,
            "overgenerate_ratio": 1.3,
            "scenarios_per_mix": 3,
            "complexity_ratio": 0.3,
            "max_refine_attempts": 2,
            "concurrency": 4,
            "checkpoint_every": 50,
        },
        "evaluation": {
            "dedupe": True,
            "coverage": True,
            "coverage_mode": "lineage",
            "complexity": False,
            "complexity_batch_size": 5,
            "complexity_samples_per_item": 2,
            "decontaminate_against": [],
            "diversity": {
                "enabled": False,
                "embedding_model": "sentence-transformers/all-MiniLM-L6-v2",
                "k_local": 10,
                "sample_cap": 1000,
                "text_field": None,
            },
        },
    }


def load_config(path: str | Path) -> Config:
    # Load YAML, overlay defaults, validate, then ensure artifact output exists.
    config_path = Path(path)
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    data = _deep_merge(default_config(), raw)
    cfg = Config(path=config_path, data=data)
    validate_config(cfg)
    ensure_dir(cfg.output_dir)
    return cfg


def validate_config(cfg: Config) -> None:
    # Required text and model-role checks catch most config mistakes before any API call.
    if not isinstance(cfg.description, str) or not cfg.description.strip():
        raise ValueError("Config requires a non-empty description.")

    # Schema is optional: absent/null configs switch the generator into text mode.
    if cfg.schema is not None:
        validate_schema_subset(cfg.schema)

    for role in ("strategic", "bulk", "critic"):
        model_cfg = cfg.data["models"].get(role, {})
        if not model_cfg.get("model"):
            raise ValueError(f"models.{role}.model is required.")
        if not model_cfg.get("base_url"):
            raise ValueError(f"models.{role}.base_url is required.")

    review_mode = cfg.data["taxonomy"].get("review_mode")
    if review_mode not in {"auto_accept", "write_then_edit", "interactive_confirm"}:
        raise ValueError("taxonomy.review_mode must be auto_accept, write_then_edit, or interactive_confirm.")

    # Evaluation modes are explicit because they change cost and whether model calls happen.
    coverage_mode = cfg.data["evaluation"].get("coverage_mode", "lineage")
    if coverage_mode not in {"lineage", "reassign", "both"}:
        raise ValueError("evaluation.coverage_mode must be lineage, reassign, or both.")

    diversity = cfg.data["evaluation"].get("diversity", {})
    if int(diversity.get("sample_cap", 1000)) <= 0:
        raise ValueError("evaluation.diversity.sample_cap must be positive.")
    if int(diversity.get("k_local", 10)) <= 0:
        raise ValueError("evaluation.diversity.k_local must be positive.")


def validate_schema_subset(schema: dict[str, Any]) -> None:
    # Keep the supported JSON Schema surface deliberately small and testable.
    Draft202012Validator.check_schema(schema)
    allowed = {"object", "string", "number", "integer", "boolean", "array"}

    def walk(node: dict[str, Any], path: str) -> None:
        node_type = node.get("type")
        if node_type not in allowed:
            raise ValueError(f"Unsupported schema type at {path}: {node_type}")
        if "enum" in node and not isinstance(node["enum"], list):
            raise ValueError(f"enum must be a list at {path}")
        if node_type == "object":
            props = node.get("properties", {})
            if not isinstance(props, dict):
                raise ValueError(f"object properties must be a mapping at {path}")
            for name, child in props.items():
                walk(child, f"{path}.{name}")
        if node_type == "array":
            if "items" not in node:
                raise ValueError(f"array schema requires items at {path}")
            walk(node["items"], f"{path}[]")

    walk(schema, "$")


def estimate_calls(cfg: Config) -> dict[str, int]:
    target = int(cfg.data["generation"]["target_size"])
    over = float(cfg.data["generation"]["overgenerate_ratio"])
    attempts = max(1, int(target * over + 0.999))
    complexity = bool(cfg.data["evaluation"].get("complexity"))
    return {
        "taxonomy": 1 + int(cfg.data["taxonomy"]["depth"]) * int(cfg.data["taxonomy"]["best_of_n"]) * 4,
        "generation": attempts * 4,
        "complexity": attempts * int(cfg.data["evaluation"].get("complexity_samples_per_item", 2)) if complexity else 0,
    }
