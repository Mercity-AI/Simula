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

Everything else — taxonomy depth, factors/axes, `best_of_n`, `children_per_node`,
`overgenerate_ratio`, `complexity_ratio`, `max_refine_attempts`, `concurrency`, model-role
assignment, whether to write a prompt module — **you decide.** Surface, don't ask.

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
   prompt-module errors with zero model calls. Always do this before anything real.
3. **Smoke test, free.** Set every model role to `"fake"` and run
   `python -m simula.cli run examples/<name>.yaml`. This is offline and deterministic — it proves
   the pipeline, schema, lineage, and prompt module work end to end before spending a cent. Inspect
   the artifacts in `runs/<name>/` (especially row shape and `taxonomy_mix`).
4. **Pilot, real (gate: spend + key).** Switch roles to real models, set `generation.target_size`
   to ~20–50, and run `taxonomy` then `generate` as two steps so you (and optionally the user) can
   eyeball `taxonomy.json` before paying for bulk generation. Inspect `dataset.final.jsonl` and
   `llm_calls.jsonl` for quality: atomic fields? faithful? right axes covered? Read the accept rate.
5. **Scale (gate: confirm).** Raise `target_size` to the real number and run. Resume is on by
   default. Then `evaluate` for dedupe/coverage/diversity. Report cost from `cost_summary.json`.

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
- **`overgenerate_ratio` ~1.2**, raise toward 1.5–2 if pilot accept rate is low.
- **`concurrency` ~4**; lower it if the provider rate-limits, raise cautiously for big runs.
- **`complexity_ratio` / `max_refine_attempts`** — leave at template defaults unless quality demands
  more refinement.

## Guardrails (never violate)

- **Smoke with `"fake"` before any real call.** It's free and offline; it catches schema and
  prompt-module bugs you'd otherwise pay to discover.
- **No real model calls without explicit user go-ahead** (spend gate) and a key in root `.env`.
  Never put a real API key in a YAML — only `api_key_env`.
- **Don't commit** `runs/`, `.env`, or `llm_calls.jsonl` (prompts/responses can be sensitive).
- **Check it actually worked.** After a real run, read the accept rate — `simula` can finish
  *below* target silently if the accept rate is low. If `len(final) < target`, say so and either
  raise `overgenerate_ratio` or investigate rejections in `dataset.raw.jsonl`. Confirm
  `taxonomy_mix` is non-empty on rows (lineage) and that fields are atomic.
- **Report honestly.** Give real counts and real cost from `cost_summary.json`; if quality is off,
  show the offending rows from `llm_calls.jsonl` rather than declaring success.

## Pointers (don't duplicate these here)

- Config mechanics + every field: `CONFIG.md`, `examples/template.yaml`.
- Architecture, hard constraints, behaviors, debugging: `AGENTS.md`.
- Prompt-module pattern for atomic fields / absent-field policy / per-row schema:
  `examples/job_extraction_prompts.py`, `examples/ecommerce_search_extraction_prompts.py`.
- Worked examples: `examples/basic_qa.yaml` (fake smoke), `examples/cat_stories_freetext.yaml`
  (free-text), `examples/query_extraction_gemini.yaml`, `examples/job_extraction.yaml`,
  `examples/ecommerce_search_extraction.yaml`.
