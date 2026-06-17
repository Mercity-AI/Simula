# syndata

`syndata` is a small CLI-first framework for generating schema-shaped or free-text synthetic datasets with taxonomy-guided coverage. It is inspired by the Simula-style workflow: map the conceptual space first, sample from that space, generate records, critique them, preserve lineage, and then trim/evaluate the result.

The current implementation is intentionally lean. It uses JSON/YAML files, OpenAI-compatible chat endpoints, JSON Schema validation, and human-inspectable artifacts.

## What It Does

- Builds taxonomies from a dataset description and optional user-provided factors.
- Generates sampling strategies over taxonomy branches.
- Samples taxonomy mixes and turns them into meta-prompts.
- Generates JSON records matching a user-defined schema, or direct free-form text when `schema: null`.
- Validates records with a JSON Schema subset.
- Runs a simple critic/refine loop.
- Writes every model response to `llm_calls.jsonl` as soon as it returns.
- Writes raw, accepted, and final datasets as JSONL.
- Supports n-gram dedupe, coverage reports, optional reassignment coverage, diversity metrics, and optional complexity scoring.

## Install

From the repository root:

```bash
python -m pip install -e ".[dev]"
```

You can also run the CLI without installing:

```bash
python -m syndata.cli validate examples/basic_qa.yaml
```

## Quick Start

Run the fake-model smoke test. This does not call any external API:

```bash
python -m syndata.cli run examples/basic_qa.yaml
```

Artifacts are written to:

```text
runs/basic_qa/
```

For a real OpenRouter/OpenAI-compatible run, provide the key named by each role's
`api_key_env`. The simplest way is a gitignored `.env` file in the repo root (or next to the
config), which `syndata` loads automatically on startup:

```bash
echo 'OPENROUTER_API_KEY=sk-or-...' > .env   # gitignored; loaded automatically
python -m syndata.cli taxonomy examples/query_extraction_gemini.yaml
python -m syndata.cli generate examples/query_extraction_gemini.yaml
```

Key resolution precedence per role: `models.<role>.api_key` (inline; avoid for real keys) →
`os.environ[api_key_env]` → `.env`. An exported shell variable still works and takes precedence
over `.env`. Never commit real keys.

The query extraction example currently uses:

```text
google/gemini-3-flash-preview
```

through the OpenRouter-compatible API.

## CLI Commands

```bash
syndata validate CONFIG.yaml
```

Validates the YAML config, JSON Schema subset, model role config, and output path. It makes no model calls.

```bash
syndata taxonomy CONFIG.yaml
```

Builds `taxonomy.json` and, depending on `taxonomy.review_mode`, either accepts it automatically or lets the user review it.

```bash
syndata generate CONFIG.yaml
```

Loads or builds the taxonomy, builds strategies if missing, generates data, validates records, runs critic/refine, and writes dataset artifacts. It does not run evaluation.

```bash
syndata evaluate CONFIG.yaml
```

Runs dedupe, coverage, and optional complexity scoring. It reads `dataset.final.jsonl` and writes
the deduped/decontaminated result to a separate `dataset.evaluated.jsonl` (it never rewrites
`dataset.final.jsonl`), plus `eval_report.json`.

```bash
syndata run CONFIG.yaml
```

Runs taxonomy, generation, and evaluation end to end.

## Config Overview

Each run is controlled by a single YAML file:

```yaml
project:
  name: "pilot"
  output_dir: "runs/pilot"
  seed: 42

description: "Describe the dataset to generate."

schema:
  type: object
  required: ["input", "output"]
  properties:
    input:
      type: string
    output:
      type: string

models:
  strategic:
    base_url: "https://openrouter.ai/api/v1"
    api_key_env: "OPENROUTER_API_KEY"
    model: "google/gemini-3-flash-preview"
  bulk:
    base_url: "https://openrouter.ai/api/v1"
    api_key_env: "OPENROUTER_API_KEY"
    model: "google/gemini-3-flash-preview"
  critic:
    base_url: "https://openrouter.ai/api/v1"
    api_key_env: "OPENROUTER_API_KEY"
    model: "google/gemini-3-flash-preview"

prompts:
  module: "config/prompts.py"

taxonomy:
  depth: 2
  factors: null
  best_of_n: 1
  review_mode: "auto_accept"
  children_per_node: 3

strategy:
  guidance: null

sampling:
  tasks: {}

generation:
  target_size: 50
  overgenerate_ratio: 1.2
  scenarios_per_mix: 3
  complexity_ratio: 0.3
  max_refine_attempts: 1
  concurrency: 4
  checkpoint_every: 50

evaluation:
  dedupe: true
  coverage: true
  coverage_mode: "lineage"
  complexity: false
  diversity:
    enabled: false
    embedding_model: "sentence-transformers/all-MiniLM-L6-v2"
    k_local: 10
    sample_cap: 1000
    text_field: null
```

### Model Roles

- `strategic`: factor discovery, taxonomy expansion, strategy generation.
- `bulk`: meta-prompt generation, complexification, record generation, repair, refinement.
- `critic`: semantic critique and optional complexity scoring.

All model roles use the same OpenAI-compatible chat completions interface. A role can use `"model": "fake"` for deterministic local tests.

### Prompt Overrides

Built-in prompt defaults live in `syndata/prompts.py`. To override them for one run, point config at a Python module:

```yaml
prompts:
  module: "config/prompts.py"
```

Paths are resolved relative to the YAML file. The module may override any subset of the built-in prompt functions, and missing functions fall back to the defaults. It may also override `SYSTEM_JSON` and `SYSTEM_TEXT`.

```python
SYSTEM_JSON = "Return valid JSON only."


def strategy_prompt(description, taxonomy, guidance=None):
    return f"""
Dataset description:
{description}

Taxonomy:
{taxonomy}

Create only combinations that make semantic sense for this dataset.
Return JSON with a strategies array.
""".strip()
```

Override functions must keep the same parameter names as their built-in counterparts in `syndata/prompts.py`. `syndata validate` imports the module and rejects missing files, import failures, non-string system prompts, and incompatible function signatures before any model call runs.

### Strategy Guidance

Strategies decide which taxonomy branches combine and how often each combination is sampled. To steer that without writing a prompt module, set free-text `strategy.guidance`:

```yaml
strategy:
  guidance: |
    - Make billing + calm + simple the most common combination.
    - Never combine enterprise_sso with mobile_app; they don't coexist.
    - Keep a dedicated strategy for furious + needs_escalation, even though it is rare.
```

The guidance is woven into the strategy prompt, so the generated `strategies.json` reflects your intent (root combinations and weights) before any bulk generation runs. Guidance is a nudge interpreted by the strategic model, not a hard constraint; for guarantees, edit `strategies.json` directly or override `strategy_prompt`. When unset (`null`), the built-in prompt is used unchanged.

### Sampling

Decoding params resolve in three layers, last one wins: built-in defaults (`temperature: 0.7`, `max_tokens: 32768`) → per-role config under `models.<role>` → per-task overrides under `sampling.tasks`. Each task is handled by exactly one role, so naming a task is enough — no role needs to be specified.

```yaml
sampling:
  tasks:
    generate:    {temperature: 1.1, top_p: 0.95, min_p: 0.05}
    repair:      {temperature: 0.0}
    meta_prompt: {temperature: 1.1}
```

Valid task names are the `TaskType` values: `factor_discovery`, `node_expansion`, `taxonomy_critic`, `level_plan`, `strategy`, `meta_prompt`, `complexify`, `generate`, `repair`, `semantic_critic`, `refine`, `complexity_score`, `node_assign`.

OpenAI-compatible params (`temperature`, `top_p`, `max_tokens`, `frequency_penalty`, `presence_penalty`, `stop`, `seed`) are sent as top-level call kwargs. Anything else (`min_p`, `top_k`, `repetition_penalty`, …) is passed through `extra_body` so provider-specific knobs work without lock-in. The resolved params are recorded per call in `llm_calls.jsonl`.

Connection/control keys on a role (`base_url`, `api_key`, `api_key_env`, `model`, `min_interval_seconds`, `timeout_seconds`, `extra_body`) are never treated as decoding params.

**Per-request timeout.** Each real call uses a default timeout of **180 seconds** so a hung or
rate-limited provider connection cannot stall a worker for the SDK default (~600s). Override per
role with `models.<role>.timeout_seconds`.

**Reasoning models.** There is no automatic model-id detection. If a model emits hidden reasoning
tokens (e.g. DeepSeek R1/V4, OpenAI o-series) and you want them excluded from output and your
`max_tokens` budget, set it explicitly per role:

```yaml
models:
  bulk:
    model: "deepseek/deepseek-v4-flash"
    max_tokens: 16384
    extra_body: {reasoning: {effort: low, exclude: true}}
```

`syndata validate` rejects unknown task names and non-numeric values before any model call runs. With no `sampling` block, behavior is unchanged except the larger default `max_tokens`. That 32K default is batteries-included: roles without an explicit `max_tokens` can now emit much larger (and pricier) completions than before — set `models.<role>.max_tokens` or a per-task `max_tokens` to cap it.

### Schema Support

The MVP supports a practical JSON Schema subset:

- `object`
- `string`
- `number`
- `integer`
- `boolean`
- `array`
- `enum`
- `required`
- nested objects and arrays

Every generated record must validate against the schema before it can be accepted.

Set `schema: null` or omit `schema` to use free-text mode. In that mode `record` is a string, JSON repair is skipped, and the critic still returns a JSON verdict.

## Artifacts

Each run writes human-inspectable files under `project.output_dir`:

```text
taxonomy.json
strategies.json
dataset.raw.jsonl
dataset.accepted.jsonl
dataset.final.jsonl
dataset.evaluated.jsonl
run_state.json
llm_calls.jsonl
eval_report.json
cost_summary.json
embeddings.cache.npz
```

`dataset.final.jsonl` is the generator's output. `dataset.evaluated.jsonl` is written by
`evaluate`/`run` and holds the deduped/decontaminated rows, leaving `dataset.final.jsonl`
untouched.

`llm_calls.jsonl` is especially useful while a run is still active. Every successful model response is appended immediately with:

- timestamp
- model role
- model name
- duration
- system prompt
- user prompt
- raw response

`dataset.final.jsonl` contains rows with full lineage:

- `id`
- `attempt_index`
- `record`
- `output_format`
- `taxonomy_mix`
- `strategy_id`
- `meta_prompt`
- `complexified`
- `generator_model`
- `critic_verdicts`
- `schema_valid`
- `accepted`
- `rejection_reason`
- `created_at`

## Query Extraction Example

The query extraction example generates records like:

```json
{
  "query": "Compare Amtrak and Greyhound bus tickets from New York Penn Station to Washington DC for this Friday...",
  "extraction": {
    "intent": "transportation comparison",
    "search_terms": ["Amtrak", "Greyhound", "New York", "Washington DC"],
    "attributes": {
      "domain": "travel",
      "category": "ground transportation",
      "entities": ["Amtrak", "Greyhound"],
      "descriptors": ["cheapest", "less than 4 hours"],
      "quantities": ["under 4 hours"]
    },
    "filters": {
      "location": "New York to Washington DC",
      "time": "this Friday",
      "sort": "cheapest first"
    },
    "exclusions": [],
    "ambiguities": ["Exact date for this Friday"]
  }
}
```

Run it in two phases if you want to inspect the taxonomy before spending generation calls:

```bash
export OPENROUTER_API_KEY="..."
python -m syndata.cli taxonomy examples/query_extraction_gemini.yaml
python -m syndata.cli generate examples/query_extraction_gemini.yaml
```

Evaluation is separate:

```bash
python -m syndata.cli evaluate examples/query_extraction_gemini.yaml
```

For diversity in JSON mode, set `evaluation.diversity.text_field` to a JSONPath such as `$.query` to embed a specific text field instead of the full JSON blob. Embeddings are cached under the run directory and reused on later `evaluate` runs.

## Tests

```bash
pytest -q
```

Current test coverage includes:

- config defaults and validation
- JSON Schema subset validation
- JSONL IO
- deterministic taxonomy sampling
- dedupe
- coverage reports
- JSON repair path
- fake-model end-to-end generation
- CLI validation

## Current Limitations

- Generation is thread-based and pilot-scale, not distributed.
- The model client only supports OpenAI-compatible chat completions.
- The critic is simple and single-pass by default.
- Complexity scoring is optional and relatively basic.
- Taxonomy coverage for generated data uses lineage, not independent LLM assignment.
- There is no database, web UI, fine-tuning harness, multimodal support, or production queue.

## Development Notes

- Keep generated outputs under `runs/`; it is gitignored.
- Avoid committing API keys or local `.env` files.
- Prefer editing example YAMLs rather than hardcoding task-specific behavior.
- Keep built-in prompt defaults centralized in `syndata/prompts.py`; use prompt modules for per-run overrides.
- Use `llm_calls.jsonl` when debugging model behavior.
