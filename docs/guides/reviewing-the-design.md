# Reviewing the design before you pay

`taxonomy.json`, with its node weights, and `strategies.json`, with its roots, weights and
`never_combine` rules, decide the whole distribution of the dataset before a single record exists. A
model drafted them, they look plausible by construction, and every error in them is carried out
faithfully at full scale. Reading them is the cheapest quality check in the pipeline.

Build both and stop:

```bash
simula generate my_config.yaml --stop-after strategies
```

This costs a handful of `strategic` calls. Edit the files, rerun the same command to see the effect
(both files are reused), and continue with plain `generate` when they look right. Two things print at
this stop: a `Mix space:` line (how many distinct mixes the plan can produce against how many the run
draws), and a `strategies skew` warning for any factor whose shares the strategies pull away from its
tree weights.

## What to check

- **Weights against reality.** Do branch weights reflect how common each branch should be in realistic
  data, not how interesting it was to enumerate? Rare forms belong about an order of magnitude below
  common ones (about 0.03 against 1.0). A factor whose weights are all near 1.0 despite obviously unequal
  prevalence is a red flag.
- **What each strategy forces.** Ask what a record drawn from each strategy looks like. A strategy whose
  roots for some factor are only unusual branches makes those branches mandatory for its share of the
  run. Let tree weights own prevalence, and use the bare factor name for any factor a strategy has no
  opinion about.
- **Two factors owning one idea.** A topic tree whose leaves already say how severe a case is, next to a
  separate severity factor, produces contradictions. Fix the factor descriptions and rebuild.
- **Impossible pairs.** Values that are fine alone but impossible together belong in `never_combine`.
  Note that a violating mix is redrawn whole, so every factor's shares move a little, not only the two
  you named; check the skew warning afterwards.
- **The skew warning.** Treat every `strategies skew` line as a question: is this skew wanted? If you
  gave target shares for a label, no strategy should restrict that label's factor.
- **The subject.** With `generation.subject: auto`, check that `"subject"` in `taxonomy.json` names the
  factor you would have picked, and change it if not.

Two real failures, in opposite directions: a story set came out 57% experimental forms (22% in second
person) because the builder subdivided the interesting branches most finely, one strategy bundled all the
experimental forms, and two factors both owned "document form". The fix then overcorrected: guidance
that hammered "rare, at most one" pinned the main strategies to conventional forms and second person fell
to 0.07%. The plan must say how often and in what combinations, and reading it is the only way to know.

## Who picks each factor, and how deep

By default code samples every factor down to a leaf. Code is exact but blind to the other factors, so it
can hand the writer a leaf that cannot go with the rest: "screen width in pixels" for a product with no
screen. Two settings change that:

- `generation.code_depth: {factor: level}`: code picks the node at that level, with its exact share,
  and the writer chooses the specifics beneath it.
- `generation.model_picked: {factor: level}` (combined path only): the writer picks from a menu and
  reports its choice. The picks fit the record, but writers favour typical options, drop rare ones and
  sometimes misreport their own picks.

Decide per factor with two questions:

1. **Must the value be known or controlled?** Yes if it is the record's subject, appears in the output or
   is a label, has shares or coverage you care about, or has rare options that matter. Then code picks
   it. Never use `model_picked` for these; it suits pure texture, such as formality.
2. **How deep can code go before its picks stop fitting?** Work up from the leaves. An option is a misfit
   when it is impossible, not merely unusual, next to about a third or more of another factor's values.
   If about one option in ten or fewer is a misfit, the level is safe. Up to about one in three, it is
   safe once you add `never_combine` rules for those options. Beyond that, go up a level.

Unusual combinations are what independent sampling is for; only impossible ones count. Cut as deep as
is safe: in two pilots, stopping two clash-prone factors at level 2 gave more natural records with no
loss of variety, while level 1 left so much to the writer that records drifted toward the typical.

Set `code_depth` before the pilot; it is part of the resume fingerprint.
