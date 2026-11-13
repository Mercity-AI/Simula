"""Experimental: ask a model who should pick each taxonomy factor (code or the writer model), and how deep.

usage: python analysis/factor_roles.py CONFIG --taxonomy runs/X/taxonomy.json --out runs/pilot/factor_roles/NAME
           [--model MODEL] [--batch 5] [--runs 3] [--free-text]

One strategic-style call per run; writes roles_<n>.json (the model's answer) and prompt.txt to --out and
prints a per-factor summary across runs. Not part of the simula package and not wired into generation:
`code_to_level` is applied in pilots by pruning a taxonomy copy (analysis/pilot_configs.py).
"""

from __future__ import annotations

import argparse
import asyncio
import collections
import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from simula.config import load_config  # noqa: E402
from simula.models import ModelRouter  # noqa: E402
from simula.prompts import schema_text  # noqa: E402
from simula.taxonomy import level_options  # noqa: E402

PROMPT = """
You are helping set up a synthetic-data pipeline. For each factor of the taxonomy below, decide who should
choose its value for each record: code, or the writer model. A person reviews your answer before any data
is generated.

## Background

Records are written in batches of {batch_size}. Each factor is a tree: broad options at level 1, more
specific ones at each level below, and the most specific ones (the leaves) at the bottom. For every
record, one value has to be chosen from each factor's tree. There are two possible choosers.

Code picks at random, using the shares in the tree, without looking at the other factors.
- Strength: across the dataset every option appears as often as intended, including the rare ones, and we
  know for certain which value each record got.
- Weakness: because it does not look at the other factors, it can pick a value that cannot go with them.

The writer model picks while it writes the record, so it sees everything else about that record.
- Strength: what it picks fits the record.
- Weakness: it favours the typical options, and it favours the same ones in every batch. Across the dataset
  the common options take over and the rare ones vanish. What it says it picked is also not always what
  it wrote.

## The three decisions

For each factor, choose one:

1. "code_to_leaves": code picks the most specific option (a leaf). The writer uses it as given.
2. "code_to_level": code picks a broader option, at the level you name. The writer then chooses something
   specific beneath that option that suits the record.
3. "model": code picks nothing for this factor. The writer chooses from the options at the level you name,
   and reports what it chose.

Illustration, from an unrelated shopping dataset: a factor "which attribute the shopper cares about" has
broad options such as "technical specifications" and leaves such as "screen width in pixels". With
"code_to_leaves", code could hand "screen width in pixels" to a record about engine oil, which makes no
sense. With "code_to_level" at the broad level, code hands over "technical specifications" and the writer
picks a specification that engine oil actually has.

Everything below the level code picks at is chosen by the writer, so it drifts toward the typical. Let
code pick as deep as it safely can, and move up only as far as you have to.

## How to decide

Step 1. Does the value need to be known or controlled?

- Known: we must be certain which value each record has. This is so when the factor is the main subject
  of the record, when its value appears in the record's output, or when it will be used as a label.
- Controlled: the value must appear in particular proportions across the dataset. This is so when the
  dataset description, the guidance or the factor's own description asks for shares, an even spread or
  full coverage, or when the factor has rare options that matter (unusual forms, edge cases, hard cases).

If either is true, code must pick this factor, at least down to the level where the options that need to
be known or controlled are listed: go to step 2. If neither is true, go to step 3.

Step 2. Find the deepest level that is safe.

Picture options of this factor next to values of the other factors, and judge each combination:

- fits: an ordinary record.
- unusual: uncommon, but a real record could have both.
- impossible: no sensible record could have both.

Unusual combinations are good. They are the variety a writer would never choose by itself, and the writer
is already told to soften an ingredient that fits badly. Only impossible combinations are a problem. Do
not treat "unusual" as "impossible".

Call an option a misfit when it would be impossible next to a large part of some other factor's values,
roughly a third of them or more. An option that only makes sense for one kind of record is a misfit in a
dataset where most records are of other kinds. For each level, estimate the misfit share: of the options
at that level (judge from the ones shown), what fraction are misfits?

Start at the deepest level and work upward, one level at a time:

- Misfit share of about one in ten or less: the level is safe. Stop here.
- Between about one in ten and one in three: the level is safe as long as code never draws those few
  options together with the values they cannot go with. Stop here, and list those pairs in "exclusions".
- More than about one in three: too many options at this level depend on the rest of the record. Go up
  one level and judge again.

Stop at the first safe level. If it is the deepest level, choose "code_to_leaves"; otherwise choose
"code_to_level" at that level. The fractions are guides, not exact cut-offs. The point is the proportion:
a handful of awkward options among many is handled by exclusions, while a level where a large part of the
options only suit some records is too deep.

Deep options often become misfits because they fix something another factor is supposed to decide. If two
factors clash mainly with each other, move up only the one that matters less to the record and keep the
other one specific.

Step 3. Only for factors that are neither known nor controlled.

Ask whether even the broad options of this factor would often be impossible next to the other factors'
values, because the right value depends on the rest of the record. If so, and the factor's block below
says the model may pick at some level, choose "model" at that level. Otherwise decide as in step 2.

When unsure, choose code. A wrong pick by code is one odd record that someone can see and fix. A wrong
pick by the writer is a bias across the whole dataset that nobody sees. Expect most factors to end with
code.

## About the examples in this prompt

Every example here is an illustration taken from some other dataset. The examples show the kind of
reasoning wanted, not the answers. Your factors are different, so reason about each one from its own
description and its own options, and do not copy a verdict because a factor resembles an example.

## The dataset

Description:
{description}

Record format:
{record_format}

## The factors

{factor_blocks}

## Answer

Return JSON with one entry per factor, in the order above. Under "levels", judge the deepest level
first and then each level above it, stopping at the first safe one. Give two to four example combinations
per level, using real options from the blocks above.

{{"factors": [{{
  "factor": "name",
  "known_or_controlled": {{"answer": "known | controlled | both | neither", "why": "one sentence"}},
  "levels": [{{
    "level": 4,
    "examples": [{{"option": "an option at this level", "with": "other_factor: one of its options", "verdict": "fits | unusual | impossible", "why": "a few words"}}],
    "misfit_share": "about 60%",
    "safe": "yes | with exclusions | no"
  }}],
  "decision": "code_to_leaves | code_to_level | model",
  "level": 2,
  "exclusions": [["an option of this factor", "other_factor: one of its options"]],
  "reason": "one or two sentences",
  "note": ""
}}]}}

"level" is a number: the level code picks at ("code_to_level"), the factor's deepest level
("code_to_leaves"), or the level whose options are offered to the writer ("model"). "exclusions" lists
pairs that must never be drawn together and may be empty. Use "note" only to flag a problem with the
factor itself, for example that it mixes several unrelated things or repeats another factor; otherwise
leave it empty.
""".strip()


def factor_block(taxonomy: dict, factor: dict, batch_size: int, rng: random.Random) -> str:
    """One factor as the prompt shows it: description, options per level (all, or a sample), where the
    model may pick (few enough options for one batch to use them all, none rarer than one per batch)."""

    def short(text: str, limit: int = 170) -> str:
        return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"

    lines = [f"### {factor['name']}", f"Description: {factor.get('description', '')}"]
    allowed = []
    level = 1
    while True:
        options = level_options(taxonomy, factor["name"], level)
        if not options or max(option["level"] for option in options) < level:
            break
        if len(options) <= batch_size and min(option["share"] for option in options) >= 1 / batch_size:
            allowed.append(level)
        shown = options if len(options) <= 12 else rng.sample(options, 6)
        label = "all of them" if len(shown) == len(options) else f"{len(shown)} at random"
        lines.append(f"Level {level}: {len(options)} options, smallest share {min(o['share'] for o in options):.1%} ({label}):")
        for option in shown:
            path = " > ".join(option["path"][1:])
            lines.append(f"- {path} ({option['share']:.1%}): {short(option['description'])}")
        level += 1
    lines.append(f"Deepest level: {level - 1}.")
    lines.append("The model may pick at: " + (", ".join(f"level {l}" for l in allowed) if allowed else "no level") + ".")
    return "\n".join(lines)


def build_prompt(taxonomy: dict, description: str, schema: dict | None, batch_size: int, seed: int = 0) -> str:
    rng = random.Random(seed)
    blocks = "\n\n".join(factor_block(taxonomy, factor, batch_size, rng) for factor in taxonomy["factors"])
    record_format = "Free-form text." if schema is None else f"A JSON object matching this JSON Schema:\n{schema_text(schema)}"
    return PROMPT.format(batch_size=batch_size, description=description, record_format=record_format, factor_blocks=blocks)


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("config", type=Path, help="config supplying the provider, key and strategic sampling")
    parser.add_argument("--taxonomy", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--model")
    parser.add_argument("--batch", type=int, default=5)
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--free-text", action="store_true", help="the dataset is schema-free (ignore the config's schema)")
    args = parser.parse_args()

    cfg = load_config(args.config)
    taxonomy = json.loads(args.taxonomy.read_text())
    data = cfg.data
    data["project"]["output_dir"] = str(args.out)  # llm_calls.jsonl for these calls lands next to the answers
    if args.model:
        data["models"]["strategic"]["model"] = args.model
    # The answer covers every factor after its reasoning (~16K tokens for 7 factors): leave room for both.
    data["models"]["strategic"]["max_tokens"] = 65536
    router = ModelRouter(data)
    prompt = build_prompt(taxonomy, taxonomy.get("description") or cfg.description, None if args.free_text else cfg.schema, args.batch)
    (args.out / "prompt.txt").write_text(prompt)

    async def one(run: int) -> dict:
        answer = await router.complete_json("strategic", prompt, system=cfg.prompts.SYSTEM_JSON, task="strategy")
        (args.out / f"roles_{run}.json").write_text(json.dumps(answer, indent=2, ensure_ascii=False))
        return answer

    answers = await asyncio.gather(*(one(run) for run in range(args.runs)), return_exceptions=True)
    await router.flush_logs()
    summary: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    for answer in answers:
        if isinstance(answer, Exception):
            print("run failed:", answer)
            continue
        for entry in answer.get("factors", []):
            verdicts = {f"L{lv.get('level')}": f"{lv.get('misfit_share')} {lv.get('safe')}" for lv in entry.get("levels", [])}
            summary[entry.get("factor")][f"{entry.get('decision')}@{entry.get('level')}"] += 1
            print(f"  {entry.get('factor'):18} {entry.get('known_or_controlled', {}).get('answer'):10} {entry.get('decision')}@{entry.get('level')}  {verdicts}  {entry.get('note') or ''}")
    print()
    for factor, counts in summary.items():
        print(f"{factor:18} {dict(counts)}")
    cost = sum(values[6] for values in router.cost.values())
    print(f"\nprompt chars {len(prompt):,}; billed ${cost:.4f}")


if __name__ == "__main__":
    asyncio.run(main())
