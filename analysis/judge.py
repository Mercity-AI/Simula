"""Blind LLM judge for pilot arms: lineage fit + faithfulness + realism, on the same attempt indexes per arm.

usage: python analysis/judge.py <run_dir> [--pre] [--k 150] [--first200]   -> appends to <run_dir>/judge2.jsonl, prints a summary
--pre judges the pre-critic record (what "critic off" would ship) instead of the final accepted one.
"""
from __future__ import annotations

import asyncio
import collections
import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).parent))
from openai import AsyncOpenAI  # noqa: E402

from analyze import pre_critic_records  # noqa: E402
from simula.config import resolve_api_key  # noqa: E402
from simula.prompts import mix_text  # noqa: E402
from simula.utils import extract_json_object, read_jsonl  # noqa: E402

JUDGE = "anthropic/claude-haiku-5.5"  # judge prompt version "v2" (results in judge2.jsonl)
PROMPT = """You are auditing ONE example from a synthetic e-commerce search-query extraction dataset. Each example
pairs a shopper's search query with its extraction into atomic, database-queryable fields. It was generated from
four sampled taxonomy nodes, its intended ingredients. Judge strictly and independently.

Intended ingredients:
{mix}

Size classes: small = 3-5 extracted fields, medium = 6-11, large = 12-20.

How to judge:
- product_fit is "yes" when the searched product is the kind of item the product_vertical node describes
  (a "writing desk" fits "Writing Desks"); "no" only when it is a different kind of product.
- Normalizations count as supported: "$30" supports currency USD, "under $80" price_max 80, "highly rated"
  rating_min ~4, "mens" gender men. A field is unsupported only if the query gives no basis for it.
- A missing constraint is a fact the shopper clearly stated that the extraction drops.

The example to audit (query + extraction):
<example>
{record}
</example>

Return JSON only:
{{"product_fit": "yes|partly|no",      // is the searched product inside the product_vertical node?
 "complexity_fit": "yes|partly|no",   // do the query's phrasing shape and constraint count match query_complexity?
 "intent_fit": "yes|partly|no",       // does the query reflect the shopper_intent node?
 "attribute_fit": "yes|partly|no",    // does the attribute_focus node show up in query + extraction?
 "unsupported_fields": ["keys whose value is not stated or clearly implied by the query"],
 "missing_constraints": ["constraints stated in the query but absent from the extraction"],
 "non_atomic_fields": ["keys packing several facts or an unsplit range into one value"],
 "realism": 1,                        // 1-5: would a real shopper plausibly type or say this?
 "coherence": 1}}                     // 1-5: does the request make sense as a whole?"""

OUT = "judge2.jsonl"
SAMPLE = sorted(random.Random(7).sample(range(500), 150))


async def main() -> None:
    args = sys.argv[1:]
    pre = "--pre" in args
    k = int(args[args.index("--k") + 1]) if "--k" in args else 150
    run = Path(args[0])
    global SAMPLE
    if "--first200" in args:  # pro arms only ran attempts 0-199: judge 100 indexes from that range
        SAMPLE = sorted(random.Random(7).sample(range(200), 100))
    rows = {r["attempt_index"]: r for r in read_jsonl(run / "dataset.raw.jsonl", tolerant=True)}
    version = "pre" if pre else "final"
    if pre:
        records = pre_critic_records(run, list(rows.values()))
        targets = [(i, records[i]) for i in SAMPLE[:k] if i in records and rows[i]["schema_valid"]]
    else:
        targets = [(i, rows[i]["record"]) for i in SAMPLE[:k] if i in rows and rows[i]["accepted"]]
    out_path = run / OUT
    done = {(j["attempt_index"], j["version"]) for j in read_jsonl(out_path, tolerant=True)}
    targets = [(i, rec) for i, rec in targets if (i, version) not in done]

    client = AsyncOpenAI(api_key=resolve_api_key("OPENROUTER_API_KEY"), base_url="https://openrouter.ai/api/v1")
    limiter = asyncio.Semaphore(16)

    async def judge(index: int, record) -> dict:
        prompt = PROMPT.format(mix=mix_text(rows[index]["taxonomy_mix"]), record=json.dumps(record, ensure_ascii=False))
        async with limiter:
            for attempt in range(3):
                try:
                    r = await client.chat.completions.create(model=JUDGE, messages=[{"role": "user", "content": prompt}], temperature=0, max_tokens=4000,
                                                             extra_body={"reasoning": {"effort": "medium", "exclude": True}})
                    verdict = extract_json_object(r.choices[0].message.content or "")
                    if "product_fit" not in verdict:
                        raise ValueError(f"incomplete verdict: {str(verdict)[:120]}")
                    return {"attempt_index": index, "version": version, "verdict": verdict, "cost": getattr(r.usage, "cost", None)}
                except Exception as exc:  # noqa: BLE001
                    error = str(exc)
            return {"attempt_index": index, "version": version, "error": error}

    results = await asyncio.gather(*(judge(i, rec) for i, rec in targets))
    with out_path.open("a") as f:
        for res in results:
            f.write(json.dumps(res) + "\n")
    summarize(run, version)


def summarize(run: Path, version: str) -> dict:
    js = [j for j in read_jsonl(run / OUT, tolerant=True) if j["version"] == version and "verdict" in j]
    n = len(js)
    out = {"arm": run.name, "version": version, "n": n}
    for key in ("product_fit", "complexity_fit", "intent_fit", "attribute_fit"):
        c = collections.Counter(str(j["verdict"].get(key)) for j in js)
        out[key] = round(c["yes"] / max(1, n), 3)
    for key in ("unsupported_fields", "missing_constraints", "non_atomic_fields"):
        out[key + "_any"] = round(sum(1 for j in js if j["verdict"].get(key)) / max(1, n), 3)
    out["faithful"] = round(sum(1 for j in js if not j["verdict"].get("unsupported_fields") and not j["verdict"].get("missing_constraints")) / max(1, n), 3)
    for key in ("realism", "coherence"):
        vals = [float(j["verdict"][key]) for j in js if isinstance(j["verdict"].get(key), (int, float))]
        out[key] = round(sum(vals) / max(1, len(vals)), 2)
    out["judge_cost"] = round(sum(j.get("cost") or 0 for j in js), 4)
    print(json.dumps(out))
    return out


if __name__ == "__main__":
    asyncio.run(main())
