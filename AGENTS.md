# AGENTS.md

This file is the operating guide for agents working on this repository. Read it before changing code.

## Project Purpose

`syndata` is a compact Python CLI for schema-driven or free-text synthetic data generation. It builds taxonomies, samples taxonomy-conditioned mixes, asks an OpenAI-compatible model to generate records, critiques/refines them, and writes auditable JSON/JSONL artifacts.

The project is intentionally small. Prefer clear, boring code over architectural ceremony.

## Current Architecture

Core package:

- `syndata/cli.py`: CLI command dispatch for `validate`, `taxonomy`, `generate`, `evaluate`, and `run`.
- `syndata/data_models.py`: Pydantic config models (the single source of defaults + validation) and the `TaskType` enum naming every model-call site. Import-leaf (no syndata imports) so config/models/generate can all use it without cycles.
- `syndata/config.py`: YAML loading, Pydantic validation/defaults, `.env`-only API-key resolution (`resolve_api_key`), JSON Schema subset checks. `load_config` returns the validated `Config`; `cfg.data` is its derived dict view.
- `syndata/console.py`: rich-based human-facing console output — the run header, phase markers (`phase`), indeterminate-phase spinners (`spinner`), the generation progress bar (`track`), and the taxonomy build logger (`taxonomy_logger` returning a `TaxonomyLogger`: a live-growing tree or flat `light` breadcrumbs, chosen by `taxonomy.log_style`), plus warnings and the taxonomy review prompt. This is the single progress system (rich, no tqdm dependency); only one live display runs at a time, so phases stay sequential (e.g. the taxonomy tree closes before the review prompt). On a TTY displays render live; off a TTY the tree prints once on close and spinners/bars fall back to plain lines; `--quiet` keeps phase markers + summary but renders no live display. Separate from the machine-readable `llm_calls.jsonl` audit log.
- `syndata/models.py`: single-provider OpenAI-compatible router (`ModelRouter`, one shared client), fake model, retry classification, per-task sampling resolution (`resolve_sampling`), live `llm_calls.jsonl` logging.
- `syndata/prompts.py`: built-in prompt templates, the global English/JSON system instruction, and the prompt-module override loader (`PromptSet`, `load_prompt_set`).
- `syndata/taxonomy.py`: factor discovery, breadth-first taxonomy expansion, review modes, strategy creation, strategy-aware sampling.
- `syndata/generate.py`: generation orchestration, meta-prompts, complexification, JSON generation/repair, critic/refine loop, concurrent workers, final trimming.
- `syndata/evaluate.py`: schema validation, dedupe, coverage reports, coverage-aware trimming, optional complexity scoring.
- `syndata/diversity.py`: optional embedding-based diversity scoring used by evaluation (deps live in the `[diversity]` extra; imported lazily only when diversity is enabled).
- `syndata/utils.py`: artifact names, JSON/JSONL helpers, timestamps, JSON extraction, record-to-text, checkpoint helpers, and `summarize_cost` (written to `cost_summary.json`).

Examples:

- `examples/basic_qa.yaml`: fake-model smoke test.
- `examples/query_extraction_gemini.yaml`: real query-extraction pilot config using Gemini via OpenRouter.
- `examples/cat_stories_freetext.yaml`: fake-model smoke test for schema-free text generation.
- `examples/job_extraction.yaml` (+ `job_extraction_prompts.py`): schema-guided `{schema, text, extraction}` envelope with a prompt-module override enforcing narrow atomic fields; DeepSeek v4 via OpenRouter.
- `examples/ecommerce_search_extraction.yaml` (+ `ecommerce_search_extraction_prompts.py`): `{query, extraction}` envelope turning NL shopper searches into atomic DB-queryable JSON (varying schema, one-level nesting, 3..20-field spread). Depth-4 taxonomy; strategic=deepseek-v4-pro, bulk/critic=deepseek-v4-flash:nitro. Used to generate a 10K dataset that validated the post-refactor pipeline end-to-end.

Tests:

- `tests/test_config.py`
- `tests/test_utils.py`
- `tests/test_evaluate.py`
- `tests/test_pipeline.py`
- `tests/test_models.py`

Generated artifacts:

- `runs/` is ignored and should not be committed.
- `llm_calls.jsonl` can be large and may contain full prompts/responses. Treat it as local run data.

## Non-Negotiable Constraints

- Do not commit API keys, bearer tokens, `.env`, or generated run artifacts. (`.env` is gitignored and auto-loaded by `load_config`; keep it local.)
- Keep the codebase compact. Do not split into many tiny files unless the current file is genuinely becoming hard to reason about.
- Preserve CLI-first behavior.
- Preserve OpenAI-compatible endpoint support. Do not add provider-specific lock-in unless it remains optional.
- Preserve JSON/JSONL artifact readability.
- Preserve lineage on generated items. `taxonomy_mix` must be non-empty when a taxonomy exists.
- Preserve live LLM logging. Every successful model response should be appended to `llm_calls.jsonl`.
- Keep evaluation separate from generation. `generate` must not create `eval_report.json`.

## Development Style

- Prefer functions and small dataclasses over class hierarchies.
- Use type hints for public-ish functions and complex data paths.
- Add comments around logical blocks in orchestration code, especially generation and taxonomy expansion.
- Every major logical block in `config.py`, `models.py`, `generate.py`, `taxonomy.py`, and `evaluate.py` should have a short orienting comment. Do not comment obvious assignments.
- Do not over-comment obvious assignments.
- Keep built-in prompt changes in `syndata/prompts.py`; use configured prompt modules for per-run overrides.
- Keep schema/task behavior in YAML config where possible.
- Use deterministic fake-model tests for behavior that should not require network access.
- When adding network/model behavior, ensure tests still pass offline.

## Running Commands

Use these from the repository root:

```bash
python -m syndata.cli validate examples/basic_qa.yaml
python -m syndata.cli run examples/basic_qa.yaml
pytest -q
```

For real OpenRouter-compatible runs, put the key named by `provider.api_key_env` in a gitignored
`.env` at the project root. The `.env` file is the ONLY source of API keys (read with
`dotenv_values`); a shell-exported variable is deliberately ignored.

```bash
echo 'OPENROUTER_API_KEY=...' > .env   # gitignored, auto-loaded
python -m syndata.cli taxonomy examples/query_extraction_gemini.yaml
python -m syndata.cli generate examples/query_extraction_gemini.yaml
```

Do not run real model calls unless the user explicitly asks. They cost money and can take time.

## Config Contract

Config is defined and validated by the Pydantic models in `syndata/data_models.py`; defaults live
there as field defaults (a documented `examples/template.yaml` shows every key, and `CONFIG.md` is
the full per-field human reference — keep both in sync when changing the contract). Sections:

- `project`: `name`, `output_dir`, `seed`
- `description`: dataset description (required, non-empty)
- `schema`: JSON Schema subset, or `null`/omitted for free-text generation
- `provider`: `base_url`, `api_key_env`, `timeout_seconds` — one OpenAI-compatible endpoint for all roles
- `models`: `strategic`, `bulk`, `critic`, each a `model` id plus optional decoding params/`extra_body`
- `prompts`: optional Python prompt module override
- `taxonomy`: depth/factors/review behavior
- `strategy`: optional free-text `guidance` woven into the strategy prompt
- `sampling`: optional per-task decoding overrides under `sampling.tasks`
- `generation`: target size, overgeneration, complexity ratio, refine attempts, concurrency
- `evaluation`: dedupe, coverage, complexity

Read typed fields off the validated model (`cfg.generation.target_size`, `cfg.provider.base_url`,
`cfg.schema`, …); `cfg.data` is a derived dict view (`model_dump`) for the few dict consumers
(`ModelRouter`, `resolve_sampling`, the resume fingerprint). Pydantic enforces non-empty description,
a `model` per role, valid `review_mode`/`coverage_mode`, and positive/in-range generation + taxonomy
knobs (a bad `target_size`/`overgenerate_ratio`/`concurrency` fails at load with a `ValueError`, not as
a silent zero-row run). `load_config` also prints a non-fatal stderr warning when a real run has no
resolvable API key in `.env`. `validate` makes no model calls.

Connection lives on `provider` (one endpoint shared by all roles):

- `provider.base_url`
- `provider.api_key_env` (the variable name read from the project-root `.env`)
- `provider.timeout_seconds` (default 180; per-request timeout for real calls)

Per-role fields under `models.<role>`:

- `model` (required model id; `"fake"` runs offline)
- `temperature`, `max_tokens` (default 32768 when unset), and any other decoding params
- `extra_body` (provider pass-through; set `{reasoning: {effort: low, exclude: true}}` here for reasoning models — there is no automatic model-id detection)

Per-task decoding overrides live under `sampling.tasks` (task name -> param mapping). `resolve_sampling` in `syndata/models.py` layers built-in defaults <- `models.<role>` static <- `sampling.tasks[task]`, then splits OpenAI-compatible params (top-level call kwargs) from provider-specific ones (`extra_body` pass-through). Resolution is a pure function so it is safe under concurrent workers. Named policies and attempt schedules were intentionally not built; run the CLI twice for a temperature spread.

Supported schema subset:

- `object`
- `string`
- `number`
- `integer`
- `boolean`
- `array`
- `enum`
- `required`
- nested `properties`
- array `items`

If extending the schema subset, update:

- `syndata/config.py`
- tests
- README

## Artifact Contract

Artifact filenames live in `syndata/utils.py`:

- `taxonomy.json`
- `strategies.json`
- `dataset.raw.jsonl`
- `dataset.accepted.jsonl`
- `dataset.final.jsonl`
- `dataset.evaluated.jsonl`
- `eval_report.json`
- `run_state.json`
- `llm_calls.jsonl`
- `cost_summary.json`
- `embeddings.cache.npz`

Generated dataset rows must keep this shape:

```json
{
  "id": "item-0-...",
  "attempt_index": 0,
  "record": {},
  "output_format": "json",
  "taxonomy_mix": [],
  "strategy_id": "general",
  "meta_prompt": "...",
  "complexified": false,
  "generator_model": "...",
  "critic_verdicts": [],
  "schema_valid": true,
  "accepted": true,
  "rejection_reason": null,
  "created_at": "..."
}
```

If changing this shape, update tests, README, and any downstream code that reads JSONL artifacts.

## Important Behaviors

### Taxonomy

Taxonomy generation is breadth-first. For each factor:

1. Expand current-level nodes.
2. Use `best_of_n` raw proposals.
3. Refine children with a critic-style model call.
4. Generate a next-level plan when more depth remains.

Review modes:

- `auto_accept`: default, writes taxonomy and proceeds.
- `write_then_edit`: writes taxonomy and tells user to edit before later generation.
- `interactive_confirm`: asks in the terminal.

### Strategy Sampling

Strategies may name full roots like `query_domain` or deeper paths like `query_domain.travel_and_hospitality`. Sampling must support both. If strategy matching fails, fall back to sampling all factors so lineage is not empty.

When modifying this code, test that `taxonomy_mix` contains one lineage entry per intended factor.

### Generation

`generate_dataset`:

1. Loads or builds taxonomy.
2. Loads or builds strategies.
3. Schedules generation attempts.
4. Writes raw/accepted rows as each future completes.
5. Updates `run_state.json`.
6. Dedupes accepted rows if configured.
7. Coverage-aware trims to `target_size`.
8. Writes `dataset.final.jsonl`.

Resume (`--resume`, the default) skips attempt indexes already in `dataset.raw.jsonl` and reuses
accepted rows. `run_state.json` stores a fingerprint of the resume-invalidating inputs (description,
schema, seed, model ids, prompt module, sampling, taxonomy, strategies, and the per-attempt
generation knobs — not `target_size`/`overgenerate_ratio`/`concurrency`, which can change across
resumes). If the fingerprint changed, resume aborts and tells the user to pass `--no-resume`.

Generation is concurrent with `asyncio.TaskGroup`. Be careful with shared state:

- `ModelRouter` schedules async log writes and flushes before CLI exit.
- Raw/accepted/final dataset writes happen in the main thread.
- Each worker gets its own seeded `random.Random`.

### Model Calls

All real calls go through:

```python
await ModelRouter.complete(role, prompt, system, task="...")
await ModelRouter.complete_json(role, prompt, system, task="...")
```

`complete_json` parses the response body as JSON, unwrapping a ```json code fence if the model added one. It deliberately does not slice JSON out of surrounding prose — the system prompt requires raw JSON, and record generation has its own repair pass for the rare model that ignores that.

The `task` argument drives per-task decoding via `resolve_sampling`; both the resolved sampling params and any `extra_body` are recorded on each `llm_calls.jsonl` row. Keep `task` accurate when adding new call sites.

Cost accounting and the log row are produced together in `ModelRouter._account`, which computes input/output tokens once (estimating `len(text)//4` when the provider omits `usage`) so `cost_summary.json` and `llm_calls.jsonl` always agree. Logs always go to `<output_dir>/llm_calls.jsonl`; there is no env override. `flush_logs` warns on stderr if any log write failed instead of swallowing it.

Retry behavior (classified, not retry-everything):

- Retries transient failures: transport/timeout errors (no HTTP status), 5xx, and 408/409/429.
- Honors `Retry-After` on 429s; otherwise backs off exponentially (capped).
- Fails fast on other 4xx (auth/bad-request) instead of burning the retry budget.
- Re-raises the provider exception when retries are exhausted.

Each real call has a per-request timeout (default 180s, `provider.timeout_seconds`) so a hung connection fails fast and the point checkpoints as a rejected row instead of stalling the worker.

Rate control is `generation.concurrency` (bounds in-flight requests) plus the retry/backoff above. There is no proactive client-side pacing knob; lower `concurrency` if a provider rate-limits.

### Evaluation

`run_evaluation` reads `dataset.final.jsonl` and writes the deduped/decontaminated result to a separate `dataset.evaluated.jsonl`. It never rewrites `dataset.final.jsonl`. It should only run from `evaluate` or `run`, not from `generate`.

Coverage uses lineage from `taxonomy_mix`; it does not do independent LLM assignment in the MVP.

Complexity scoring is optional and makes extra model calls.

## Testing Expectations

Before finalizing code changes, run:

```bash
pytest -q
```

Add or update tests when changing:

- config defaults or validation
- schema subset support
- artifact filenames or row shape
- taxonomy sampling
- generation/refinement behavior
- model logging
- evaluation outputs

Use `"model": "fake"` for tests. Do not require network access in tests.

## Known Cleanup / Improvement Candidates

- **Per-role providers (collapsed to a single `provider` in round 4, 2026-06-23).** Connection
  (`base_url`/`api_key_env`/`timeout_seconds`) now lives on one `provider` block shared by all roles;
  the per-role `base_url`/`api_key` fields were removed and `ModelRouter` uses one shared client. This
  is the common case, but a real workflow may legitimately want different endpoints/keys per role
  (e.g. a strong `strategic` model on one provider, a cheap `bulk` model on another). If that need
  arises, reintroduce optional per-role connection overrides that fall back to `provider`, and re-key
  `ModelRouter`'s client cache by connection identity instead of using a single client.
- NOTE for future edits: `ModelRouter.model_name()` looks like a trivial accessor but is a test seam
  (`test_point_failure_becomes_rejected_row` injects a router exposing only that method, with no
  `.config`) — do not inline it.
- Add a command to tail/summarize `llm_calls.jsonl`.
- Add batched and multi-turn generation modes (see `TODO.md`).

## Safety and Privacy

The tool records full prompts and responses in `llm_calls.jsonl`. This is useful for debugging but can contain sensitive user descriptions, generated data, or provider outputs. Treat run directories as local artifacts unless explicitly sanitized.

Do not paste real API keys into configs. Use `api_key_env`.

## Practical Debugging Tips

- If generation quality looks odd, inspect `llm_calls.jsonl` first.
- If final dataset size is smaller than target, check rejection reasons in `dataset.raw.jsonl`.
- If `taxonomy_mix` is empty, inspect `strategies.json` and `sample_mix` matching logic.
- If JSON parsing fails often, inspect `SYSTEM_JSON` and `generate_record_prompt`.
- If the model is slow, reduce taxonomy depth or increase `generation.concurrency` carefully.
- If the provider rate-limits, lower `generation.concurrency` (the 429 retry/backoff still handles bursts).
