"""Offline input-token comparison: inline specs (combined_batch_prompt) vs the NODES/ID table
(combined_table_batch_prompt) for the combined path. No model calls.

usage: python analysis/prompt_tokens.py CONFIG [--attempts 500] [--batch 5] [--calibrate RUN_DIR]

Mixes are drawn exactly as generation draws them (_sample_lineage, seeded per attempt), from the run's
taxonomy.json/strategies.json. Batches are formed two ways: in attempt order (random, what the pipeline
does) and sorted by the first factor's path (mixes sharing a subject branch batched together).
Counts use tiktoken o200k as a proxy; --calibrate scales them to the provider's own counts using the
logged prompt sizes of a real combined run (in_tokens vs the proxy count of the same prompt).
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path

import tiktoken

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from simula.config import load_config  # noqa: E402
from simula.generate import _sample_lineage  # noqa: E402
from simula.prompts import mix_text, nodes_table  # noqa: E402
from simula.utils import artifact_path, read_json, read_jsonl  # noqa: E402

ENC = tiktoken.get_encoding("o200k_base")


def tokens(text: str) -> int:
    return len(ENC.encode(text))


def inline_specs(items: list[dict]) -> str:
    # The spec block combined_batch_prompt (and the e-commerce override) writes.
    return "\n\n".join(f"{item['id']}:\n{mix_text(item['mix'])}" for item in items)


def json_specs(items: list[dict]) -> str:
    # The format the meta-prompt prompts used before the text form: the raw mix as JSON.
    return "\n\n".join(f"{item['id']}:\n{json.dumps(item['mix'], ensure_ascii=False)}" for item in items)


def batches(mixes: list[dict], size: int, key=None) -> list[list[dict]]:
    ordered = sorted(mixes, key=key) if key else mixes
    groups = [ordered[start : start + size] for start in range(0, len(ordered), size)]
    return [[{**item, "id": f"r{position + 1}"} for position, item in enumerate(group)] for group in groups]


def calibration(run_dir: Path) -> float:
    # provider in_tokens / proxy tokens over the run's generate calls (system + user prompt).
    calls = [c for c in read_jsonl(run_dir / "llm_calls.jsonl", tolerant=True) if c["task"] == "generate"]
    return statistics.median(c["in_tokens"] / tokens(c["system"] + "\n" + c["prompt"]) for c in calls)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("config")
    parser.add_argument("--attempts", type=int, default=500)
    parser.add_argument("--batch", type=int, default=5)
    parser.add_argument("--calibrate", type=Path)
    args = parser.parse_args()

    cfg = load_config(args.config)
    taxonomy = read_json(artifact_path(cfg.output_dir, "taxonomy"))
    strategies = read_json(artifact_path(cfg.output_dir, "strategies"))["strategies"]
    mixes = []
    for index in range(args.attempts):
        lineage = _sample_lineage(cfg, taxonomy, strategies, index, 1)
        mixes.append({"mix": lineage["taxonomy_mix"]})

    # Static part of the real prompt (everything but the spec block), from the configured prompt module.
    sample = batches(mixes, args.batch)[0]
    full = cfg.prompts.combined_batch_prompt(cfg.description, cfg.schema, sample, [])
    static = tokens(cfg.prompts.SYSTEM_JSON) + tokens(full) - tokens(inline_specs(sample))
    scale = calibration(args.calibrate) if args.calibrate else 1.0

    first_factor = lambda item: tuple(item["mix"][0]["path"])  # noqa: E731 - sort key: subject branch
    report = {"attempts": args.attempts, "batch_size": args.batch, "static_prompt_tokens": round(static * scale),
              "calibration": round(scale, 3)}
    for grouping, key in (("random", None), ("by_subject_branch", first_factor)):
        groups = batches(mixes, args.batch, key)
        raw_json = sum(tokens(json_specs(group)) for group in groups) * scale
        inline = sum(tokens(inline_specs(group)) for group in groups) * scale
        table = sum(tokens(nodes_table(group)) for group in groups) * scale
        total_inline = inline + static * scale * len(groups)
        total_table = table + static * scale * len(groups)
        report[grouping] = {
            "spec_tokens_per_record": {"json": round(raw_json / args.attempts), "inline": round(inline / args.attempts),
                                       "table": round(table / args.attempts)},
            "spec_saving_table_vs_inline": f"{1 - table / inline:.0%}",
            "spec_saving_inline_vs_json": f"{1 - inline / raw_json:.0%}",
            "prompt_tokens_per_record": {"inline": round(total_inline / args.attempts), "table": round(total_table / args.attempts)},
            "prompt_saving_table_vs_inline": f"{1 - total_table / total_inline:.0%}",
            "distinct_nodes_per_batch": round(statistics.mean(
                len({tuple(e["path"]) for item in group for e in item["mix"]}) for group in groups), 2),
        }
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
