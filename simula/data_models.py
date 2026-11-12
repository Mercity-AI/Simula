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
    SUBJECT = "subject"
    SITUATIONS = "situations"


# Decoding params known to numeric validation (unknown params still pass through to extra_body).
_NUMERIC_SAMPLING_PARAMS = {
    "temperature",
    "top_p",
    "frequency_penalty",
    "presence_penalty",
    "min_p",
    "repetition_penalty",
}


class _Section(BaseModel):
    """Base for config sections: an unknown key (a typo, or a knob that was renamed) is an
    error at load instead of being silently ignored."""

    model_config = ConfigDict(extra="forbid")


class ProjectCfg(_Section):
    """Run identity + output location. output_dir holds every artifact; seed makes runs reproducible.
    Consumed via Config.output_dir / Config.seed and by ModelRouter (for the llm_calls.jsonl path)."""

    name: str = "pilot"
    output_dir: str = "runs/pilot"
    seed: int = 42


class ProviderCfg(_Section):
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


class ModelsCfg(_Section):
    """The three call roles. strategic = taxonomy/strategy, bulk = generation/refine, critic = critique."""

    strategic: ModelCfg = Field(default_factory=ModelCfg)
    bulk: ModelCfg = Field(default_factory=ModelCfg)
    critic: ModelCfg = Field(default_factory=ModelCfg)


class TaxonomyCfg(_Section):
    """Taxonomy build knobs (consumed by taxonomy.build_taxonomy). factors=None lets the model
    discover them; review_mode gates whether the run halts for manual edits."""

    depth: int = Field(2, ge=0)
    factors: list[dict[str, Any]] | None = None
    best_of_n: int = Field(1, gt=0)  # proposals per node, then one critic refine call when critic is on
    critic: bool = True  # False: use the single proposal as is (1 call per node; coarser, more duplicates)
    review_mode: str = "auto_accept"
    children_per_node: int = Field(4, gt=0)
    log_style: str = "light"  # how the build is surfaced: flat per-node breadcrumbs, or a live tree

    @field_validator("review_mode")
    @classmethod
    def _known_review_mode(cls, value: str) -> str:
        if value not in {"auto_accept", "write_then_edit", "interactive_confirm"}:
            raise ValueError("taxonomy.review_mode must be auto_accept, write_then_edit, or interactive_confirm.")
        return value

    @field_validator("log_style")
    @classmethod
    def _known_log_style(cls, value: str) -> str:
        if value not in {"tree", "light"}:
            raise ValueError("taxonomy.log_style must be tree or light.")
        return value

    @model_validator(mode="after")
    def _critic_merges_proposals(self) -> "TaxonomyCfg":
        # Several proposals need the critic to merge them into one child list.
        if not self.critic and self.best_of_n > 1:
            raise ValueError("taxonomy.critic: false requires taxonomy.best_of_n: 1 (several proposals need the critic to merge them).")
        return self


class DiversityCfg(_Section):
    """Embedding-diversity settings (consumed by evaluate.run_evaluation -> diversity.embedding_diversity).
    Off by default; needs the [diversity] extra. text_field is a dotted path into the record to embed."""

    enabled: bool = False
    embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2"
    k_local: int = Field(10, gt=0)
    sample_cap: int = Field(1000, gt=0)
    text_field: str | None = None


class EvaluationCfg(_Section):
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


class StrategyCfg(_Section):
    """Optional steering for the strategy build (consumed by taxonomy.build_strategies): free-text
    guidance woven into the prompt, and an exact strategy count (None keeps the model's 2-5 choice)."""

    guidance: str | None = None
    count: int | None = Field(None, ge=1, le=12)

    @field_validator("guidance", mode="before")
    @classmethod
    def _guidance_is_text(cls, value: Any) -> Any:
        # Reject non-text early (a YAML number would otherwise coerce); free-text steering only.
        if value is not None and not isinstance(value, str):
            raise ValueError("strategy.guidance must be a string when set.")
        return value


class GenerationCfg(_Section):
    """Generation volume + behavior knobs (consumed by generate.generate_dataset). attempts =
    ceil(target_size * overgenerate_ratio); trim controls whether accepted rows are capped at target.
    stop_after halts generate/run once that stage's artifact is written, so it can be inspected or
    hand-edited before the next stage spends model calls (the CLI --stop-after flag overrides it).
    Batching: meta_batch_size mixes per meta-prompt call (mode A), record_batch_size records per record call
    (mode B, or mode C when meta_prompts=False: records straight from their sampled mixes, no meta-prompt
    step). model_picked {factor: level} lets the model choose those factors per record on the combined
    path instead of code sampling them. code_depth {factor: level} stops code at that level of the
    factor's tree (exact shares there); the writer chooses the specifics beneath. subject names the factor
    that says what a record is about ("auto" lets the model pick it once the tree is built): each of its nodes gets
    a list of situations, one per record, and batches hold one node, so records sharing a node are about different
    concrete things. critic=False accepts every schema-valid record."""

    # validate_assignment: the CLI's --stop-after is set after load and must hit the same checks.
    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    target_size: int = Field(50, gt=0)
    overgenerate_ratio: float = Field(1.3, ge=1.0)
    trim: bool = True
    meta_prompts: bool = True
    meta_prompts_per_mix: int = Field(1, gt=0)
    meta_batch_size: int = Field(1, gt=0)
    record_batch_size: int = Field(1, gt=0)
    model_picked: dict[str, int] = Field(default_factory=dict)
    code_depth: dict[str, int] = Field(default_factory=dict)
    subject: str | None = None
    complexity_ratio: float = Field(0.3, ge=0.0, le=1.0)
    critic: bool = True
    max_refine_attempts: int = Field(2, ge=0)
    concurrency: int = Field(4, gt=0)
    checkpoint_every: int = Field(50, gt=0)
    stop_after: str | None = None

    @field_validator("stop_after")
    @classmethod
    def _known_stop_stage(cls, value: str | None) -> str | None:
        if value is not None and value not in {"taxonomy", "strategies", "meta_prompts"}:
            raise ValueError("generation.stop_after must be taxonomy, strategies, or meta_prompts.")
        return value

    @model_validator(mode="after")
    def _mode_knobs_match(self) -> "GenerationCfg":
        # meta_prompts_per_mix and meta_batch_size shape the meta-prompt step, which the combined path skips.
        for knob in ("meta_prompts_per_mix", "meta_batch_size"):
            if not self.meta_prompts and getattr(self, knob) > 1:
                raise ValueError(f"generation.{knob} > 1 requires generation.meta_prompts: true.")
        # The combined path writes no meta-prompt artifact, so there is nothing to stop after and edit.
        if not self.meta_prompts and self.stop_after == "meta_prompts":
            raise ValueError("generation.stop_after: meta_prompts requires generation.meta_prompts: true (the combined path writes no meta_prompts.jsonl).")
        # Model-picked factors are offered as options inside the combined batch prompt.
        if self.model_picked and self.meta_prompts:
            raise ValueError("generation.model_picked requires generation.meta_prompts: false (the combined path).")
        if any(level < 1 for level in self.model_picked.values()):
            raise ValueError("generation.model_picked levels must be >= 1 (the tree level whose nodes are offered).")
        # code_depth stops code sampling at a level; a factor cannot be both cut and handed to the model.
        if any(level < 1 for level in self.code_depth.values()):
            raise ValueError("generation.code_depth levels must be >= 1 (the deepest tree level code samples).")
        if both := sorted(set(self.code_depth) & set(self.model_picked)):
            raise ValueError(f"generation.code_depth and generation.model_picked both name {both}; a factor is picked by one or the other.")
        # Situations are listed per code-sampled subject node, so the subject cannot be model-picked.
        if self.subject is not None and self.subject in self.model_picked:
            raise ValueError(f"generation.subject {self.subject!r} is also in generation.model_picked; the subject must be code-sampled.")
        return self


class SamplingCfg(_Section):
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
        extra="forbid",  # an unknown top-level section is an error, like an unknown key in a section
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
