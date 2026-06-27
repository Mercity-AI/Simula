# Config Reference

Every `simula` run is driven by one YAML file. This document explains every field: what it does,
its default, and when it matters. It is the human-facing companion to two other sources:

- `simula/data_models.py` is the **single source of truth** — Pydantic defines defaults, bounds, and
  validation. If this doc and the models ever disagree, the models win.
- `examples/template.yaml` is a **copy-me skeleton** with every key set to its default and a one-line
  comment each. Copy it, edit, and point the CLI at it.

Only two things are required: a non-empty `description`, and a `model` id for each of the three roles
(`strategic`, `bulk`, `critic`). Everything else has a default — omit a key to accept it. A blank
section header (e.g. `evaluation:` with nothing under it) is treated as "use defaults", not "wipe the
section".

Read the related prose in `README.md` for prompt overrides, strategy guidance, the sampling layering,
and the schema subset; this file does not repeat those at length.

---

## `project`

| Key | Default | What it does |
|---|---|---|
| `name` | `"pilot"` | Human label for the run. Cosmetic. |
| `output_dir` | `"runs/pilot"` | Directory for **all** artifacts: datasets, `llm_calls.jsonl`, `cost_summary.json`, `run_state.json`. `runs/` is gitignored. |
| `seed` | `42` | Master seed. Makes taxonomy sampling and per-attempt generation reproducible. Each attempt uses `seed + attempt_index`. |

## `description` (required)

Free text describing the dataset to generate. This steers **every** prompt — taxonomy discovery,
strategies, meta-prompts, generation, critique. The single highest-leverage field. Must be non-empty.

## `schema`

A JSON Schema **subset**: `object`, `string`, `number`, `integer`, `boolean`, `array`, `enum`,
`required`, nested `properties`, and array `items`. Every generated record must validate against it
before it can be accepted.

Set to `null` (or omit) for **free-text mode**: `record` becomes a string, JSON repair is skipped,
and the critic still returns a JSON verdict.

## `provider`

One OpenAI-compatible endpoint shared by all three roles. The API key is read **only** from a `.env`
file at the project root (a shell-exported variable is deliberately ignored).

| Key | Default | What it does |
|---|---|---|
| `base_url` | `"https://openrouter.ai/api/v1"` | Chat-completions endpoint for every role. |
| `api_key_env` | `"OPENROUTER_API_KEY"` | Variable name looked up in the project-root `.env`. |
| `timeout_seconds` | `180` | Per-request timeout. A hung connection fails fast and checkpoints as a rejected row instead of stalling a worker. |

## `models`

Three call roles, each a `model` id plus optional decoding params. Use `"fake"` for a role to run it
fully offline (no key needed) — used by the smoke-test examples and tests.

- `strategic` — factor discovery, taxonomy expansion, strategy generation.
- `bulk` — meta-prompts, complexification, record/text generation, repair, refinement.
- `critic` — semantic critique and optional complexity scoring.

| Key | Default | What it does |
|---|---|---|
| `model` | `""` (**required**) | Model id, e.g. `"google/gemini-3-flash-preview"`, or `"fake"`. |
| `temperature`, `top_p`, `max_tokens`, … | see below | Role-static decoding params. Unknown params (`min_p`, `top_k`, …) pass through to `extra_body`. |
| `extra_body` | `null` | Provider pass-through. Set `{reasoning: {effort: low, exclude: true}}` here for reasoning models — there is **no** automatic model-id detection. |

Decoding defaults when unset: `temperature: 0.7`, `max_tokens: 32768`. That 32K default is generous —
roles without an explicit `max_tokens` can emit large (pricier) completions, so cap it per role or
per task if cost matters.

## `prompts` (optional)

| Key | Default | What it does |
|---|---|---|
| `module` | unset | Path (relative to the YAML) to a Python module overriding any subset of the prompt builders and `SYSTEM_JSON`/`SYSTEM_TEXT`. Missing functions fall back to built-ins. `validate` imports it and rejects bad files/signatures before any model call. |

See README → *Prompt Overrides* for the function-signature rules.

## `taxonomy`

Knobs for the breadth-first taxonomy build (`simula taxonomy`).

| Key | Default | What it does |
|---|---|---|
| `depth` | `2` | Levels expanded below each factor root. `0` = factors only, no children. More depth = more structure, more model calls. |
| `factors` | `null` | `null` lets the model discover 3–6 factors. Or provide a list of `{name, description}` to fix them yourself. |
| `best_of_n` | `2` | Candidate child-lists drafted per node before a critic refine picks/merges. Higher = more diverse children, more cost. |
| `children_per_node` | `4` | Target number of children requested when expanding each node (passed to the expand prompt). |
| `review_mode` | `"auto_accept"` | Gate after the taxonomy is built — see below. |
| `log_style` | `"light"` | How the build is surfaced live — see below. |

`review_mode` values:

- `auto_accept` — write `taxonomy.json` and proceed. The default.
- `write_then_edit` — write `taxonomy.json` and **halt**, telling you to hand-edit it before running
  `generate`. Use when you want to curate the space manually.
- `interactive_confirm` — print the taxonomy and prompt in the terminal for yes/no.

`log_style` values (both surface nodes as they are generated; `--quiet` suppresses either):

- `light` — flat, append-only breadcrumb lines: a `▸ factor` header per factor, then
  `+ <path> → k children` per expanded node. Compact and chronological; best when you just want a
  pulse, especially for large/deep taxonomies where the tree gets unwieldy. The default.
- `tree` — a tree that grows as nodes are generated. On a TTY it redraws live (each factor shows an
  `(expanding…)` marker until its subtree completes); off a TTY the finished tree prints once. Best
  for reviewing the *shape*. Note a deep taxonomy can be a tall tree.

## `strategy`

| Key | Default | What it does |
|---|---|---|
| `guidance` | `null` | Optional free text woven into the strategy prompt to steer which taxonomy roots combine and how often. A nudge, not a hard constraint — for guarantees, edit `strategies.json` directly. See README → *Strategy Guidance*. |

## `sampling`

| Key | Default | What it does |
|---|---|---|
| `tasks` | `{}` | Per-task decoding overrides: `tasks.<task> -> {param: value}`. |

Decoding params resolve in three layers, last wins: built-in defaults → `models.<role>` static →
`sampling.tasks.<task>`. Each task is handled by exactly one role, so naming the task is enough.
Valid task names are the `TaskType` values: `factor_discovery`, `node_expansion`, `taxonomy_critic`,
`level_plan`, `strategy`, `meta_prompt`, `complexify`, `generate`, `repair`, `semantic_critic`,
`refine`, `complexity_score`, `node_assign`. There is no temperature-spread schedule — run the CLI
twice for a spread. Full details in README → *Sampling*.

```yaml
sampling:
  tasks:
    generate: {temperature: 1.1, top_p: 0.95, min_p: 0.05}
    repair:   {temperature: 0.0}
```

## `generation`

Volume and behavior of the generation pass (`simula generate`).

| Key | Default | What it does |
|---|---|---|
| `target_size` | `50` | Number of rows you want in `dataset.final.jsonl`. Accepted rows are coverage-trimmed back to this. |
| `overgenerate_ratio` | `1.3` | `attempts = ceil(target_size × ratio)`. Buffer for rows the critic/schema rejects. `1.0` = no buffer. |
| `scenarios_per_mix` | `3` | How many candidate meta-prompts the model drafts per sampled taxonomy mix; each attempt picks one at random. Higher = more variety per mix. |
| `complexity_ratio` | `0.3` | Fraction of attempts (sampled per point) routed through the extra "complexify" step so the set mixes easy and hard items. `0.0` disables it. |
| `max_refine_attempts` | `2` | Critic→refine retries per record before it is accepted or rejected. `0` = critique once, never refine. |
| `concurrency` | `4` | In-flight model calls (a semaphore). Lower it if the provider rate-limits; raise it cautiously to go faster. |
| `checkpoint_every` | `50` | Write `run_state.json` every N completed attempts. Pure resume/durability cadence. |

**Resume note:** `target_size`, `overgenerate_ratio`, `concurrency`, and `checkpoint_every` are
excluded from the resume fingerprint, so you can grow or re-pace a run and still `--resume`. Changing
`scenarios_per_mix`, `complexity_ratio`, `max_refine_attempts` (or the description/schema/seed/models/
prompts/sampling/taxonomy) invalidates resume and requires `--no-resume`.

## `evaluation`

What `simula evaluate` / `run` compute. Reads `dataset.final.jsonl`, writes
`dataset.evaluated.jsonl` + `eval_report.json` — it never rewrites `final`.

| Key | Default | What it does | Extra model calls? |
|---|---|---|---|
| `dedupe` | `true` | n-gram dedupe of accepted rows. | No |
| `coverage` | `true` | Report taxonomy coverage. | Only in `reassign`/`both` |
| `coverage_mode` | `"lineage"` | `lineage` uses each row's `taxonomy_mix` (free). `reassign` asks the model to re-assign each row to a node. `both` does both. | `reassign`/`both` |
| `complexity` | `false` | Elo complexity scoring (see below). | **Yes** |
| `complexity_batch_size` | `5` | Records per critic batch when scoring complexity. | — |
| `complexity_samples_per_item` | `2` | How many shuffled batches each record appears in; more = more stable Elo, more calls. | — |
| `decontaminate_against` | `[]` | Paths to reference/test JSONL files; rows overlapping those are dropped. | No |
| `diversity` | see below | Optional embedding-based diversity metric. | No (local embeddings) |

**Complexity scoring** (only when `complexity: true`): each record appears in
`complexity_samples_per_item` shuffled batches of `complexity_batch_size`; the critic scores each
1–10 within a batch, and pairwise Elo aggregates those into a global ordering.

### `evaluation.diversity`

Embedding-based diversity. Off by default; needs the extra: `pip install 'simula[diversity]'`
(numpy / scikit-learn / sentence-transformers). Embeddings are cached under the run dir and reused.

| Key | Default | What it does |
|---|---|---|
| `enabled` | `false` | Turn diversity scoring on. |
| `embedding_model` | `"sentence-transformers/all-MiniLM-L6-v2"` | Sentence-embedding model. |
| `k_local` | `10` | Nearest neighbours used for the local-diversity metric. |
| `sample_cap` | `1000` | Max rows embedded; beyond this a random subset is scored (logged when it happens). |
| `text_field` | `null` | Dotted path into the record to embed (e.g. `query`, `extraction.intent`). `null` embeds the whole record JSON. Plain nested-key access, not full JSONPath. |
