# Changelog

Completed work, newest first. Forward-looking items live in the root `TODO.md`.

## 2026-06-23 — Structural refactor (round 4)

A larger sanctioned refactor landed (60 tests pass).

- **Pydantic config.** `simula/data_models.py` holds the config models (single source of defaults +
  validation) and `TaskType`. `config.py` shrank to YAML load + Pydantic validate + schema-subset
  check + the missing-key warning. `cfg.data` is now a derived `model_dump()` view, not a
  hand-merged dict. Deleted `_deep_merge`/`_section`/`_parse_sections`/`default_config`/the
  dataclasses. `tasks.py` merged into `data_models.py`; `cost.py` merged into `utils.summarize_cost`.
- **Single provider.** Per-role `base_url`/`api_key` are gone from the contract; connection lives on a
  `provider` block (`base_url`/`api_key_env`/`timeout_seconds`). `ModelRouter` uses one shared client.
- **.env-only keys.** `resolve_api_key` reads the project-root `.env` directly (`dotenv_values`); a
  shell export is ignored. No `os.getenv`, no two-location load.
- **rich logging.** `simula/console.py` routes human-facing status/warnings/the review prompt;
  `llm_calls.jsonl` stays plain JSONL.
- **Retry classification.** Fail fast on 4xx (except 408/409/429), retry transport/5xx/429.
- **Pile A done.** `_critic_loop` schema-free branches consolidated; `_generate_one_safe` flattened.
- **Misc.** required `system` param (dropped the "helpful assistant" fallback); required `router` in
  `run_evaluation`; dropped the `tqdm` try/except; diversity deps top-of-file behind one guard with
  the import boundary in `run_evaluation`; `examples/template.yaml` added.

## 2026-06-23 — Simplification review (decisions)

A complexity/bloat review concluded that most of the perceived bloat is **feature accumulation, not
bad style** — roughly half the codebase is optional/off-by-default capability (the eval metrics),
safety layers (typed config), and quality multipliers (best_of_n, complexify, critic/refine), each
added deliberately. Decisions:

- **Keep all features**, including Elo complexity scoring and LLM reassignment coverage. Both are
  off by default and DO write into `eval_report.json` when enabled. Known gap, left as-is:
  the complexity ranking is reported but not consumed by any dataset-shaping step — it would only
  earn its weight once `coverage_aware_trim` or a curriculum export actually uses it.
- **Keep the generation methodology unchanged** (meta-prompt indirection, best_of_n, critic/refine).
- Only **behavior-preserving cleanup ("Pile A")** was sanctioned, and it landed in round 4:
  1. Collapse the schema-free-vs-JSON duality duplicated across `_make_record` / `_make_text` and the
     `if cfg.is_schema_free` branches inside `_critic_loop`. The two flows are genuinely different
     (JSON parses/validates/repairs; text doesn't), so this consolidates shared structure — it does
     not fully merge them.
  2. Flatten the nested try/except in `_generate_one_safe`: compute the strategy+mix once and build
     the row progressively so the failure path doesn't recompute the mix.

  CAVEAT (still live): `ModelRouter.model_name()` looks like a trivial accessor but is a **test seam** —
  `test_point_failure_becomes_rejected_row` injects a `BadRouter` with no `.config`, only
  `model_name()`. Do NOT inline it.

## 2026-06-17 — Deferred-review fixes (the ones that landed)

A review pass produced a batch of findings; the correctness/cleanup ones were done on
`code-refactor`: `write_then_edit` now halts before generation; resume aborts on a run-fingerprint
change; lineage coverage counts ancestor prefixes; `extract_json_object` no longer slices JSON out
of prose; diversity deps moved to a `[diversity]` extra and `httpx` was dropped. (The findings that
were *deferred* remain open in `TODO.md`.)

## Earlier

### Configurable prompt overrides

Runs can point at a Python prompt module from config (`prompts.module: "config/prompts.py"`). The
module may override any subset of built-in prompt functions plus `SYSTEM_JSON` and `SYSTEM_TEXT`;
missing overrides fall back to the built-ins in `simula/prompts.py`. The loader resolves paths
relative to the YAML file and validates imports, string system prompts, and compatible function
signatures during config loading. Python modules (not text templates) so advanced users can
customize prompt-building logic, not only wording.

### Strategy guidance (prompt-level control)

Free-text `strategy.guidance` in config is woven into the strategy-generation prompt, shaping the
generated `strategies.json` (roots and weights) before any bulk generation runs. Optional, defaults
to `null` (built-in prompt unchanged); `validate` rejects non-string guidance.

### Per-task sampling overrides

Decoding params are no longer static per role. `sampling.tasks` maps a task name to a decoding-param
mapping, and `resolve_sampling` in `simula/models.py` layers built-in defaults <- `models.<role>`
static <- `sampling.tasks[task]`. OpenAI-compatible params go top-level; provider-specific ones
(`min_p`, `top_k`, `repetition_penalty`, …) pass through `extra_body`. Resolution is pure (safe under
concurrent workers); resolved params are logged per call. `validate` rejects unknown task names and
non-numeric values. Default `max_tokens` is now 32768.

Deliberately **not** built (revisit only if a real workflow needs in-run decoding schedules that two
CLI runs cannot cover):
- **Named policies + task assignment** — each task maps to exactly one role, so a task key alone
  identifies a call; the policy-name indirection added ceremony without payoff at this size.
- **Schedules / policy sequences across attempts** — kept resolution a pure function of `(role, task)`
  with no attempt plumbing. For a temperature spread, run the CLI twice with different `sampling`.
- **Dataset-row provenance field** — params are deterministic from `task`, so the row shape is
  untouched; per-call provenance lives in `llm_calls.jsonl`.

### Tighten strategy subtree matching

`sample_mix` now does strict subtree matching instead of forgiving two-way prefix matching: a
factor-level root samples anywhere in that factor tree; a node-path root samples that node or its
descendants; anything else falls back clearly while preserving non-empty lineage. Tests cover
factor-level, subtree, leaf, invalid, and overlapping-name roots.

### Improve global level-plan prompt

Taxonomy expansion keeps the single global level-plan (no per-node mini-plans). The prompt now
explicitly asks for a plan that applies across all nodes at the current level: avoid branch-specific
examples unless they apply to every node, prefer abstract granularity guidance, stay useful for
heterogeneous siblings, preserve each node's own domain.

### Removed the `SYNDATA_LLM_LOG` global override

The env override is gone. `ModelRouter._write_log` always writes to `<output_dir>/llm_calls.jsonl`,
so concurrent runs stay isolated and resume always reads the right file. Deliberately did NOT route
logging through `logging`/`structlog`: a per-run JSONL sink is all this tool needs. `flush_logs`
reports failed log writes on stderr instead of swallowing them.
