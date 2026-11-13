# Examples

The `examples/` directory of the repository holds complete configs. Copy the one closest to your
dataset and change it. The two fake-model examples run offline with no key; the others make real calls
through OpenRouter.

| Example | Copy it for | Path | Models |
|---|---|---|---|
| `basic_qa` | a smoke test, the simplest schema run | meta-prompt | fake |
| `cat_stories_freetext` | a smoke test of free-text mode | meta-prompt | fake |
| `query_extraction_gemini` | natural-language query to structured JSON, fixed schema | meta-prompt | Gemini 3 Flash |
| `job_extraction` | text to atomic JSON, absent fields omitted, a strict critic | meta-prompt | DeepSeek V4 Flash |
| `ecommerce_search_extraction` | a schema that varies per row; the reference for the combined path | combined, 5 per call | DeepSeek V4 Pro + Flash |
| `support_tickets` | classification with several labels at target shares | combined, 5 per call | GLM 5.3 Flash |
| `stories_freetext_v2` | long free text, rare forms held at set rates by weights | meta-prompt, batched | Kimi K3 + GPT-5.6 Luna |

The copy-me skeleton with every key at its default is in [Configuration](reference/configuration.md#full-template).
Examples with a prompt module (`<name>_prompts.py` next to the YAML) show how to enforce rules the config
cannot express; see [Custom prompts](guides/custom-prompts.md).

## `basic_qa`

Question-answer pairs about machine learning, with fake models for every role. The fastest way to check
an install or a config change end to end.

```bash
simula run examples/basic_qa.yaml
```

```{literalinclude} ../examples/basic_qa.yaml
:language: yaml
```

## `cat_stories_freetext`

Short stories with `schema: null`, so each record is a plain string. Fake models; runs offline.

```{literalinclude} ../examples/cat_stories_freetext.yaml
:language: yaml
```

## `query_extraction_gemini`

Natural-language queries turned into a fixed extraction schema (intent, search terms, attributes,
filters, exclusions, ambiguities), with `query_domain` as the subject.

```{literalinclude} ../examples/query_extraction_gemini.yaml
:language: yaml
```

## `job_extraction`

Job postings turned into a `{schema, text, extraction}` envelope: each row carries its own schema, the
posting, and an extraction with narrow atomic fields, omitting what the posting does not say. The prompt
module (`job_extraction_prompts.py`) enforces the field rules and makes the critic strict about them.

```{literalinclude} ../examples/job_extraction.yaml
:language: yaml
```

## `ecommerce_search_extraction`

Shopper searches turned into atomic, database-queryable JSON whose fields vary from row to row (from 3 to
20 fields, one level of nesting). A depth-4 tree, a stronger model for `strategic` and a cheap one for
`bulk` and `critic`, the combined path at 5 records per call. It is the setup behind the pilot numbers in
[Batching](guides/batching.md) and [Cost](guides/cost.md); its prompt module overrides every prompt the
combined path uses.

```{literalinclude} ../examples/ecommerce_search_extraction.yaml
:language: yaml
```

## `support_tickets`

Support tickets for triage, labelled with a category and a priority at target shares. The setup from the
November 2026 benchmark: combined path, 5 per call, `category` as the subject, label levels pinned in the
tree, and a critic focused on whether the labels fit the text. Its prompt module covers both generation
paths and renders situations.

```{literalinclude} ../examples/support_tickets.yaml
:language: yaml
```

## `stories_freetext_v2`

Long free-text stories. Rare narrative forms are kept at set rates by taxonomy weights, and length is a
hand-written factor with word-count bands. The meta-prompt path, 10 mixes per meta-prompt call, `genre` as
the subject. (`stories_freetext.yaml` is the earlier version, kept for reference.)

```{literalinclude} ../examples/stories_freetext_v2.yaml
:language: yaml
```
