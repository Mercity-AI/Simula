# Batching

Batching means asking for several items in one model call instead of one. It is the main lever on how
many calls a run makes, and so on cost, but it also changes the records: they come out shorter, and
long or complex ones drift from what was sampled for them. This page explains the settings, what each
one trades, and how to pick a batch size.

## The settings

| Setting | Path | One call writes | Default |
|---|---|---|---|
| `meta_batch_size` (mode A) | meta-prompt path | the meta-prompts for several mixes | `1` |
| `record_batch_size` (mode B) | meta-prompt path | the records for several meta-prompts | `1` |
| `record_batch_size` (mode C) | combined path (`meta_prompts: false`) | several records straight from their mixes, each with a one-line plan | `1` |

A and B can be combined. A related setting, `meta_prompts_per_mix`, has one call write several
meta-prompts for the *same* mix; it reduces the number of distinct mixes rather than batching different
ones. See [Situations and meta-prompts](../concepts/situations-and-meta-prompts.md) for the two paths.

```yaml
# combined path, 5 records per call
generation:
  meta_prompts: false
  record_batch_size: 5

# meta-prompt path, batched at both steps
generation:
  meta_prompts: true
  meta_batch_size: 10
  record_batch_size: 5
```

## What a batch does not change

Batching only changes how records are *written*. Everything after that is still per record:

- each record keeps its own mix as lineage, and its own situation when `generation.subject` is set;
- each record is validated, repaired and critiqued on its own, and the critic is never batched;
- a malformed or missing item loses only itself, not the batch;
- `complexify` stays a separate call per record.

With `generation.subject` set, every batch holds records of one subject node, each about a different
situation.

## What it trades

### Fewer calls, lower cost

A batch sends the instructions once instead of once per record, and on the combined path there is no
meta-prompt call at all. On an e-commerce search dataset (`deepseek-v4-flash`, 500 attempts per
setting, the same 500 mixes each time):

| Setting | Model calls per row | $ per 1K rows |
|---|---|---|
| Meta-prompt path, unbatched | 3.33 | 0.84 |
| A: 5 mixes per meta-prompt call | 2.56 | 0.74 |
| B: 5 records per record call | 2.54 | 0.70 |
| A + B | 1.72 | 0.55 |
| C: combined path, 5 per call | 1.22 | 0.40 |

These are single pilot runs on one dataset and one model, billed on default OpenRouter routing; see
[Cost](cost.md). Your numbers will differ, the ordering usually will not.

### Hidden reasoning does not shrink

A reasoning model thinks about every record it writes, however many share a call. A 10-record batch
reasons about as much per record as ten single calls (about 1–2K tokens each at reasoning effort low).
Batching saves the repeated instructions and the meta-prompt step, not reasoning. It also means a batch
needs room: reasoning plus every record must fit in `max_tokens`, and a reply cut off at the limit loses
the whole batch. The e-commerce example sets `models.bulk.max_tokens: 65536` for 10 records.

### Shorter records

A model sizes a batched reply roughly like a single answer and divides it among the records, so each
record comes out shorter than it would have alone. Its visible output tokens per record drop, and
records sampled to be long or complex drift toward the middle. On the same e-commerce dataset, combined
path:

| Records per call | Records sized as sampled | Complexity as sampled | $ per 1K rows (list) |
|---|---|---|---|
| 1 | 91% | 80% | 1.43 |
| 5 | 82% | 73% | 0.91 |
| 10 | 79% | 69% | 0.76 |

In one case a spec for a "large multi-need" query came back as a 5-word query. The subject held up
(the product was right in 91–95% of records at every size); what slipped was size and shape. On support
tickets, an outside experiment saw the median ticket shrink from 147 to 123 words at 6 per call.

This matters most for long records. A story or a long document written five to a call is noticeably
shorter than one written alone, and the length you asked for in the description will not hold.

### Variety: helps within a call, not proven overall

Records written in one call are seen together, and the built-in batch prompts tell the writer to keep
them apart: different names, people and details. That removes repetition *inside* a batch. Across the
whole dataset, the measured effect is mixed. On the e-commerce queries, Vendi diversity was flat at 1,
5 and 10 per call (78.4, 78.8, 79.1). The outside ticket experiment saw a large gain (Vendi 39 → 52, one
recurring customer name falling from 43% to 7% of tickets), but it changed finer taxonomy leaves at the
same time, so the gain cannot be put on batching alone.

The variety lever that has been measured on its own is `generation.subject`: situations took Vendi from
42 to 51 on 1,000 tickets and removed near-copies in 10,000. Use batching for cost, and situations for
variety.

### Cheap models drop items

As batches grow, weaker models drop, merge or garble items in the reply: 2–5% of attempts on
GLM 5.3 Flash came back as `Record missing from the batch response`. Those attempts are rejected and the
run finishes short. Raise `overgenerate_ratio` to cover them; do not raise the batch size.

### Meta-prompt batches above about 10 degrade

At 30 mixes per meta-prompt call, a cheap model started returning meta-prompts that only restated their
ingredients, and the share of records whose labels matched their mix fell from 94% to 88–90%. Keep
`meta_batch_size` at 10 or under.

## Picking a batch size

| Your records | Setting |
|---|---|
| Short: queries, QA pairs, tickets, labels | Combined path, `record_batch_size: 5`. Past about 5, records drift from the shape sampled for them. |
| Long free text: stories, documents, long answers | `record_batch_size: 1` or `2`, plus a length factor in the taxonomy (see [Writing a config](writing-a-config.md#output-length)). |
| You want to read or edit the meta-prompts | Meta-prompt path with `meta_batch_size: 10` and `record_batch_size` as above. Its cost is in the same range as the combined path. |
| A cheap model that drops items | Keep the batch size, raise `overgenerate_ratio` to about 1.25. |

Batch size is part of the resume fingerprint: pick it before the pilot, and changing it later needs
`--no-resume`.

## Concurrency

`generation.concurrency` counts calls in flight, and a record batch counts as `record_batch_size` of
them, because its records are critiqued in parallel once it returns. So `concurrency: 40` with
`record_batch_size: 5` runs 8 batches at once.

## Checking it on your own data

Run a small pilot at batch 1 and at your chosen size, then compare:

- **Length.** Word counts of the records, per length band if you have one. One cluster for every band
  means the writer is ignoring the band.
- **Output tokens per record.** In `llm_calls.jsonl`, rows with `task: generate` hold one call each:
  `(out_tokens - reasoning_tokens) / record_batch_size` is the visible output per record.
- **Fit.** Read a dozen records against their `taxonomy_mix`, especially the ones sampled to be long or
  complex.
- **Cost.** Sum `cost` over `llm_calls.jsonl` and divide by accepted rows. See [Cost](cost.md).
