from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft202012Validator

from . import prompts
from .tasks import TaskType
from .utils import ensure_dir


@dataclass(frozen=True)
class GenerationCfg:
    target_size: int = 50
    overgenerate_ratio: float = 1.3
    scenarios_per_mix: int = 3
    complexity_ratio: float = 0.3
    max_refine_attempts: int = 2
    concurrency: int = 4
    checkpoint_every: int = 50


@dataclass(frozen=True)
class TaxonomyCfg:
    depth: int = 2
    factors: list[dict[str, Any]] | None = None
    best_of_n: int = 2
    review_mode: str = "auto_accept"
    children_per_node: int = 4


@dataclass(frozen=True)
class DiversityCfg:
    enabled: bool = False
    embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2"
    k_local: int = 10
    sample_cap: int = 1000
    text_field: str | None = None


@dataclass(frozen=True)
class EvaluationCfg:
    dedupe: bool = True
    coverage: bool = True
    coverage_mode: str = "lineage"
    complexity: bool = False
    complexity_batch_size: int = 5
    complexity_samples_per_item: int = 2
    decontaminate_against: list[str] = field(default_factory=list)
    diversity: DiversityCfg = field(default_factory=DiversityCfg)


@dataclass
class Config:
    path: Path
    data: dict[str, Any]
    prompts: prompts.PromptSet
    generation: GenerationCfg
    taxonomy: TaxonomyCfg
    evaluation: EvaluationCfg

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
    # Load .env so `api_key_env` actually resolves without a manual `export`. We look next to
    # the config file and in the current directory; existing env vars always win (override=False).
    candidates = [config_path.resolve().parent / ".env", Path.cwd() / ".env"]
    try:
        from dotenv import load_dotenv
    except ImportError:
        load_dotenv = None
    for env_path in candidates:
        if not env_path.is_file():
            continue
        if load_dotenv is not None:
            load_dotenv(env_path, override=False)
            continue
        # Thin fallback parser when python-dotenv is not installed.
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _section(data: dict[str, Any], key: str) -> dict[str, Any]:
    section = data.get(key)
    return section if isinstance(section, dict) else {}


def _parse_sections(data: dict[str, Any]) -> tuple[GenerationCfg, TaxonomyCfg, EvaluationCfg]:
    # Parse the structured sections once into typed views so the rest of the codebase reads
    # cfg.generation.target_size instead of int(cfg.data["generation"]["target_size"]).
    gen = _section(data, "generation")
    generation = GenerationCfg(
        target_size=int(gen.get("target_size", 50)),
        overgenerate_ratio=float(gen.get("overgenerate_ratio", 1.3)),
        scenarios_per_mix=int(gen.get("scenarios_per_mix", 3)),
        complexity_ratio=float(gen.get("complexity_ratio", 0.3)),
        max_refine_attempts=int(gen.get("max_refine_attempts", 2)),
        concurrency=int(gen.get("concurrency", 4)),
        checkpoint_every=int(gen.get("checkpoint_every", 50)),
    )
    tax = _section(data, "taxonomy")
    taxonomy = TaxonomyCfg(
        depth=int(tax.get("depth", 2)),
        factors=tax.get("factors"),
        best_of_n=int(tax.get("best_of_n", 2)),
        review_mode=str(tax.get("review_mode", "auto_accept")),
        children_per_node=int(tax.get("children_per_node", 4)),
    )
    ev = _section(data, "evaluation")
    div = _section(ev, "diversity")
    evaluation = EvaluationCfg(
        dedupe=bool(ev.get("dedupe", True)),
        coverage=bool(ev.get("coverage", True)),
        coverage_mode=str(ev.get("coverage_mode", "lineage")),
        complexity=bool(ev.get("complexity", False)),
        complexity_batch_size=int(ev.get("complexity_batch_size", 5)),
        complexity_samples_per_item=int(ev.get("complexity_samples_per_item", 2)),
        decontaminate_against=[str(p) for p in (ev.get("decontaminate_against") or [])],
        diversity=DiversityCfg(
            enabled=bool(div.get("enabled", False)),
            embedding_model=str(div.get("embedding_model", "sentence-transformers/all-MiniLM-L6-v2")),
            k_local=int(div.get("k_local", 10)),
            sample_cap=int(div.get("sample_cap", 1000)),
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

    for role in ("strategic", "bulk", "critic"):
        model_cfg = cfg.data["models"].get(role, {})
        if not model_cfg.get("model"):
            raise ValueError(f"models.{role}.model is required.")
        if not model_cfg.get("base_url"):
            raise ValueError(f"models.{role}.base_url is required.")

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
