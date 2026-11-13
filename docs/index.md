# simula

`simula` is a command-line tool for generating synthetic datasets with a language model: JSON records
that match a schema, or free text. You describe the dataset in a YAML file; `simula` maps out what it
should cover, samples from that map in code, has a model write each record, has another check it, and
writes everything to plain JSON and JSONL files you can read and edit.

It is inspired by the Simula approach to synthetic data
([arXiv:2603.29791](https://arxiv.org/abs/2603.29791)): map the space first, then sample from it.

## Why

Ask a model for 1,000 examples and you get 1,000 variations of the same few things: it keeps returning
its most typical answer. `simula` takes the choice of *what each record is about* away from the model:

- **A taxonomy of what to cover.** A model breaks your description into factors of variation (topic,
  tone, difficulty, length…) and grows each into a tree. You can read and edit the tree, and weight its
  branches.
- **Code draws the ingredients.** Every record gets one node per factor, drawn by code in the shares you
  set, so coverage and label balance are under your control, not the model's.
- **Situations keep records apart.** Records that share a topic each get their own concrete situation,
  listed side by side, so they do not all tell the topic's most obvious story.
- **Every record is checked.** It must match your schema, and a critic model accepts it, sends it back
  for a fix, or corrects it.
- **Every record carries its lineage.** Each row records the ingredients it was built from, the
  instruction it was written from and the critic's verdicts.
- **Every call is logged.** Each model call is appended to `llm_calls.jsonl` with its prompt, reply,
  tokens and cost as soon as it returns.

## How a run looks

```bash
simula validate my_dataset.yaml                        # check the config, no model calls
simula generate my_dataset.yaml --stop-after strategies   # build the plan, then stop to review it
simula generate my_dataset.yaml                        # write the records
simula evaluate my_dataset.yaml                        # dedupe, coverage, diversity
```

The result is `dataset.final.jsonl` in the run's output directory, next to the taxonomy, the strategies
and the logs. [How a run works](concepts/pipeline.md) walks through every stage.

## What it is not

`simula` is a small, file-based tool at pilot stage. It runs on one machine with asyncio concurrency, not
a distributed queue. It speaks the OpenAI-compatible chat-completions API (OpenRouter by default) and
nothing else. It has no web UI, no database, no fine-tuning harness, and no multi-turn or multimodal
generation yet.

## Where to go next

- [Installation](installation.md), then the [Quickstart](quickstart.md): an offline run in a minute, then
  your first real one.
- [Writing a config](guides/writing-a-config.md) and [Batching](guides/batching.md) before your first
  real dataset.
- [Configuration](reference/configuration.md) for every field.

```{toctree}
:hidden:
:caption: Getting started

installation
quickstart
```

```{toctree}
:hidden:
:caption: Concepts

concepts/pipeline
concepts/taxonomy
concepts/sampling
concepts/situations-and-meta-prompts
concepts/model-roles
concepts/evaluation
```

```{toctree}
:hidden:
:caption: Guides

guides/writing-a-config
guides/reviewing-the-design
guides/batching
guides/cost
guides/custom-prompts
guides/providers-and-models
guides/running-a-pilot
```

```{toctree}
:hidden:
:caption: Reference

reference/cli
reference/configuration
reference/artifacts
```

```{toctree}
:hidden:
:caption: More

examples
troubleshooting
changelog
```
