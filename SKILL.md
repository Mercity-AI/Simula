---
name: generate-dataset
description: Use when the user wants to generate a synthetic dataset with this repo (simula) — e.g. "make me an extraction dataset", "generate ~5k QA pairs", "build training data for X". Walks the user through it asking only what dataset and how much, deciding the rest from baked-in defaults and surfacing only the few high-leverage choices.
---

# Generate a dataset with simula

You are driving `simula` for a user who wants a dataset, not a config tutorial. Read `AGENTS.md` once
for the architecture and hard constraints; this file is the process and the judgment on top of it.
`CONFIG.md` is the field reference and `examples/template.yaml` the copy-me skeleton, so nothing here
repeats field tables.

## Words used here

- **Mix.** One sampled node per factor: the ingredients of one record. Code draws it.
- **Subject.** The factor that says what a record is about (topic, issue, item, document type).
- **Situation.** One sentence naming the concrete thing a record is about: one specific case of its
  subject node. Content only, nothing about tone, length or who is writing. Listed per subject node
  before any writing; each is used by exactly one record. Needs `generation.subject`.
- **Meta-prompt.** A short instruction for one record, written by a model from the whole mix (and the
  situation, when there is one). Needs `generation.meta_prompts: true`.
- **Record.** The finished row.

A situation and a meta-prompt are different things. A situation fixes *what* the record is about and
comes from one factor; a meta-prompt is the full plan for the record and comes from all of them. A run
can use neither, either or both.

## Prime directive

Ask the user only two things up front:

1. **What dataset**: what each row should look like and what it is for. Push for the use; "extraction
   for a model that must emit a fixed schema" implies different choices than "examples for a demo".
2. **How much**: the target row count.

Decide everything else yourself from this file, and bring the user back only at the gates below. State
the few consequential choices you made in one line ("combined path, 5 per call, subject auto, omit-absent fields,
fake smoke first") so they can veto; do not ask permission for each.

## Decision gates

| Gate | Why it must be asked | How |
|---|---|---|
| **Spending real money** | Real calls cost money and time; `AGENTS.md` forbids them without a go-ahead. | Before the first real run: "Smoke test passed. A pilot of N rows on `<model>` will make about M calls and cost roughly $X. Go ahead?" |
| **API key missing** | Keys are read only from a gitignored `.env` at the repo root. | Ask them to add the variable named by `provider.api_key_env`. |
| **Use-dependent representation choice** | The right answer depends on their downstream model (see "Representation decisions"). | One plain question with a recommendation. |
| **Fixed vs per-row schema** | Decides between a strict `schema` and an open envelope plus a prompt module. | Ask only if the description is ambiguous; otherwise infer and say so. |
| **Record critic off** | `generation.critic: false` is the cheapest setting but ships unchecked records. | Only if they ask for the cheapest possible run; tell them the trade. |
| **Pilot size** | The pilot is the only place a bias is cheap to fix. Skipping or shrinking it is the user's call, not yours. | Say it in these terms: "Before the full run I strongly suggest a pilot on the same tree and strategies the full run will use. Ideally 5–7% of the target, and no less than 2–5%; in rows, at least about 1,000, and 3,000–5,000 for a large run. That is enough to see biases (label shares off target, repeated names and details, labels that do not match the text, near-copies) and fix them before paying for all of it. It will cost about $X. OK?" |
| **Conflicting combinations** | Excluding them, or keeping them, changes what the dataset contains and what a model trained on it learns. | After the pilot, show the grid of label agreement by pair of factors and the options from "Combinations that pull against a label", with your recommendation. |
| **Final scale-up** | A large run is real money. | Report what the pilot showed and what you fixed, then confirm once before going to full size. |

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
5. **Shake-out.** Set `target_size` to 20–50 and run `generate`. This only proves the prompts and the
   schema work on the real model. Read a few rows; fix anything broken.
6. **Pilot (gate: pilot size).** Raise `target_size` to the pilot size (see "The pilot") and rerun in the
   same output directory. Then do "Check the pilot", all of it. A fix to the prompts, the tree or the
   strategies changes the run's fingerprint, so rerun the pilot with `--no-resume` and check again.
7. **Scale (gate: confirm).** Raise `target_size` and rerun in the same output directory; resume is on
   by default and keeps the pilot's rows, so a clean pilot is not wasted money. Watch the run and report
   progress as it goes: if no model call lands for a few minutes a request has hung, so stop and rerun
   (only the missing attempts are redone). Then `evaluate`.

## Pick the generation path

Choose once, before the pilot. Switching later needs `--no-resume`.

| Path | Config | Use it when |
|---|---|---|
| **Combined** (recommended) | `meta_prompts: false`, `record_batch_size: 5` | You want the cheapest good data. One call writes 5 records straight from their mixes, each with a one-line plan; every record is still validated and critiqued on its own. In our pilots it cost about half the meta-prompt path with one record per call, and its records were more faithful to their mixes. |
| **Meta-prompt** | `meta_prompts: true` (built-in default), `meta_batch_size: 10`, `record_batch_size: 5–10` | The user wants to read or edit the per-record meta-prompts (`meta_prompts.jsonl`, `--stop-after meta_prompts`) before records are written, or stock phrasing is the main complaint: with the meta-prompt written first, records announced their ingredients less often (follow-up framing in 13% of tickets against 15%). Batched like this its cost is in the same range as the combined path. |

Do you need meta-prompts? Usually not. In a 1,000-ticket benchmark with situations on, the two paths gave
the same variety (Vendi 51.2 combined, 51.4 meta-prompt). The meta-prompt path earns its extra step when
a human will edit the meta-prompts, or when a record needs planning the writer cannot do in one line.

- **Set the subject.** `generation.subject: auto` (or name the factor). Each node of the subject then
  gets a list of situations, one per record, so records that share a node are about different things.
  It is the largest variety gain we have measured (Vendi 42 → 51 on 1,000 tickets, and no near-copies in
  10,000) for about 4% extra cost. Skip it only when no factor is about content. After the design step,
  open `taxonomy.json` and check `"subject"` names the factor you would have picked; edit it if not.
- **Batch size follows record length.** 5 for short records (queries, QA pairs, tickets); 1–2 for long
  free text, because records written together come out shorter. Past about 5 on the combined path,
  records drift from the shape that was sampled for them.
- **Keep meta-prompt batches at 10 or under.** At 30 per call a cheap model returned meta-prompts that only
  restated their ingredients, and the share of records whose labels matched their mix fell from 94% to
  88–90%.
- **Give `bulk` room.** A batch reasons before it answers. Set `models.bulk.max_tokens` high (the
  e-commerce example uses 65536); a reply cut off at the limit loses the whole batch.
- **Keep the critic on, and know what it does not do.** It roughly halves invented content; with it off,
  about one row in three in our pilot had an unsupported or missing fact. It is also about half of a
  combined-path run's cost. It does not make a label fit a text that was written against it (see "Check
  the pilot", labels).
- **Routing matters more than any knob.** On OpenRouter a `:nitro` model id billed about twice list
  price and reasoned about twice as much as the same model on default routing. Use the plain id for big
  runs unless speed matters; default routing is slower and sometimes hangs.
- **Complexify is its own call** for `complexity_ratio` of the records: it rewrites the meta-prompt, or
  on the combined path takes the written record and returns a harder one. Budget that many extra calls
  (12% of cost at 0.2), or set it to 0 when "harder" means nothing for the dataset.

## Write the description like a spec, not a story

The description (and anything a prompt module pastes in beside it) is read by every prompt. State what a
record is, what it is for, and the rules. Leave out anecdotes, example incidents, sample IDs, named
people and "recent events": whatever is concrete in the context gets copied. In one benchmark a feature
name mentioned once in the description appeared in 26% of tickets and an error code in 5%; the built-in context
rules cut the codes to about 1%, and deleting the line from the description took them to zero. If the model
needs facts to stay consistent (plan names, limits, units), give those; give no incidents.

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

### Output length is a factor you usually have to add yourself

Ask the user whether the length of the output matters. It often does (a model trained on 70-word
examples writes 70-word answers), and it depends on the use, so it is theirs to say. If it matters, do not
leave it to the writer: a model left alone settles on one length. One writer-labelled story set came out
at about 900 words for nearly every story although the request was a mix of flash, short and medium, and
records written several per call come out shorter than records written alone.

Control it with a length factor of its own in `taxonomy.factors`, written by hand:

```yaml
taxonomy:
  factors:
    - name: "length"
      description: "How long the record is, which the text must actually match. Bands by word count:
        one line (under 25 words), short (25-80), medium (80-200), long (200-400)."
```

Then, after the design step, open `taxonomy.json` and set the bands' `weight`s to the mix the user wants
(the tree builder guesses them). Keep the factor about length only; shape and register belong in other
factors. Say in the writer prompt that the band is binding. With `examples/stories_freetext_v2.yaml`,
which does this, stories landed on their band's target in every run (about 280, 800 and 1,200 words).
In the pilot, check the word-count distribution per band, not just the overall median.

## Review the design before you pay

`taxonomy.json` (with per-node `weight`) and `strategies.json` (roots, weights, `never_combine`)
decide the whole distribution before a single record exists. A model drafted them, they look plausible
by construction, and every error in them is executed faithfully at full scale. Reading them is the
cheapest quality gate in the pipeline. Check five things:

- **Weights vs. reality.** Do branch weights reflect how common each branch should be in realistic
  data, not how interesting it was to enumerate? Rare forms belong an order of magnitude below common
  ones (about 0.03 against 1.0). A factor whose weights are all near 1.0 despite obviously unequal
  prevalence is a red flag.
- **Bundles vs. concentration.** Ask what a record drawn from each strategy looks like. A strategy
  whose roots for some factor are only unusual branches makes them mandatory. Let taxonomy weights own
  prevalence, and use a bare factor name for any factor a strategy has no opinion about.
- **Combinations vs. coherence.** Look for two factors owning the same idea (a topic tree whose leaves
  already say how severe the case is, next to a separate severity factor), and for pairs that are fine
  alone but impossible together. A few such pairs are what `never_combine` is for; it has side effects,
  described in "Combinations that pull against a label".
- **Strategies vs. shares.** A strategy that lists only some branches of a factor overrides that
  factor's tree weights for its share of the run. `generate` measures this as soon as strategies exist
  (so the design step shows it) and prints `strategies skew '<factor>': …` naming the strategy. Treat every such line as a question for
  you: is this skew wanted? In one benchmark a model-drafted "high stakes" strategy forced high or urgent
  priority on a quarter of the tickets, so high came out at 40% against a 25% target; deleting it
  restored the target. When the user gave target shares for a label, no strategy should restrict that
  label's factor.
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

- `code_depth: {factor: level}`: code picks the node at that level, with its exact share, and the
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

Then set `generation.code_depth` before the pilot (it is part of the resume fingerprint). If you
cannot run the script, answer the two questions yourself from `taxonomy.json`; it is the same
reasoning.

## Combinations that pull against a label

Factors are drawn independently, but in the real world they are not independent. An angry customer
usually has a serious problem. A large paying account's outage is urgent; a hobbyist's is not. Independent
draws therefore produce records whose ingredients pull against a value stored in the record: a low
priority with a furious tone, an urgent priority on a free plan, a low priority on a topic that is an
incident by nature.

These records are not mistakes. They exist in real data (an angry customer with a trivial problem), and
they are exactly what a classifier most needs to see. They are also the hardest records to write, because
the writer must hold one ingredient against the grain of another. A cheap bulk model, the kind you use
when generating at scale, usually cannot: it resolves the tension in the text and leaves the label as
rolled. The ticket gains stakes that justify the anger, now reads as high priority, and still says low. A
stronger model can probably hold both; we have not measured that, so check it in the pilot before relying
on it.

**The example this comes from.** 10,000 support tickets written by a small, cheap model (GLM 5.3 Flash),
with category, priority, mood, customer tier and ticket style all rolled independently. A second model
labelled every ticket blind, and we counted how often it gave the priority the dataset had.

| Tickets | Share of the set | Priority confirmed |
|---|---|---|
| No ingredient pulls against the priority | 66% | 69% |
| Mood pulls against it (low + frustrated or angry; high or urgent + friendly) | 16% | 33% |
| Customer tier pulls against it (urgent + free plan; low or medium + enterprise) | 12% | 30% |
| Topic pulls against it (an incident topic rolled low; a billing question rolled urgent) | 9% | 26% |
| Style pulls against it (low + a thread that raises several issues) | 9% | 35% |

The two-thirds with no conflict were confirmed as often as a set where the writer chose its own labels
(68%). The whole gap between the two sets sat in the conflicting third, which produced half of all the
disagreements. Low + angry alone was confirmed 11% of the time. The critic did not repair it: it rejected
about half of those tickets, and the rewrite it returned read the same way.

**What excluding them did.** We then put all four kinds in `never_combine`, re-fitted the weights so every
factor kept its shares, and generated 2,000 more tickets with the same cheap model:

| | Topic conflicts excluded | All four kinds excluded | Writer-labelled set |
|---|---|---|---|
| Priority confirmed | 55% | 65% | 68% |
| Low confirmed | 48% | 70% | 78% |
| Urgent confirmed | 38% | 54% | 54% |
| Off by two or more levels | 4.9% | 1.6% | 0.8% |

The labels became about as readable as the writer-labelled set's, and the marginal shares stayed on target.
The price shows only when you look inside: every enterprise ticket is now high or urgent, no free-plan
ticket is urgent, no frustrated or angry ticket is low, and no multi-issue thread is low. A model trained
on this set learns those shortcuts, and it never sees an angry customer with a small problem.

**Find them in the pilot.** Have a model label a sample blind, then tabulate agreement for each label
against every other factor, one grid per pair. A cell far below the rest of its row is a conflict. Read
ten records from that cell before deciding anything: sometimes the dataset is right and the labeller is
reading tone as severity.

**Then choose, and know what each choice costs.** Every option has a second-order consequence.

- **Exclude the pair with `never_combine`.** A violating mix is redrawn whole, so *every* factor's shares
  move, not only the two you named. In the example, excluding the topic conflicts alone would have taken
  urgent from 10% to 5% and medium from 35% to 42%. The `strategies skew` warning reports this; re-fit the
  tree's top-level weights until the shares are back, and expect the weights to end up far from the shares
  (low priority needed a weight of 1.55 to stay at 30%). Exclusion also rewrites the joint distribution:
  with enterprise barred from low and medium, every enterprise ticket is high or urgent, and a model
  trained on that learns "enterprise means high". And it removes the hard cases, so the dataset becomes
  easier than the world it stands for.
- **Keep the combination and give it to a model that can write it.** A stronger `bulk` model costs more per
  record; measure on a few hundred records whether it actually holds the label.
- **Keep it and say which ingredient decides which field** in the writer prompt (for a ticket: the facts
  decide priority, the wording carries mood). Cheap to try; untested in our runs.
- **Keep it and accept the noise.** Then tell the user how much, and in which cells.

Exclude what is impossible. For what is merely hard, the decision is the user's: it changes what the
dataset contains, so bring it to them with the grid and the options (it is a gate, see "Decision gates").

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
- **Subject.** `generation.subject: auto`, checked in `taxonomy.json` after the design step.
- **Models.** A stronger model for `strategic`, a cheap fast one for `bulk` and `critic`, `"fake"` for
  smoke tests. For reasoning models set `extra_body: {reasoning: {effort: low, exclude: true}}` per
  role; nothing is auto-detected.
- **`overgenerate_ratio` about 1.1.** Accept rates are usually near 99% with a working prompt module on
  a strong model; cheap models drop or garble batch items (81–97% accepted in our runs), so use 1.25
  there, and let the pilot's accept rate set the final value.
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
- `meta_prompt_prompt` / `meta_prompt_batch_prompt`: the main lever on the meta-prompt path. A
  meta-prompt describes the record; one that contains the finished record gets copied, invented details
  included.
- `situations_prompt`: how the situations for a subject node are listed. Override it only to say what a
  "situation" is for an unusual dataset; keep it about subject matter alone.
- `critique_prompt` / `critique_text_prompt`: make the critic reject your anti-patterns. On rejection
  it may return the corrected record as `"revised"`, which is accepted directly when valid.

The built-in prompts share a few rules, defined as constants at the top of `simula/prompts.py`. An
override replaces the whole prompt, so import and include the ones that apply:

| Rule | Goes in | What happens without it |
|---|---|---|
| `TAXONOMY_CONTEXT_RULE` | `expand_prompt`, `refine_nodes_prompt` | Node descriptions copy the description's named details as "such as" examples, and those land in most records |
| `CONTEXT_RULE` | every writer prompt | The context's few concrete details show up in a large share of records |
| `PHRASING_RULE` | every writer prompt | Records announce their ingredients in the same stock phrases ("I'm the admin", "third time writing") |
| `DIVERSITY_RULE` | writer prompts that produce several records per call | Records of one call converge on one template |
| `REVISE_RULE` | `critique_prompt` (its `"revised"` record), `refine_record_prompt`, `refine_text_prompt` | The critic fixes a mismatch by changing the label instead of the text, and label shares drift |

With a subject set, an override of `combined_batch_prompt` or `meta_prompt_batch_prompt` must also render
each item's situation: add `situation_line(item)` to the item's spec and `situation_note(items)` to the
instructions (both in `simula/prompts.py`). An override that leaves them out silently drops every
situation. `PHRASING_RULE` and the others are worded for any dataset; add your own dataset's stock
phrases next to them in the module.

Three rules, each learned from a bad run:

- **Never ask the model to vary something code already sampled.** "Vary the product across the
  candidates" produced a dataset where about 60% of rows were not about the product in their lineage.
- **Say what wins when ingredients clash.** They are drawn independently. Give a precedence (subject
  first) and permission to soften or drop a texture ingredient that does not fit. Never give that
  permission for a label or anything else stored in the record: those must hold exactly, so say which
  ingredient decides which field (for a ticket, the facts decide priority and the wording carries mood).
- **Tell the writer the nodes are instructions, not content,** or taxonomy names leak into records.

Start from `examples/ecommerce_search_extraction_prompts.py` (every prompt for the combined path) or
`examples/job_extraction_prompts.py` (atomic fields, omit-when-absent, strict critic).

## The pilot

The pilot is a small run of the real thing: same tree, same strategies, same prompts and models as the
full run, and it is not optional. A model drafted the tree and the strategies, the mixes are drawn
independently, and the writer has habits; each of these produces biases that are invisible in 50 rows and
expensive in 50,000.

- **Size.** 5–7% of the target is ideal; 2–5% is the floor. In rows, at least about 1,000, and
  3,000–5,000 for a large run. Shares, repeated details and label problems do not show reliably in
  fewer. If the whole target is only a few thousand rows, pilot about a third of it.
- **Same output directory.** The pilot's rows are the first rows of the full run. Growing `target_size`
  resumes from them, so a clean pilot costs nothing extra.
- **Tell the user.** This is a gate (see "Decision gates"): say what you will run, why, and what it
  costs, in plain terms, before you run it.

What our own pilots would have caught, had they been this size: one label at 40% of the data against a
25% target (a strategy); one name in 8% of records; an example from the description in 26% of them; and low-priority
labels on tickets that read as high.

## Check the pilot

Work through all of these. Each line says what to look at and what to do when it is off.

- **Shares.** Count every label and every factor's top-level branches in `dataset.final.jsonl` and
  compare with what the user asked for or the tree weights. A share more than about 5 points off has a
  cause: a `strategies skew` warning you skipped, a `never_combine` rule, or the writer or critic
  changing labels (compare each record's label with its `taxonomy_mix`). Fix `strategies.json` or the
  prompt, not the count.
- **Labels against the text.** For any dataset with labels, have a model label a few hundred records
  blind (it sees the record, never the dataset's label) and compare, per label and per pair of factors.
  Low agreement concentrated in a few cells means ingredients are pulling against the label there: follow
  "Combinations that pull against a label". Low agreement spread evenly means the label definitions
  themselves are loose; tighten them with the user before generating more.
- **Lineage.** Nothing verifies that a record matches its `taxonomy_mix`. Read a dozen rows against
  their mix: right subject, right shape or length? If a factor is routinely ignored, fix the prompt or
  the factor before scaling. `evaluation.coverage_mode: reassign` measures it with extra critic calls.
- **Sameness.** Take the subject node with the most records and read ten of them side by side. They
  should be about ten different things. If they are not, check that `situations.jsonl` has a row for
  nearly every attempt and that a prompt override is not dropping the situation line. Turn on
  `evaluation.diversity` for a number; compare only runs scored at the same `sample_cap`.
- **Repeated details.** Count the names, places, numbers and product details that recur: any single
  name in more than a few percent of records, any detail from the description in more than a handful.
  Details from the description are removed at the source ("Write the description like a spec"). Default
  names survive every instruction we have tried ("draw names from many cultures" moved one name from 5%
  to 1% and left another at 8%); when names matter, make them a factor so code picks them.
- **Length.** If the user cares about length, compare word counts with each record's length band. One
  cluster for every band means the writer is ignoring the factor; see "Output length is a factor you
  usually have to add yourself".
- **Stock phrases.** Read twenty openings and twenty closings in a row. Records that all start by
  stating who is writing, or all sign off the same way, need the dataset's own stock phrases added next
  to `PHRASING_RULE` in the prompt module.
- **Accept rate and rejections.** Read `rejection_reason` in `dataset.raw.jsonl`. A run can finish
  below target without an error; if it did, say so and raise `overgenerate_ratio` or fix the cause. An
  accepted row whose last critic verdict is "reject" is normal: the critic returned the fix. If the
  critic rewrites more than about a third of records, read what it rejects: a rule the writer keeps
  breaking belongs in the writer prompt, and a field that follows a fixed rule from other fields is
  better computed after generation than written by a model and judged by another.
- **Quality.** Atomic fields, faithful content, no taxonomy names in records. `llm_calls.jsonl` has
  every prompt and response.
- **Cost.** Sum `cost` over `llm_calls.jsonl` (the provider-billed amount when reported), per task.
  `cost_summary.json` covers only the last command. Multiply out to the full target and give the user
  that number at the scale-up gate.

## Guardrails

- Smoke with `"fake"` before any real call.
- No real model calls without the user's go-ahead and a key in the root `.env`. Never put a key in YAML.
- Review the design artifacts before paid generation.
- Do not commit `runs/`, `.env` or `llm_calls.jsonl`.
- Do not skip or shrink the pilot on your own; that gate belongs to the user.
- Do not change a fingerprinted setting mid-run: the path, batch sizes, `subject`, `code_depth`,
  `model_picked`, `complexity_ratio`, critic settings, models, prompts, taxonomy, strategies. `target_size`,
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
