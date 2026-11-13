# Writing a config

A config needs only a description and three model ids. Everything else has a default, so write only the
keys you want to change:

```yaml
project:
  name: "support_tickets"
  output_dir: "runs/support_tickets"

description: >
  Inbound customer-support tickets for a B2B invoicing SaaS, each labelled with a category and a
  priority, for training a ticket-routing classifier.

schema:
  type: object
  required: ["subject", "body", "category", "priority"]
  properties:
    subject:  { type: string }
    body:     { type: string }
    category: { type: string, enum: ["billing", "technical", "account", "feature_request"] }
    priority: { type: string, enum: ["low", "medium", "high", "urgent"] }

models:
  strategic: { model: "deepseek/deepseek-v4-pro" }
  bulk:      { model: "deepseek/deepseek-v4-flash" }
  critic:    { model: "deepseek/deepseek-v4-flash" }

generation:
  target_size: 1000
  meta_prompts: false        # the combined path: cheaper, see Batching
  record_batch_size: 5
  subject: auto              # a list of situations per subject node, for variety
  concurrency: 40
```

Start from the closest file in `examples/` (see [Examples](../examples.md)) or from
`examples/template.yaml`, which lists every key at its default. Run `simula validate` after every edit:
it makes no model calls, and a misspelled key is an error.

## The description

The description is read by every prompt, so it shapes everything. Write it like a spec, not a story:
what a record is, what it is for, and the rules it must follow.

Leave out anecdotes, example incidents, sample ids, named people and "recent events". Whatever is
concrete in the description gets copied into records. In one benchmark, a feature name mentioned once in
the description appeared in 26% of tickets and an error code in 5%; deleting the line took both to zero.
If the model needs facts to stay consistent, such as plan names, limits or units, give those, and no
incidents.

## JSON or free text

With a `schema`, every record is a JSON object that must validate against it; see the supported subset
in [Configuration](../reference/configuration.md#schema). Without one (`schema: null`, or no `schema`
key), every record is a plain string, which suits stories, emails, documents and other free text.

For JSON records, decide how absent information is represented before you generate. When the model you
train must always emit a fixed schema, present-but-null fields teach it to acknowledge absence. When each
row has its own schema, omitting absent fields is cleaner. Keep fields atomic and normalized:
`{"salary_min": 90000, "salary_max": 120000, "salary_currency": "USD"}`, never
`{"salary_range": "90k–120k/yr"}`. Put these rules in the schema and in a
[prompt module](custom-prompts.md), and have the critic reject violations.

## Factors

The taxonomy is your coverage, and choosing its factors is the decision that matters most. You can let
the model discover them (`taxonomy.factors: null`), but when the domain has an obvious dominant axis, give
it yourself:

```yaml
taxonomy:
  factors:
    - {name: category, description: "What the ticket is about: billing, technical, account or feature request"}
    - {name: priority, description: "How urgent the ticket is, judged from its facts"}
    - {name: customer_tier, description: "Free, paid or enterprise account"}
    - {name: ticket_style, description: "How the ticket is written: one-liner, detailed report, reply in a thread"}
```

Axes that usually matter:

| Kind of dataset | Axes that usually matter most |
|---|---|
| Extraction (text to JSON) | document length, register, density of extractable facts, how many requested fields are absent, distractors, domain |
| Natural-language query to structure | intent, query length and specificity, number of constraints, ambiguity, domain |
| Classification | class balance including hard and ambiguous cases, text length, domain, near-miss pairs |
| QA | question type, reasoning depth, answer length, domain, answerable vs unanswerable |
| Free text | genre, tone, length, point of view |

Keep each factor about one thing. A factor that packs several ideas into one tree gives each record only
one of them, and a factor whose deep leaves describe another factor's territory produces contradictions.

When a dataset needs a case at a controlled rate (absent values, hard negatives, unanswerable questions),
make it a factor so it appears at the rate you set with weights.

## Output length

If the length of the records matters, and it usually does (a model trained on 70-word examples writes
70-word answers), do not leave it to the writer. A model left alone settles on one length: one story set
came out at about 900 words for nearly every story although the request was a mix of flash, short and
medium. Records written several per call also come out shorter (see [Batching](batching.md)).

Make length a factor of its own, written by hand, with bands the text must match:

```yaml
taxonomy:
  factors:
    - name: "length"
      description: "How long the record is, which the text must actually match. Bands by word count:
        one line (under 25 words), short (25-80), medium (80-200), long (200-400)."
```

After the taxonomy is built, set the bands' `weight`s in `taxonomy.json` to the mix you want, and say in
the writer prompt that the band is binding. With `examples/stories_freetext_v2.yaml`, which does this,
stories landed on their band's target in every run.

## Settings worth changing from the defaults

| Setting | Suggested | Why |
|---|---|---|
| `generation.meta_prompts`, `record_batch_size` | `false`, `5` for short records | The cheaper path, with records as faithful to their mixes. See [Batching](batching.md). |
| `generation.subject` | `auto` | Situations, the largest measured variety gain, for about 4% extra cost. Check the pick in `taxonomy.json`. |
| `generation.concurrency` | 40–80 | The default of 4 is slow. Lower it if the provider rate-limits. |
| `generation.overgenerate_ratio` | 1.1–1.25 | Strong models accept about 99% of records; cheap models drop or garble some batch items. Let the pilot set it. |
| `models.bulk.max_tokens` | room for a whole batch | A batch reasons before it answers; a reply cut off at the limit loses the batch. |
| `taxonomy.depth` | `2` | `1` for a narrow dataset, `3` only for a broad domain. |
| `complexity_ratio` | `0` when "harder" means nothing | Saves one call per complexified record. |

Leave `taxonomy.best_of_n: 1` and `taxonomy.critic: true`: turning the taxonomy critic off halves the
build cost but gave a much thinner tree.

Every field is described in [Configuration](../reference/configuration.md).
