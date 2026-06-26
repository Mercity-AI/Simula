from __future__ import annotations

import asyncio
import hashlib
import json
import math
import random
import uuid
from typing import Any

from .config import Config
from .console import info, phase, track
from .evaluate import coverage_aware_trim, dedupe_rows, validate_record
from .models import ModelRouter
from .data_models import TaskType
from .taxonomy import build_strategies, choose_strategy, load_or_build_taxonomy, sample_mix
from .utils import (
    append_jsonl,
    artifact_path,
    extract_json_object,
    load_completed_attempt_indexes,
    now_iso,
    read_json,
    read_jsonl,
    write_json,
    write_jsonl,
)


async def generate_dataset(cfg: Config, router: ModelRouter, *, resume: bool = True, quiet: bool = False) -> list[dict[str, Any]]:
    """Build/load taxonomy + strategies, run point generation concurrently, then dedupe + trim.

    Writes raw/accepted rows as each attempt completes, checkpoints run_state.json, and finally writes
    dataset.final.jsonl from accepted rows. Resume (the default) skips attempt indexes already present
    and refuses to mix rows across a fingerprint change. Evaluation stays a separate command.
    """
    taxonomy = await load_or_build_taxonomy(cfg, router)
    strategies = await build_strategies(cfg, router, taxonomy)
    raw_path = artifact_path(cfg.output_dir, "raw")
    accepted_path = artifact_path(cfg.output_dir, "accepted")
    final_path = artifact_path(cfg.output_dir, "final")
    state_path = artifact_path(cfg.output_dir, "state")

    # Resume reuses accepted rows by attempt_index. If an input that determines a row's content or
    # validity changed since the last checkpoint, blending old and new rows would corrupt the
    # dataset, so refuse to resume across a fingerprint change and tell the user to pass --no-resume.
    fingerprint = _run_fingerprint(cfg, taxonomy, strategies)
    if resume:
        prior = read_json(state_path)
        if prior and prior.get("fingerprint") and prior["fingerprint"] != fingerprint:
            raise ValueError(
                "Run config changed since the last checkpoint (schema/seed/model/prompts/sampling/"
                "taxonomy/strategies). Resuming would mix inconsistent rows; rerun with --no-resume."
            )

    # Optional restart clears generated dataset artifacts without touching taxonomy/strategies.
    if not resume:
        write_jsonl(raw_path, [])
        write_jsonl(accepted_path, [])
        write_jsonl(final_path, [])
        write_json(state_path, {"attempted": 0, "accepted": 0, "fingerprint": fingerprint})

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
        phase(f"Generating {len(indexes)} attempts (target {target})")
        # Bound in-flight attempts so a large target does not launch every attempt at once
        # (which would exhaust the HTTP pool and trigger provider rate limits).
        limiter = asyncio.Semaphore(cfg.generation.concurrency)

        async def _bounded(index: int) -> dict[str, Any]:
            async with limiter:
                return await _generate_one_safe(cfg, router, taxonomy, strategies, random.Random(seed + index), index)

        async with asyncio.TaskGroup() as tg:
            tasks = [tg.create_task(_bounded(index)) for index in indexes]
            with track(len(tasks), "Generating", quiet=quiet) as advance:
                for future in asyncio.as_completed(tasks):
                    row = await future
                    completed += 1
                    append_jsonl(raw_path, row)
                    if row["accepted"]:
                        accepted_count += 1
                        append_jsonl(accepted_path, row)
                    advance(description=f"Generating · {accepted_count} accepted")
                    if completed % checkpoint_every == 0 or completed >= attempts:
                        write_json(
                            state_path,
                            {"attempted": completed, "target_attempts": attempts, "accepted": accepted_count, "fingerprint": fingerprint},
                        )
    else:
        info("[dim]All attempts already complete; nothing to generate.[/dim]")

    # Build the final artifact from accepted rows only; evaluation remains a separate command.
    accepted = [row for row in read_jsonl(accepted_path, tolerant=True) if row.get("accepted")]
    accepted_total = len(accepted)
    if cfg.evaluation.dedupe:
        accepted, _ = dedupe_rows(accepted)
    final = coverage_aware_trim(accepted, target)
    write_jsonl(final_path, final)
    info(f"[dim]Accepted {accepted_total} → {len(final)} final after dedupe/trim (target {target})[/dim]")
    return final


def _run_fingerprint(cfg: Config, taxonomy: dict[str, Any], strategies: list[dict[str, Any]]) -> str:
    # Hash only the inputs that change a generated row's content or validity. target_size,
    # overgenerate_ratio, concurrency, and checkpoint_every are deliberately excluded so a run can
    # be grown or re-paced and still resume. A prompt-module edit is caught via its file contents.
    module_path = cfg.prompts.module_path
    payload = {
        "description": cfg.description,
        "schema": cfg.schema,
        "seed": cfg.seed,
        "models": {role: cfg.data["models"][role]["model"] for role in ("strategic", "bulk", "critic")},
        "scenarios_per_mix": cfg.generation.scenarios_per_mix,
        "complexity_ratio": cfg.generation.complexity_ratio,
        "max_refine_attempts": cfg.generation.max_refine_attempts,
        "sampling": cfg.data.get("sampling"),
        "prompts": module_path.read_text(encoding="utf-8") if module_path else None,
        "taxonomy": taxonomy,
        "strategies": strategies,
    }
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


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
    """Run one point generation, converting any failure into a checkpointable rejected row.

    asyncio.TaskGroup cancels sibling tasks on the first unhandled exception, so this boundary stops a
    single bad point from aborting the whole batch. Strategy + mix are sampled once up front (with
    fallback defaults), so the failure path reuses the same lineage instead of recomputing it.
    """
    mix: list[dict[str, Any]] = []
    strategy_id = "error"
    try:
        strategy = choose_strategy(strategies, rng)
        strategy_id = strategy.get("id", "general")
        mix = sample_mix(taxonomy, strategy, rng)
        return await _generate_one(cfg, router, index, mix, strategy_id, rng)
    except Exception as exc:  # noqa: BLE001 - point-level failures should checkpoint as rejected rows.
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
    index: int,
    mix: list[dict[str, Any]],
    strategy_id: str,
    rng: random.Random,
) -> dict[str, Any]:
    # Taxonomy/strategy were sampled by the caller; branch only at the output layer (JSON vs text).
    meta_prompt, complexified = await _make_meta_prompt(cfg, router, mix, rng)
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
        strategy_id=strategy_id,
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
    """Generate a JSON record, validate against the schema, and give one repair attempt on failure.

    Returns (record, schema_valid, rejection_reason). The repair pass is the only place we re-ask the
    model after a parse/validation miss; persistent failures become a rejected row.
    """
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
    """Generate a free-text data point. Text has no schema, so it is always structurally valid."""
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
    """Critique, then refine up to max_refine_attempts, over a loop shared by JSON and text records.

    The only mode-specific parts are the critique/refine prompts and that JSON refinement re-validates
    against the schema (a refine that breaks the schema rejects the point); text refinement just
    replaces the candidate. Returns (record, accepted, rejection_reason, verdicts).
    """
    schema_free = cfg.is_schema_free

    async def critique(current: Any) -> dict[str, Any]:
        prompt = (
            cfg.prompts.critique_text_prompt(cfg.description, meta_prompt, str(current))
            if schema_free
            else cfg.prompts.critique_prompt(cfg.description, cfg.schema, meta_prompt, current)
        )
        return await router.complete_json("critic", prompt, system=cfg.prompts.SYSTEM_JSON, task=TaskType.SEMANTIC_CRITIC)

    async def refine(current: Any, explanation: str) -> tuple[Any, str | None]:
        # Returns (revised, error). Text refine never errors here; JSON refine reports a schema miss.
        if schema_free:
            text = await router.complete(
                "bulk",
                cfg.prompts.refine_text_prompt(cfg.description, meta_prompt, str(current), explanation),
                system=cfg.prompts.SYSTEM_TEXT,
                task=TaskType.REFINE,
            )
            return text.strip(), None
        revised = await router.complete_json(
            "bulk",
            cfg.prompts.refine_record_prompt(cfg.schema, meta_prompt, current, explanation),
            system=cfg.prompts.SYSTEM_JSON,
            task=TaskType.REFINE,
        )
        valid, error = validate_record(cfg.schema, revised, validator=cfg.validator)
        return revised, None if valid else error

    verdicts: list[dict[str, Any]] = []
    current = record
    for _ in range(cfg.generation.max_refine_attempts + 1):
        verdict = await critique(current)
        verdicts.append(verdict)
        if verdict.get("verdict") == "accept":
            return current, True, None, verdicts
        try:
            current, error = await refine(current, verdict.get("explanation", ""))
        except Exception as exc:  # noqa: BLE001 - a failed refine call rejects this point.
            return current, False, f"Refinement failed: {exc}", verdicts
        if error is not None:  # JSON refinement produced a schema-invalid record.
            return current, False, error, verdicts
    return current, False, verdicts[-1].get("explanation", "Critic rejected record."), verdicts
