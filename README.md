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

For a real OpenRouter/OpenAI-compatible run:

```bash
export OPENROUTER_API_KEY="..."
python -m syndata.cli taxonomy examples/query_extraction_gemini.yaml
python -m syndata.cli generate examples/query_extraction_gemini.yaml
```

The query extraction example currently uses:

```text
google/gemini-3-flash-preview
```

through the OpenRouter-compatible API.

## CLI Commands

```bash
syndata validate CONFIG.yaml
```

Validates the YAML config, JSON Schema subset, model role config, and output path. It also prints a rough call estimate.

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

Runs dedupe, coverage, and optional complexity scoring against `dataset.final.jsonl`.

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

taxonomy:
  depth: 2
  factors: null
  best_of_n: 1
  review_mode: "auto_accept"
  children_per_node: 3

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
run_state.json
llm_calls.jsonl
eval_report.json
cost_summary.json
embeddings.cache.npz
```

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
- Keep prompts centralized in `syndata/prompts.py`.
- Use `llm_calls.jsonl` when debugging model behavior.
