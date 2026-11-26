# Sharing on the Hugging Face Hub

`simula push` uploads a finished run to the [Hugging Face Hub](https://huggingface.co/datasets) as a
dataset, with a dataset card that says how it was made.

```bash
pip install "simula-gen[hub]"
echo 'HF_TOKEN=hf_...' >> .env          # or run `hf auth login` once
simula push my_dataset.yaml --repo your-name/my-dataset --dry-run   # look at the card first
simula push my_dataset.yaml --repo your-name/my-dataset --license mit
```

The token needs write access. Like the provider key, it is read from `.env` in the directory you run
from; without it, `simula push` uses the session saved by `hf auth login`.

## What is uploaded

| File on the Hub | From |
|---|---|
| `README.md` | The dataset card, written by `push`. |
| `data/train.jsonl` | `dataset.evaluated.jsonl` when `evaluate` has run, else `dataset.final.jsonl`. Rows keep their lineage (see [Artifacts](../reference/artifacts.md#dataset-rows)). |
| `taxonomy.json`, `strategies.json` | The plan the records were sampled from. |
| `eval_report.json` | The evaluation report, when there is one. |

`llm_calls.jsonl`, `run_state.json`, `dataset.raw.jsonl` and `dataset.accepted.jsonl` stay on your
machine. `--dry-run` writes exactly what would be uploaded to `hub/` in the output directory, so you
can read the card and the files before anything leaves your machine.

:::{warning}
The card quotes your dataset description, and the dataset holds every generated record with its
meta-prompt and critic verdicts. Read the dry run before pushing, and add `--private` for anything
that should not be public.
:::

## The card

The card's metadata carries three tags: `simula`, `simula-<version>` and `synthetic`. The Hub uses the
`simula` tag to name the library that made the dataset, and the tags make datasets made with `simula`
easy to find. The card also lists the models per role, the record count, the factors of variation and
what evaluation removed, and shows how to load the dataset:

```python
from datasets import load_dataset

ds = load_dataset("your-name/my-dataset", split="train")
```

Edit the card on the Hub afterwards if you want to add more; a later `push` replaces it.

## Records with changing fields

The Hub's dataset viewer infers one column type per field. If a field holds a string in some records
and a number in others, or records carry different nested keys, the viewer may not render them. The file
itself is unchanged JSONL.
