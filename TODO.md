# TODO

Forward-looking work only. Completed work and decision records live in
[`notes/CHANGELOG.md`](notes/CHANGELOG.md). Keep any addition compact — see the minimalism
constraints in `AGENTS.md`.

## Structured sampling knobs (only if prompts prove insufficient)

Prompt-level `strategy.guidance` (done) is a nudge, not a guarantee. If users need hard control,
consider small compact config knobs that plug into `sample_mix`/`choose_strategy` in
`simula/taxonomy.py`:

- leaf-only sampling
- explicit per-branch weights
- exclusions (combinations that must never appear)
- quotas (minimum/maximum share per branch)

Do not jump straight to a large sampling-rule DSL. Synthetic-data users often need targeted coverage,
not just broad random coverage; prompt guidance covers the common "emphasize/avoid" cases, so
structured knobs are only worth adding for guarantees the model cannot reliably honor on its own.

## Batched and multi-turn generation modes

Every current model call is a fresh chat with one system message and one user message. Some
generation workflows need a different conversation shape. Consider explicit modes for:

- batched generation, where one model reply returns multiple records for the same meta-prompt
- repeated generation from the same meta-prompt under different sampling policies
- multi-turn continuation, where the model sees prior outputs and gets follow-ups like "generate
  more, but make them distinct from the previous examples"

Keep these separate from sampling policy — they change prompt/session structure, output parsing,
lineage, acceptance accounting, and dedupe risk differently than decoding params do. Design
questions: array-of-records vs. several independently parseable records; how much prior context to
retain without inflating duplication/cost; how to keep one row per final item while logging shared
conversations and parent/child provenance; whether critic/refine runs per item even when generation
was batched.

## Deferred review findings (recorded 2026-06-17, still open)

The correctness/cleanup findings from this review already landed (see the changelog). These were
deliberately deferred — keep any fix compact:

- **Generation can finish below target silently.** `generate_dataset` runs a fixed
  `ceil(target * overgenerate_ratio)` attempts and trims; a low accept rate yields fewer than
  `target` rows while the CLI still exits 0. Minimum fix: warn loudly (stderr) with the accept rate
  when `len(final) < target`. Only build a bounded adaptive-refill loop if hitting target must be a
  guarantee — it adds cost and weakens determinism, so gate it behind a knob/flag.
- **Model-output shapes are under-validated.** `_discover_factors` → `factor["name"]` is outside any
  try/except, so one nameless factor crashes the whole taxonomy build; a non-numeric strategy
  `weight` makes every row fail (silent zero-accept). Add small normalizers (drop nameless
  factors/children, coerce weights to positive floats, guarantee a non-empty strategy list). No
  schema framework.
- **Config booleans use Python truthiness.** `bool("false") is True` (config.py), so a quoted YAML
  bool silently inverts. Add a tiny `_require_bool` that fails loudly on non-bools.
- **Sampling includes abstract internal/root nodes.** `_sample_descendant` samples every node
  uniformly, so a mix can be just a vague root. Not a bug — it adds breadth. Add a single
  `prefer_leaf`/leaf-only sampling knob only if real meta-prompts come out too vague (see the
  structured sampling-knobs note above).
