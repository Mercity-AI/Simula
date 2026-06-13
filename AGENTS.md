# AGENTS.md

This file is the operating guide for agents working on this repository. Read it before changing code.

## Project Purpose

`syndata` is a compact Python CLI for schema-driven or free-text synthetic data generation. It builds taxonomies, samples taxonomy-conditioned mixes, asks an OpenAI-compatible model to generate records, critiques/refines them, and writes auditable JSON/JSONL artifacts.

The project is intentionally small. Prefer clear, boring code over architectural ceremony.

## Current Architecture

Core package:

- `syndata/cli.py`: CLI command dispatch for `validate`, `taxonomy`, `generate`, `evaluate`, and `run`.
- `syndata/config.py`: YAML loading, defaults, config validation, JSON Schema subset checks, call estimates.
- `syndata/models.py`: OpenAI-compatible model router, fake model, retry/rate-limit handling, live `llm_calls.jsonl` logging.
- `syndata/prompts.py`: all built-in prompt templates and the global English/JSON system instruction.
- `syndata/taxonomy.py`: factor discovery, breadth-first taxonomy expansion, review modes, strategy creation, strategy-aware sampling.
- `syndata/generate.py`: generation orchestration, meta-prompts, complexification, JSON generation/repair, critic/refine loop, concurrent workers, final trimming.
- `syndata/evaluate.py`: schema validation, dedupe, coverage reports, coverage-aware trimming, optional complexity scoring.
- `syndata/utils.py`: artifact names, JSON/JSONL helpers, timestamps, JSON extraction, record-to-text, checkpoint helpers.

Examples:

- `examples/basic_qa.yaml`: fake-model smoke test.
- `examples/query_extraction_gemini.yaml`: real query-extraction pilot config using Gemini via OpenRouter.
- `examples/cat_stories_freetext.yaml`: fake-model smoke test for schema-free text generation.

Tests:

- `tests/test_config.py`
- `tests/test_utils.py`
- `tests/test_evaluate.py`
- `tests/test_pipeline.py`

Generated artifacts:

- `runs/` is ignored and should not be committed.
- `llm_calls.jsonl` can be large and may contain full prompts/responses. Treat it as local run data.

## Non-Negotiable Constraints

- Do not commit API keys, bearer tokens, `.env`, or generated run artifacts.
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

For real OpenRouter-compatible runs:

```bash
export OPENROUTER_API_KEY="..."
python -m syndata.cli taxonomy examples/query_extraction_gemini.yaml
python -m syndata.cli generate examples/query_extraction_gemini.yaml
```

Do not run real model calls unless the user explicitly asks. They cost money and can take time.

## Config Contract

The config loader applies defaults from `syndata/config.py`. Required user-facing sections:

- `project`: `name`, `output_dir`, `seed`
- `description`: dataset description
- `schema`: JSON Schema subset, or `null`/omitted for free-text generation
- `models`: `strategic`, `bulk`, `critic`
- `prompts`: optional Python prompt module override
- `taxonomy`: depth/factors/review behavior
- `generation`: target size, overgeneration, complexity ratio, retries, concurrency
- `evaluation`: dedupe, coverage, complexity

Supported model config fields:

- `base_url`
- `api_key` or `api_key_env`
- `model`
- `temperature`
- `max_tokens`
- `min_interval_seconds`
- `extra_body`

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

`complete_json` expects parseable JSON somewhere in the response. If you strengthen parsing, keep fenced JSON support.

Rate-limit behavior:

- Retries 429s.
- Uses `Retry-After` when present.
- Otherwise backs off up to 60 seconds.
- Raises with the provider response body when retries are exhausted.

### Evaluation

`run_evaluation` may rewrite `dataset.final.jsonl` after dedupe. It should only run from `evaluate` or `run`, not from `generate`.

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

- Add a command to tail/summarize `llm_calls.jsonl`.
- Add prompt override support from config files.

## Safety and Privacy

The tool records full prompts and responses in `llm_calls.jsonl`. This is useful for debugging but can contain sensitive user descriptions, generated data, or provider outputs. Treat run directories as local artifacts unless explicitly sanitized.

Do not paste real API keys into configs. Use `api_key_env`.

## Practical Debugging Tips

- If generation quality looks odd, inspect `llm_calls.jsonl` first.
- If final dataset size is smaller than target, check rejection reasons in `dataset.raw.jsonl`.
- If `taxonomy_mix` is empty, inspect `strategies.json` and `sample_mix` matching logic.
- If JSON parsing fails often, inspect `SYSTEM_JSON` and `generate_record_prompt`.
- If the model is slow, reduce taxonomy depth or increase `generation.concurrency` carefully.
- If the provider rate-limits, set `min_interval_seconds` or lower concurrency.
