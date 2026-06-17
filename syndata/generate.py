from __future__ import annotations

import asyncio
import math
import random
import uuid
from typing import Any

from .config import Config
from .evaluate import coverage_aware_trim, dedupe_rows, validate_record
from .models import ModelRouter
from .tasks import TaskType
from .taxonomy import build_strategies, choose_strategy, load_or_build_taxonomy, sample_mix
from .utils import (
    append_jsonl,
    artifact_path,
    extract_json_object,
    load_completed_attempt_indexes,
    now_iso,
    read_jsonl,
    write_json,
    write_jsonl,
)

try:
    from tqdm import tqdm
except ImportError:  # pragma: no cover - tqdm is a declared dependency, this keeps local smoke tests light.
    tqdm = None


async def generate_dataset(cfg: Config, router: ModelRouter, *, resume: bool = True, quiet: bool = False) -> list[dict[str, Any]]:
    taxonomy = await load_or_build_taxonomy(cfg, router)
    strategies = await build_strategies(cfg, router, taxonomy)
    raw_path = artifact_path(cfg.output_dir, "raw")
    accepted_path = artifact_path(cfg.output_dir, "accepted")
    final_path = artifact_path(cfg.output_dir, "final")

    # Optional restart clears generated dataset artifacts without touching taxonomy/strategies.
    if not resume:
        write_jsonl(raw_path, [])
        write_jsonl(accepted_path, [])
        write_jsonl(final_path, [])
        write_json(artifact_path(cfg.output_dir, "state"), {"attempted": 0, "accepted": 0})

    # Determine the deterministic attempt queue from target size, overgeneration, and checkpoint state.
    target = cfg.generation.target_size
    attempts = math.ceil(target * cfg.generation.overgenerate_ratio)
    seed = cfg.seed
    completed_indexes = load_completed_attempt_indexes(raw_path) if resume else set()
    indexes = [index for index in range(attempts) if index not in completed_indexes]

    # Run point generation concurrently, writing artifacts in completion order from the main task.
    # accepted.jsonl is read tolerantly: a crash mid-append can leave a torn final line.
    completed = len(completed_indexes)
    accepted_count = len([row for row in read_jsonl(accepted_path, tolerant=True) if row.get("accepted")]) if resume else 0
    checkpoint_every = max(1, cfg.generation.checkpoint_every)
    if indexes:
        # Bound in-flight attempts so a large target does not launch every attempt at once
        # (which would exhaust the HTTP pool and trigger provider rate limits).
        limiter = asyncio.Semaphore(cfg.generation.concurrency)

        async def _bounded(index: int) -> dict[str, Any]:
            async with limiter:
                return await _generate_one_safe(cfg, router, taxonomy, strategies, random.Random(seed + index), index)

        async with asyncio.TaskGroup() as tg:
            tasks = [tg.create_task(_bounded(index)) for index in indexes]
            iterator = asyncio.as_completed(tasks)
            if tqdm is not None and not quiet:
                iterator = tqdm(iterator, total=len(tasks), desc="Generating")
            for future in iterator:
                row = await future
                completed += 1
                append_jsonl(raw_path, row)
                if row["accepted"]:
                    accepted_count += 1
                    append_jsonl(accepted_path, row)
                if completed % checkpoint_every == 0 or completed >= attempts:
                    write_json(
                        artifact_path(cfg.output_dir, "state"),
                        {"attempted": completed, "target_attempts": attempts, "accepted": accepted_count},
                    )

    # Build the final artifact from accepted rows only; evaluation remains a separate command.
    accepted = [row for row in read_jsonl(accepted_path, tolerant=True) if row.get("accepted")]
    if cfg.evaluation.dedupe:
        accepted, _ = dedupe_rows(accepted)
    final = coverage_aware_trim(accepted, target)
    write_jsonl(final_path, final)
    return final


def _build_row(
    cfg: Config,
    router: ModelRouter,
    index: int,
    *,
    record: Any,
    mix: list[dict[str, Any]],
    strategy_id: str,
    meta_prompt: str,
    complexified: bool,
    schema_valid: bool,
    accepted: bool,
    rejection_reason: str | None,
    critic_verdicts: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    # Single source of truth for the dataset row shape (see the Artifact Contract in AGENTS.md).
    return {
        "id": f"item-{index}-{uuid.uuid4().hex[:8]}",
        "attempt_index": index,
        "record": record,
        "output_format": cfg.output_format,
        "taxonomy_mix": mix,
        "strategy_id": strategy_id,
        "meta_prompt": meta_prompt,
        "complexified": complexified,
        "generator_model": router.model_name("bulk"),
        "critic_verdicts": critic_verdicts or [],
        "schema_valid": schema_valid,
        "accepted": accepted,
        "rejection_reason": rejection_reason,
        "created_at": now_iso(),
    }


async def _generate_one_safe(
    cfg: Config,
    router: ModelRouter,
    taxonomy: dict[str, Any],
    strategies: list[dict[str, Any]],
    rng: random.Random,
    index: int,
) -> dict[str, Any]:
    try:
        return await _generate_one(cfg, router, taxonomy, strategies, rng, index)
    except Exception as exc:  # noqa: BLE001 - point-level failures should checkpoint as rejected rows.
        try:
            strategy = choose_strategy(strategies, rng)
            mix = sample_mix(taxonomy, strategy, rng)
            strategy_id = strategy.get("id", "general")
        except Exception:  # noqa: BLE001 - fallback only applies when sampling itself is broken.
            mix = []
            strategy_id = "error"
        return _build_row(
            cfg,
            router,
            index,
            record="" if cfg.is_schema_free else {},
            mix=mix,
            strategy_id=strategy_id,
            meta_prompt="",
            complexified=False,
            schema_valid=cfg.is_schema_free,
            accepted=False,
            rejection_reason=f"Generation failed: {exc}",
        )


async def _generate_one(
    cfg: Config,
    router: ModelRouter,
    taxonomy: dict[str, Any],
    strategies: list[dict[str, Any]],
    rng: random.Random,
    index: int,
) -> dict[str, Any]:
    strategy = choose_strategy(strategies, rng)
    mix = sample_mix(taxonomy, strategy, rng)
    meta_prompt, complexified = await _make_meta_prompt(cfg, router, mix, rng)

    # Branch only at the output layer: taxonomy, strategy, and meta-prompt logic are shared.
    if cfg.is_schema_free:
        record, schema_valid, rejection_reason = await _make_text(cfg, router, meta_prompt)
    else:
        record, schema_valid, rejection_reason = await _make_record(cfg, router, meta_prompt)

    row = _build_row(
        cfg,
        router,
        index,
        record=record,
        mix=mix,
        strategy_id=strategy.get("id", "general"),
        meta_prompt=meta_prompt,
        complexified=complexified,
        schema_valid=schema_valid,
        accepted=False,
        rejection_reason=rejection_reason,
    )
    if not schema_valid:
        return row

    # Semantic critique/refinement is applied to both JSON and free-text records.
    row["record"], row["accepted"], row["rejection_reason"], row["critic_verdicts"] = await _critic_loop(cfg, router, meta_prompt, record)
    if not cfg.is_schema_free:
        row["schema_valid"] = validate_record(cfg.schema, row["record"], validator=cfg.validator)[0]
        if not row["schema_valid"]:
            row["accepted"] = False
            row["rejection_reason"] = "Record failed schema validation after critique refinement."
    return row


async def _make_meta_prompt(cfg: Config, router: ModelRouter, mix: list[dict[str, Any]], rng: random.Random) -> tuple[str, bool]:
    response = await router.complete_json(
        "bulk",
        cfg.prompts.meta_prompt_prompt(cfg.description, cfg.schema, mix, cfg.generation.scenarios_per_mix),
        system=cfg.prompts.SYSTEM_JSON,
        task=TaskType.META_PROMPT,
    )
    options = response.get("meta_prompts") or ["Generate one synthetic data point."]
    meta_prompt = rng.choice(options)

    # Complexification is sampled per point so the final set mixes simple and difficult items.
    complexified = rng.random() < cfg.generation.complexity_ratio
    if complexified:
        response = await router.complete_json(
            "bulk",
            cfg.prompts.complexify_prompt(cfg.description, meta_prompt),
            system=cfg.prompts.SYSTEM_JSON,
            task=TaskType.COMPLEXIFY,
        )
        meta_prompt = response.get("meta_prompt", meta_prompt)
    return meta_prompt, complexified


async def _make_record(cfg: Config, router: ModelRouter, meta_prompt: str) -> tuple[Any, bool, str | None]:
    raw_response = await router.complete(
        "bulk",
        cfg.prompts.generate_record_prompt(cfg.description, cfg.schema, meta_prompt),  # type: ignore[arg-type]
        system=cfg.prompts.SYSTEM_JSON,
        task=TaskType.GENERATE,
    )
    try:
        record = extract_json_object(raw_response)
        valid, error = validate_record(cfg.schema, record, validator=cfg.validator)
        if valid:
            return record, True, None
    except Exception as exc:
        record, error = None, str(exc)

    # Give the model one tightly scoped repair attempt before rejecting.
    try:
        repaired = await router.complete_json(
            "bulk",
            cfg.prompts.repair_json_prompt(cfg.schema, raw_response, error or "schema validation failed"),  # type: ignore[arg-type]
            system=cfg.prompts.SYSTEM_JSON,
            task=TaskType.REPAIR,
        )
        valid, repair_error = validate_record(cfg.schema, repaired, validator=cfg.validator)
        return repaired, valid, None if valid else repair_error
    except Exception as exc:
        return record, False, f"JSON repair failed: {exc}"


async def _make_text(cfg: Config, router: ModelRouter, meta_prompt: str) -> tuple[str, bool, str | None]:
    text = await router.complete(
        "bulk",
        cfg.prompts.generate_text_prompt(cfg.description, meta_prompt),
        system=cfg.prompts.SYSTEM_TEXT,
        task=TaskType.GENERATE,
    )
    return text.strip(), True, None


async def _critic_loop(
    cfg: Config,
    router: ModelRouter,
    meta_prompt: str,
    record: Any,
) -> tuple[Any, bool, str | None, list[dict[str, Any]]]:
    verdicts: list[dict[str, Any]] = []
    current = record
    for _ in range(cfg.generation.max_refine_attempts + 1):
        if cfg.is_schema_free:
            critique = await router.complete_json(
                "critic",
                cfg.prompts.critique_text_prompt(cfg.description, meta_prompt, str(current)),
                system=cfg.prompts.SYSTEM_JSON,
                task=TaskType.SEMANTIC_CRITIC,
            )
        else:
            critique = await router.complete_json(
                "critic",
                cfg.prompts.critique_prompt(cfg.description, cfg.schema, meta_prompt, current),  # type: ignore[arg-type]
                system=cfg.prompts.SYSTEM_JSON,
                task=TaskType.SEMANTIC_CRITIC,
            )
        verdicts.append(critique)
        if critique.get("verdict") == "accept":
            return current, True, None, verdicts

        # Refinement keeps JSON mode schema-bound and text mode direct-output only.
        try:
            if cfg.is_schema_free:
                current = (
                    await router.complete(
                        "bulk",
                        cfg.prompts.refine_text_prompt(cfg.description, meta_prompt, str(current), critique.get("explanation", "")),
                        system=cfg.prompts.SYSTEM_TEXT,
                        task=TaskType.REFINE,
                    )
                ).strip()
            else:
                current = await router.complete_json(
                    "bulk",
                    cfg.prompts.refine_record_prompt(cfg.schema, meta_prompt, current, critique.get("explanation", "")),  # type: ignore[arg-type]
                    system=cfg.prompts.SYSTEM_JSON,
                    task=TaskType.REFINE,
                )
                valid, error = validate_record(cfg.schema, current, validator=cfg.validator)
                if not valid:
                    return current, False, error, verdicts
        except Exception as exc:
            return current, False, f"Refinement failed: {exc}", verdicts
    return current, False, verdicts[-1].get("explanation", "Critic rejected record."), verdicts
