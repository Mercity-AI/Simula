# Model roles

`simula` calls one OpenAI-compatible endpoint (`provider`) with three **roles**, configured under
`models`. They can all use the same model id or three different ones.

```yaml
models:
  strategic: { model: "deepseek/deepseek-v4-pro" }     # few calls, high leverage
  bulk:      { model: "deepseek/deepseek-v4-flash" }   # most of the calls and tokens
  critic:    { model: "deepseek/deepseek-v4-flash" }
```

| Role | Tasks | Produces |
|---|---|---|
| `strategic` | `factor_discovery`, `node_expansion`, `taxonomy_critic`, `level_plan`, `strategy`, `subject` | The taxonomy, the strategies and the subject pick |
| `bulk` | `situations`, `meta_prompt`, `complexify`, `generate`, `repair`, `refine` | Situations, meta-prompts and the records themselves |
| `critic` | `semantic_critic`, `complexity_score`, `node_assign` | Accept or reject verdicts, and the optional evaluation scores |

The task names are what you use in `sampling.tasks` to set decoding params for one kind of call, and
what each row of `llm_calls.jsonl` and `cost_summary.json` is labelled with.

## Choosing models

- **`strategic`** runs a small number of calls up front whose output the whole run reuses. A stronger
  model is usually worth it here.
- **`bulk`** writes the records and does most of the volume: this is where most of the tokens and money
  go. A cheap, fast model is usually the right choice.
- **`critic`** judges every record. It can match `bulk`. The critic catches real problems (in one pilot
  it halved unsupported fields and lifted faithful records by 10–15 points), so turning it off
  (`generation.critic: false`) is the cheapest setting but ships unchecked records.

## The fake model

`model: "fake"` runs a role offline with deterministic placeholder output and no API key. The
`basic_qa` and `cat_stories_freetext` examples use it for every role, which makes them a smoke test for
your install and your config:

```bash
simula run examples/basic_qa.yaml
```

## "Generator model"

Every dataset row has a `generator_model` field. There is no separate generator role: it is the `bulk`
model id, because `bulk` writes the records.
