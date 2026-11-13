<p align="center">
  <img src="assets/hero.png" alt="Simula" width="640">
</p>

<p align="center">
  <img alt="Python" src="https://img.shields.io/badge/python-%E2%89%A53.11-blue">
  <a href="LICENSE"><img alt="License: MIT" src="https://img.shields.io/badge/license-MIT-green"></a>
  <img alt="Version" src="https://img.shields.io/badge/version-0.1.0-blue">
  <img alt="Status" src="https://img.shields.io/badge/status-pilot-orange">
  <a href="https://arxiv.org/abs/2603.29791"><img alt="Paper" src="https://img.shields.io/badge/paper-arXiv%3A2603.29791-b31b1b"></a>
  <a href="https://github.com/Mercity-AI"><img alt="Built by Mercity" src="https://img.shields.io/badge/built%20by-Mercity-1f6feb"></a>
</p>

# simula

`simula` is a command-line tool for generating synthetic datasets with a language model: JSON records
that match a schema, or free text. You describe the dataset in a YAML file; `simula` maps out what it
should cover, samples from that map in code, has a model write each record, has another check it, and
writes everything to plain JSON and JSONL files you can read and edit.

- **A taxonomy of what to cover**, built by a model from your description, that you can read, edit and
  weight.
- **Code draws each record's ingredients** in the shares you set, so coverage and label balance are
  yours, not the model's.
- **Situations keep records apart**: records that share a topic each get their own concrete case.
- **Every record is checked** against your schema and by a critic model, and carries its lineage.
- **Every model call is logged** with its prompt, reply, tokens and cost.

## Install

```bash
pip install simula-gen
```

The command is `simula`. Python 3.11+. For the latest code, install from a checkout with
`pip install -e .`; see [Installation](docs/installation.md).

## Quick start

From a checkout, an offline run with fake models (no key, no cost):

```bash
simula run examples/basic_qa.yaml
```

A real run reads its key from `.env` in the directory you run from:

```bash
echo 'OPENROUTER_API_KEY=sk-or-...' > .env
simula generate my_dataset.yaml --stop-after strategies   # build the plan, then stop to review it
simula generate my_dataset.yaml                           # write the records
simula evaluate my_dataset.yaml                           # dedupe, coverage, diversity
```

The [Quickstart](docs/quickstart.md) walks through both.

## Documentation

The full documentation is in [`docs/`](docs/index.md), built with Sphinx for Read the Docs.

- **Getting started:** [Installation](docs/installation.md), [Quickstart](docs/quickstart.md)
- **Concepts:** [How a run works](docs/concepts/pipeline.md), [Taxonomy](docs/concepts/taxonomy.md),
  [Strategies and sampling](docs/concepts/sampling.md),
  [Situations and meta-prompts](docs/concepts/situations-and-meta-prompts.md),
  [Model roles](docs/concepts/model-roles.md), [Evaluation](docs/concepts/evaluation.md)
- **Guides:** [Writing a config](docs/guides/writing-a-config.md),
  [Reviewing the design](docs/guides/reviewing-the-design.md), [Batching](docs/guides/batching.md),
  [Cost](docs/guides/cost.md), [Custom prompts](docs/guides/custom-prompts.md),
  [Providers and models](docs/guides/providers-and-models.md),
  [Running a pilot](docs/guides/running-a-pilot.md)
- **Reference:** [CLI](docs/reference/cli.md), [Configuration](docs/reference/configuration.md),
  [Artifacts](docs/reference/artifacts.md)
- [Examples](docs/examples.md), [Troubleshooting](docs/troubleshooting.md)

For agents: [`SKILL.md`](SKILL.md) is the playbook for generating a dataset with `simula`, and
[`AGENTS.md`](AGENTS.md) is the guide to changing the code.

## Development

```bash
pip install -e ".[dev]"
pytest -q
```

To preview the docs locally:

```bash
pip install -r docs/requirements.txt sphinx-autobuild
sphinx-autobuild docs docs/_build/html
```

## License

MIT. See [LICENSE](LICENSE).
