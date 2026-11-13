# Evaluation

Evaluation is a separate step from generation. `simula generate` never evaluates; `simula evaluate`
(or the second half of `simula run`) reads `dataset.final.jsonl` and writes:

- `dataset.evaluated.jsonl`: the final rows after dedupe and decontamination;
- `eval_report.json`: counts, what was removed, coverage, and the optional scores.

`dataset.final.jsonl` itself is never rewritten, so you can rerun evaluation with different settings.
Each check is configured under `evaluation` (see [Configuration](../reference/configuration.md#evaluation)).

## Dedupe

`evaluation.dedupe` (on by default) removes near-duplicates. It compares records as lower-cased word
sequences (JSON records are serialized with sorted keys, so keys and values both count; punctuation does
not) and drops a record when its 13-word sequences overlap an earlier record's by a Jaccard similarity of
0.8 or more, keeping the first. It is text-overlap detection, not semantic or field-aware comparison.

It is built to scale: candidate pairs are found through a temporary on-disk index and every match is
verified exactly, so a hash collision can never delete a distinct record. The index is removed
afterwards. The same dedupe also runs inside `generate` before the final trim.

## Decontamination

`evaluation.decontaminate_against` takes paths to JSONL files, such as a benchmark's test set. Rows that
overlap any of them are dropped, using the same overlap measure as dedupe.

## Coverage

Coverage reports, per factor and level, how many taxonomy nodes the dataset reached.

- **`coverage_mode: lineage`** (the default) counts the nodes in each row's `taxonomy_mix`. It makes no
  model calls, but it measures what each record was *asked* to cover, not what it actually contains. If
  a writer drifted away from its mix, lineage coverage does not notice.
- **`coverage_mode: reassign`** has the `critic` model read each record and assign it to a node itself,
  which measures what the dataset appears to contain, at the cost of one call per record per factor.
- **`both`** reports the two side by side.

Treat lineage coverage as "what we asked for" and reassignment coverage as "what we appear to have got".

## Diversity

With `evaluation.diversity.enabled` (and the `diversity` extra installed), records are embedded with a
sentence-transformers model and three numbers are reported:

- `vendi`: the Vendi score, the effective number of distinct records (1 when all are identical, n when
  all are unrelated);
- `local_diversity`: mean distance to the `k_local` nearest neighbours, which catches clusters of
  near-copies;
- `global_diversity`: mean pairwise cosine distance.

Vendi and local diversity were stable across embedding models in our benchmark; mean pairwise distance
was not. All three depend on sample size and embedding model, so compare only runs scored with the same
`sample_cap` and `embedding_model`. Use `text_field` to embed one field (`query`, `extraction.intent`)
instead of the whole record. Embeddings are cached in the run directory.

## Complexity

With `evaluation.complexity` on, the `critic` model scores records 1–10 in shuffled batches, and
pairwise Elo turns those scores into one ordering from easiest to hardest. It makes extra model calls:
each record appears in `complexity_samples_per_item` batches of `complexity_batch_size`.

## What evaluation does not check

None of these checks reads a record against its labels. A record whose sampled label disagrees with
what its text says passes dedupe, coverage and diversity. [Running a pilot](../guides/running-a-pilot.md)
shows how to check that by hand on a sample.
