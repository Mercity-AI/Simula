# CLI

```text
simula <command> CONFIG.yaml [options]
```

Run it from the directory that holds your `.env` file: the API key is read from `./.env`, and
relative paths in the config (`project.output_dir`) resolve from there too. Without installing the
package, `python -m simula.cli <command> CONFIG.yaml` does the same from a source checkout.

## Commands

| Command | What it does | Model calls |
|---|---|---|
| `validate` | Loads and validates the config: fields, the schema subset, the prompt module, the output path. Prints the output directory and format. | None |
| `taxonomy` | Builds the taxonomy and writes `taxonomy.json`, then applies `taxonomy.review_mode`. | `strategic` |
| `generate` | Loads or builds the taxonomy, strategies, situations and meta-prompts, then writes, validates and critiques records and writes the dataset files. Does not evaluate. | all roles |
| `evaluate` | Reads `dataset.final.jsonl`, runs dedupe, decontamination, coverage and the optional scores, and writes `dataset.evaluated.jsonl` and `eval_report.json`. | only for `reassign` coverage and complexity scoring |
| `run` | `generate`, then `evaluate`. | all roles |

:::{warning}
`simula taxonomy` always builds a new tree and overwrites `taxonomy.json`. To build the tree once and
keep your edits, use `simula generate CONFIG.yaml --stop-after taxonomy` instead: `generate` and `run`
reuse an existing `taxonomy.json`.
:::

## Options

| Option | Commands | What it does |
|---|---|---|
| `--quiet` | all | Hide live spinners, progress bars and the taxonomy build log. Phase markers and the final summary still print. |
| `--resume` / `--no-resume` | `generate`, `run` | Resume is the default: attempts already in `dataset.raw.jsonl` are skipped. `--no-resume` empties the dataset files and makes every attempt again; the taxonomy, strategies, situations and meta-prompts are still reused. |
| `--stop-after STAGE` | `generate`, `run` | Halt once a stage's artifact is written so you can edit it: `taxonomy`, `strategies` or `meta_prompts`. Overrides `generation.stop_after`; `none` cancels a stop set in the config. |

## Resuming

`generate` and `run` reuse every artifact that already exists in `project.output_dir`
(`taxonomy.json`, `strategies.json`, `situations.jsonl`, `meta_prompts.jsonl`) and skip attempts
already in `dataset.raw.jsonl`. That makes a stopped, crashed or stalled run cheap to restart: run
the same command again and only the missing attempts are made.

`run_state.json` stores a fingerprint of the inputs that would change what an attempt produces. If
they changed since the last run, resume stops with an error and asks for `--no-resume`. The knobs you
can change freely between runs are listed in [Configuration](configuration.md#generation). To
regenerate one stage, delete its file and rerun.

## Exit status and output

- `0` on success, including a planned halt (`--stop-after`, `review_mode: write_then_edit`).
- `1` on any error: an invalid config, a resume fingerprint mismatch, a provider error that outlived
  its retries, or answering no at the `interactive_confirm` prompt. The error is printed.

Every command ends by writing `cost_summary.json` for that command and, if it made model calls,
printing a line like:

```text
Cost summary: calls=1243 in_tokens=2104981 out_tokens=1877410 (reasoning=1402233) cost=$0.4012
```

`cost_summary.json` is overwritten by each command. For the cost of a run spread over several
commands, add up `llm_calls.jsonl`; see [Cost](../guides/cost.md).
