# TODO

## Done: Add Configurable Prompt Overrides

Runs can now point at a Python prompt module from config:

```yaml
prompts:
  module: "config/prompts.py"
```

The module may override any subset of built-in prompt functions plus `SYSTEM_JSON` and `SYSTEM_TEXT`; missing overrides fall back to the built-ins in `syndata/prompts.py`. The loader resolves paths relative to the YAML file and validates module imports, string system prompts, and compatible function signatures during config loading.

Reason: prompt wording is the main way users steer dataset style, quality criteria, taxonomy behavior, generation format, and critic behavior. The implementation uses Python modules rather than text templates so advanced users can customize prompt-building logic, not only wording.

## Let Users Influence Strategy And Sampling Behavior

The strategy step is meant to decide which taxonomy branches can be combined and how often they should be used, but users currently have limited direct control over that behavior. The model generates `strategies.json`, and users can edit that artifact, but the strategy prompt and sampling behavior are mostly fixed.

Add a way for users to influence this through configurable prompts first, especially the strategy-generation prompt. This may be enough for many cases because users can say things like:

- prefer specific branches more often
- avoid specific branches
- combine some taxonomy factors only in certain ways
- make particular domains or edge cases more common
- avoid unrealistic taxonomy combinations

Do not jump straight to a large sampling-rule DSL. Start with prompt/config control and keep the implementation compact. If prompt-level control proves insufficient, later consider small config knobs such as leaf-only sampling, branch weights, exclusions, or quotas.

Reason: synthetic data users often need targeted coverage, not just broad random coverage. The current strategy system points in that direction, but users need more say in how strategies are created and how taxonomy combinations are emphasized.

## Add LLM Sampling Policies For Generation

Model configuration is currently static per role: one `temperature`, one `max_tokens`, and optional provider `extra_body` settings are reused for every call made by that role. This is too rigid for advanced synthetic-data workflows where generation quality depends on coordinating decoding parameters across attempts.

Add a compact sampling-policy layer for model calls, especially `bulk` generation calls, while preserving the current static config as the default.

Potential capabilities:

- per-task sampling overrides, so taxonomy, meta-prompt generation, record generation, repair, critique, and refinement can use different decoding settings
- named sampling policies that bundle related parameters such as `temperature`, `top_p`, `min_p`, `frequency_penalty`, `presence_penalty`, `repetition_penalty`, and provider-specific extras
- scheduled policies across repeated attempts, such as gradually increasing `temperature` while reducing `min_p`
- policy sequences for the same prompt/meta-prompt, such as generating several variants under progressively more exploratory settings
- enough provenance in `llm_calls.jsonl` and dataset artifacts to tell which sampling policy produced each output

Do not turn this into a provider-specific abstraction. Keep the public config OpenAI-compatible where possible, and pass less common provider settings through `extra_body` when needed.

Reason: prompt design controls *what* the model should do, but decoding controls strongly affect diversity, repetition, novelty, and failure modes. Synthetic data generation often needs coordinated decoding schedules rather than one static temperature for an entire run.

## Add Batched And Multi-Turn Generation Modes

Every current model call is a fresh chat with one system message and one user message. That keeps the MVP simple, but some useful generation workflows need a different conversation shape.

Consider adding explicit generation modes for:

- batched generation, where one model reply returns multiple records for the same meta-prompt
- repeated generation from the same meta-prompt under different sampling policies
- multi-turn continuation, where the model sees prior assistant outputs and receives follow-up user instructions such as "generate more, but make them distinct from the previous examples"

Keep these separate from sampling policy itself. They change prompt/session structure, output parsing, lineage, acceptance accounting, and dedupe risk in a different way than decoding parameters do.

Likely design questions:

- whether batching returns a JSON array of records or several independently parseable records
- how much prior conversation context to retain without increasing duplication or cost too much
- how to preserve one row per final item while logging shared conversations and parent/child provenance
- whether critic/refine should operate per item even when generation happened in a batch

Reason: some diversity gains come from sampling settings, while others come from asking the model to continue, contrast, or expand within an existing conversational context. The system should support both without conflating them.

## Done: Tighten Strategy Subtree Matching

`sample_mix` currently supports forgiving strategy-root matching in `syndata/taxonomy.py`.
That is useful when a model emits slightly partial taxonomy paths, but the matching can be too permissive because it accepts both:

- node path starts with strategy root
- strategy root starts with node path

Improve this to strict subtree matching:

- If a strategy root exactly names a factor, sample anywhere inside that factor tree.
- If a strategy root exactly names a node path, sample that node or one of its descendants.
- If a strategy root does not exactly match a factor or node path, fall back clearly and preserve non-empty lineage.
- Add tests for factor-level roots, subtree roots, leaf roots, invalid roots, and overlapping names.

Reason: strategy roots should be interpreted as intentional taxonomy paths. Loose prefix matching can accidentally broaden a strategy and sample from an ancestor or neighboring path the strategy did not mean.

## Done: Improve Global Level-Plan Prompt

Taxonomy expansion currently asks for one global plan before expanding the next level. Keep that global-plan approach rather than adding per-node mini-plans, because per-node plans may over-bias each branch and reduce the benefit of a consistent shared granularity.

Improve the global plan prompt so it explicitly asks for a plan that applies across all nodes at the current level:

- avoid branch-specific examples unless they apply to every listed node
- prefer abstract granularity guidance
- keep the plan useful for heterogeneous sibling nodes
- preserve flexibility for each node's own domain

Reason: a global plan is compact and helps keep same-level taxonomy nodes comparable, but if it becomes too specific to one branch, it can distort expansion for unrelated siblings.
