---
name: generate-dataset
description: Use when the user wants to generate a synthetic dataset with this repo (simula) — e.g. "make me an extraction dataset", "generate ~5k QA pairs", "build training data for X". Walks the user through it asking only what dataset and how much, deciding the rest from baked-in defaults and surfacing only the few high-leverage choices.
---

# Generate a dataset with simula

You are driving `simula` for a user who wants a dataset, not a config tutorial. This skill
encodes the judgment so you can do almost all of it yourself. **Read `AGENTS.md` once** for the
architecture and hard constraints; this file is the *process and decision* layer on top of it.

## Prime directive

Ask the user only **two** things up front:

1. **What dataset** — a plain-English description of what each row should look like and what it's
   for (the downstream use). Push for the *use* — "extraction for a model that must emit a fixed
   schema" implies different choices than "examples for a demo".
2. **How much** — target row count.

Everything else you **decide yourself from the defaults below**, and you pull the user back in
**only at the explicit decision gates** in the next section. Do not walk them through config keys,
taxonomy depth, concurrency, or model knobs — those are yours unless a gate says otherwise. State
the handful of consequential choices you made in one short line ("depth-2 taxonomy, omit-absent
fields, fake-model smoke first") so they can veto, but don't ask permission for each.

## When to bring the user in (decision gates)

Only these. If a choice isn't here, pick the default and move on.

| Gate | Why it must be asked | How to ask |
|---|---|---|
| **Spending real money** | `AGENTS.md`: never make real model calls unless explicitly asked. Real runs cost money + time. | Before the first real run: "Smoke test passed. A pilot of N rows on `<model>` will make ~M calls and cost roughly $X — go ahead?" |
| **Provider / API key missing** | Real runs read the key only from a gitignored root `.env`. | If `.env` lacks the key named by `provider.api_key_env`, ask them to add it (`echo 'OPENROUTER_API_KEY=...' > .env`). Don't proceed to real calls without it. |
| **Use-dependent representation choice** | A choice about how rows encode information whose right answer depends on the user's downstream model/use, so it can't be safely defaulted (e.g. absent-value policy, label granularity, hard/negative cases). See "Representation decisions". | One plain question with a recommendation. |
| **Fixed vs per-row schema** | Determines whether you write a strict `schema` or an open envelope + prompt module. | Only ask if the description is ambiguous; otherwise infer and state it. |
| **Final scale-up** | A 10k run is real money. | Confirm once before scaling from pilot to full target. |

Everything else — taxonomy depth, factors/axes, `best_of_n`, `children_per_node`, the generation path
and batch size, `code_level`, `overgenerate_ratio`, `complexity_ratio`, `max_refine_attempts`,
`concurrency`, model-role assignment, whether to write a prompt module — **you decide.** Surface, don't
ask. One exception: turning the record critic off (`generation.critic: false`) trades quality for cost,
so only do it when the user asks for the cheapest possible run and accepts the trade (see "Pick the
generation path").

## Workflow

Use the CLI from the repo root. Commands: `validate`, `taxonomy`, `generate`, `evaluate`, `run`.
`examples/template.yaml` is the copy-me skeleton; `CONFIG.md` is the field reference; don't
re-derive defaults — copy the template and change only what the decisions below dictate.

1. **Classify + draft.** From the description, pick the archetype (table below), infer the schema
   shape, and choose the variation axes. Write `examples/<name>.yaml` from `template.yaml`. If the
   task needs atomic fields / an absent-field policy / a varying schema, also write
   `examples/<name>_prompts.py` (model after `examples/job_extraction_prompts.py` or
   `examples/ecommerce_search_extraction_prompts.py`).
2. **Validate.** `python -m simula.cli validate examples/<name>.yaml` — catches config/schema/
   prompt-module errors with zero model calls. Always do this before anything real. Unknown or
   misspelled config keys are errors, not silently ignored, so a typo shows up here.
3. **Smoke test, free.** Set every model role to `"fake"` and run
   `python -m simula.cli run examples/<name>.yaml`. This is offline and deterministic — it proves
   the pipeline, schema, lineage, and prompt module work end to end before spending a cent. Inspect
   the artifacts in `runs/<name>/` (especially row shape and `taxonomy_mix`).
4. **Pilot, real (gate: spend + key).** Switch roles to real models, set `generation.target_size`
   to ~20–50, and run `taxonomy` then `generate` as two steps so you (and optionally the user) can
   eyeball `taxonomy.json` **and `strategies.json`** before paying for bulk generation — see "The
   strategy phase" below for what to actually look for in them. Inspect `dataset.final.jsonl` and
   `llm_calls.jsonl` for quality: atomic fields? faithful? right axes covered? Read the accept rate,
   and **check lineage**: read a dozen rows next to their `taxonomy_mix` and confirm each record is
   really about what was sampled for it (see "Check lineage on the pilot").
5. **Scale (gate: confirm).** Raise `target_size` to the real number and run. Resume is on by
   default. Watch the run: if no model call lands for a few minutes, a request has hung — stop it and
   rerun, resume only redoes the missing attempts. Then `evaluate` for dedupe/coverage/diversity.
   Report cost from the `cost` logged per call in `llm_calls.jsonl` (the provider-billed amount, when
   the provider reports one); `cost_summary.json` only covers the last command you ran.

## Pick the generation path

There are two ways records get written. Choose once, before the pilot; switching later means
`--no-resume`.

| Path | Config | Use it when |
|---|---|---|
| **Combined** (the default recommendation) | `meta_prompts: false`, `record_batch_size: 5` | You want the cheapest good data. One call writes 5 records straight from their sampled mixes, each with a one-line plan; every record is still validated and critiqued on its own. In our pilot it cost about half as much as the scenario path on the same model, and its records were more faithful. |
| **Scenario** | `meta_prompts: true` (the built-in default) | The user wants to read or hand-edit the per-record scenarios (`meta_prompts.jsonl`, `--stop-after meta_prompts`) before records are written. Costs an extra call per record; `meta_batch_size` and `record_batch_size` can batch it. |

- **Batch size follows record length.** `5` for short records (queries, QA pairs, tickets). `1`–`2` for
  long free text, because records written together come out shorter. Past ~5 the records drift from
  their requested shape (measured: fit to the sampled shape 80% → 73% → 69% at 1 / 5 / 10 per call).
- **Give `bulk` room.** A batch reasons before it answers; set `models.bulk.max_tokens` high (the
  e-commerce example uses 65536) or replies get cut off and the whole batch is lost.
- **Keep the critic on.** It roughly halves invented content. Off (`generation.critic: false`) is the
  cheapest setting, but in our pilot about one row in three then had an unsupported or missing fact.
- **Routing matters more than any knob.** On OpenRouter, a `:nitro` model id billed about twice list
  price and reasoned about twice as much per call as the same model on default routing. Use the plain
  model id for big runs unless the user needs speed; default routing is slower and occasionally hangs.
- **Complexify is always its own call** (`complexity_ratio` of records): it rewrites the scenario, or on
  the combined path takes the written record and returns a harder one. Budget `complexity_ratio` extra
  calls per record, or set it to `0` when "harder" has no meaning for the dataset.

## Archetype → natural variation axes

The taxonomy *is* your coverage. Seed `taxonomy.factors` with the axes that actually matter for the
domain (or let discovery run and review them). The high-leverage move is choosing the right axes —
this is where domain reasoning earns its keep.

| Archetype | Axes that usually matter most |
|---|---|
| **Extraction (text → JSON)** | document **length** (short blurb ↔ long doc), register/style, **density of extractable facts**, **how many requested fields are absent**, presence of distractors/noise, domain/vertical, format variations |
| **NL query → structured** (search) | intent type, query **length/specificity**, number of constraints/filters, **ambiguity**, domain |
| **Classification** | class balance incl. hard/ambiguous cases, text length, domain, near-miss pairs |
| **QA** | question type, **reasoning depth**, answer length, domain, answerable vs unanswerable |
| **Free-text generation** | genre, tone/register, length, persona/POV |

Example of the judgment expected: for **job-description extraction**, posting **length** is a major
axis (a one-line listing vs a 600-word posting extract very differently) — so it belongs in the
factors, not left to chance. Reason about the domain like this every time; don't ship a generic
taxonomy when the domain has an obvious dominant axis.

## The strategy phase — read the plan before you pay

Pay close attention to how the work is being *planned* during the design phase. `taxonomy.json`
(with its per-node `weight`s) and `strategies.json` (roots, weights, `never_combine`) together
decide the dataset's entire distribution — what "typical" looks like across thousands of rows —
before a single record is generated. They are drafted by a model, they are plausible-looking by
construction, and every judgment error in them gets executed faithfully at full scale. Ninety
seconds reading two JSON files is the cheapest QA gate in the pipeline; treat it as mandatory
before any paid generation, not optional.

**Cautionary example — a 2k short-story corpus that came out wrong.** A free-text story dataset was
generated with a taxonomy whose `pov` factor listed second-person, epistolary, documentary-archive,
and frame/mixed-form narration as sibling branches of ordinary first/third person. Every individual
piece was defensible: the branches were real narrative forms, the strategy bundles were thematically
coherent, and no single prompt or config line was obviously wrong. The corpus still came out with
**~57% of stories in a high-artifice form** (21.9% second person alone), because several small,
individually-reasonable choices compounded: the builder subdivided the *interesting* branches most
finely (second person got 10 subtypes, workhorse close-third got 5) and the sampler of the time
turned that granularity directly into probability; one strategy bundled *all* the experimental forms
together, so every record it produced was unusual in several ways at once; and two factors (`pov`
and `structure`) both contained document/frame ideas, so mixes could demand the same gimmick twice
or contradict themselves. The result read like a corpus of narrative tricks, not stories.

Be nuanced about the lesson. It is **not** "rare forms are bad" — a story dataset *should* contain
epistolary and second-person stories, and coverage of the space is a real goal. The failure was
that nothing in the plan encoded *how often* and *in what combinations*: enumeration granularity
silently became prevalence, thematic affinity ("the weird stuff goes together") silently became
concentration, and per-factor independence silently became constraint-stacking. When you review the
design artifacts, you are checking exactly those three translations:

- **Weights vs. reality.** For each factor, do the branch `weight`s in `taxonomy.json` reflect how
  common each branch should be *in realistic data*, not how interesting it was to enumerate? Rare
  forms should sit an order of magnitude below common ones (~0.03 vs ~1.0). A factor whose weights
  are all ~1.0 despite obviously unequal real-world prevalence is a red flag.
- **Bundles vs. concentration.** Read each strategy and ask what a record sampled from it actually
  looks like. A strategy whose roots for some factor are *only* unusual branches makes them
  mandatory, not occasional — rare things should be diluted across strategies, never concentrated
  into one. Check every strategy covers every factor (bare factor name = fine).
- **Combinations vs. coherence.** Look for factor overlap (two factors owning the same idea) and
  for pairs that are individually fine but jointly incoherent or impossible at the record's scale —
  that's what `never_combine` is for. A missing rule ships silently; sanity-check the model's rules
  and add your own.

- **Depth vs. fit.** Factors are sampled independently, so check whether the *deep* leaves of one
  factor can go with anything. A leaf like "screen width in pixels" only suits some products; an
  "intent" leaf that fixes the price and shipping constraints steps on another factor. When a factor's
  leaves often cannot go with the other factors' values, stop code one or two levels up with
  `generation.code_level: {factor: level}`: code still picks that level with exact shares, and the
  writer chooses the specifics beneath it to suit the record. Judge by *impossible* pairs only —
  unusual pairs are the variety you are paying for. Cut as deep as is safe: in two pilots, cutting two
  such factors at level 2 gave more natural records with no loss of variety, while cutting at level 1
  let the writer drift toward typical records. A handful of impossible pairs is a `never_combine` job,
  not a reason to cut. (`analysis/factor_roles.py` asks a model for these levels; it makes real calls,
  so it sits behind the spend gate, and its answer is a majority of several runs, not one.)

Also read the `Mix space:` line `generate` prints before it starts: it says how many distinct mixes the
plan can produce against how many this run draws, and warns when mixes will repeat.

Leave `generation.model_picked` alone unless a factor is pure texture (formality, sign-off style) that
nobody trains on. When the writer chooses a factor itself it over-picks the typical options, drops rare
ones, and its own report of what it picked is unreliable — never use it for a label.

All of it is hand-editable: fix weights in `taxonomy.json`, rebalance or split bundles and add
`never_combine` pairs in `strategies.json`, then rerun — both files are reused verbatim. Use
`--stop-after strategies` (or `generation.stop_after`) to halt there deliberately. If the mix
distribution matters a lot, sample it for free: `sample_mix` is pure RNG, so a few thousand draws
in a scratch script show the marginals before any money moves.

## Representation decisions (the high-leverage, easy-to-miss part)

Beyond schema shape and coverage axes, every dataset has subtle **representation choices** — *how*
each row encodes information — that don't show up in a naive reading of the description but strongly
shape what a model learns from the data. This is where datasets are quietly won or lost. For every
task, actively hunt for these; don't take the user's description at face value. Each one falls into
one of two buckets, and the bucket tells you whether to ask:

- **Use-dependent → ASK** (with a recommendation). The right answer depends on what the user is
  training and how they'll use the output, so you can't safely default it. Surface one plain
  question. Recurring forms across archetypes: how to represent **absent/missing information**; label
  **granularity** (coarse vs fine classes); whether to include **negative / unanswerable / hard
  cases** and at what rate; **class balance**; output **length/verbosity**; canonical vs surface
  forms of values.
- **Universal quality → DECIDE and ENFORCE** (don't ask). Almost always right for training quality,
  so bake them in rather than asking. Recurring forms: **atomic / narrow / normalized fields**;
  **consistent formatting** (dates, units, enums); **faithful, non-hallucinated** content; varied,
  deduped rows. Enforce these in the schema **and** the prompt module (the loose envelope alone can't
  express them) and have the critic reject violations.

Where you land a choice, push it into the schema, the prompt module, **and** the taxonomy — e.g. if a
representation choice introduces a case (absent values, hard negatives), make that case an explicit
taxonomy axis so it actually appears in the data at a controlled rate.

**Concrete illustration — extraction (text → JSON).** The same two buckets, made specific:

1. *Absent-field policy* (use-dependent → **ASK**). When a requested field's value isn't in the input
   text, emit the key with `null`/empty, or omit it? Recommend **present-but-null** when the
   downstream model must reliably produce a **fixed schema** — it teaches the model to acknowledge
   absence instead of hallucinating or silently dropping fields (then make "some fields absent" a
   taxonomy axis and mark those fields non-`required`/nullable). Use **omit-when-absent** for
   per-row/varying schemas (what `job_extraction` and `ecommerce_search_extraction` do). Ask, because
   it depends on their model.
2. *Atomic, narrow fields* (universal → **ENFORCE**). Good: `{"salary_min": 90000, "salary_max":
   120000, "salary_currency": "USD"}`, `{"location_city": "Berlin", "location_country": "DE"}`, dates
   as `YYYY-MM-DD`. Bad: `{"salary_range": "90k–120k/yr"}`, `{"location": "Berlin, Germany"}`,
   free-form dates. Give the model a menu of good atomic fields plus explicit anti-examples in the
   prompt module (see `examples/job_extraction_prompts.py`) and make the critic reject compound junk.

Reason about *your* task's representation choices the same way every time — the extraction pair is
just the worked example, not the whole list.

## Defaults you apply silently

Copy from `template.yaml`; change only these per the task. Don't ask about any of them.

- **Taxonomy depth.** `depth: 2` is the default and right for most datasets. Use `1` for a narrow,
  single-axis dataset; `3` only for genuinely broad/heterogeneous domains where 2 levels can't span
  the space. Deeper = more taxonomy-build calls and time, so don't reach for 3 reflexively.
- **`review_mode: auto_accept`** for smoke/pilot. Switch to `write_then_edit` only if the user
  wants to hand-edit the taxonomy before generation.
- **Models.** Stronger model for `strategic`, cheaper/faster for `bulk` and `critic` (e.g. a "pro"
  strategic + a "flash" bulk/critic on the same OpenRouter-compatible `provider`). `"fake"` for
  smoke tests. For reasoning models, set `extra_body: {reasoning: {effort: low, exclude: true}}` per
  role — there's no auto-detection.
- **Taxonomy build.** Leave `best_of_n: 1` and `taxonomy.critic: true` (2 calls per expanded node).
  Turning the taxonomy critic off halves the build cost but gave a much thinner tree when we tried it
  (3 of 21 described branches survived); only do it for throwaway runs.
- **`overgenerate_ratio` ~1.1**, raise toward 1.5 if the pilot accept rate is low. With a working
  prompt module the accept rate is usually near 99%.
- **`concurrency`.** It caps model calls in flight; the built-in `4` is slow. Use 40–80 for real runs
  if the provider allows, and lower it when you see rate-limit retries. On the combined path a batch
  counts as `record_batch_size` calls.
- **`complexity_ratio` / `max_refine_attempts`** — leave at template defaults unless quality demands
  more refinement. An accepted row whose last critic verdict says "reject" is normal: the critic
  returned a corrected record and it was accepted directly.

## Custom prompts (a Python prompt module)

YAML + schema control *what* shape to produce; the **prompt module** controls *how* the model is
asked, and it's how you enforce the representation decisions above (atomic fields, absent-field
policy, per-row varying schema, a stricter critic, a specific free-text style). Reach for one when
the config can't express the task's rules. Simple datasets don't need one — don't add it reflexively.

How it works:

1. Write a `.py` file next to the config (convention: `examples/<name>_prompts.py`) and point the
   config at it. The path resolves **relative to the YAML file**:
   ```yaml
   prompts:
     module: "<name>_prompts.py"
   ```
2. The module **overrides any subset** of the built-in prompt functions in `simula/prompts.py`, plus
   the `SYSTEM_JSON` / `SYSTEM_TEXT` system strings. Anything you don't define falls back to the
   built-in — so override only what you need.
3. Each override must keep the **same parameter names** as its built-in counterpart (signature-
   compatible). `simula validate` imports the module and rejects a missing file, an import error, a
   non-string system prompt, or an incompatible signature — always `validate` before generating.

The high-leverage functions to override (see `simula/prompts.py` for the full list + exact
signatures — don't guess them):

- **`combined_batch_prompt(...)`** — the main lever on the combined path. It gets several records'
  sampled mixes at once and must say how to plan and write each record. Most task-specific rules go
  here; `complexify_record_prompt(...)` says what "harder" means for a finished record.
- **`meta_prompt_prompt(...)`** — the main lever on the scenario path. It plans each example (context,
  the concrete target schema for that row, which fields are present/absent). A scenario should
  *describe* the record, not write it: a scenario that contains the finished record gets copied,
  invented details included.
- **`critique_prompt(...)`** (JSON) / **`critique_text_prompt(...)`** (free-text) — make the critic
  actively reject your anti-patterns (compound fields, hallucinated values, wrong absent-field policy).
- **`generate_record_prompt(...)`**, **`strategy_prompt(...)`**, the taxonomy prompts
  (`factor_prompt`, `expand_prompt`, `level_plan_prompt`, …) — override only if the defaults fight
  your task.

Minimal skeleton:

```python
# examples/my_task_prompts.py — only the param NAMES must match simula/prompts.py
SYSTEM_JSON = "Return one valid JSON object. No prose, no code fences."

def critique_prompt(description, schema, meta_prompt, record):
    return f"Reject the record if any field is compound or not stated in the source. Record:\n{record}"
```

Copy `examples/job_extraction_prompts.py` (atomic fields + omit-when-absent + strict critic) or
`examples/ecommerce_search_extraction_prompts.py` (per-row varying schema; every prompt for the
combined path, including a critic that returns the corrected record) as a working starting point
rather than writing from scratch.

Three rules for any prompt you write, each learned from a bad run:

- **Never ask the model to vary something code already sampled.** A prompt that said "vary the product
  across the candidates" produced a dataset where about 60% of rows were not about the product recorded
  in their lineage. The mix decides *what*; the prompt decides *how*.
- **Say what wins when ingredients clash.** The sampled nodes are drawn independently. Give the writer
  a precedence (subject first) and permission to soften or drop an ingredient that does not fit.
- **Tell the writer the nodes are instructions, not content.** Otherwise taxonomy names and
  descriptions leak into records.

## Check lineage on the pilot

Lineage (`taxonomy_mix` on every row) is only worth something if the record matches it, and nothing in
the pipeline verifies that by default. On the pilot, read rows against their mix: is the record about
the sampled subject, does it have the sampled shape or length, does it carry the sampled label? If a
factor is routinely ignored, fix the prompt (or the factor) before scaling — a big run copies the
mismatch faithfully. For a measured number, `evaluation.coverage_mode: reassign` has the critic
classify each record independently (extra calls).

## Guardrails (never violate)

- **Smoke with `"fake"` before any real call.** It's free and offline; it catches schema and
  prompt-module bugs you'd otherwise pay to discover.
- **Review `taxonomy.json` and `strategies.json` before any paid generation** (see "The strategy
  phase"). The design artifacts are the dataset's distribution; a plausible-looking bad plan
  executes faithfully at full scale.
- **No real model calls without explicit user go-ahead** (spend gate) and a key in root `.env`.
  Never put a real API key in a YAML — only `api_key_env`.
- **Don't commit** `runs/`, `.env`, or `llm_calls.jsonl` (prompts/responses can be sensitive).
- **Check it actually worked.** After a real run, read the accept rate — `simula` can finish
  *below* target silently if the accept rate is low. If `len(final) < target`, say so and either
  raise `overgenerate_ratio` or investigate rejections in `dataset.raw.jsonl`. Confirm
  `taxonomy_mix` is non-empty on rows (lineage) and that fields are atomic.
- **Report honestly.** Give real counts and the real billed cost (sum of `cost` in `llm_calls.jsonl`;
  `cost_summary.json` is per command); if quality is off, show the offending rows from
  `llm_calls.jsonl` rather than declaring success.
- **Don't change a fingerprinted knob mid-run.** The path, batch sizes, `code_level`, `model_picked`,
  `complexity_ratio`, critic settings, models, prompts, taxonomy and strategies are part of the resume
  fingerprint. `target_size`, `overgenerate_ratio` and `concurrency` are not, so growing a run is safe.

## Pointers (don't duplicate these here)

- Config mechanics + every field: `CONFIG.md`, `examples/template.yaml`.
- Concepts, true pipeline flow, the three model roles, config reference (with diagrams): `DOCS.md`.
- Architecture, hard constraints, behaviors, debugging: `AGENTS.md`.
- **`examples/` — copy the closest one to start.** Pick by task and adapt rather than writing config
  from a blank file:

  | Example | Copy it when the task is… |
  |---|---|
  | `basic_qa.yaml` | a quick fake-model smoke test / simplest schema run |
  | `template.yaml` | you want every knob visible at its default to fill in |
  | `cat_stories_freetext.yaml` | free-text generation (`schema: null`), no JSON |
  | `query_extraction_gemini.yaml` | NL query → structured JSON, fixed schema |
  | `job_extraction.yaml` + `job_extraction_prompts.py` | text → atomic JSON, **omit-when-absent**, strict critic (the prompt-module reference) |
  | `ecommerce_search_extraction.yaml` + `ecommerce_search_extraction_prompts.py` | extraction with a **per-row varying schema**; also the reference for the **combined path** (5 records per call, critic on, about $0.40 per 1K rows in our pilot) |

  The `*_prompts.py` files are the worked references for the custom-prompts section above.
