# Quickstart

This page runs `simula` twice: once offline with fake models, to see what a run produces, and once for
real on a small dataset.

## 1. An offline run

Save this as `quickstart.yaml`. Every role uses the `fake` model, which returns placeholder output, so
it runs offline with no key and costs nothing:

```yaml
project:
  name: "quickstart"
  output_dir: "runs/quickstart"

description: "A small dataset of concise question-answer pairs about practical machine learning concepts."

schema:
  type: object
  required: ["input", "output"]
  properties:
    input:  { type: string }
    output: { type: string }

provider:
  base_url: "fake"

models:
  strategic: { model: "fake" }
  bulk:      { model: "fake" }
  critic:    { model: "fake" }

taxonomy:
  children_per_node: 2

generation:
  target_size: 5
```

Run it end to end:

```bash
simula run quickstart.yaml
```

```text
› Building taxonomy
▸ topic — Subject area
  + topic → 2 children
  + topic/alpha → 2 children
  + topic/beta → 2 children
▸ difficulty — Difficulty level
  ...
Taxonomy: 2 factors, depth 2

Mix space: up to 16 distinct mixes; this run samples 7

› Generating 7 meta-prompts

› Generating 7 attempts (target 5)
Accepted 7 → 5 final (target 5, trim enabled)

› Evaluating

Run complete: 5 final records, eval count=5
Artifacts: runs/quickstart
Cost summary: calls=40 in_tokens=12161 out_tokens=985 (reasoning=0)
```

`target_size: 5` with the default `overgenerate_ratio` of 1.3 makes 7 attempts; the 7 accepted records
are trimmed back to 5.

## 2. Look at what it wrote

Everything is in `runs/quickstart/`:

```text
taxonomy.json            the factor tree
strategies.json          how the tree is sampled
meta_prompts.jsonl       one instruction per attempt
dataset.raw.jsonl        every attempt, accepted or not
dataset.accepted.jsonl   attempts that passed the schema and the critic
dataset.final.jsonl      accepted rows trimmed to target_size: the dataset
dataset.evaluated.jsonl  the final rows after dedupe
eval_report.json         counts, coverage
llm_calls.jsonl          every model call
cost_summary.json        calls and tokens
run_state.json           checkpoint for resuming
```

Each row of `dataset.final.jsonl` holds the record and how it was made:

```json
{
  "id": "item-0-146bb4af",
  "attempt_index": 0,
  "record": {"input": "Question 77f1870d?", "output": "Answer 77f1870d with traceable lineage."},
  "output_format": "json",
  "taxonomy_mix": [
    {"factor": "topic", "node": "beta", "level": 2, "path": ["topic", "alpha", "beta"], "description": "Beta branch"},
    {"factor": "difficulty", "node": "beta", "level": 2, "path": ["difficulty", "alpha", "beta"], "description": "Beta branch"}
  ],
  "strategy_id": "general",
  "meta_prompt": "Create a concise input and output pair about alpha.",
  "complexified": false,
  "generator_model": "fake",
  "critic_verdicts": [{"verdict": "accept", "explanation": "The record is valid and follows the prompt."}],
  "schema_valid": true,
  "accepted": true,
  "rejection_reason": null,
  "created_at": "2026-11-13T10:09:51.563058+00:00"
}
```

The fake model's text is placeholder; the shape is what a real run produces. Every field is explained
in [Artifacts](reference/artifacts.md).

## 3. A real run

Add your key to `.env` in the same directory (see [Installation](installation.md#api-key)):

```bash
echo 'OPENROUTER_API_KEY=sk-or-...' > .env
```

Then make the config real. In `quickstart.yaml`, replace the `project`, `provider`, `models` and
`generation` blocks with these; the new output directory keeps the fake run's files from being reused:

```yaml
project:
  name: "ml_qa"
  output_dir: "runs/ml_qa"

provider:
  base_url: "https://openrouter.ai/api/v1"
  api_key_env: "OPENROUTER_API_KEY"

models:
  strategic: { model: "deepseek/deepseek-v4-pro" }
  bulk:      { model: "deepseek/deepseek-v4-flash" }
  critic:    { model: "deepseek/deepseek-v4-flash" }

generation:
  target_size: 20
  meta_prompts: false       # the combined path: records written straight from their mixes
  record_batch_size: 5      # 5 records per call
  subject: auto             # one concrete situation per record
  concurrency: 8
```

This spends real money, though a run this small costs cents. Build the plan first and stop:

```bash
simula generate quickstart.yaml --stop-after strategies
```

Open `runs/ml_qa/taxonomy.json` and `strategies.json`. They decide what the dataset covers and in what
shares; edit them if something is off (see [Reviewing the design](guides/reviewing-the-design.md)). Then
write the records and evaluate:

```bash
simula generate quickstart.yaml
simula evaluate quickstart.yaml
```

The command ends with a cost line, and `runs/ml_qa/cost_summary.json` breaks it down by role and task.
Read a few rows of `dataset.final.jsonl`, and any `rejection_reason` in `dataset.raw.jsonl`.

## Next

- [How a run works](concepts/pipeline.md): what each stage does and which file it writes.
- [Writing a config](guides/writing-a-config.md): the description, factors and settings for your own
  dataset.
- [Running a pilot](guides/running-a-pilot.md): the path from a 20-row test to a full run.
