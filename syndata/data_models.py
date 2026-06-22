"""Typed config models (Pydantic) and the task enum.

This module is the single source of truth for the config contract. Pydantic validates and
fills defaults; `Config.data` is a derived dict view for the few consumers (`ModelRouter`,
`resolve_sampling`, the resume fingerprint) that still read a plain mapping. Kept import-leaf
(only stdlib + pydantic + jsonschema) so config/models/generate can all import it without cycles.
"""

from __future__ import annotations

from enum import StrEnum
from functools import cached_property
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class TaskType(StrEnum):
    """Names every model-call site; drives per-task sampling overrides and llm_calls logging."""

    FACTOR_DISCOVERY = "factor_discovery"
    NODE_EXPANSION = "node_expansion"
    TAXONOMY_CRITIC = "taxonomy_critic"
    LEVEL_PLAN = "level_plan"
    STRATEGY = "strategy"
    META_PROMPT = "meta_prompt"
    COMPLEXIFY = "complexify"
    GENERATE = "generate"
    REPAIR = "repair"
    SEMANTIC_CRITIC = "semantic_critic"
    REFINE = "refine"
    COMPLEXITY_SCORE = "complexity_score"
    NODE_ASSIGN = "node_assign"


# Decoding params known to numeric validation (unknown params still pass through to extra_body).
_NUMERIC_SAMPLING_PARAMS = {
    "temperature",
    "top_p",
    "frequency_penalty",
    "presence_penalty",
    "min_p",
    "repetition_penalty",
}


class ProjectCfg(BaseModel):
    """Run identity + output location. output_dir holds every artifact; seed makes runs reproducible.
    Consumed via Config.output_dir / Config.seed and by ModelRouter (for the llm_calls.jsonl path)."""

    name: str = "pilot"
    output_dir: str = "runs/pilot"
    seed: int = 42


class ProviderCfg(BaseModel):
    """Single OpenAI-compatible endpoint shared by every role. Consumed by ModelRouter._get_client
    (base_url + timeout) and resolve_api_key (reads api_key_env from the project-root .env). The
    per-call model id lives on each role in ModelsCfg, not here."""

    base_url: str = "https://openrouter.ai/api/v1"
    api_key_env: str = "OPENROUTER_API_KEY"
    timeout_seconds: float = Field(180.0, gt=0)


class ModelCfg(BaseModel):
    """One call role: the model id plus any decoding params. Consumed by ModelRouter.complete (model
    id) and resolve_sampling (decoding params). extra="allow" keeps role-level decoding params
    (temperature, top_p, min_p, ...) — resolve_sampling splits them into call kwargs vs extra_body,
    so they must survive into model_dump rather than being dropped as unknown fields."""

    model_config = ConfigDict(protected_namespaces=(), extra="allow")

    model: str = ""
    extra_body: dict[str, Any] | None = None


class ModelsCfg(BaseModel):
    """The three call roles. strategic = taxonomy/strategy, bulk = generation/refine, critic = critique."""

    strategic: ModelCfg = Field(default_factory=ModelCfg)
    bulk: ModelCfg = Field(default_factory=ModelCfg)
    critic: ModelCfg = Field(default_factory=ModelCfg)


class TaxonomyCfg(BaseModel):
    """Taxonomy build knobs (consumed by taxonomy.build_taxonomy). factors=None lets the model
    discover them; review_mode gates whether the run halts for manual edits."""

    depth: int = Field(2, ge=0)
    factors: list[dict[str, Any]] | None = None
    best_of_n: int = Field(2, gt=0)
    review_mode: str = "auto_accept"
    children_per_node: int = Field(4, gt=0)

    @field_validator("review_mode")
    @classmethod
    def _known_review_mode(cls, value: str) -> str:
        if value not in {"auto_accept", "write_then_edit", "interactive_confirm"}:
            raise ValueError("taxonomy.review_mode must be auto_accept, write_then_edit, or interactive_confirm.")
        return value


class DiversityCfg(BaseModel):
    """Embedding-diversity settings (consumed by evaluate.run_evaluation -> diversity.embedding_diversity).
    Off by default; needs the [diversity] extra. text_field is a dotted path into the record to embed."""

    enabled: bool = False
    embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2"
    k_local: int = Field(10, gt=0)
    sample_cap: int = Field(1000, gt=0)
    text_field: str | None = None


class EvaluationCfg(BaseModel):
    """What `evaluate`/`run` compute (consumed by evaluate.run_evaluation). coverage/complexity in
    reassign/complexity modes make extra model calls; dedupe + lineage coverage are call-free."""

    dedupe: bool = True
    coverage: bool = True
    coverage_mode: str = "lineage"
    complexity: bool = False
    complexity_batch_size: int = Field(5, gt=0)
    complexity_samples_per_item: int = Field(2, gt=0)
    decontaminate_against: list[str] = Field(default_factory=list)
    diversity: DiversityCfg = Field(default_factory=DiversityCfg)

    @field_validator("coverage_mode")
    @classmethod
    def _known_coverage_mode(cls, value: str) -> str:
        if value not in {"lineage", "reassign", "both"}:
            raise ValueError("evaluation.coverage_mode must be lineage, reassign, or both.")
        return value


class StrategyCfg(BaseModel):
    """Optional free-text steering woven into the strategy prompt (consumed by taxonomy.build_strategies)."""

    guidance: str | None = None

    @field_validator("guidance", mode="before")
    @classmethod
    def _guidance_is_text(cls, value: Any) -> Any:
        # Reject non-text early (a YAML number would otherwise coerce); free-text steering only.
        if value is not None and not isinstance(value, str):
            raise ValueError("strategy.guidance must be a string when set.")
        return value


class GenerationCfg(BaseModel):
    """Generation volume + behavior knobs (consumed by generate.generate_dataset). attempts =
    ceil(target_size * overgenerate_ratio); accepted rows are then coverage-trimmed back to target."""

    target_size: int = Field(50, gt=0)
    overgenerate_ratio: float = Field(1.3, ge=1.0)
    scenarios_per_mix: int = Field(3, gt=0)
    complexity_ratio: float = Field(0.3, ge=0.0, le=1.0)
    max_refine_attempts: int = Field(2, ge=0)
    concurrency: int = Field(4, gt=0)
    checkpoint_every: int = Field(50, gt=0)


class SamplingCfg(BaseModel):
    """Per-task decoding overrides: tasks[<TaskType value>] -> {param: value}. Consumed by
    resolve_sampling, which layers these on top of role-static params. Param names stay open so
    unknown provider knobs pass through to extra_body; known numerics are type-checked here."""

    tasks: dict[str, Any] = Field(default_factory=dict)

    @field_validator("tasks")
    @classmethod
    def _valid_task_overrides(cls, tasks: dict[str, Any]) -> dict[str, Any]:
        # Per-task overrides must name real tasks; known numerics must be numbers, but param names
        # stay open so unknown provider params pass through to extra_body.
        valid = {task.value for task in TaskType}
        for name, params in tasks.items():
            if name not in valid:
                raise ValueError(f"sampling.tasks has unknown task '{name}'. Valid tasks: {sorted(valid)}.")
            if not isinstance(params, dict):
                raise ValueError(f"sampling.tasks.{name} must be a mapping of decoding params.")
            for key, value in params.items():
                if key in _NUMERIC_SAMPLING_PARAMS and not isinstance(value, (int, float)):
                    raise ValueError(f"sampling.tasks.{name}.{key} must be a number.")
                if key == "max_tokens" and not isinstance(value, int):
                    raise ValueError(f"sampling.tasks.{name}.max_tokens must be an integer.")
        return tasks


class Config(BaseModel):
    """The whole validated config tree plus a few runtime extras (path, loaded prompts, compiled
    validator). Built by config.load_config and passed as `cfg` throughout. Read typed fields
    directly (cfg.generation.target_size, cfg.provider.base_url, cfg.schema); cfg.data is the derived
    dict view consumed by ModelRouter, resolve_sampling, and the resume fingerprint."""

    # protected_namespaces=() lets a field be named `model`; ignored_types keeps cached_property
    # out of Pydantic's field machinery so `validator` can memoize the compiled schema.
    model_config = ConfigDict(
        protected_namespaces=(),
        populate_by_name=True,
        arbitrary_types_allowed=True,
        ignored_types=(cached_property,),
    )

    path: Path = Field(exclude=True)
    description: str
    # `record_schema` is aliased to YAML `schema` (the field name `schema` would shadow a BaseModel
    # method); the `schema` property below restores cfg.schema access for the rest of the codebase.
    record_schema: dict[str, Any] | None = Field(default=None, alias="schema")
    project: ProjectCfg = Field(default_factory=ProjectCfg)
    provider: ProviderCfg = Field(default_factory=ProviderCfg)
    models: ModelsCfg = Field(default_factory=ModelsCfg)
    taxonomy: TaxonomyCfg = Field(default_factory=TaxonomyCfg)
    strategy: StrategyCfg = Field(default_factory=StrategyCfg)
    sampling: SamplingCfg = Field(default_factory=SamplingCfg)
    generation: GenerationCfg = Field(default_factory=GenerationCfg)
    evaluation: EvaluationCfg = Field(default_factory=EvaluationCfg)
    # Loaded prompt set (runtime object, not config data); attached by load_config, never serialized.
    prompts: Any = Field(default=None, exclude=True)

    @model_validator(mode="before")
    @classmethod
    def _drop_null_sections(cls, data: Any) -> Any:
        # A blank YAML section (`evaluation:` with nothing under it) parses as None; drop it so the
        # field default applies instead of clobbering it. Replaces the old hand-rolled deep-merge.
        if isinstance(data, dict):
            return {key: value for key, value in data.items() if value is not None}
        return data

    @field_validator("description")
    @classmethod
    def _non_empty_description(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Config requires a non-empty description.")
        return value

    @model_validator(mode="after")
    def _require_role_models(self) -> "Config":
        for role in ("strategic", "bulk", "critic"):
            if not getattr(self.models, role).model:
                raise ValueError(f"models.{role}.model is required.")
        return self

    # --- derived views (genuinely computed; not pass-through wrappers) ---

    @property
    def schema(self) -> dict[str, Any] | None:
        return self.record_schema

    @property
    def is_schema_free(self) -> bool:
        return self.record_schema is None

    @property
    def output_format(self) -> str:
        return "text" if self.is_schema_free else "json"

    @property
    def output_dir(self) -> Path:
        return Path(self.project.output_dir)

    @property
    def seed(self) -> int:
        return self.project.seed

    @cached_property
    def validator(self) -> Draft202012Validator | None:
        # Compile the record validator once per run and reuse it across all record validations.
        return Draft202012Validator(self.record_schema) if self.record_schema is not None else None

    @property
    def data(self) -> dict[str, Any]:
        # Derived dict view (YAML-shaped, `schema` re-aliased) for dict-consuming code paths.
        return self.model_dump(by_alias=True, mode="python")
