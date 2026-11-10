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
section". An unknown key (a typo, or a knob that was renamed
or `batch_size` → `record_batch_size`) is an error, not silently ignored; only `models.<role>` accepts
extra keys, because those are decoding params passed through to the provider.

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
| `best_of_n` | `1` | Candidate child-lists drafted per node (in parallel). A critic call then refines them (when `critic` is on) for completeness and low duplication, so each expanded node costs `best_of_n + 1` calls (`1` with `critic: false`). `2` adds variety at 1.5× the cost of `1`. `children_per_node` is a request, not a cap: the critic may add children the node's description implies. |
| `critic` | `true` | Refine each node's proposals with a critic call. `false` uses the single proposal as is: 1 call per expanded node instead of 2, but measured on the e-commerce tree it kept 3 of the 21 verticals the factor description lists and repeated node names rose from 4% to 21%, because the critic is what adds the children a node's description implies and drops duplicates. Requires `best_of_n: 1`. |
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
| `count` | `null` | Exact number of strategies to request (integer 1–12). `null` keeps the model's own 2–5 choice. Only takes effect when strategies are actually built — an existing `strategies.json` is reused as-is. |

Strategies may also carry `never_combine` (pairs of taxonomy paths that must not appear in the same
sampled mix) and reference per-node `weight`s in `taxonomy.json` — both live in the artifacts, not
this config, and are meant to be reviewed and hand-edited there. Every path in `taxonomy_roots` and
`never_combine` is validated against the real tree at build time; invalid paths fail loudly.

A strategy whose roots limit a factor to some of its branches changes that factor's shares: the tree
weights say one thing and the dataset gets another. Right after strategies are built or loaded (so
`--stop-after strategies` shows it), `generate` draws 5,000 mixes the way the run will and prints
`strategies skew '<factor>': …` for every code-sampled factor whose
top-level shares end up more than 5 points from its tree weights, naming the strategies that restrict
it. In one benchmark a model-drafted "high stakes" strategy pushed one label from 25% to 40% of the
dataset; deleting it from `strategies.json` restored the target. If the skew is intended, ignore the
warning.

## `sampling`

| Key | Default | What it does |
|---|---|---|
| `tasks` | `{}` | Per-task decoding overrides: `tasks.<task> -> {param: value}`. |

Decoding params resolve in three layers, last wins: built-in defaults → `models.<role>` static →
`sampling.tasks.<task>`. Each task is handled by exactly one role, so naming the task is enough.
Valid task names are the `TaskType` values: `factor_discovery`, `node_expansion`, `taxonomy_critic`,
`level_plan`, `strategy`, `subject`, `situations`, `meta_prompt`, `complexify`, `generate`, `repair`,
`semantic_critic`, `refine`, `complexity_score`, `node_assign`. There is no temperature-spread schedule — run the CLI
twice for a spread. Full details in README → *Sampling*.

```yaml
sampling:
  tasks:
    generate: {temperature: 1.1, top_p: 0.95, min_p: 0.05}
    repair:   {temperature: 0.0}
```

## `generation`

Volume and behavior of the generation pass (`simula generate`).

Four words used below, in the order they appear in a run:

- **Mix.** One sampled node per factor: the ingredients of one record. Drawn by code.
- **Situation.** One sentence naming the concrete thing a record is about: one specific case of its
  subject node ("a duplicate line item on a monthly invoice"). Subject matter only, nothing about tone,
  length or who is writing. Listed per subject node before any writing, each used by exactly one record.
  Exists only when `subject` is set; kept in `situations.jsonl`.
- **Meta-prompt.** A short instruction (about 80 words) for one record that fuses the whole mix, and the
  situation when there is one, into a plan: what happens, who is involved, how it is written. One model
  call per meta-prompt (or per batch of them). Exists only when `meta_prompts` is true; kept in
  `meta_prompts.jsonl`, where it can be read and edited before records are written.
- **Record.** The finished row.

A situation says *what* a record is about and comes from one factor. A meta-prompt is the full plan for
the record and comes from all of them. They stack: with both on, the situation goes into the meta-prompt;
with `meta_prompts: false` it goes straight into the writer's spec and the writer makes its own one-line
plan in the same call that writes the record.

| Key | Default | What it does |
|---|---|---|
| `target_size` | `50` | Desired row count. Sets the attempt budget; caps `dataset.final.jsonl` only when `trim` is true. Rejections/dedupe can leave fewer rows. |
| `overgenerate_ratio` | `1.3` | `attempts = ceil(target_size × ratio)`. Buffer for rows the critic/schema rejects. `1.0` = no buffer. |
| `trim` | `true` | Coverage-trim accepted rows to `target_size`: rows that add taxonomy coverage first, then the earliest attempts. Set false to skip selection and retain all accepted rows after optional dedupe, in attempt order. |
| `meta_prompts` | `true` | Write a reviewable meta-prompt per attempt first (`meta_prompts.jsonl`, editable via `stop_after: meta_prompts`), then generate the record from it. `false` = the **combined path**: records are written straight from their sampled mixes, `record_batch_size` per call, with no meta-prompt step or artifact. |
| `meta_prompts_per_mix` | `1` | Meta-prompts one call writes for one sampled mix. Every one becomes its own attempt sharing that mix (attempts `k·K … k·K+K−1`), so `K>1` means fewer distinct mixes and fewer meta-prompt calls. Requires `meta_prompts: true`. |
| `meta_batch_size` | `1` | **Batch mode A:** mixes per meta-prompt call (each still gets its own meta-prompt(s) and keeps its own lineage). Requires `meta_prompts: true`. |
| `record_batch_size` | `1` | Records written per record call. With `meta_prompts: true` this is **batch mode B** (records from several meta-prompts per call; set both batch sizes for A+B); with `meta_prompts: false` it is **mode C**, the combined path. Each record is still validated, repaired and critiqued on its own and keeps its own lineage. Batching amortizes the instruction prompt, not hidden reasoning — see the note below. |
| `model_picked` | `{}` | Combined path only. `{factor: level}`: instead of code sampling these factors, the batch prompt offers the factor's nodes at that tree level (with the share code sampling would give each) and the model picks one per record, reporting it in `"picked"`. The pick becomes the row's lineage entry for that factor, marked `"picked_by": "model"`; code-picked factors are sampled exactly as before. Strategy roots and `never_combine` do not apply to model-picked factors. Factor names are checked against the taxonomy before any strategy or record call. A pick that matches no option, or a record missing from the batch, keeps a lineage entry at the factor root (node `unpicked` or the raw pick). Experimental: in the pilot the model skewed the distribution and mislabeled its picks (`plan.md` §0). |
| `code_level` | `{}` | `{factor: level}`: code samples that factor only down to this tree level, and the writer chooses the specifics beneath the node it is given. Shares at that level stay exact (the leaf is still drawn, then replaced by its ancestor, so `never_combine` and other factors' draws are unchanged). Use it for a factor whose deep leaves often cannot go with the other factors' values (for example attribute leaves that only suit some products): in two pilots, cutting two such factors at level 2 raised realism and coherence with no loss of diversity, while cutting at level 1 drifted toward typical records. Cut as deep as is safe. Works on both record paths; a factor cannot be in both `code_level` and `model_picked`. |
| `subject` | `null` | The factor that says what a record is about (its topic, issue, item or document type): a factor name, or `auto` to let the model pick. When set, each node of that factor gets a list of **situations**, one per record that sampled it, and every batch (meta-prompt batches and record batches) holds one node. Records that share a node are then written about different concrete things; without it, each call invents the node's most obvious instance on its own and records converge. Situations are listed 25 per call, one call after another for a node (each call is shown the node's last 120 so it does not repeat them), nodes in parallel; each is used by exactly one record, as a `situation` line in its spec, and the critic sees it. An attempt whose listing call failed twice is written from its mix alone. Cost: about one extra `bulk` call per 25 records (4% of a run in our benchmark). Use it whenever a node is shared by more than a handful of records, which is nearly always. Measured on 10,000 support tickets: no near-duplicate records, and tickets with a situation varied more within a node than those without (0.58 against 0.51 mean pairwise distance). `auto` makes one `strategic` call once the tree exists and stores the answer in `taxonomy.json` as `"subject"` (a factor name, or `null` when no factor is about content); edit it there like the rest of the tree. The subject must be code-sampled, so it cannot be in `model_picked`. |
| `complexity_ratio` | `0.3` | Fraction of attempts (sampled per point) made harder so the set mixes easy and hard items: an extra "complexify" call that rewrites the meta-prompt, or on the combined path a "complexify" call that takes the written record and returns a harder version of it (`complexify_record_prompt`), before validation and critique. Complexify is always its own call, so the model has something concrete to make harder. `0.0` disables it. |
| `critic` | `true` | Critique every structurally valid record. `false` accepts every schema-valid record with no critic or refine calls (repair still runs). |
| `max_refine_attempts` | `2` | Refines per record after critic rejections; every refined record is critiqued again, so the last word is always a verdict. `0` = critique once, never refine. A critic that returns a corrected record (`"revised"`, as the built-in JSON critic does) has a schema-valid fix accepted directly, with no refine. |
| `concurrency` | `4` | In-flight model calls (a semaphore). On the combined path a batch counts as `record_batch_size` calls (its records are critiqued in parallel), so `concurrency // record_batch_size` batches run at once. Lower it if the provider rate-limits; raise it cautiously to go faster. |
| `checkpoint_every` | `50` | Write `run_state.json` every N completed attempts. Pure resume/durability cadence. |
| `stop_after` | `null` | Halt `generate`/`run` once this stage's artifact is written: `taxonomy`, `strategies`, or `meta_prompts`. Edit the artifact, then rerun — existing artifacts are reused, so the run continues from where it stopped. The CLI `--stop-after` flag overrides this per invocation (`--stop-after none` disables a config-set stop). `meta_prompts` is rejected when `generation.meta_prompts` is false: that path writes no meta-prompt artifact. |

**Resume note:** `target_size`, `overgenerate_ratio`, `trim`, `concurrency`, and `checkpoint_every` are
excluded from the resume fingerprint, so you can grow or re-pace a run and still `--resume`. Changing
`meta_prompts`, `meta_prompts_per_mix`, `meta_batch_size`, `record_batch_size`, `model_picked`, `code_level`, `subject`, `complexity_ratio`, `critic`, `max_refine_attempts` (or the
description/schema/seed/models/prompts/sampling/taxonomy) invalidates resume and requires `--no-resume`.

**Cost note (measured on DeepSeek v4 flash, reasoning effort low):** hidden reasoning is most of the
billed output of every call, and it scales with the number of records written, not the number of calls:
a 10-record batch reasons about as much per record as ten single calls. So `record_batch_size` mainly saves the
repeated instruction prompt (input tokens) and, with `meta_prompts: false`, the meta-prompt calls. Check
`reasoning_tokens` and `cost` in `llm_calls.jsonl` when tuning. A batch's reasoning plus output must fit
`max_tokens`: give the `bulk` role room (e.g. `65536` for 10 records).

## `evaluation`

What `simula evaluate` / `run` compute. Reads `dataset.final.jsonl`, writes
`dataset.evaluated.jsonl` + `eval_report.json` — it never rewrites `final`.

| Key | Default | What it does | Extra model calls? |
|---|---|---|---|
| `dedupe` | `true` | 13-word n-gram dedupe of accepted rows at 0.8 Jaccard similarity. Uses a temporary disk index and exact verification. JSON records are serialized with sorted keys; all keys and values contribute. | No |
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

The report gives `global_diversity` (mean pairwise cosine distance), `local_diversity` (mean distance to
the `k_local` nearest neighbours) and `vendi` (the Vendi score: the effective number of distinct records,
1 = all identical, n = all unrelated). Vendi and local diversity depend on sample size and embedding
model: compare only runs scored with the same `sample_cap` and `embedding_model`; mean pairwise distance
is the least stable of the three across embedding models.

| `sample_cap` | `1000` | Max rows embedded; beyond this a random subset is scored (logged when it happens). |
| `text_field` | `null` | Dotted path into the record to embed (e.g. `query`, `extraction.intent`). `null` embeds the whole record JSON. Plain nested-key access, not full JSONPath. |
