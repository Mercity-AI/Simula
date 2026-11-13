# Artifacts

Every run writes plain JSON and JSONL files to `project.output_dir`. They are meant to be read, and
the first four are meant to be edited: a rerun reuses any of them that exist.

| File | Written by | What it holds |
|---|---|---|
| `taxonomy.json` | `taxonomy`, `generate` | The factor tree, with a `weight` per node, and the `subject` pick when `generation.subject: auto`. |
| `strategies.json` | `generate` | The sampling strategies: `taxonomy_roots`, `weight`, optional `never_combine`. |
| `situations.jsonl` | `generate` | One row per attempt when `generation.subject` is set: `attempt_index`, `subject` (the node path), `situation`. |
| `meta_prompts.jsonl` | `generate` | One row per attempt when `generation.meta_prompts` is true: `attempt_index`, `strategy_id`, `taxonomy_mix`, `meta_prompt`, `complexified`. |
| `dataset.raw.jsonl` | `generate` | Every attempt, accepted or not. Look here for `rejection_reason`. |
| `dataset.accepted.jsonl` | `generate` | The attempts that passed the schema and the critic. |
| `dataset.final.jsonl` | `generate` | Accepted rows after optional dedupe and the trim to `target_size`. The generator's output. |
| `dataset.evaluated.jsonl` | `evaluate`, `run` | `dataset.final.jsonl` after dedupe and decontamination. `dataset.final.jsonl` itself is never rewritten. |
| `eval_report.json` | `evaluate`, `run` | Row count, dedupe and decontamination removals, coverage, and the optional diversity and complexity scores. |
| `run_state.json` | `generate` | Progress checkpoint and the resume fingerprint. |
| `llm_calls.jsonl` | every command | One row per model response, appended as soon as it returns. |
| `cost_summary.json` | every command | Calls, tokens and cost for the last command, per role, task and model. |
| `embeddings.cache.npz` | `evaluate`, `run` | Cached embeddings for the diversity metrics. |

To regenerate a stage, delete its file and rerun. To stop at a stage and edit its file first, use
`--stop-after` (see [CLI](cli.md)).

## Dataset rows

`dataset.raw.jsonl`, `dataset.accepted.jsonl`, `dataset.final.jsonl` and `dataset.evaluated.jsonl` all
hold rows of this shape:

```json
{
  "id": "item-3-1a2b3c4d",
  "attempt_index": 3,
  "record": {"input": "What is overfitting?", "output": "When a model memorizes training noise..."},
  "output_format": "json",
  "taxonomy_mix": [
    {"factor": "topic", "node": "generalization", "level": 1, "path": ["topic", "generalization"], "description": "..."}
  ],
  "strategy_id": "general",
  "meta_prompt": "Write a Q&A pair explaining overfitting for a beginner.",
  "complexified": false,
  "generator_model": "deepseek/deepseek-v4-flash",
  "critic_verdicts": [{"verdict": "accept", "explanation": "..."}],
  "schema_valid": true,
  "accepted": true,
  "rejection_reason": null,
  "created_at": "2026-11-13T12:00:00+00:00"
}
```

| Field | Meaning |
|---|---|
| `id` | Unique row id. |
| `attempt_index` | Which attempt produced the row. Resume and the trim order use it. |
| `record` | The generated record: an object in JSON mode, a string in free-text mode. |
| `output_format` | `json` or `text`. |
| `taxonomy_mix` | The row's lineage: one entry per factor, naming the node the record was built from. An entry the model chose (`generation.model_picked`) carries `"picked_by": "model"`. |
| `strategy_id` | The strategy the mix was sampled under. |
| `meta_prompt` | The meta-prompt the record was written from. On the combined path, the writer's one-line plan for the record followed by its sampled ingredients. |
| `complexified` | Whether the attempt went through the `complexify` call. |
| `generator_model` | The `bulk` model id. There is no separate generator role. |
| `critic_verdicts` | Every critic verdict for the record, in order, each with an explanation. |
| `schema_valid` | Whether the final record matches the schema. |
| `accepted` | Whether the row passed. A row can be accepted while its last verdict is `reject`: the critic returned a corrected record itself, and that record was accepted with no refine call. |
| `rejection_reason` | Why a rejected row failed. |
| `created_at` | UTC timestamp. |

The row shape and the file names are a contract: downstream code can rely on them.

## `llm_calls.jsonl`

Every successful model response is appended as soon as it returns, so you can watch a run while it is
still going. Each row holds:

- `created_at`, `role`, `task`, `model`, `duration_seconds`
- `system` and `prompt` (the full messages sent) and `response` (the raw reply)
- `in_tokens`, `out_tokens`, and when the provider reports them, `reasoning_tokens` (hidden reasoning,
  counted inside `out_tokens`), `cached_tokens` (prompt-cache hits, counted inside `in_tokens`) and
  `cost` (the billed cost, in USD on OpenRouter). When the provider omits token counts they are
  estimated at four characters per token.
- `sampling` and `extra_body`: the decoding params actually sent.

:::{warning}
`llm_calls.jsonl` holds full prompts and replies, which can include your description, generated data
and anything sensitive in them. It can also grow large. Treat run directories as local data.
:::

## `cost_summary.json`

Totals for the last command (`total_calls`, `total_input_tokens`, `total_output_tokens`,
`total_reasoning_tokens`, `total_cached_tokens`, `total_cost`, `total_duration_seconds`, and the
wall-clock `elapsed_seconds`) and the same numbers
per role, task and model under `by_role_task_model`. Each command overwrites it; see
[Cost](../guides/cost.md) for the total of a run spread over several commands.
