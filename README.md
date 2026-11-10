<p align="center">
  <img src="assets/hero.png" alt="Simula" width="640">
</p>

<p align="center">
  <img alt="Python" src="https://img.shields.io/badge/python-%E2%89%A53.11-blue">
  <a href="LICENSE"><img alt="License: MIT" src="https://img.shields.io/badge/license-MIT-green"></a>
  <img alt="Version" src="https://img.shields.io/badge/version-0.1.0-blue">
  <img alt="Status" src="https://img.shields.io/badge/status-pilot-orange">
  <a href="https://arxiv.org/abs/2603.29791"><img alt="Paper" src="https://img.shields.io/badge/paper-arXiv%3A2603.29791-b31b1b"></a>
  <a href="https://github.com/Mercity-AI"><img alt="Built by Mercity" src="https://img.shields.io/badge/built%20by-Mercity-1f6feb"></a>
</p>

# simula

> 📖 **[Concepts & Configuration](DOCS.md)** — the pipeline, the model roles, and every config knob, with diagrams.

`simula` is a small CLI-first framework for generating schema-shaped or free-text synthetic datasets with taxonomy-guided coverage. It is inspired by the Simula-style workflow: map the conceptual space first, sample from that space, generate records, critique them, preserve lineage, and then trim/evaluate the result.

The current implementation is intentionally lean. It uses JSON/YAML files, OpenAI-compatible chat endpoints, JSON Schema validation, and human-inspectable artifacts.

## What It Does

- Builds taxonomies from a dataset description and optional user-provided factors.
- Generates sampling strategies over taxonomy branches.
- Samples taxonomy mixes and turns them into meta-prompts.
- Optionally gives every record its own situation: the concrete thing it is about, listed per node of the subject factor so records that share a node differ (`generation.subject`).
- Generates JSON records matching a user-defined schema, or direct free-form text when `schema: null`.
- Validates records with a JSON Schema subset.
- Runs a simple critic/refine loop.
- Warns before generation when strategies pull a factor's shares away from its tree weights.
- Optionally writes several records per call straight from their sampled mixes (the combined path), with every record still validated and critiqued on its own.
- Writes every model response to `llm_calls.jsonl` as soon as it returns.
- Writes raw, accepted, and final datasets as JSONL.
- Supports n-gram dedupe, coverage reports, optional reassignment coverage, diversity metrics, and optional complexity scoring.

## Install

From the repository root:

```bash
python -m pip install -e ".[dev]"
```

The base install is light. The optional embedding-based **diversity** metric needs heavier packages
(numpy, scikit-learn, sentence-transformers / PyTorch); install them only if you enable it:

```bash
python -m pip install -e ".[diversity]"
```

You can also run the CLI without installing:

```bash
python -m simula.cli validate examples/basic_qa.yaml
```

## Quick Start

Run the fake-model smoke test. This does not call any external API:

```bash
python -m simula.cli run examples/basic_qa.yaml
```

Artifacts are written to:

```text
runs/basic_qa/
```

For a real OpenRouter/OpenAI-compatible run, put the key named by `provider.api_key_env` in a
gitignored `.env` file at the project root:

```bash
echo 'OPENROUTER_API_KEY=sk-or-...' > .env   # gitignored; the only source of API keys
python -m simula.cli taxonomy examples/query_extraction_gemini.yaml
python -m simula.cli generate examples/query_extraction_gemini.yaml
```

Key resolution: the project-root `.env` file is read directly (via `dotenv_values`) and is the
**only** source of API keys — a shell-exported variable is deliberately ignored, so the key always
comes from the file you can see. A missing key warns at `validate` and fails real model calls. Never
commit real keys.

The query extraction example currently uses:

```text
google/gemini-3-flash-preview
```

through the OpenRouter-compatible API.

## CLI Commands

```bash
simula validate CONFIG.yaml
```

Validates the YAML config, JSON Schema subset, model role config, and output path. It makes no model calls.

```bash
simula taxonomy CONFIG.yaml
```

Builds `taxonomy.json` and, depending on `taxonomy.review_mode`, either accepts it automatically or lets the user review it.

```bash
simula generate CONFIG.yaml
```

Loads or builds the taxonomy, builds strategies and meta prompts if missing (skipped with `generation.meta_prompts: false`), generates data, validates records, runs critic/refine, and writes dataset artifacts. It does not run evaluation.

Pass `--stop-after {taxonomy,strategies,meta_prompts}` (or set `generation.stop_after` in the
config) to halt once that stage's artifact is written. Edit the artifact, then rerun: existing
artifacts are reused, so the run picks up where it stopped. `--stop-after none` overrides a
config-set stop for one invocation. To regenerate a stage instead, delete its file
(`taxonomy.json`, `strategies.json`, `meta_prompts.jsonl`) and rerun. With `generation.meta_prompts: false` there is no
meta-prompt stage, so `meta_prompts` is not a valid stop there.

```bash
simula evaluate CONFIG.yaml
```

Runs dedupe, coverage, and optional complexity scoring. It reads `dataset.final.jsonl` and writes
the deduped/decontaminated result to a separate `dataset.evaluated.jsonl` (it never rewrites
`dataset.final.jsonl`), plus `eval_report.json`.

```bash
simula run CONFIG.yaml
```

Runs taxonomy, generation, and evaluation end to end.

## Config Overview

Each run is controlled by a single YAML file. **`examples/template.yaml`** is a copy-me skeleton with
every key at its default, and **`CONFIG.md`** is the full per-field reference. Only two things are
required — a non-empty `description` and a `model` id for each of the three roles; everything else has
a default, so the minimum viable config is small:

```yaml
description: "Describe the dataset to generate."

schema:                       # omit or set null for free-text mode
  type: object
  required: ["input", "output"]
  properties:
    input: { type: string }
    output: { type: string }

provider:
  base_url: "https://openrouter.ai/api/v1"
  api_key_env: "OPENROUTER_API_KEY"   # variable name read from the project-root .env

models:
  strategic: { model: "google/gemini-3-flash-preview" }
  bulk:      { model: "google/gemini-3-flash-preview" }
  critic:    { model: "google/gemini-3-flash-preview" }
```

Copy `examples/template.yaml` for the full set of knobs (`project`, `taxonomy`, `strategy`,
`sampling`, `generation`, `evaluation`); the sections below explain the ones worth steering by hand.

### Model Roles

- `strategic`: factor discovery, taxonomy expansion, strategy generation.
- `bulk`: meta-prompt generation, complexification, record generation, repair, refinement.
- `critic`: semantic critique and optional complexity scoring.

All model roles use the same OpenAI-compatible chat completions interface. A role can use `"model": "fake"` for deterministic local tests.

### Prompt Overrides

Built-in prompt defaults live in `simula/prompts.py`. To override them for one run, point config at a Python module:

```yaml
prompts:
  module: "config/prompts.py"
```

Paths are resolved relative to the YAML file. The module may override any subset of the built-in prompt functions, and missing functions fall back to the defaults. It may also override `SYSTEM_JSON` and `SYSTEM_TEXT`.

```python
SYSTEM_JSON = "Return valid JSON only."


def strategy_prompt(description, taxonomy, valid_paths, guidance=None, count=None):
    return f"""
Dataset description:
{description}

Taxonomy:
{taxonomy}

Use only these taxonomy paths, verbatim:
{valid_paths}

Create only combinations that make semantic sense for this dataset.
Return JSON with a strategies array.
""".strip()
```

The built-in prompts share a few rules defined as constants at the top of `simula/prompts.py`
(`CONTEXT_RULE`, `PHRASING_RULE`, `DIVERSITY_RULE`, `REVISE_RULE`, `TAXONOMY_CONTEXT_RULE`). An override replaces the whole prompt, so import the rules that apply and
include them; each one is there because a run went wrong without it (see `SKILL.md`, "Custom prompts").

Override functions must keep the same parameter names as their built-in counterparts in `simula/prompts.py`. `simula validate` imports the module and rejects missing files, import failures, non-string system prompts, and incompatible function signatures before any model call runs.

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

Connection lives on `provider` (`base_url`, `api_key_env`, `timeout_seconds`), shared by all roles;
the non-decoding role keys (`model`, `extra_body`) are never treated as decoding params.

**Per-request timeout.** Each real call uses a default timeout of **180 seconds** so a hung or
rate-limited provider connection cannot stall a worker for the SDK default (~600s). Override with
`provider.timeout_seconds`.

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

`simula validate` rejects unknown task names and non-numeric values before any model call runs. With no `sampling` block, behavior is unchanged except the larger default `max_tokens`. That 32K default is batteries-included: roles without an explicit `max_tokens` can now emit much larger (and pricier) completions than before — set `models.<role>.max_tokens` or a per-task `max_tokens` to cap it.

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
situations.jsonl
meta_prompts.jsonl
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

By default, generation coverage-trims accepted rows to `generation.target_size` after optional
deduplication. Set `generation.trim: false` to skip trimming and keep all remaining accepted rows
in their existing order, even above the target. The attempt budget still uses
`ceil(target_size * overgenerate_ratio)`. You can change `trim` on resume without regenerating
completed attempts. Deduplication is controlled independently by `evaluation.dedupe`.

Deduplication compares case-folded word sequences from `record` (for JSON, serialized with sorted
object keys, retaining array order). Keys and values both contribute; punctuation does not. It is
text-overlap detection, not semantic or field-aware JSON comparison. The default is 13-word
sequences with Jaccard similarity at least 0.8, keeping the first retained match in input order.
A temporary SQLite index stores 16-byte hashes of a lexicographically ordered prefix (about 20%
of each record's distinct sequences at the default threshold). Sequence-count bounds reject
impossible matches, and candidate pairs are verified against exact sequences; hash collisions
cannot delete distinct records. The index is removed when deduplication exits. Its page cache is
8 MiB, but temporary disk usage grows with indexed sequences and the input rows still reside in
memory. Highly repetitive inputs can still produce many candidate comparisons.

`situations.jsonl` exists when `generation.subject` is set: one row per attempt (`attempt_index`,
`subject` = the node path, `situation` = one sentence naming the concrete thing that record is about).
It is written before anything else and reused when present, so a rerun only fills attempts that have
none. With `subject: auto` the model's pick of the subject factor is stored in `taxonomy.json` under
`"subject"`. A situation is not a meta-prompt: it covers one factor and says only what the record is
about, while a meta-prompt is the whole brief for the record (`DOCS.md`, "Situations and meta-prompts").

`meta_prompts.jsonl` holds one row per attempt (`attempt_index`, sampled `strategy_id` +
`taxonomy_mix`, `meta_prompt`, `complexified`). Like `taxonomy.json` and `strategies.json` it is
reused when present — stop with `--stop-after meta_prompts`, hand-edit the prompts, and rerun to
generate from the edited versions. With `generation.meta_prompts: false` (the combined path) there is
no meta-prompt step and no `meta_prompts.jsonl`: records are written straight from their sampled
mixes, `generation.record_batch_size` per call. On that path, factors listed in `generation.model_picked` are
chosen by the model per record from the offered options; their lineage entries carry
`"picked_by": "model"`. Factors listed in `generation.code_level` are sampled by code only down to
that tree level (their lineage entry is the node at that level) and the writer chooses the specifics
beneath it; this works on both paths. A record whose complexify coin came up is rewritten by a
separate `complexify` call: on the meta-prompt, or on the combined path on the written record.

`llm_calls.jsonl` is especially useful while a run is still active. Every successful model response is appended immediately with:

- timestamp
- model role
- model name
- duration
- system prompt
- user prompt
- raw response
- `in_tokens` / `out_tokens`, plus `reasoning_tokens` (hidden reasoning, part of `out_tokens`),
  `cached_tokens` (prompt-cache hits, part of `in_tokens`) and `cost` (the provider-billed cost; USD on
  OpenRouter) when the provider reports them (0 / null otherwise)
- the resolved sampling params and `extra_body`

`cost_summary.json` rolls these up per role/task/model for the last command (`total_cost` is the sum
of provider-reported costs). It is overwritten by each command, so aggregate `llm_calls.jsonl` for the
cost of a whole multi-command run.

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

On the combined path `meta_prompt` holds the model's one-line plan for the record followed by its
sampled ingredients: what the record was built from, and what the critic judged it against. A row that
is accepted while its last critic verdict is `reject` was corrected by the critic itself (a critic
response carrying `"revised"`), with no refine call.

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
echo 'OPENROUTER_API_KEY=sk-or-...' > .env   # the only source of API keys; a shell export is ignored
python -m simula.cli taxonomy examples/query_extraction_gemini.yaml
python -m simula.cli generate examples/query_extraction_gemini.yaml
```

Evaluation is separate:

```bash
python -m simula.cli evaluate examples/query_extraction_gemini.yaml
```

For diversity in JSON mode, set `evaluation.diversity.text_field` to a dotted field path such as `query` or `extraction.intent` to embed a specific field instead of the full JSON blob (a leading `$.` is accepted too). This is plain nested-key access, not full JSONPath. Embeddings are cached under the run directory and reused on later `evaluate` runs.

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

- Generation is `asyncio`-concurrent and pilot-scale, not distributed.
- The model client only supports OpenAI-compatible chat completions.
- The critic is simple: one critique per record, plus a re-critique after each refine.
- Complexity scoring is optional and relatively basic.
- Taxonomy coverage uses lineage by default (what was sampled, not what the record verifiably contains); `evaluation.coverage_mode: reassign` adds independent LLM assignment at the cost of extra calls.
- There is no database, web UI, fine-tuning harness, multimodal support, or production queue.

## Development Notes

- Keep generated outputs under `runs/`; it is gitignored.
- Avoid committing API keys or local `.env` files.
- Prefer editing example YAMLs rather than hardcoding task-specific behavior.
- Keep built-in prompt defaults centralized in `simula/prompts.py`; use prompt modules for per-run overrides.
- Use `llm_calls.jsonl` when debugging model behavior.
