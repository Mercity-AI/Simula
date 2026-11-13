# Situations and meta-prompts

Between the sampled mix and the written record, `simula` can add two steps. They are different things,
and a run can use neither, either or both.

## Words

- **Mix.** One sampled node per factor: the ingredients of one record. Drawn by code.
- **Subject.** The factor that says what a record is about: its topic, issue, item or document type
  (`generation.subject`).
- **Situation.** One sentence naming the concrete thing a record is about: one specific case of its
  subject node ("a duplicate line item on a monthly invoice"). Subject matter only, nothing about tone,
  length or who is writing.
- **Meta-prompt.** A short instruction (about 80 words) for one record, written by a model from the
  whole mix and the situation when there is one: what happens, who is involved, how it is written.
- **Record.** The finished row.
- **Combined path.** `generation.meta_prompts: false`: records are written straight from their mixes,
  with no meta-prompt step.

| | Situation | Meta-prompt |
|---|---|---|
| What it is | One sentence: one specific case of the record's subject node | A short instruction for one record: the full plan |
| Built from | One factor, the subject | The whole mix, plus the situation when there is one |
| Turned on by | `generation.subject` (a factor name, or `auto`) | `generation.meta_prompts: true` (the default) |
| Written | Before anything else, as one list per subject node; each item is used by exactly one record | One per attempt, or several mixes per call with `meta_batch_size` |
| File | `situations.jsonl` | `meta_prompts.jsonl` |
| Why | Records that share a subject node otherwise all tell that node's most obvious story | A planning step you can read and edit between the mix and the record |

## Why situations exist

A tree with 70 leaves and 10,000 records puts about 140 records on each leaf. Every writer call sees
only its own records, so each one invents the leaf's most typical instance, and the dataset fills with
near-copies of one story per leaf. Listing a leaf's situations in one place, with the earlier ones in
view, is the only step where a model compares them and keeps them apart.

In a 10,000-ticket benchmark this removed near-duplicate records entirely and was the largest variety
gain of everything tried, for about 4% extra cost. Use it whenever a subject node is shared by more than
a handful of records, which is nearly always.

How it works:

- With `generation.subject: auto`, the `strategic` model picks the subject factor once and stores the
  pick in `taxonomy.json` as `"subject"`, where you can change it.
- For every subject node, the `bulk` model lists one situation per attempt that sampled it, 25 per call.
  Calls for one node run one after another, and each sees the node's last 120 situations so it does not
  repeat them; different nodes run in parallel.
- Every batch, of meta-prompts or of records, holds records of one subject node.
- The situation becomes a line of the record's spec, and the critic sees it too.

## Why meta-prompts exist

A meta-prompt turns a list of ingredients into a plan the writer can follow, and it is a file you can
review: stop with `--stop-after meta_prompts`, read or edit `meta_prompts.jsonl`, and rerun to write the
records from your edited versions. Every row of the dataset records the exact meta-prompt it was built
from.

`generation.meta_prompts_per_mix: K` has one call write K meta-prompts for one mix, and every one
becomes its own attempt sharing that mix. That means fewer distinct mixes and fewer meta-prompt calls.

## The combined path

With `generation.meta_prompts: false` there is no meta-prompt step and no `meta_prompts.jsonl`. One
`bulk` call writes `record_batch_size` records straight from their mixes, each with a one-line plan of
its own, and that plan (followed by the record's ingredients) is stored in the row's `meta_prompt` field.
Every record is still validated, repaired and critiqued on its own and keeps its own mix as lineage, so a
bad record never sinks its batch.

It is the cheaper path: no meta-prompt calls, and the instructions are sent once per batch instead of
once per record. It gives up the reviewable meta-prompt step. See [Batching](../guides/batching.md) for
the trade-offs of writing several records per call, and [Cost](../guides/cost.md) for measured numbers.

## How they combine

The order of decisions is the same on both paths: code rolls the mix, the situation fixes what
happened, and the writer, through a meta-prompt or directly, fits the two together.

- **Meta-prompt path:** the situation is a line of the spec the meta-prompt is written from, so the
  meta-prompt carries it to the record.
- **Combined path:** the situation is a line of the record's own spec, and the writer's one-line plan
  takes the meta-prompt's place.

## Harder records

`generation.complexity_ratio` (default `0.3`) sends that share of attempts through a separate
`complexify` call: on the meta-prompt path it rewrites the meta-prompt into a harder one; on the
combined path it takes the written record and returns a harder version, which is then validated and
critiqued like any other. The row's `complexified` field says which records went through it.
