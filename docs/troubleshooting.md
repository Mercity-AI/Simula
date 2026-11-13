# Troubleshooting

For anything about a model's output, look in `llm_calls.jsonl` first: it holds every prompt and reply of
the run, with tokens, cost and the params sent.

## Errors

**`Extra inputs are not permitted` for a config key.** Unknown keys are errors, so a typo or a renamed
setting fails at load. Check the name in [Configuration](reference/configuration.md). Renamed so far:
`generation.batch_size` is now `record_batch_size`, `generation.code_level` is now `code_depth`.

**No API key, or `401`.** The key is read only from `.env` in the directory you run `simula` from, under
the name in `provider.api_key_env`. A shell-exported variable is ignored. See
[Installation](installation.md#api-key).

**`402 Insufficient credits` while the key still has budget.** The account behind the key is empty. Top it
up; retrying will not help.

**`Run config changed since the last checkpoint … rerun with --no-resume`.** Something that decides what
an attempt produces changed since the last run (models, prompts, schema, taxonomy, strategies, the path,
batch sizes…). Undo the change to keep resuming, or pass `--no-resume` to make every attempt again. See
[How a run works](concepts/pipeline.md#resume).

**`strategies reference unknown taxonomy paths`.** The strategic model used paths that are not in the tree,
twice. `strategies.json` was written anyway: fix the paths by hand, or delete the file to rebuild it.

**`never_combine rules … still violated after … redraws`.** A strategy's rules exclude every mix its roots
can produce. Loosen the rules or widen the strategy's roots.

**The prompt module is rejected by `validate`.** An override must keep the built-in function's parameter
names exactly; copy the signature from `simula/prompts.py`.

## The dataset

**Fewer rows than `target_size`.** Read `rejection_reason` in `dataset.raw.jsonl`. Then fix the cause, or
raise `overgenerate_ratio` and rerun: resume adds only the missing attempts.

**Many `Record missing from the batch response` rejections.** Cheap models drop or garble items in batched
replies (2–5% of attempts on GLM 5.3 Flash). Raise `overgenerate_ratio`; do not raise the batch size.

**Whole batches rejected, or replies cut off.** A batch's reasoning plus all its records must fit in
`max_tokens`. Raise `models.bulk.max_tokens` (65536 for 10 records is a reasonable start).

**Records much shorter than asked for.** Records written several per call come out shorter. Lower
`record_batch_size` for long records, and make length a factor. See [Batching](guides/batching.md).

**Records that all tell the same story.** Set `generation.subject` so each record gets its own situation.
If it is set, check that `situations.jsonl` has a row for nearly every attempt and that a custom prompt
is not dropping the situation line. See [Situations and meta-prompts](concepts/situations-and-meta-prompts.md).

**One name, product or detail in a large share of records.** If it appears in the description, remove it
there. Names the model defaults to survive instructions; make them a factor so code picks them.

**Label shares off target.** Read the `strategies skew` warning `generate` printed: a strategy that
restricts the label's factor overrides its tree weights. See [Strategies and sampling](concepts/sampling.md#the-skew-warning).

**Labels that disagree with the text, concentrated in a few factor combinations.** Ingredients drawn
independently pull against the label, and the critic does not fix it. See
[Running a pilot](guides/running-a-pilot.md#combinations-that-pull-against-a-label).

**Records that ignore part of their mix.** Lineage records what was asked for, not what was written. Read
records against their `taxonomy_mix`, check the prompt does not tell the model to vary something code
already sampled, and consider `code_depth` for factors whose deep leaves clash. `coverage_mode: reassign`
measures it.

**My edits to `taxonomy.json` disappeared.** `simula taxonomy` always builds a new tree. Use
`simula generate --stop-after taxonomy` to build once and keep edits.

## Speed

**The run is slow.** The default `concurrency` of 4 is conservative; 40–80 is usual if the provider allows.
A deep taxonomy also takes many calls to build; lower `depth` or `children_per_node`.

**Rate-limit errors (429).** They are retried with backoff, but lower `concurrency` if they keep coming.

**The run stalls near the end.** A request has probably hung; default routing occasionally does this. If no
new call or row appears for about three minutes, stop the process and rerun the same command: it resumes
and redoes only the missing attempts.
