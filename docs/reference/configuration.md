# Configuration

Every run is driven by one YAML file. This page lists every field: what it does, its default, and
when it matters.

- The defaults and validation rules live in the Pydantic models in `simula/data_models.py`. If this
  page and the models disagree, the models win.
- `examples/template.yaml` is a copy-me skeleton with every key at its default. It is reproduced at
  the [bottom of this page](#full-template).

Only two things are required: a non-empty `description`, and a `model` id for each of the three roles
(`strategic`, `bulk`, `critic`). Everything else has a default, so omit a key to accept it. A blank
section header (`evaluation:` with nothing under it) means "use the defaults", not "wipe the section".

An unknown key, such as a typo or a renamed knob, is an error rather than being silently ignored. Only
`models.<role>` accepts extra keys, because those are decoding params passed through to the provider.
`simula validate CONFIG.yaml` checks all of this without making a model call.

## `project`

| Key | Default | What it does |
|---|---|---|
| `name` | `"pilot"` | Label for the run, shown in the run header. |
| `output_dir` | `"runs/pilot"` | Directory for every artifact: datasets, `llm_calls.jsonl`, `cost_summary.json`, `run_state.json`. Relative paths resolve from where you run the CLI. |
| `seed` | `42` | Master seed. Makes taxonomy sampling and per-attempt generation reproducible. |

## `description` (required)

Free text describing the dataset to generate. It steers every prompt: factor discovery, strategies,
meta-prompts, generation and critique. It is the single highest-leverage field; see
[Writing a config](../guides/writing-a-config.md) for how to write one.

## `schema`

A JSON Schema that every generated record must validate against before it can be accepted. A record
that fails validation is repaired once and rejected if it still fails. The supported subset:

- types: `object`, `string`, `number`, `integer`, `boolean`, `array`
- `enum`, `required`, nested `properties`, and array `items`

Every node must declare one `type` from that list, and every array must declare `items`. An `object`
with no `properties` accepts any object, which is useful when the record's shape legitimately varies
from row to row (the e-commerce example uses this for its `extraction` field).

Set `schema: null`, or omit it, for **free-text mode**: each `record` is a plain string, JSON repair
is skipped, and the critic still returns a JSON verdict.

## `provider`

One OpenAI-compatible endpoint shared by all three roles.

| Key | Default | What it does |
|---|---|---|
| `base_url` | `"https://openrouter.ai/api/v1"` | Chat-completions endpoint for every role. `"fake"` runs offline. |
| `api_key_env` | `"OPENROUTER_API_KEY"` | Name of the variable holding the API key, read **only** from a `.env` file in the directory you run `simula` from. A shell-exported variable is deliberately ignored. |
| `timeout_seconds` | `180` | Per-request timeout. A hung connection fails fast and is checkpointed as a rejected row instead of stalling a worker. |

A real run with no key it can resolve prints a warning at load time and fails on its first model call.

## `models`

Three roles, each a `model` id plus optional decoding params. See
[Model roles](../concepts/model-roles.md) for which role does what.

- `strategic`: factor discovery, taxonomy expansion, strategies, the subject pick.
- `bulk`: situations, meta-prompts, complexify, records, repair, refinement.
- `critic`: critique of every record, and the optional complexity and reassignment scoring.

| Key | Default | What it does |
|---|---|---|
| `model` | required | Model id, for example `"google/gemini-3-flash-preview"`, or `"fake"` to run that role offline with no key. |
| `temperature`, `top_p`, `max_tokens`, … | see below | Decoding params for every call the role makes. Params the OpenAI API does not know (`min_p`, `top_k`, …) are passed through in `extra_body`. |
| `extra_body` | `null` | Provider pass-through. Set `{reasoning: {effort: low, exclude: true}}` here for reasoning models; there is **no** automatic model-id detection. |

Decoding defaults when unset: `temperature: 0.7`, `max_tokens: 32768`. That 32K default is
generous: roles without an explicit `max_tokens` can emit large, pricier completions, so cap it per
role or per task if cost matters. See [Providers and models](../guides/providers-and-models.md).

## `prompts`

| Key | Default | What it does |
|---|---|---|
| `module` | unset | Path, relative to the YAML file, to a Python module that overrides any subset of the built-in prompt functions and `SYSTEM_JSON`/`SYSTEM_TEXT`. Missing functions fall back to the built-ins. `validate` imports it and rejects missing files, import errors and mismatched signatures before any model call. |

See [Custom prompts](../guides/custom-prompts.md).

## `taxonomy`

Knobs for the breadth-first taxonomy build. See [Taxonomy](../concepts/taxonomy.md).

| Key | Default | What it does |
|---|---|---|
| `depth` | `2` | Levels expanded below each factor root. `0` means factors only, no children. More depth gives more structure and more model calls. |
| `factors` | `null` | `null` lets the model discover 3–6 factors. Or give a list of `{name, description}` to fix them yourself. |
| `best_of_n` | `1` | Candidate child lists drafted per node, in parallel. With `critic` on, a critic call then refines them, so each expanded node costs `best_of_n + 1` calls. `2` adds variety at 1.5× the cost of `1`. |
| `critic` | `true` | Refine each node's proposals with a critic call, which adds children the node's description implies and drops duplicates. `false` uses the single proposal as is: 1 call per node instead of 2, but on the e-commerce tree it kept 3 of the 21 verticals the factor description listed, and repeated node names rose from 4% to 21%. Requires `best_of_n: 1`. |
| `children_per_node` | `4` | Number of children requested per node. A request, not a cap: the critic may add children. |
| `review_mode` | `"auto_accept"` | What happens once the taxonomy is built (below). |
| `log_style` | `"light"` | How the build is shown while it runs (below). |

`review_mode` values:

- `auto_accept`: write `taxonomy.json` and carry on. The default.
- `write_then_edit`: write `taxonomy.json` and halt, so you can hand-edit it before generating.
- `interactive_confirm`: print the taxonomy and ask yes or no in the terminal.

`log_style` values (`--quiet` hides either):

- `light`: one line per expanded node (`+ <path> → k children`) under a header per factor. Compact;
  best for large or deep trees. The default.
- `tree`: a tree that grows as nodes are generated. On a terminal it redraws live; elsewhere the
  finished tree prints once. Best for judging the shape.

## `strategy`

| Key | Default | What it does |
|---|---|---|
| `guidance` | `null` | Free text woven into the strategy prompt to steer which taxonomy roots combine and how often. A nudge interpreted by the `strategic` model, not a hard constraint; for guarantees, edit `strategies.json`. |
| `count` | `null` | Exact number of strategies to request (1–12). `null` lets the model choose 2–5. Only applies when strategies are built; an existing `strategies.json` is reused as is. |

Strategies can also carry `never_combine` rules, and the tree carries per-node `weight`s. Both live in
the artifacts, not in this config, and are meant to be reviewed and edited there. Every path in a
strategy is checked against the real tree when strategies are built; an invalid path is an error.
See [Strategies and sampling](../concepts/sampling.md), including the warning `generate` prints
when a strategy pulls a factor away from its tree weights.

## `sampling`

| Key | Default | What it does |
|---|---|---|
| `tasks` | `{}` | Per-task decoding overrides: `tasks.<task>: {param: value}`. |

Decoding params resolve in three layers, last one wins: built-in defaults, then `models.<role>`, then
`sampling.tasks.<task>`. Each task is handled by exactly one role, so naming the task is enough. The
task names are `factor_discovery`, `node_expansion`, `taxonomy_critic`, `level_plan`, `strategy`,
`subject`, `situations`, `meta_prompt`, `complexify`, `generate`, `repair`, `semantic_critic`,
`refine`, `complexity_score` and `node_assign`. There is no temperature schedule; run the CLI twice
for a spread.

```yaml
sampling:
  tasks:
    generate: {temperature: 1.1, top_p: 0.95, min_p: 0.05}
    repair:   {temperature: 0.0}
```

## `generation`

Volume and behaviour of the generation pass. The words used below (mix, subject, situation,
meta-prompt, combined path) are explained in [Situations and meta-prompts](../concepts/situations-and-meta-prompts.md).

| Key | Default | What it does |
|---|---|---|
| `target_size` | `50` | Rows wanted. Sets the attempt budget, and caps `dataset.final.jsonl` when `trim` is on. Rejections and dedupe can leave fewer rows. |
| `overgenerate_ratio` | `1.3` | `attempts = ceil(target_size × ratio)`: a buffer for rows the critic or schema rejects. `1.0` means no buffer. |
| `trim` | `true` | Trim accepted rows to `target_size`, keeping rows that add taxonomy coverage first, then the earliest attempts. `false` keeps every accepted row after optional dedupe, in attempt order. |
| `meta_prompts` | `true` | Write a reviewable meta-prompt for every attempt first (`meta_prompts.jsonl`), then the record from it. `false` is the **combined path**: records are written straight from their mixes, `record_batch_size` per call, with no meta-prompt step. |
| `meta_prompts_per_mix` | `1` | Meta-prompts one call writes for one sampled mix. Every one becomes its own attempt sharing that mix, so `K > 1` means fewer distinct mixes and fewer meta-prompt calls. Requires `meta_prompts: true`. |
| `meta_batch_size` | `1` | Mixes per meta-prompt call (batch mode A). Each mix still gets its own meta-prompt and keeps its own lineage. Keep it at 10 or under. Requires `meta_prompts: true`. |
| `record_batch_size` | `1` | Records written per record call: batch mode B with meta-prompts, mode C (the combined path) without. Each record is still validated, repaired and critiqued on its own. See [Batching](../guides/batching.md). |
| `model_picked` | `{}` | Combined path only. `{factor: level}`: instead of code sampling the factor, the prompt offers its nodes at that level (with the share code sampling would give each) and the model picks one per record. The pick becomes the row's lineage, marked `"picked_by": "model"`. Strategy roots and `never_combine` do not apply to these factors. Shares are no longer exact. |
| `code_depth` | `{}` | `{factor: level}`: code samples the factor only down to this level, and the writer chooses the specifics beneath. Shares at that level stay exact. Use it for a factor whose deep leaves often clash with other factors' values; in two pilots, cutting such factors at level 2 raised realism and coherence with no loss of diversity, while level 1 drifted toward typical records. A factor cannot be in both `code_depth` and `model_picked`. |
| `subject` | `null` | The factor that says what a record is about: a factor name, or `auto` to let the `strategic` model pick once the tree is built (stored in `taxonomy.json` as `"subject"`). Each node of the subject then gets a list of situations, one per record, and every batch holds one node. Costs about one extra `bulk` call per 25 records. The subject must be code-sampled, so it cannot be in `model_picked`. |
| `complexity_ratio` | `0.3` | Fraction of attempts made harder by a separate `complexify` call: on the meta-prompt, or on the combined path on the written record. `0.0` disables it. |
| `critic` | `true` | Critique every schema-valid record. `false` accepts every schema-valid record with no critic or refine calls (repair still runs). |
| `max_refine_attempts` | `2` | Refines per record after critic rejections; every refined record is critiqued again. `0` critiques once and never refines. A critic reply that carries a schema-valid corrected record is accepted directly, with no refine. |
| `concurrency` | `4` | In-flight model calls. A record batch counts as `record_batch_size` calls, so `concurrency // record_batch_size` batches run at once. Lower it if the provider rate-limits. |
| `checkpoint_every` | `50` | Write `run_state.json` every N completed attempts. |
| `stop_after` | `null` | Halt `generate`/`run` once this stage's artifact is written: `taxonomy`, `strategies` or `meta_prompts`. Edit the artifact and rerun to continue. The CLI `--stop-after` overrides it. `meta_prompts` needs `meta_prompts: true`. |

**Resume.** `target_size`, `overgenerate_ratio`, `trim`, `concurrency` and `checkpoint_every` can
change between runs and `--resume` still works, so you can grow or re-pace a run. Changing any other
generation knob, or the description, schema, seed, models, prompts, sampling, taxonomy or strategies,
makes resume refuse; pass `--no-resume` to start over. See [How a run works](../concepts/pipeline.md).

## `evaluation`

What `evaluate` and `run` compute. Evaluation reads `dataset.final.jsonl` and writes
`dataset.evaluated.jsonl` and `eval_report.json`; it never rewrites `dataset.final.jsonl`. See
[Evaluation](../concepts/evaluation.md).

| Key | Default | What it does | Extra model calls? |
|---|---|---|---|
| `dedupe` | `true` | Remove near-duplicate rows: 13-word sequences at 0.8 Jaccard similarity. | No |
| `coverage` | `true` | Report taxonomy coverage. | Only in `reassign`/`both` |
| `coverage_mode` | `"lineage"` | `lineage` uses each row's `taxonomy_mix`. `reassign` asks the `critic` model to assign each row to a node. `both` does both. | `reassign`/`both` |
| `complexity` | `false` | Elo complexity scoring (below). | Yes |
| `complexity_batch_size` | `5` | Records per critic batch when scoring complexity. | — |
| `complexity_samples_per_item` | `2` | Shuffled batches each record appears in; more is more stable and costs more calls. | — |
| `decontaminate_against` | `[]` | Paths to reference or test JSONL files; rows overlapping them are dropped. | No |
| `diversity` | see below | Embedding-based diversity metrics. | No (local embeddings) |

**Complexity scoring.** Each record appears in `complexity_samples_per_item` shuffled batches of
`complexity_batch_size`; the critic scores each record 1–10 within its batch, and pairwise Elo turns
those scores into one ordering.

### `evaluation.diversity`

Off by default. Needs the optional extra: `pip install "simula-gen[diversity]"` (numpy,
scikit-learn, sentence-transformers). Embeddings are cached in the run directory and reused.

| Key | Default | What it does |
|---|---|---|
| `enabled` | `false` | Turn diversity scoring on. |
| `embedding_model` | `"sentence-transformers/all-MiniLM-L6-v2"` | Sentence-embedding model. |
| `k_local` | `10` | Nearest neighbours used for local diversity. |
| `sample_cap` | `1000` | Max rows embedded; beyond this a random subset is scored. |
| `text_field` | `null` | Dotted path into the record to embed, such as `query` or `extraction.intent`. `null` embeds the whole record. Plain nested-key access, not JSONPath. |

## Full template

`examples/template.yaml`, every key at its default:

```{literalinclude} ../../examples/template.yaml
:language: yaml
```
