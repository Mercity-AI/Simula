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

### Done: Strategy Guidance (prompt-level control)

Users can now set free-text `strategy.guidance` in config, which is woven into the
strategy-generation prompt. This lets them say things like: prefer specific branches
more often, avoid specific branches, combine factors only in certain ways, make
particular domains/edge cases more common, or avoid unrealistic combinations. The
guidance shapes the generated `strategies.json` (roots and weights) before any bulk
generation runs. Guidance is optional and defaults to `null` (built-in prompt
unchanged); `validate` rejects non-string guidance.

### Remaining: structured sampling knobs (only if prompts prove insufficient)

Prompt-level guidance is a nudge, not a guarantee. If users need hard control, consider
small compact config knobs that plug into `sample_mix`/`choose_strategy` in
`syndata/taxonomy.py`:

- leaf-only sampling
- explicit per-branch weights
- exclusions (combinations that must never appear)
- quotas (minimum/maximum share per branch)

Do not jump straight to a large sampling-rule DSL. Keep any additions compact.

Reason: synthetic data users often need targeted coverage, not just broad random coverage. Prompt guidance covers the common "emphasize/avoid" cases; structured knobs are only worth adding for guarantees the model cannot reliably honor on its own.

## Add LLM Sampling Policies For Generation

### Done: Per-task sampling overrides

Decoding params are no longer static per role. `sampling.tasks` maps a task name to a
decoding-param mapping, and `resolve_sampling` in `syndata/models.py` layers built-in
defaults <- `models.<role>` static <- `sampling.tasks[task]`. OpenAI-compatible params
go top-level; provider-specific ones (`min_p`, `top_k`, `repetition_penalty`, …) pass
through `extra_body`. Resolution is pure (safe under concurrent workers). The resolved
params are logged per call in `llm_calls.jsonl`. `validate` rejects unknown task names
and non-numeric values. Default `max_tokens` is now 32768 (batteries-included).

### Deliberately not built

- **Named policies + task assignment.** Each task maps to exactly one role, so a task
  key alone identifies a call; the policy-name indirection added ceremony without
  payoff at this size. Params are assigned directly to tasks.
- **Schedules / policy sequences across attempts.** Dropped to keep resolution a pure
  function of `(role, task)` with no attempt plumbing or shared schedule state. For a
  temperature spread, run the CLI twice with different `sampling` (each run already
  takes its own `seed` and `output_dir`).
- **Dataset-row provenance field.** Params are deterministic from `task`, so the row
  shape is untouched; per-call provenance lives in `llm_calls.jsonl`.

Revisit only if a real workflow needs in-run decoding schedules that two CLI runs cannot cover.

Reason: prompt design controls *what* the model should do, but decoding controls strongly affect diversity, repetition, novelty, and failure modes. Per-task overrides cover that without a scheduler; coordinated schedules can be achieved with multiple runs.

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
