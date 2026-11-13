# Running a pilot

A pilot is a small run of the real thing: the same tree, strategies, prompts and models as the full
run. A model drafted the tree and the strategies, mixes are drawn independently, and the writer has
habits; each of these produces biases that are invisible in 50 rows and expensive in 50,000. The pilot
is where they are cheap to fix.

## From config to full run

1. **Validate.** `simula validate my_config.yaml` catches config, schema and prompt-module errors with
   no model calls.
2. **Smoke test, free.** Set every role to `model: "fake"` and `run`. It is offline and proves the
   pipeline, schema, lineage and prompt module end to end.
3. **Design.** Switch to real models and run `simula generate my_config.yaml --stop-after strategies`.
   Review the tree and strategies ([Reviewing the design](reviewing-the-design.md)).
4. **Shake-out.** Set `target_size` to 20–50 and run `generate`. This only proves the prompts and schema
   work on the real model. Read a few rows and fix anything broken.
5. **Pilot.** Raise `target_size` to the pilot size and rerun in the same `output_dir`. Check it (below).
   A fix to the prompts, tree or strategies changes the run's fingerprint, so rerun the pilot with
   `--no-resume` and check again.
6. **Scale.** Raise `target_size` to the full size and rerun in the same `output_dir`. Resume keeps the
   pilot's rows, so a clean pilot is not wasted money. Then `evaluate`.

Choose the generation path and batch sizes before the pilot: they are part of the fingerprint.

## How big

- 5–7% of the target is ideal; 2–5% is the floor.
- In rows, at least about 1,000, and 3,000–5,000 for a large run. Shares, repeated details and label
  problems do not show reliably in fewer.
- If the whole target is only a few thousand rows, pilot about a third of it.

What our own pilots would have caught at this size: one label at 40% of the data against a 25% target (a
strategy), one name in 8% of records, an example from the description in 26% of them, and low-priority
labels on tickets that read as high.

## Checking it

- **Shares.** Count every label and every factor's top-level branches in `dataset.final.jsonl` and
  compare with what you wanted. A share more than about 5 points off has a cause: a skew warning you
  skipped, a `never_combine` rule, or the writer or critic changing labels (compare each record's label
  with its `taxonomy_mix`). Fix `strategies.json` or the prompt, not the count.
- **Labels against the text.** Have a model label a few hundred records blind, seeing the record but
  never the dataset's label, and compare per label and per pair of factors (below).
- **Lineage.** Nothing verifies that a record matches its `taxonomy_mix`. Read a dozen rows against their
  mix. If a factor is routinely ignored, fix the prompt or the factor. `coverage_mode: reassign` measures
  it with extra calls.
- **Sameness.** Take the subject node with the most records and read ten side by side. They should be
  about ten different things. If not, check that `situations.jsonl` has a row for nearly every attempt
  and that a prompt override is not dropping the situation line.
- **Repeated details.** Count recurring names, places, numbers and product details. Details from the
  description are fixed at the source (see [Writing a config](writing-a-config.md#the-description)).
  Default names survive every instruction we have tried; when names matter, make them a factor so code
  picks them.
- **Length.** Compare word counts with each record's length band. One cluster for every band means the
  writer is ignoring the factor.
- **Stock phrases.** Read twenty openings and twenty closings in a row. If they all start or sign off the
  same way, add the dataset's stock phrases next to `PHRASING_RULE` in a [prompt module](custom-prompts.md).
- **Rejections.** Read `rejection_reason` in `dataset.raw.jsonl`. A run can finish below target without an
  error; raise `overgenerate_ratio` or fix the cause. If the critic rewrites more than about a third of
  records, read what it rejects: a rule the writer keeps breaking belongs in the writer prompt.
- **Cost.** Sum `cost` over `llm_calls.jsonl` and multiply out to the full target ([Cost](cost.md)).

## Combinations that pull against a label

Factors are drawn independently, but in the real world they are not. An angry customer usually has a
serious problem; a large paying account's outage is urgent. Independent draws therefore produce records
whose ingredients pull against a value stored in the record: a low priority with a furious tone, an
urgent priority on a free plan.

These records are not mistakes, and they are exactly what a classifier most needs to see. But they are
the hardest records to write, and a cheap model usually resolves the tension in the text and leaves the
label as rolled: the ticket gains stakes that justify the anger, reads as high priority, and still says
low. The critic does not repair this.

In 10,000 support tickets written by a cheap model, with category, priority, mood, tier and style rolled
independently, a blind labeller confirmed the priority on 69% of tickets with no conflicting ingredient,
but only 26–35% where an ingredient pulled against it, and 11% for low priority with an angry tone.

**Find them.** Label a sample blind and tabulate agreement for each label against every other factor, one
grid per pair. A cell far below the rest of its row is a conflict. Read ten records from it before
deciding: sometimes the dataset is right and the labeller is reading tone as severity.

**Then choose, knowing the cost of each option:**

- **Exclude the pair with `never_combine`.** Readable labels, but a violating mix is redrawn whole, so
  every factor's shares move; re-fit the tree weights until the skew warning is gone. Exclusion also
  teaches shortcuts (with enterprise barred from low priority, every enterprise ticket is high or urgent)
  and removes the hard cases.
- **Keep it and use a stronger `bulk` model.** Costs more per record; measure on a few hundred records
  whether it actually holds the label.
- **Keep it and say which ingredient decides which field** in the writer prompt (the facts decide
  priority, the wording carries mood). Cheap to try; not yet measured.
- **Keep it and accept the noise,** knowing how much and in which cells.

Exclude what is impossible. What is merely hard is a choice about what the dataset should contain.

## Watching a long run

A run writes `llm_calls.jsonl` and `dataset.raw.jsonl` as it goes, so you can watch progress. If no new
call or row appears for about three minutes, a request has probably hung (default routing occasionally
hangs one near the end of a run). Stop the process and rerun the same command: it resumes and redoes only
the missing attempts.
