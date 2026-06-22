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

## Done: Removed the `SYNDATA_LLM_LOG` global override

The env override is gone. `ModelRouter._write_log` always writes to `<output_dir>/llm_calls.jsonl`,
so concurrent runs stay isolated and `monitor.py`/resume always read the right file. We deliberately
did NOT route logging through `logging`/`structlog`: a per-run JSONL sink is all this tool needs and
a logging framework is ceremony at this size. `flush_logs` now reports failed log writes on stderr
instead of swallowing them (the `llm_calls.jsonl` contract says every response is logged).

## Deferred review findings (recorded 2026-06-17)

A review pass produced a batch of findings. The correctness/cleanup ones were done on
`code-refactor`: `write_then_edit` now halts before generation; resume aborts on a run-fingerprint
change; lineage coverage counts ancestor prefixes; `extract_json_object` no longer slices JSON out
of prose; diversity deps moved to a `[diversity]` extra and `httpx` was dropped. The following were
deliberately deferred — keep any fix compact:

- **Generation can finish below target silently.** `generate_dataset` runs a fixed
  `ceil(target * overgenerate_ratio)` attempts and trims; a low accept rate yields fewer than
  `target` rows while the CLI still exits 0. Minimum fix: warn loudly (stderr) with the accept rate
  when `len(final) < target`. Only build a bounded adaptive-refill loop if hitting target must be a
  guarantee — it adds cost and weakens determinism, so gate it behind a knob/flag.
- **Model-output shapes are under-validated.** `_discover_factors` → `factor["name"]` is outside any
  try/except, so one nameless factor crashes the whole taxonomy build; a non-numeric strategy
  `weight` makes every row fail (silent zero-accept). Add small normalizers (drop nameless
  factors/children, coerce weights to positive floats, guarantee a non-empty strategy list). No
  schema framework.
- **Retry policy is too broad.** `ModelRouter.complete` retries every non-429 exception 8×, so a
  400/401/403 burns pointless retries before failing. Classify: fail fast on 4xx (except
  408/409/429), retry transport errors + 5xx.
- **Config booleans use Python truthiness.** `bool("false") is True` (config.py), so a quoted YAML
  bool silently inverts. Add a tiny `_require_bool` that fails loudly on non-bools.
- **`monitor.py` duplicates artifact knowledge and is extraction-specific.** It hardcodes artifact
  filenames and re-parses config with a different `overgenerate_ratio` default (1.0 vs 1.3), and its
  quality block assumes `record["extraction"]`. Reuse `syndata.utils.artifact_path`/`read_jsonl` and
  `load_config` defaults; gate the extraction block behind a presence check. (Unpackaged, untested
  dev tool, so low priority.)
- **Sampling includes abstract internal/root nodes.** `_sample_descendant` samples every node
  uniformly, so a mix can be just a vague root. Not a bug — it adds breadth. Add a single
  `prefer_leaf`/leaf-only sampling knob only if real meta-prompts come out too vague (see the
  structured sampling-knobs note above).

## Simplification review (2026-06-23): decisions + sanctioned cleanup

A complexity/bloat review concluded that most of the perceived bloat is **feature accumulation, not
bad style** — roughly half the codebase is optional/off-by-default capability (the eval metrics),
safety layers (typed config), and quality multipliers (best_of_n, complexify, critic/refine), each
added deliberately. Decisions (no code changed in this pass):

- **Keep all features**, including Elo complexity scoring and LLM reassignment coverage. Both are
  off by default and DO write into `eval_report.json` when enabled (confirmed). Known gap, left as-is:
  the complexity ranking is reported but not consumed by any dataset-shaping step — it would only
  earn its weight once `coverage_aware_trim` or a curriculum export actually uses it.
- **Keep the generation methodology unchanged** (meta-prompt indirection, best_of_n, critic/refine).
- Only **behavior-preserving cleanup ("Pile A")** is sanctioned. Not yet done:

  1. Collapse the schema-free-vs-JSON duality duplicated across `_make_record` / `_make_text` and the
     `if cfg.is_schema_free` branches inside `_critic_loop`. The two flows are genuinely different
     (JSON parses/validates/repairs; text doesn't), so this consolidates shared structure — it does
     not fully merge them.
  2. Flatten the nested try/except in `_generate_one_safe`: compute the strategy+mix once and build
     the row progressively so the failure path doesn't recompute the mix.

  CAVEAT: `ModelRouter.model_name()` looks like a trivial accessor but is a **test seam** —
  `test_point_failure_becomes_rejected_row` injects a `BadRouter` with no `.config`, only
  `model_name()`. Do NOT inline it. Realistic Pile A savings are modest (~30–50 lines); the big
  reductions only come from cutting features, which was declined.
