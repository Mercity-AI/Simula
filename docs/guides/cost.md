# Cost

What a run costs depends on the models, the provider's routing, the generation path, the batch size and
how long the records are. This page explains where the money goes, which settings move it most, and how
to estimate a full run from a pilot.

## Where the money goes

Almost all of the cost is the per-record work, done again for every attempt:

| Calls per attempt | Role | When |
|---|---|---|
| Meta-prompt: `1 / (meta_batch_size × meta_prompts_per_mix)` | `bulk` | meta-prompt path |
| Record: `1 / record_batch_size` | `bulk` | always |
| Situations: about `1 / 25` | `bulk` | `generation.subject` set |
| Complexify: `complexity_ratio` | `bulk` | `complexity_ratio > 0` |
| Repair: only for records that fail the schema | `bulk` | JSON mode |
| Critique: at least 1, plus 1 after every refine | `critic` | `generation.critic: true` |
| Refine: only for rejected records, up to `max_refine_attempts` | `bulk` | `generation.critic: true` |

The taxonomy and strategies are built once and reused, so on a run of thousands of records they are a
small share, unless the tree is very deep (see [Taxonomy](../concepts/taxonomy.md#size-compounds)).

With a reasoning model, hidden reasoning is most of the billed output of every call, and it scales with
the number of records written, not the number of calls. Batching saves the repeated instructions, not
the reasoning; see [Batching](batching.md).

## The levers, largest first

1. **Routing.** On OpenRouter, the same model can be served by different providers at different prices.
   A `:nitro` model id billed about 2.1× list price and reasoned about twice as much per call as the plain
   id on default routing, which made default routing about 5× cheaper for the same configuration. Default
   routing also billed below list, because 45% of the input hit the provider's prompt cache. It is slower
   and occasionally hangs a request (see [Running a pilot](running-a-pilot.md#watching-a-long-run)).
2. **Path and batch size.** The combined path at 5 records per call made 1.22 calls per row against 3.33
   for the unbatched meta-prompt path, at about half the cost.
3. **The critic.** About half of a combined-path run's cost. Turning it off is the cheapest setting, but
   in one pilot one record in three then had an unsupported or missing fact.
4. **Model choice.** A cheap, fast model for `bulk` and `critic`; a stronger one only for `strategic`,
   which makes few calls. See [Model roles](../concepts/model-roles.md).
5. **Complexify.** One extra call for `complexity_ratio` of the attempts: about 12% of cost at `0.2`. Set
   it to `0` when "harder" means nothing for your dataset.
6. **Situations.** About 4% extra, for the largest measured variety gain. Worth keeping.
7. **`max_tokens`.** The default is 32K per call. It caps runaway replies; it does not lower the cost of
   normal ones.

## Measured in pilots

These are single runs, kept to show orders of magnitude, not promises. They were measured with
`deepseek-v4-flash` for `bulk` and `critic` at reasoning effort low unless noted.

| Dataset and setup | $ per 1K rows |
|---|---|
| E-commerce search extraction, meta-prompt path, unbatched | 0.84 |
| E-commerce search extraction, combined path, 5 per call | 0.40 |
| E-commerce search extraction, combined path, 10 per call, critic off (list price) | 0.30 |
| E-commerce search extraction, combined path, 5 per call, `deepseek-v4-pro` (list price) | 1.94 |
| Support tickets, the `support_tickets` example setup, GLM 5.3 Flash | about 0.85 |

Billed amounts are what OpenRouter charged on default routing; list price is computed from token counts
and the model's listed rates.

## Estimating your run

Run a pilot with the same models, routing, path and prompts as the full run (see
[Running a pilot](running-a-pilot.md)), then:

1. Sum the cost the provider reported in `llm_calls.jsonl`:

   ```bash
   python -c "import json,sys; rows=[json.loads(l) for l in open(sys.argv[1])]; print(sum(r['cost'] or 0 for r in rows))" runs/my_run/llm_calls.jsonl
   ```

   Use `llm_calls.jsonl`, not `cost_summary.json`: the summary covers only the last command, while the log
   holds every call of the run. To see where the money went, group the same sum by `task`.
2. Subtract the one-off taxonomy and strategy calls (`task` values `factor_discovery`, `node_expansion`,
   `taxonomy_critic`, `level_plan`, `strategy`, `subject`) if the full run will reuse them.
3. Divide by the number of rows in `dataset.final.jsonl` and multiply by your target.

When a provider does not report cost, `cost` is `null`; work from `in_tokens`, `out_tokens` and the
model's price instead. `reasoning_tokens` and `cached_tokens` show how much of each is hidden reasoning
and how much hit the prompt cache.
