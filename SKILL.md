---
name: generate-dataset
description: Use when the user wants to generate a synthetic dataset with this repo (simula) — e.g. "make me an extraction dataset", "generate ~5k QA pairs", "build training data for X". Walks the user through it asking only what dataset and how much, deciding the rest from baked-in defaults and surfacing only the few high-leverage choices.
---

# Generate a dataset with simula

You are driving `simula` for a user who wants a dataset, not a config tutorial. Read `AGENTS.md` once
for the architecture and hard constraints; this file is the process and the judgment on top of it.
`CONFIG.md` is the field reference and `examples/template.yaml` the copy-me skeleton, so nothing here
repeats field tables.

## Prime directive

Ask the user only two things up front:

1. **What dataset**: what each row should look like and what it is for. Push for the use; "extraction
   for a model that must emit a fixed schema" implies different choices than "examples for a demo".
2. **How much**: the target row count.

Decide everything else yourself from this file, and bring the user back only at the gates below. State
the few consequential choices you made in one line ("combined path, 5 per call, omit-absent fields,
fake smoke first") so they can veto; do not ask permission for each.

## Decision gates

| Gate | Why it must be asked | How |
|---|---|---|
| **Spending real money** | Real calls cost money and time; `AGENTS.md` forbids them without a go-ahead. | Before the first real run: "Smoke test passed. A pilot of N rows on `<model>` will make about M calls and cost roughly $X. Go ahead?" |
| **API key missing** | Keys are read only from a gitignored `.env` at the repo root. | Ask them to add the variable named by `provider.api_key_env`. |
| **Use-dependent representation choice** | The right answer depends on their downstream model (see "Representation decisions"). | One plain question with a recommendation. |
| **Fixed vs per-row schema** | Decides between a strict `schema` and an open envelope plus a prompt module. | Ask only if the description is ambiguous; otherwise infer and say so. |
| **Record critic off** | `generation.critic: false` is the cheapest setting but ships unchecked records. | Only if they ask for the cheapest possible run; tell them the trade. |
| **Final scale-up** | A large run is real money. | Confirm once before going from pilot to full size. |

## Workflow

Run the CLI from the repo root: `validate`, `taxonomy`, `generate`, `evaluate`, `run`.

1. **Draft.** Pick the archetype and its variation axes (table below), infer the schema, choose the
   generation path. Copy the closest example to `examples/<name>.yaml`. Write
   `examples/<name>_prompts.py` only if the task has rules the config cannot express.
2. **Validate.** `python -m simula.cli validate examples/<name>.yaml` makes no model calls and catches
   config, schema and prompt-module errors. Unknown or misspelled keys are errors, so typos show here.
3. **Smoke, free.** Set every role to `"fake"` and `run`. It is offline and proves the pipeline,
   schema, lineage and prompt module end to end. Look at the row shape and `taxonomy_mix` in
   `runs/<name>/`.
4. **Design, real (gate: spend + key).** Switch to real models and run
   `generate --stop-after strategies`. This builds `taxonomy.json` and `strategies.json` and stops.
   Review them as described in "Review the design before you pay", including who picks each factor.
5. **Pilot.** Set `target_size` to 20–50 and run `generate`. Then do "Check the pilot".
6. **Scale (gate: confirm).** Raise `target_size` and rerun in the same output directory; resume is on
   by default. Watch it: if no model call lands for a few minutes a request has hung, so stop and
   rerun (only the missing attempts are redone). Then `evaluate`.

## Pick the generation path

Choose once, before the pilot. Switching later needs `--no-resume`.

| Path | Config | Use it when |
|---|---|---|
| **Combined** (recommended) | `meta_prompts: false`, `record_batch_size: 5` | You want the cheapest good data. One call writes 5 records straight from their sampled mixes, each with a one-line plan; every record is still validated and critiqued on its own. In our pilot it cost about half the scenario path on the same model and its records were more faithful. |
| **Scenario** | `meta_prompts: true` (built-in default) | The user wants to read or edit the per-record scenarios (`meta_prompts.jsonl`, `--stop-after meta_prompts`) before records are written. One extra call per record. |

- **Batch size follows record length.** 5 for short records (queries, QA pairs, tickets); 1–2 for long
  free text, because records written together come out shorter. Past about 5, records drift from the
  shape that was sampled for them.
- **Give `bulk` room.** A batch reasons before it answers. Set `models.bulk.max_tokens` high (the
  e-commerce example uses 65536); a reply cut off at the limit loses the whole batch.
- **Keep the critic on.** It roughly halves invented content. With it off, about one row in three in
  our pilot had an unsupported or missing fact.
- **Routing matters more than any knob.** On OpenRouter a `:nitro` model id billed about twice list
  price and reasoned about twice as much as the same model on default routing. Use the plain id for big
  runs unless speed matters; default routing is slower and sometimes hangs.
- **Name the subject.** Set `generation.subject` to the factor that says what a record is about (topic, issue,
  document type) whenever its nodes are shared by many records. Batches then hold one subject node and see the
  situations already written for it, so records on a shared node stop repeating the node's obvious instance.
- **Complexify is its own call** for `complexity_ratio` of the records: it rewrites the scenario, or on
  the combined path takes the written record and returns a harder one. Budget that many extra calls,
  or set it to 0 when "harder" means nothing for the dataset.

## Archetype → variation axes

The taxonomy is your coverage, and choosing the right axes is the highest-leverage decision you make.
Seed `taxonomy.factors` with the axes that matter for the domain; do not ship a generic taxonomy when
the domain has an obvious dominant axis (for job-posting extraction, posting length is one).

| Archetype | Axes that usually matter most |
|---|---|
| Extraction (text → JSON) | document length, register, density of extractable facts, how many requested fields are absent, distractors, domain |
| NL query → structured | intent, query length and specificity, number of constraints, ambiguity, domain |
| Classification | class balance including hard and ambiguous cases, text length, domain, near-miss pairs |
| QA | question type, reasoning depth, answer length, domain, answerable vs unanswerable |
| Free text | genre, tone, length, point of view |

Keep each factor about one thing. A factor that packs several unrelated ideas into one tree (say size,
sentence shape and special expressions) gives each record only one of them, and a factor whose deep
leaves describe another factor's territory produces contradictions.

## Review the design before you pay

`taxonomy.json` (with per-node `weight`) and `strategies.json` (roots, weights, `never_combine`)
decide the whole distribution before a single record exists. A model drafted them, they look plausible
by construction, and every error in them is executed faithfully at full scale. Reading them is the
cheapest quality gate in the pipeline. Check four things:

- **Weights vs. reality.** Do branch weights reflect how common each branch should be in realistic
  data, not how interesting it was to enumerate? Rare forms belong an order of magnitude below common
  ones (about 0.03 against 1.0). A factor whose weights are all near 1.0 despite obviously unequal
  prevalence is a red flag.
- **Bundles vs. concentration.** Ask what a record drawn from each strategy looks like. A strategy
  whose roots for some factor are only unusual branches makes them mandatory. Let taxonomy weights own
  prevalence, and use a bare factor name for any factor a strategy has no opinion about.
- **Combinations vs. coherence.** Look for two factors owning the same idea, and for pairs that are
  fine alone but impossible together. A few such pairs are what `never_combine` is for.
- **Depth vs. fit.** See the next section.

Two real failures, in opposite directions. A story corpus came out 57% high-artifice (22% second
person) because the builder subdivided the interesting branches most finely, one strategy bundled all
the experimental forms, and two factors both owned "document form". The fix attempt then
over-corrected: guidance that hammered "rare, at most one" pinned the main lanes to conventional forms
and second person fell to 0.07%. The lesson is not that rare forms are bad; it is that the plan must
say how often and in what combinations, and you must read it to know.

Both files are plain JSON and reused verbatim on rerun, so fix weights, split bundles and add rules by
hand. `generate` prints a `Mix space:` line before it starts: how many distinct mixes the plan can
produce against how many the run draws.

### Who picks each factor, and how deep

By default code samples every factor down to a leaf. Two settings change that:

- `code_level: {factor: level}`: code picks the node at that level, with its exact share, and the
  writer chooses the specifics beneath it to suit the record.
- `model_picked: {factor: level}` (combined path only): code picks nothing; the writer chooses from a
  menu and reports its choice.

Code is exact but blind to the other factors, so it can hand the writer a leaf that cannot go with the
rest ("screen width in pixels" for a product with no screen). The writer's choices fit, but it favours
the typical options in every batch, drops rare ones, and misreports its own picks. So decide per factor
with two questions:

1. **Must the value be known or controlled?** Yes if it is the record's subject, appears in the output
   or is a label, has shares or coverage the user asked for, or has rare options that matter. Then code
   picks it.
2. **How deep can code go before its picks stop fitting?** Work from the leaves up. An option is a
   misfit when it is impossible, not merely unusual, next to about a third or more of another factor's
   values. If roughly one option in ten or fewer is a misfit, the level is safe. Up to about one in
   three, it is safe once you add `never_combine` rules for those options. Beyond that, go up a level.

Unusual combinations are what independent sampling is for; only impossible ones count. Cut as deep as
is safe: in two pilots, stopping two clash-prone factors at level 2 gave more natural records with no
loss of variety, while level 1 left so much to the writer that records drifted toward the typical.

`analysis/factor_roles.py` asks a model these questions for every factor (real calls, a few cents, so
it sits behind the spend gate):

```bash
python analysis/factor_roles.py examples/<name>.yaml --taxonomy runs/<name>/taxonomy.json \
    --out runs/<name>/factor_roles --batch <record_batch_size> --runs 5   # add --free-text for schema: null
```

It prints each factor's decision per run and writes the full answers to `roles_<n>.json`. Treat it as
a first opinion and check its work before you apply it:

- **Take the majority across runs.** A factor that flips between two levels is borderline; prefer the
  deeper level and handle its misfits with `never_combine`.
- **Read its example combinations.** Reject any "impossible" verdict that is only unusual, and redo
  that factor yourself. This is the model's most common mistake, and it pushes the cut too high.
- **Never accept `model` for a label, the subject, or a factor with rare options.** `model_picked` is
  only for pure texture nobody trains on, such as formality.
- **Act on its `note`.** A factor it flags as mixing unrelated things or repeating another factor is a
  taxonomy problem: fix the factor's description and rebuild, do not cut around it.
- **Turn its `exclusions` into `never_combine` pairs** in `strategies.json`, using exact tree paths.

Then set `generation.code_level` before the pilot (it is part of the resume fingerprint). If you
cannot run the script, answer the two questions yourself from `taxonomy.json`; it is the same
reasoning.

## Representation decisions

Every dataset has choices about how a row encodes information that a naive reading of the description
misses, and they shape what a model learns. Hunt for them. Each falls into one of two buckets:

- **Use-dependent → ask**, with a recommendation: how absent information is represented, label
  granularity, whether and how often to include negative, unanswerable or hard cases, class balance,
  output length, canonical vs surface forms.
- **Universal quality → decide and enforce**: atomic, narrow, normalized fields; consistent formats
  for dates, units and enums; faithful content; varied rows. Enforce them in the schema and in the
  prompt module, and have the critic reject violations.

Whatever you decide, push it into the schema, the prompts and the taxonomy. If a choice introduces a
case (absent values, hard negatives), make it a taxonomy axis so it appears at a controlled rate.

Worked example, extraction. Absent-field policy is use-dependent: recommend present-but-null when the
downstream model must emit a fixed schema (it teaches acknowledging absence), and omit-when-absent for
per-row schemas, as `job_extraction` and `ecommerce_search_extraction` do. Atomic fields are universal:
`{"salary_min": 90000, "salary_max": 120000, "salary_currency": "USD"}`, never
`{"salary_range": "90k–120k/yr"}`. Give the model a menu of good fields and explicit anti-examples.

## Defaults you apply silently

- **Taxonomy.** `depth: 2` for most datasets; 1 for a narrow one; 3 only for a broad domain. Leave
  `best_of_n: 1` and `taxonomy.critic: true`. Turning that critic off halves build cost but gave a
  much thinner tree when we tried it.
- **Models.** A stronger model for `strategic`, a cheap fast one for `bulk` and `critic`, `"fake"` for
  smoke tests. For reasoning models set `extra_body: {reasoning: {effort: low, exclude: true}}` per
  role; nothing is auto-detected.
- **`overgenerate_ratio` about 1.1.** Accept rates are usually near 99% with a working prompt module;
  raise it if the pilot says otherwise.
- **`concurrency`.** It caps model calls in flight and the built-in 4 is slow. Use 40–80 for real runs
  if the provider allows, lower it on rate-limit retries. On the combined path a batch counts as
  `record_batch_size` calls.
- **`complexity_ratio`, `max_refine_attempts`.** Template defaults unless quality demands otherwise.

## Custom prompts

The config says what shape to produce; a prompt module says how the model is asked, and it is how you
enforce representation decisions. Simple datasets do not need one.

Point the config at a `.py` file next to it (`prompts: {module: "<name>_prompts.py"}`). It may override
any subset of the functions in `simula/prompts.py`, plus `SYSTEM_JSON` and `SYSTEM_TEXT`; the rest fall
back to the built-ins. Each override must keep the built-in's parameter names (read them there, do not
guess), and `validate` rejects a mismatch.

Which functions matter:

- `combined_batch_prompt`: the main lever on the combined path. It receives several records' mixes and
  says how to plan and write each one. `complexify_record_prompt` defines "harder" for a written record.
- `meta_prompt_prompt`: the main lever on the scenario path. A scenario describes the record; one that
  contains the finished record gets copied, invented details included.
- `critique_prompt` / `critique_text_prompt`: make the critic reject your anti-patterns. On rejection
  it may return the corrected record as `"revised"`, which is accepted directly when valid.

Three rules, each learned from a bad run:

- **Never ask the model to vary something code already sampled.** "Vary the product across the
  candidates" produced a dataset where about 60% of rows were not about the product in their lineage.
- **Say what wins when ingredients clash.** They are drawn independently. Give a precedence (subject
  first) and permission to soften or drop one that does not fit.
- **Tell the writer the nodes are instructions, not content,** or taxonomy names leak into records.

Start from `examples/ecommerce_search_extraction_prompts.py` (every prompt for the combined path) or
`examples/job_extraction_prompts.py` (atomic fields, omit-when-absent, strict critic).

## Check the pilot

- **Lineage.** Nothing verifies that a record matches its `taxonomy_mix`. Read a dozen rows against
  their mix: right subject, right shape or length, right label? If a factor is routinely ignored, fix
  the prompt or the factor before scaling. `evaluation.coverage_mode: reassign` measures it with extra
  critic calls.
- **Accept rate and rejections.** Read `rejection_reason` in `dataset.raw.jsonl`. A run can finish
  below target without an error; if it did, say so and raise `overgenerate_ratio` or fix the cause. An
  accepted row whose last critic verdict is "reject" is normal: the critic returned the fix.
- **Quality.** Atomic fields, faithful content, no taxonomy names in records. `llm_calls.jsonl` has
  every prompt and response.
- **Cost.** Sum `cost` over `llm_calls.jsonl` (the provider-billed amount when reported).
  `cost_summary.json` covers only the last command.

## Guardrails

- Smoke with `"fake"` before any real call.
- No real model calls without the user's go-ahead and a key in the root `.env`. Never put a key in YAML.
- Review the design artifacts before paid generation.
- Do not commit `runs/`, `.env` or `llm_calls.jsonl`.
- Do not change a fingerprinted setting mid-run: the path, batch sizes, `code_level`, `model_picked`,
  `complexity_ratio`, critic settings, models, prompts, taxonomy, strategies. `target_size`,
  `overgenerate_ratio` and `concurrency` are safe to change, so growing a run is fine.
- Report real counts and real cost; when quality is off, show the offending rows.

## Examples to copy

| Example | Copy it when the task is… |
|---|---|
| `basic_qa.yaml` | a fake-model smoke test, the simplest schema run |
| `template.yaml` | you want every knob visible at its default |
| `cat_stories_freetext.yaml` | free-text generation (`schema: null`) |
| `stories_freetext_v2.yaml` + `stories_freetext_v2_prompts.py` | long free text with rare forms kept at set rates by taxonomy weights |
| `query_extraction_gemini.yaml` | NL query → structured JSON, fixed schema |
| `job_extraction.yaml` + `job_extraction_prompts.py` | text → atomic JSON, omit-when-absent, strict critic |
| `ecommerce_search_extraction.yaml` + `ecommerce_search_extraction_prompts.py` | per-row varying schema; the reference for the combined path (5 per call, critic on, about $0.40 per 1K rows in our pilot) |

More: concepts and pipeline in `DOCS.md`, every field in `CONFIG.md`, architecture and constraints in
`AGENTS.md`.
