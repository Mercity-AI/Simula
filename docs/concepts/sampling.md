# Strategies and sampling

The taxonomy says what *can* appear in the dataset. Sampling decides how often each thing does. Two
files control it, and both are worth reading before you pay for records: the node `weight`s in
`taxonomy.json`, and the strategies in `strategies.json`.

## Strategies

A strategy is a thematic lane over the tree, drafted by the `strategic` model:

```json
{
  "strategies": [
    {
      "id": "general",
      "description": "Broad coverage across every vertical, intent and query size.",
      "taxonomy_roots": ["product_vertical", "shopper_intent", "query_complexity"],
      "weight": 3.0
    },
    {
      "id": "directed_detailed",
      "description": "Known-item searches with many constraints.",
      "taxonomy_roots": ["product_vertical", "shopper_intent/directed_purchase", "query_complexity/large"],
      "weight": 1.0,
      "never_combine": [["product_vertical/groceries", "query_complexity/large"]]
    }
  ]
}
```

- `taxonomy_roots` says which parts of the tree the lane draws from. A bare factor name means that
  factor's whole tree; a deeper path such as `shopper_intent/directed_purchase` means only that branch.
- `weight` is how often the lane is used, relative to the others.
- `never_combine` lists pairs of paths that must not appear in the same mix.

Paths are separated by `/` (a `.` also works when the path has no `/`). Every path is checked against
the real tree when strategies are built; an invalid path is an error, not a silent no-op.
`strategy.count` fixes how many strategies are requested, and `strategy.guidance` steers what they
cover:

```yaml
strategy:
  guidance: |
    - Make billing + calm + free tier the most common combination.
    - Never combine enterprise_sso with mobile_app; they don't coexist.
    - Keep a dedicated strategy for furious + needs_escalation, even though it is rare.
```

Guidance is a nudge the model interprets, not a guarantee. For guarantees, edit `strategies.json`.

## How a mix is drawn

For every attempt, code:

1. picks a strategy, in proportion to the strategies' weights;
2. for **every** factor, picks a node: from the strategy's roots in that factor, or from the factor's
   whole tree when the strategy does not mention it, so no factor is ever missing from the lineage;
3. walks down from there level by level, choosing among siblings in proportion to their `weight`
   (a missing weight counts as `1.0`, `0` switches a branch off), until it reaches a leaf;
4. redraws the whole mix if it breaks one of the strategy's `never_combine` rules (rules that can never
   be satisfied raise an error instead of looping).

Because the walk goes level by level, a branch's share comes from its weight, never from how many
children the builder gave it. Each mix is seeded from `project.seed` and the attempt number, so a rerun
draws the same mixes.

Two settings change what lands in the mix:

- **`generation.code_depth: {factor: level}`** stops code at that level for the factor. The leaf is still
  drawn, then replaced by its ancestor at that level, so shares there stay exact and the writer chooses
  the specifics beneath. Use it when a factor's deep leaves often clash with the other factors' values
  (an attribute like "screen width" on a product with no screen). In two pilots, level 2 raised realism
  and coherence with no loss of diversity, while level 1 was too broad and records drifted toward the
  typical. Cut as deep as is safe.
- **`generation.model_picked: {factor: level}`** (combined path only) takes the factor out of code's
  hands: the writer sees the factor's nodes at that level, with the share each should get, and picks one
  per record. Labels then fit the text better, but shares are no longer exact. Strategies and
  `never_combine` do not apply to these factors.

## The skew warning

A strategy whose roots name only some branches of a factor draws that factor from those branches
alone. The dataset's shares are then a blend of the tree weights and every such strategy, and nothing
in the files shows the result. So right after strategies are built or loaded, `generate` draws 5,000
mixes the way the run will and warns when a code-sampled factor's top-level shares land more than
5 points from its tree weights:

```text
Warning: strategies skew 'priority': low 23% of mixes (tree weight 30%); medium 26% of mixes (tree weight
35%); high 38% of mixes (tree weight 25%) (strategies restricting it: high_stakes). Edit strategies.json
if the tree weights are the shares you want.
```

If the tree weights are the shares you want, delete or widen the strategy. If the skew is the point of
the strategy, leave it. In one benchmark a model-drafted "high stakes" strategy pushed one label from
25% to 40% of the dataset; deleting it restored the target. `--stop-after strategies` shows the warning
before any record is paid for.

## Editing strategies

`strategies.json` is reused as is on every rerun, so edit it directly: flatten weights, delete a lane,
add `never_combine` pairs. Two things to know:

- Paths are only checked when strategies are built. A path you edit by hand that matches nothing is not
  reported; that factor quietly falls back to its whole tree. Check spelling against `taxonomy.json`.
- If you change node names in `taxonomy.json` after strategies exist, delete `strategies.json` so it is
  rebuilt against the new tree.

## Accepted shares and the final trim

Accepted rows follow the weights. The final trim to `target_size` then keeps rows that add taxonomy
nodes not yet covered first, which flattens the distribution in `dataset.final.jsonl` somewhat. If the
shares matter, set them with weights and strategies rather than relying on the trim; for even coverage,
flatten the weights, ask for it in `strategy.guidance`, or raise `overgenerate_ratio` to give the trim a
larger pool. `generation.trim: false` keeps every accepted row instead.
