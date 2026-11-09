"""Pilot analysis: cost, critic behaviour, size/lineage fidelity, diversity, repetition per arm.

usage: python analysis/analyze.py <run_dir> [<run_dir> ...] [--n N]   (writes <run_dir>/analysis.json + prints a table)
Pre-critic records are rebuilt from llm_calls.jsonl so "critic off" is measured on the same generations.
"""
from __future__ import annotations

import collections
import json
import re
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from simula.diversity import embedding_diversity  # noqa: E402
from simula.generate import _batch_items  # noqa: E402
from simula.utils import extract_json_object, read_jsonl  # noqa: E402

PRICES = {  # USD per 1M tokens (OpenRouter list, 2026-11-08)
    "deepseek/deepseek-v4-flash": (0.091, 0.182),
    "deepseek/deepseek-v4-flash:nitro": (0.091, 0.182),
    "deepseek/deepseek-v4-pro": (0.241338, 0.482676),
    "deepseek/deepseek-v4-pro:nitro": (0.241338, 0.482676),
    "deepseek/deepseek-v4.1-flash-1017:nitro": (0.15, 0.6),  # list, 2026-11-08
}
SIZES = {"small": (3, 5), "medium": (6, 11), "large": (12, 20)}


def leaf_count(value) -> int:
    if isinstance(value, dict):
        return sum(leaf_count(v) for v in value.values())
    return 1


def size_class(mix) -> str | None:
    for entry in mix:
        if entry["factor"] == "query_complexity" and len(entry["path"]) > 2 and entry["path"][1] == "constraint_count":
            return entry["path"][2]
    return None


def pct(values, q):
    values = sorted(values)
    return values[min(len(values) - 1, int(q * len(values)))] if values else None


def pre_critic_records(run: Path, rows: list[dict]) -> dict[int, dict]:
    """attempt_index -> the record as the generator first wrote it (before critic/refine)."""
    calls = [c for c in read_jsonl(run / "llm_calls.jsonl", tolerant=True) if c["task"] == "generate"]
    pre: dict[int, dict] = {}
    batch = any("Sampled ingredients:" in str(r.get("meta_prompt") or "") for r in rows)
    if batch:
        by_plan = {}
        for call in calls:
            for item in _batch_items(call["response"], "record"):
                by_plan[str(item.get("plan") or "").strip()] = item.get("record")
        for r in rows:
            plan = r["meta_prompt"].split("\nSampled ingredients:")[0].strip()
            if plan in by_plan:
                pre[r["attempt_index"]] = by_plan[plan]
    else:
        by_meta = {}
        batch_marker = "Meta-prompts (each is the specific instance to build):\n"
        for call in calls:
            marker = "Meta-prompt (the specific instance to build):\n"
            if batch_marker in call["prompt"]:  # batch mode B: ids map to scenarios in the prompt
                section = call["prompt"].split(batch_marker, 1)[1].rsplit("\n\nReturn JSON", 1)[0]
                specs = dict(re.findall(r"^(r\d+):\n(.*?)(?=\n\nr\d+:\n|\Z)", section, flags=re.S | re.M))
                for item in _batch_items(call["response"], "record"):
                    if str(item.get("id")) in specs:
                        by_meta[specs[str(item["id"])].strip()] = item["record"]
            elif marker in call["prompt"]:
                meta = call["prompt"].split(marker, 1)[1].rsplit("\n\nReturn ONLY", 1)[0].strip()
                try:
                    by_meta[meta] = extract_json_object(call["response"])
                except Exception:
                    pass
        for r in rows:
            meta = r["meta_prompt"] if isinstance(r["meta_prompt"], str) else json.dumps(r["meta_prompt"], ensure_ascii=False)
            if meta.strip() in by_meta:
                pre[r["attempt_index"]] = by_meta[meta.strip()]
    return pre


def analyze(run: Path, n_div: int) -> dict:
    rows = read_jsonl(run / "dataset.raw.jsonl", tolerant=True)
    final = read_jsonl(run / "dataset.final.jsonl", tolerant=True)
    calls = read_jsonl(run / "llm_calls.jsonl", tolerant=True)
    out: dict = {"arm": run.name, "attempts": len(rows)}

    # --- cost: billed (provider-reported, when logged) and list-priced from tokens ---
    by_task = collections.defaultdict(lambda: {"calls": 0, "in": 0, "out": 0, "reasoning": 0, "cached": 0, "billed": 0.0, "list": 0.0})
    for c in calls:
        t = by_task[c["task"]]
        pi, po = PRICES[c["model"]]
        t["calls"] += 1
        t["in"] += c["in_tokens"]
        t["out"] += c["out_tokens"]
        t["reasoning"] += c.get("reasoning_tokens") or 0
        t["cached"] += c.get("cached_tokens") or 0
        t["billed"] += c.get("cost") or 0.0
        t["list"] += (c["in_tokens"] * pi + c["out_tokens"] * po) / 1e6
    out["by_task"] = dict(by_task)
    total_list = sum(t["list"] for t in by_task.values())
    total_billed = sum(t["billed"] for t in by_task.values())
    accepted = [r for r in rows if r["accepted"]]
    out["accepted"] = len(accepted)
    out["final"] = len(final)
    out["accept_rate"] = len(accepted) / max(1, len(rows))
    out["calls"] = len(calls)
    out["calls_per_accepted"] = len(calls) / max(1, len(accepted))
    out["list_cost"] = total_list
    out["billed_cost"] = total_billed or None
    out["list_per_1k_accepted"] = 1000 * total_list / max(1, len(accepted))
    out["billed_per_1k_accepted"] = 1000 * total_billed / max(1, len(accepted)) if total_billed else None
    critic_cost = sum(by_task[t]["list"] for t in ("semantic_critic", "refine") if t in by_task)
    out["list_per_1k_critic_off"] = 1000 * (total_list - critic_cost) / max(1, sum(1 for r in rows if r["schema_valid"]))

    # --- rejection reasons + critic behaviour ---
    reasons = collections.Counter()
    for r in rows:
        if not r["accepted"]:
            reason = r["rejection_reason"] or ""
            key = "critic" if r["critic_verdicts"] else reason.split(":")[0][:60]
            reasons[key] += 1
    out["rejections"] = dict(reasons)
    critiqued = [r for r in rows if r["critic_verdicts"]]
    first_accept = sum(1 for r in critiqued if r["critic_verdicts"][0].get("verdict") == "accept")
    fixed = sum(1 for r in critiqued if r["accepted"] and r["critic_verdicts"][-1].get("verdict") == "reject")
    out["critic_first_accept"] = first_accept / max(1, len(critiqued))
    out["critic_fixed_share"] = fixed / max(1, len(critiqued))

    # --- record shape + size-class fidelity ---
    def stats(records_with_mix):
        fields, words, in_range, sized = [], [], 0, 0
        for record, mix in records_with_mix:
            if not isinstance(record, dict) or not isinstance(record.get("extraction"), dict):
                continue
            n = leaf_count(record["extraction"])
            fields.append(n)
            words.append(len(str(record.get("query", "")).split()))
            cls = size_class(mix)
            if cls in SIZES:
                sized += 1
                lo, hi = SIZES[cls]
                in_range += lo <= n <= hi
        return {
            "fields_p10_p50_p90": [pct(fields, 0.1), pct(fields, 0.5), pct(fields, 0.9)],
            "fields_mean": round(statistics.mean(fields), 2) if fields else None,
            "words_p10_p50_p90": [pct(words, 0.1), pct(words, 0.5), pct(words, 0.9)],
            "words_mean": round(statistics.mean(words), 2) if words else None,
            "size_in_range": in_range / max(1, sized),
            "sized_rows": sized,
        }

    out["shape"] = stats([(r["record"], r["taxonomy_mix"]) for r in accepted])
    pre = pre_critic_records(run, rows)
    out["pre_critic_matched"] = len(pre)
    valid_rows = [r for r in rows if r["schema_valid"] and r["attempt_index"] in pre]
    out["shape_pre_critic"] = stats([(pre[r["attempt_index"]], r["taxonomy_mix"]) for r in valid_rows])
    changed = sum(1 for r in accepted if r["attempt_index"] in pre and pre[r["attempt_index"]] != r["record"])
    out["critic_changed_share_of_accepted"] = changed / max(1, len(accepted))

    # --- diversity on equal-size samples of query text (deterministic sample, same n for every arm) ---
    queries = [str(r["record"].get("query", "")) for r in sorted(accepted, key=lambda r: r["attempt_index"]) if isinstance(r["record"], dict)]
    div = embedding_diversity(queries, "sentence-transformers/all-MiniLM-L6-v2", run / "analysis_embeddings.npz", sample_cap=n_div, k_local=10)
    out["diversity"] = {k: div[k] for k in ("vendi", "local_diversity", "global_diversity", "sample_size")}

    # Length-matched: only 8-25-word queries, so arms with shorter/longer queries compare fairly.
    band = [q for q in queries if 8 <= len(q.split()) <= 25]
    div_band = embedding_diversity(band, "sentence-transformers/all-MiniLM-L6-v2", run / "analysis_embeddings.npz", sample_cap=90, k_local=10)
    out["diversity_len_matched"] = {k: div_band[k] for k in ("vendi", "local_diversity", "sample_size")}

    # Taxonomy text leaking into extractions (a value that is a "a > b > c" path).
    def leaks(value) -> bool:
        if isinstance(value, dict):
            return any(leaks(v) for v in value.values())
        return isinstance(value, str) and " > " in value
    out["taxonomy_path_leak"] = sum(1 for r in accepted if isinstance(r["record"], dict) and leaks(r["record"].get("extraction"))) / max(1, len(accepted))

    # --- repetition: openers, brands, numbers ---
    openers = collections.Counter(" ".join(q.lower().split()[:3]) for q in queries)
    out["top_openers"] = openers.most_common(6)
    out["top3_opener_share"] = sum(c for _, c in openers.most_common(3)) / max(1, len(queries))
    first2 = collections.Counter(" ".join(re.findall(r"[a-z']+", q.lower())[:2]) for q in queries)
    out["top_first2"] = first2.most_common(6)
    brands = collections.Counter()
    for r in accepted:
        ext = r["record"].get("extraction", {}) if isinstance(r["record"], dict) else {}
        b = ext.get("brand")
        if isinstance(b, str):
            brands[b.lower()] += 1
    out["brands_distinct_over_rows_with_brand"] = [len(brands), sum(brands.values())]
    out["top_brands"] = brands.most_common(5)
    prices = collections.Counter(m for q in queries for m in re.findall(r"\$\s?(\d+)", q))
    out["top_prices"] = prices.most_common(6)
    return out


def main() -> None:
    args = sys.argv[1:]
    n = 400
    if "--n" in args:
        i = args.index("--n")
        n = int(args[i + 1])
        args = args[:i] + args[i + 2 :]
    results = []
    for arg in args:
        run = Path(arg)
        res = analyze(run, n)
        (run / "analysis.json").write_text(json.dumps(res, indent=2, default=str))
        results.append(res)
    cols = [
        ("arm", lambda r: r["arm"]),
        ("acc", lambda r: f"{r['accepted']}/{r['attempts']}"),
        ("calls/acc", lambda r: f"{r['calls_per_accepted']:.2f}"),
        ("$list/1k", lambda r: f"{r['list_per_1k_accepted']:.3f}"),
        ("$billed/1k", lambda r: f"{r['billed_per_1k_accepted']:.3f}" if r["billed_per_1k_accepted"] else "-"),
        ("$1k noCritic", lambda r: f"{r['list_per_1k_critic_off']:.3f}"),
        ("crit1st", lambda r: f"{r['critic_first_accept']:.0%}"),
        ("fixed", lambda r: f"{r['critic_fixed_share']:.0%}"),
        ("size✓", lambda r: f"{r['shape']['size_in_range']:.0%}"),
        ("size✓pre", lambda r: f"{r['shape_pre_critic']['size_in_range']:.0%}"),
        ("fields p50/90", lambda r: f"{r['shape']['fields_p10_p50_p90'][1]}/{r['shape']['fields_p10_p50_p90'][2]}"),
        ("words p50/90", lambda r: f"{r['shape']['words_p10_p50_p90'][1]}/{r['shape']['words_p10_p50_p90'][2]}"),
        ("vendi", lambda r: f"{r['diversity']['vendi']:.1f}"),
        ("local", lambda r: f"{r['diversity']['local_diversity']:.3f}"),
        ("vendi~len", lambda r: f"{r['diversity_len_matched']['vendi']:.1f}/{r['diversity_len_matched']['sample_size']}"),
        ("local~len", lambda r: f"{r['diversity_len_matched']['local_diversity']:.3f}"),
        ("top3open", lambda r: f"{r['top3_opener_share']:.0%}"),
        ("pathleak", lambda r: f"{r['taxonomy_path_leak']:.1%}"),
    ]
    print(" | ".join(name for name, _ in cols))
    for r in results:
        print(" | ".join(str(fn(r)) for _, fn in cols))


if __name__ == "__main__":
    main()
