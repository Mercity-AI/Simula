<p align="center">
  <img src="https://raw.githubusercontent.com/Mercity-AI/Simula/main/assets/hero.png" alt="Simula" width="640">
</p>

<p align="center">
  <img alt="Python" src="https://img.shields.io/badge/python-%E2%89%A53.11-blue">
  <a href="https://github.com/Mercity-AI/Simula/blob/main/LICENSE"><img alt="License: MIT" src="https://img.shields.io/badge/license-MIT-green"></a>
  <a href="https://pypi.org/project/simula-gen/"><img alt="PyPI" src="https://img.shields.io/pypi/v/simula-gen"></a>
  <img alt="Status" src="https://img.shields.io/badge/status-pilot-orange">
  <a href="https://simula.readthedocs.io/en/latest/"><img alt="Docs" src="https://img.shields.io/readthedocs/simula"></a>
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
`pip install -e .`; see [Installation](https://simula.readthedocs.io/en/latest/installation.html).

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

The [Quickstart](https://simula.readthedocs.io/en/latest/quickstart.html) walks through both.

## Documentation

The full documentation is at **[simula.readthedocs.io](https://simula.readthedocs.io/en/latest/)**.

- **Getting started:** [Installation](https://simula.readthedocs.io/en/latest/installation.html),
  [Quickstart](https://simula.readthedocs.io/en/latest/quickstart.html)
- **Concepts:** [How a run works](https://simula.readthedocs.io/en/latest/concepts/pipeline.html),
  [Taxonomy](https://simula.readthedocs.io/en/latest/concepts/taxonomy.html),
  [Strategies and sampling](https://simula.readthedocs.io/en/latest/concepts/sampling.html),
  [Situations and meta-prompts](https://simula.readthedocs.io/en/latest/concepts/situations-and-meta-prompts.html),
  [Model roles](https://simula.readthedocs.io/en/latest/concepts/model-roles.html),
  [Evaluation](https://simula.readthedocs.io/en/latest/concepts/evaluation.html)
- **Guides:** [Writing a config](https://simula.readthedocs.io/en/latest/guides/writing-a-config.html),
  [Reviewing the design](https://simula.readthedocs.io/en/latest/guides/reviewing-the-design.html),
  [Batching](https://simula.readthedocs.io/en/latest/guides/batching.html),
  [Cost](https://simula.readthedocs.io/en/latest/guides/cost.html),
  [Custom prompts](https://simula.readthedocs.io/en/latest/guides/custom-prompts.html),
  [Providers and models](https://simula.readthedocs.io/en/latest/guides/providers-and-models.html),
  [Running a pilot](https://simula.readthedocs.io/en/latest/guides/running-a-pilot.html),
  [Sharing on the Hugging Face Hub](https://simula.readthedocs.io/en/latest/guides/sharing-on-the-hub.html)
- **Reference:** [CLI](https://simula.readthedocs.io/en/latest/reference/cli.html),
  [Configuration](https://simula.readthedocs.io/en/latest/reference/configuration.html),
  [Artifacts](https://simula.readthedocs.io/en/latest/reference/artifacts.html)
- [Examples](https://simula.readthedocs.io/en/latest/examples.html),
  [Troubleshooting](https://simula.readthedocs.io/en/latest/troubleshooting.html)

For agents: [`SKILL.md`](https://github.com/Mercity-AI/Simula/blob/main/SKILL.md) is the playbook for generating a dataset with `simula`, and
[`AGENTS.md`](https://github.com/Mercity-AI/Simula/blob/main/AGENTS.md) is the guide to changing the code.

## Development

```bash
pip install -e ".[dev]"
pytest -q
```

The docs source is in [`docs/`](https://github.com/Mercity-AI/Simula/tree/main/docs) (Markdown, built with Sphinx); Read the Docs publishes `latest`
from `main`. To preview locally:

```bash
pip install -r docs/requirements.txt sphinx-autobuild
sphinx-autobuild docs docs/_build/html
```

## License

MIT. See [LICENSE](https://github.com/Mercity-AI/Simula/blob/main/LICENSE).
