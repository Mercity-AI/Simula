from __future__ import annotations

import asyncio
import json
import random
from itertools import combinations
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from .config import Config
from .diversity import embedding_diversity
from .models import ModelRouter
from .tasks import TaskType
from .taxonomy import taxonomy_nodes_by_level, taxonomy_to_text, walk_nodes
from .utils import artifact_path, ngrams_for_text, read_json, read_jsonl, record_to_text, write_json, write_jsonl

try:
    from tqdm import tqdm
except ImportError:  # pragma: no cover - tqdm is a declared dependency.
    tqdm = None


def validate_record(
    schema: dict[str, Any] | None,
    record: Any,
    *,
    validator: Draft202012Validator | None = None,
) -> tuple[bool, str | None]:
    # Pass a precompiled `validator` (e.g. cfg.validator) on hot paths to avoid recompiling per call;
    # otherwise the schema is compiled here. Either way schema=None means "free-text, always valid".
    if validator is None:
        if schema is None:
            return True, None
        validator = Draft202012Validator(schema)
    errors = sorted(validator.iter_errors(record), key=lambda e: list(e.path))
    if errors:
        return False, errors[0].message
    return True, None


def dedupe_rows(rows: list[dict[str, Any]], n: int = 13, threshold: float = 0.8) -> tuple[list[dict[str, Any]], list[str]]:
    kept: list[dict[str, Any]] = []
    kept_grams: list[set[tuple[str, ...]]] = []
    removed: list[str] = []

    # Compare each candidate against kept records using the paper-style n-gram overlap.
    for row in rows:
        grams = ngrams_for_text(record_to_text(row.get("record")), n)
        # Empty n-grams (record_to_text has no word tokens) carry no signal: two such records are
        # not duplicates of each other. Without this guard _jaccard(set(), set()) == 1.0 silently
        # collapses every empty/symbol-only record into one.
        duplicate = bool(grams) and any(_jaccard(grams, existing) >= threshold for existing in kept_grams)
        if duplicate:
            removed.append(row["id"])
            continue
        kept.append(row)
        kept_grams.append(grams)
    return kept, removed


def decontaminate_rows(rows: list[dict[str, Any]], paths: list[str], n: int = 13, threshold: float = 0.8) -> tuple[list[dict[str, Any]], list[str]]:
    reference_grams = [ngrams_for_text(text, n) for text in _load_reference_texts(paths)]
    if not reference_grams:
        return rows, []

    # Drop rows with high overlap against any reference/test example.
    kept: list[dict[str, Any]] = []
    removed: list[str] = []
    for row in rows:
        grams = ngrams_for_text(record_to_text(row.get("record")), n)
        if grams and any(_jaccard(grams, reference) >= threshold for reference in reference_grams):
            removed.append(row["id"])
            continue
        kept.append(row)
    return kept, removed


def coverage_report(taxonomy: dict[str, Any], rows: list[dict[str, Any]]) -> dict[str, Any]:
    total = taxonomy_nodes_by_level(taxonomy)
    covered: dict[str, dict[int, set[str]]] = {factor: {level: set() for level in levels} for factor, levels in total.items()}

    # Lineage coverage trusts the taxonomy_mix saved during generation. A sampled node also covers
    # its ancestors, so count every path prefix (matching reassignment_coverage); otherwise a leaf
    # sample would report its root and parent levels as uncovered.
    for row in rows:
        for mix in row.get("taxonomy_mix", []):
            factor = mix["factor"]
            for level, prefix in enumerate(_path_prefixes(mix.get("path", [mix["node"]]))):
                covered.setdefault(factor, {}).setdefault(level, set()).add("/".join(prefix))
    return _coverage_from_sets(total, covered)


def coverage_aware_trim(rows: list[dict[str, Any]], target_size: int) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    used: set[str] = set()
    # Precompute each row's taxonomy-path set once instead of rebuilding it on every scan.
    remaining = [(row, _row_paths(row)) for row in rows]

    # Greedily prefer rows that add the most unseen taxonomy paths.
    while remaining and len(selected) < target_size:
        best_idx = 0
        best_gain = -1
        for idx, (_, paths) in enumerate(remaining):
            gain = len(paths - used)
            if gain > best_gain:
                best_idx, best_gain = idx, gain
        row, paths = remaining.pop(best_idx)
        selected.append(row)
        used.update(paths)
    return selected


def _row_paths(row: dict[str, Any]) -> set[str]:
    return {f"{m['factor']}:{'/'.join(m.get('path', [m['node']]))}" for m in row.get("taxonomy_mix", [])}


async def run_evaluation(cfg: Config, router: ModelRouter | None = None, *, quiet: bool = False) -> dict[str, Any]:
    eval_cfg = cfg.evaluation
    taxonomy = read_json(artifact_path(cfg.output_dir, "taxonomy"), {"factors": []})
    # Read the generator's final dataset tolerantly (a torn line from a killed run must not abort eval).
    rows = read_jsonl(artifact_path(cfg.output_dir, "final"), tolerant=True)
    report: dict[str, Any] = {"count": len(rows)}

    # Dedupe/decontamination write a SEPARATE evaluated artifact; the generator's dataset.final.jsonl
    # is never rewritten so `evaluate` is a read-only-on-final, side-effect-isolated step.
    if eval_cfg.dedupe:
        deduped, removed = dedupe_rows(rows)
        rows = deduped
        report["dedupe"] = {"removed_count": len(removed), "removed_ids": removed}
    if eval_cfg.decontaminate_against:
        rows, removed = decontaminate_rows(rows, eval_cfg.decontaminate_against)
        report["decontamination"] = {"removed_count": len(removed), "removed_ids": removed}
    write_jsonl(artifact_path(cfg.output_dir, "evaluated"), rows)

    # Coverage can use saved lineage, independent LLM reassignment, or both.
    if eval_cfg.coverage:
        mode = eval_cfg.coverage_mode
        if mode in {"lineage", "both"}:
            report["coverage"] = coverage_report(taxonomy, rows)
        if mode in {"reassign", "both"}:
            if router is None:
                raise ValueError("Reassignment coverage requires a model router.")
            report["reassignment_coverage"] = await reassignment_coverage(cfg, router, rows, taxonomy, quiet=quiet)

    # Optional eval enrichments are deliberately separate from generation.
    diversity_cfg = eval_cfg.diversity
    if diversity_cfg.enabled:
        texts = [record_to_text(row.get("record"), diversity_cfg.text_field) for row in rows]
        report["diversity"] = embedding_diversity(
            texts,
            diversity_cfg.embedding_model,
            artifact_path(cfg.output_dir, "embedding_cache"),
            sample_cap=diversity_cfg.sample_cap,
            k_local=diversity_cfg.k_local,
        )

    if eval_cfg.complexity:
        if router is None:
            raise ValueError("Complexity scoring requires a model router.")
        report["complexity"] = await complexity_scores(cfg, router, rows, quiet=quiet)

    report["count"] = len(rows)
    write_json(artifact_path(cfg.output_dir, "eval"), report)
    return report


async def complexity_scores(cfg: Config, router: ModelRouter, rows: list[dict[str, Any]], *, quiet: bool = False) -> dict[str, Any]:
    batch_size = cfg.evaluation.complexity_batch_size
    appearances = cfg.evaluation.complexity_samples_per_item
    raw: dict[str, list[float]] = {row["id"]: [] for row in rows}
    ratings: dict[str, float] = {row["id"]: 1000.0 for row in rows}
    schedule = _complexity_schedule(rows, batch_size, appearances)

    async def score_batch(batch: list[dict[str, Any]]) -> dict[str, float]:
        payload = [{"id": row["id"], "record": row["record"]} for row in batch]
        response = await router.complete_json(
            "critic",
            cfg.prompts.complexity_prompt(cfg.description, payload),
            system=cfg.prompts.SYSTEM_JSON,
            task=TaskType.COMPLEXITY_SCORE,
        )
        return {str(s["id"]): float(s["score"]) for s in response.get("scores", []) if "id" in s and "score" in s}

    # Score repeated shuffled batches concurrently, then update Elo in deterministic schedule order.
    async def score_indexed(idx: int, batch: list[dict[str, Any]]) -> tuple[int, dict[str, float]]:
        return idx, await score_batch(batch)

    results: list[dict[str, float]] = [{} for _ in schedule]
    async with asyncio.TaskGroup() as tg:
        tasks = [tg.create_task(score_indexed(idx, batch)) for idx, batch in enumerate(schedule)]
        iterator = asyncio.as_completed(tasks)
        if tqdm is not None and not quiet:
            iterator = tqdm(iterator, total=len(tasks), desc="Scoring complexity")
        for future in iterator:
            idx, score_map = await future
            results[idx] = score_map

    for batch, score_map in zip(schedule, results):
        for row in batch:
            raw[row["id"]].append(score_map.get(row["id"], 5.0))
        _update_elo(ratings, {row["id"]: score_map.get(row["id"], 5.0) for row in batch})

    return {
        "raw_scores": {item_id: (sum(vals) / len(vals) if vals else None) for item_id, vals in raw.items()},
        "elo": ratings,
    }


async def reassignment_coverage(
    cfg: Config,
    router: ModelRouter,
    rows: list[dict[str, Any]],
    taxonomy: dict[str, Any],
    *,
    quiet: bool = False,
) -> dict[str, Any]:
    total = taxonomy_nodes_by_level(taxonomy)
    covered: dict[str, dict[int, set[str]]] = {factor: {level: set() for level in levels} for factor, levels in total.items()}
    text_field = cfg.evaluation.diversity.text_field
    sem = asyncio.Semaphore(cfg.generation.concurrency)

    async def assign(row: dict[str, Any], factor_root: dict[str, Any]) -> tuple[str, str | None]:
        async with sem:
            text = record_to_text(row.get("record"), text_field)
            response = await router.complete_json(
                "critic",
                cfg.prompts.node_assign_prompt(taxonomy_to_text(factor_root), factor_root["name"], text),
                system=cfg.prompts.SYSTEM_JSON,
                task=TaskType.NODE_ASSIGN,
            )
            return factor_root["name"], response.get("node_name")

    # Ask the model to assign every row to every factor, then count assigned paths and ancestors.
    async with asyncio.TaskGroup() as tg:
        tasks: list[asyncio.Task[tuple[str, str | None]]] = []
        for row in rows:
            for factor_root in taxonomy.get("factors", []):
                tasks.append(tg.create_task(assign(row, factor_root)))
        iterator = asyncio.as_completed(tasks)
        if tqdm is not None and not quiet:
            iterator = tqdm(iterator, total=len(tasks), desc="Reassigning coverage")
        for future in iterator:
            factor_name, node_name = await future
            factor_root = next((f for f in taxonomy.get("factors", []) if f["name"] == factor_name), None)
            if factor_root is None or node_name is None:
                continue
            node = _find_node_by_name(factor_root, node_name)
            if node is None:
                continue
            for level, path in enumerate(_path_prefixes(node.get("path", [node["name"]]))):
                covered.setdefault(factor_name, {}).setdefault(level, set()).add("/".join(path))
    return _coverage_from_sets(total, covered)


def _complexity_schedule(rows: list[dict[str, Any]], batch_size: int, appearances: int) -> list[list[dict[str, Any]]]:
    rng = random.Random(0)
    schedule: list[list[dict[str, Any]]] = []
    for _ in range(max(1, appearances)):
        shuffled = rows[:]
        rng.shuffle(shuffled)
        batches = [shuffled[idx : idx + batch_size] for idx in range(0, len(shuffled), batch_size)]
        # A trailing batch of one can't be ranked within itself; merge it back so the item is not
        # silently dropped from this appearance pass.
        if len(batches) >= 2 and len(batches[-1]) < 2:
            batches[-2].extend(batches.pop())
        schedule.extend(batch for batch in batches if len(batch) >= 2)
    return schedule


def _update_elo(ratings: dict[str, float], scores: dict[str, float]) -> None:
    for left, right in combinations(scores.keys(), 2):
        expected = 1 / (1 + 10 ** ((ratings[right] - ratings[left]) / 400))
        actual = 1.0 if scores[left] > scores[right] else 0.0 if scores[left] < scores[right] else 0.5
        delta = 16 * (actual - expected)
        ratings[left] += delta
        ratings[right] -= delta


def _coverage_from_sets(total: dict[str, dict[int, set[str]]], covered: dict[str, dict[int, set[str]]]) -> dict[str, Any]:
    report: dict[str, Any] = {}
    for factor, levels in total.items():
        report[factor] = {}
        for level, total_nodes in levels.items():
            got = covered.get(factor, {}).get(level, set())
            report[factor][str(level)] = {
                "covered": len(got),
                "total": len(total_nodes),
                "ratio": len(got) / len(total_nodes) if total_nodes else 0,
                "nodes": sorted(got),
            }
    return report


def _jaccard(left: set[tuple[str, ...]], right: set[tuple[str, ...]]) -> float:
    if not left and not right:
        return 1.0
    return len(left & right) / max(1, len(left | right))


def _load_reference_texts(paths: list[str]) -> list[str]:
    texts: list[str] = []
    for raw_path in paths:
        path = Path(raw_path)
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
                texts.append(record_to_text(row.get("record", row)))
            except json.JSONDecodeError:
                texts.append(line)
    return texts


def _find_node_by_name(root: dict[str, Any], name: str) -> dict[str, Any] | None:
    wanted = name.casefold()
    for node in walk_nodes(root):
        if str(node.get("name", "")).casefold() == wanted:
            return node
    return None


def _path_prefixes(path: list[str]) -> list[list[str]]:
    return [path[: idx + 1] for idx in range(len(path))]
