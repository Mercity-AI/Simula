from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft202012Validator

from . import prompts
from .tasks import TaskType
from .utils import ensure_dir


# Typed views of the structured config sections. Default values live ONLY in default_config();
# these are pure structs built from the already-merged dict in _parse_sections.
@dataclass(frozen=True)
class GenerationCfg:
    target_size: int
    overgenerate_ratio: float
    scenarios_per_mix: int
    complexity_ratio: float
    max_refine_attempts: int
    concurrency: int
    checkpoint_every: int


@dataclass(frozen=True)
class TaxonomyCfg:
    depth: int
    factors: list[dict[str, Any]] | None
    best_of_n: int
    review_mode: str
    children_per_node: int


@dataclass(frozen=True)
class DiversityCfg:
    enabled: bool
    embedding_model: str
    k_local: int
    sample_cap: int
    text_field: str | None


@dataclass(frozen=True)
class EvaluationCfg:
    dedupe: bool
    coverage: bool
    coverage_mode: str
    complexity: bool
    complexity_batch_size: int
    complexity_samples_per_item: int
    decontaminate_against: list[str]
    diversity: DiversityCfg


@dataclass
class Config:
    path: Path
    data: dict[str, Any]
    prompts: prompts.PromptSet
    generation: GenerationCfg
    taxonomy: TaxonomyCfg
    evaluation: EvaluationCfg

    @cached_property
    def validator(self) -> Draft202012Validator | None:
        # Compile the schema validator once per run and reuse it across all record validations.
        return Draft202012Validator(self.schema) if self.schema is not None else None

    @property
    def output_dir(self) -> Path:
        return Path(self.data["project"]["output_dir"])

    @property
    def seed(self) -> int:
        return int(self.data["project"].get("seed", 42))

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
        elif value is None and isinstance(merged.get(key), dict):
            # A blank section (`evaluation:` with nothing under it parses as None) must not
            # clobber a dict default — keep the defaults. `schema` is unaffected: its default
            # is None, not a dict, so `schema: null` still selects free-text mode.
            continue
        else:
            merged[key] = value
    return merged


def default_config() -> dict[str, Any]:
    # Defaults define the public config contract; YAML files can override any nested key.
    return {
        "project": {"name": "pilot", "output_dir": "runs/pilot", "seed": 42},
        "description": "Describe the dataset to generate.",
        "schema": None,
        "models": {
            "strategic": {"base_url": "https://openrouter.ai/api/v1", "api_key_env": "OPENROUTER_API_KEY", "model": ""},
            "bulk": {"base_url": "https://openrouter.ai/api/v1", "api_key_env": "OPENROUTER_API_KEY", "model": ""},
            "critic": {"base_url": "https://openrouter.ai/api/v1", "api_key_env": "OPENROUTER_API_KEY", "model": ""},
        },
        "prompts": {"module": None},
        "taxonomy": {"depth": 2, "factors": None, "best_of_n": 2, "review_mode": "auto_accept", "children_per_node": 4},
        "strategy": {"guidance": None},
        "sampling": {"tasks": {}},
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


def load_env_files(config_path: Path) -> None:
    # Load .env so `api_key_env` resolves without a manual `export`. We look next to the config
    # file and in the current directory; existing env vars win (override=False). load_dotenv
    # no-ops on a missing file. python-dotenv is a declared dependency.
    from dotenv import load_dotenv

    load_dotenv(config_path.resolve().parent / ".env", override=False)
    load_dotenv(Path.cwd() / ".env", override=False)


def _section(data: dict[str, Any], key: str) -> dict[str, Any]:
    section = data.get(key)
    return section if isinstance(section, dict) else {}


def _parse_sections(data: dict[str, Any]) -> tuple[GenerationCfg, TaxonomyCfg, EvaluationCfg]:
    # Build typed views from the already-merged dict (every key is guaranteed present by
    # _deep_merge, so we read directly and only coerce types — defaults live in default_config()).
    gen = _section(data, "generation")
    generation = GenerationCfg(
        target_size=int(gen["target_size"]),
        overgenerate_ratio=float(gen["overgenerate_ratio"]),
        scenarios_per_mix=int(gen["scenarios_per_mix"]),
        complexity_ratio=float(gen["complexity_ratio"]),
        max_refine_attempts=int(gen["max_refine_attempts"]),
        concurrency=int(gen["concurrency"]),
        checkpoint_every=int(gen["checkpoint_every"]),
    )
    tax = _section(data, "taxonomy")
    taxonomy = TaxonomyCfg(
        depth=int(tax["depth"]),
        factors=tax.get("factors"),
        best_of_n=int(tax["best_of_n"]),
        review_mode=str(tax["review_mode"]),
        children_per_node=int(tax["children_per_node"]),
    )
    ev = _section(data, "evaluation")
    div = _section(ev, "diversity")
    evaluation = EvaluationCfg(
        dedupe=bool(ev["dedupe"]),
        coverage=bool(ev["coverage"]),
        coverage_mode=str(ev["coverage_mode"]),
        complexity=bool(ev["complexity"]),
        complexity_batch_size=int(ev["complexity_batch_size"]),
        complexity_samples_per_item=int(ev["complexity_samples_per_item"]),
        decontaminate_against=[str(p) for p in (ev.get("decontaminate_against") or [])],
        diversity=DiversityCfg(
            enabled=bool(div["enabled"]),
            embedding_model=str(div["embedding_model"]),
            k_local=int(div["k_local"]),
            sample_cap=int(div["sample_cap"]),
            text_field=div.get("text_field"),
        ),
    )
    return generation, taxonomy, evaluation


def load_config(path: str | Path) -> Config:
    # Load YAML, overlay defaults, validate, then ensure artifact output exists.
    config_path = Path(path)
    load_env_files(config_path)
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    data = _deep_merge(default_config(), raw)
    prompt_set = prompts.load_prompt_set(config_path, data.get("prompts"))
    generation, taxonomy, evaluation = _parse_sections(data)
    cfg = Config(
        path=config_path,
        data=data,
        prompts=prompt_set,
        generation=generation,
        taxonomy=taxonomy,
        evaluation=evaluation,
    )
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

    missing_keys = []
    for role in ("strategic", "bulk", "critic"):
        model_cfg = cfg.data["models"].get(role, {})
        if not model_cfg.get("model"):
            raise ValueError(f"models.{role}.model is required.")
        if not model_cfg.get("base_url"):
            raise ValueError(f"models.{role}.base_url is required.")
        # Warn (don't fail — keep `validate` offline-friendly) when a real model has no resolvable key.
        if model_cfg["model"] != "fake" and not (model_cfg.get("api_key") or os.getenv(model_cfg.get("api_key_env", ""))):
            missing_keys.append(f"{role} (api_key_env={model_cfg.get('api_key_env') or 'unset'})")
    if missing_keys:
        print(f"Warning: no API key resolved for model role(s): {', '.join(missing_keys)}.", file=sys.stderr)

    if cfg.taxonomy.review_mode not in {"auto_accept", "write_then_edit", "interactive_confirm"}:
        raise ValueError("taxonomy.review_mode must be auto_accept, write_then_edit, or interactive_confirm.")

    # Strategy guidance is free-text steering woven into the strategy prompt; reject non-text early.
    guidance = _section(cfg.data, "strategy").get("guidance")
    if guidance is not None and not isinstance(guidance, str):
        raise ValueError("strategy.guidance must be a string when set.")

    # Per-task sampling overrides must name real tasks; param names stay open for extra_body pass-through.
    validate_sampling(cfg.data.get("sampling"))

    # Evaluation modes are explicit because they change cost and whether model calls happen.
    if cfg.evaluation.coverage_mode not in {"lineage", "reassign", "both"}:
        raise ValueError("evaluation.coverage_mode must be lineage, reassign, or both.")

    if cfg.evaluation.diversity.sample_cap <= 0:
        raise ValueError("evaluation.diversity.sample_cap must be positive.")
    if cfg.evaluation.diversity.k_local <= 0:
        raise ValueError("evaluation.diversity.k_local must be positive.")

    # Generation knobs must be in range so a typo fails here, not as a silent zero-row run.
    gen = cfg.generation
    if gen.target_size <= 0:
        raise ValueError("generation.target_size must be a positive integer.")
    if gen.overgenerate_ratio < 1.0:
        raise ValueError("generation.overgenerate_ratio must be >= 1.0.")
    if gen.scenarios_per_mix <= 0:
        raise ValueError("generation.scenarios_per_mix must be positive.")
    if gen.concurrency <= 0:
        raise ValueError("generation.concurrency must be a positive integer.")
    if gen.max_refine_attempts < 0:
        raise ValueError("generation.max_refine_attempts must be >= 0.")
    if not 0.0 <= gen.complexity_ratio <= 1.0:
        raise ValueError("generation.complexity_ratio must be between 0.0 and 1.0.")
    if cfg.taxonomy.depth < 0:
        raise ValueError("taxonomy.depth must be >= 0.")
    if cfg.taxonomy.children_per_node <= 0:
        raise ValueError("taxonomy.children_per_node must be positive.")
    if cfg.taxonomy.best_of_n <= 0:
        raise ValueError("taxonomy.best_of_n must be positive.")


def validate_sampling(sampling: Any) -> None:
    # Validate per-task decoding overrides early so a typo fails during `validate`, not mid-run.
    if sampling is None:
        return
    if not isinstance(sampling, dict):
        raise ValueError("sampling must be a mapping.")
    tasks = sampling.get("tasks") or {}
    if not isinstance(tasks, dict):
        raise ValueError("sampling.tasks must be a mapping of task name to decoding params.")

    valid_tasks = {t.value for t in TaskType}
    numeric_params = {"temperature", "top_p", "frequency_penalty", "presence_penalty", "min_p", "repetition_penalty"}
    for name, params in tasks.items():
        if name not in valid_tasks:
            raise ValueError(f"sampling.tasks has unknown task '{name}'. Valid tasks: {sorted(valid_tasks)}.")
        if not isinstance(params, dict):
            raise ValueError(f"sampling.tasks.{name} must be a mapping of decoding params.")
        # Param names stay open (unknowns pass through to extra_body), but known numerics must be numbers.
        for key, value in params.items():
            if key in numeric_params and not isinstance(value, (int, float)):
                raise ValueError(f"sampling.tasks.{name}.{key} must be a number.")
            if key == "max_tokens" and not isinstance(value, int):
                raise ValueError(f"sampling.tasks.{name}.max_tokens must be an integer.")


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
