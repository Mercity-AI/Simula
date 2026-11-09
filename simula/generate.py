from __future__ import annotations

import asyncio
import hashlib
import json
import math
import random
import re
import uuid
from typing import Any

from .config import Config
from .console import info, phase, track, warn
from .evaluate import coverage_aware_trim, dedupe_rows, validate_record
from .models import ModelRouter
from .data_models import TaskType
from .prompts import mix_text
from .taxonomy import build_strategies, choose_strategy, find_option, level_options, load_or_build_taxonomy, mix_space, sample_mix
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
    """Build/load taxonomy + strategies (+ meta prompts), run record generation, then dedupe + trim.

    Two record paths: with generation.meta_prompts (the default) every attempt first gets a reviewable
    scenario in meta_prompts.jsonl and then its own record call; without it, records are written
    straight from their sampled mixes, generation.record_batch_size records per call. Writes raw/accepted rows
    as each attempt completes, checkpoints run_state.json, and finally writes dataset.final.jsonl from
    accepted rows. Resume (the default) skips attempt indexes already present and refuses to mix rows
    across a fingerprint change. generation.stop_after halts the run at a stage boundary so its
    artifact can be edited first. Evaluation stays a separate command.
    """
    taxonomy = await load_or_build_taxonomy(cfg, router)
    _stop_if_requested(cfg, "taxonomy")
    strategies = await build_strategies(cfg, router, taxonomy)
    _stop_if_requested(cfg, "strategies")
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
                "taxonomy/strategies/generation mode). Resuming would mix inconsistent rows; rerun with --no-resume."
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
    _report_mix_space(cfg, taxonomy, strategies, attempts)
    meta_rows = await _build_meta_prompts(cfg, router, taxonomy, strategies, attempts, quiet=quiet) if cfg.generation.meta_prompts else {}
    _stop_if_requested(cfg, "meta_prompts")
    completed_indexes = load_completed_attempt_indexes(raw_path) if resume else set()
    indexes = [index for index in range(attempts) if index not in completed_indexes]

    # Run generation concurrently, writing artifacts in completion order from the main task.
    # accepted.jsonl is read tolerantly: a crash mid-append can leave a torn final line.
    completed = len(completed_indexes)
    accepted_count = len([row for row in read_jsonl(accepted_path, tolerant=True) if row.get("accepted")]) if resume else 0
    checkpoint_every = max(1, cfg.generation.checkpoint_every)
    if indexes:
        phase(f"Generating {len(indexes)} attempts (target {target})")
        # A unit is the record_batch_size attempts one record call writes: from their scenarios on the
        # meta-prompt path (record_batch_size 1 = one record per call), or straight from their mixes on the
        # combined path. A unit's records are then critiqued in parallel. Bounding whole units
        # (concurrency // record_batch_size of them) keeps in-flight model calls near `concurrency`, so a large
        # target does not launch every attempt at once and exhaust the HTTP pool or trip provider rate
        # limits, and a unit's critiques never queue behind other units' batch calls. Missing indexes are
        # regrouped on resume, so a batch that was cut short is finished in a later batch.
        size = cfg.generation.record_batch_size
        limiter = asyncio.Semaphore(max(1, cfg.generation.concurrency // size))

        async def _unit(unit: list[int]) -> list[dict[str, Any]]:
            async with limiter:
                if not cfg.generation.meta_prompts:
                    return await _combined_batch(cfg, router, taxonomy, strategies, unit)
                if size == 1:
                    return [await _generate_one_safe(cfg, router, meta_rows[unit[0]])]
                return await _scenario_batch(cfg, router, [meta_rows[index] for index in unit])

        units = [indexes[start : start + size] for start in range(0, len(indexes), size)]
        async with asyncio.TaskGroup() as tg:
            tasks = [tg.create_task(_unit(unit)) for unit in units]
            with track(len(indexes), "Generating", quiet=quiet) as advance:
                for future in asyncio.as_completed(tasks):
                    for row in await future:
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
        info("")
        info("[dim]All attempts already complete; nothing to generate.[/dim]")

    # Build the final artifact from accepted rows only; evaluation remains a separate command.
    accepted = [row for row in read_jsonl(accepted_path, tolerant=True) if row.get("accepted")]
    accepted_total = len(accepted)
    if cfg.evaluation.dedupe:
        accepted, _ = dedupe_rows(accepted)
    final = coverage_aware_trim(accepted, target) if cfg.generation.trim else accepted
    write_jsonl(final_path, final)
    info(f"[dim]Accepted {accepted_total} → {len(final)} final (target {target}, trim {'enabled' if cfg.generation.trim else 'disabled'})[/dim]")
    return final


def _stop_if_requested(cfg: Config, stage: str) -> None:
    # Halt at a stage boundary (same SystemExit mechanism as write_then_edit) so the stage's artifact
    # can be inspected/edited; a rerun reuses existing artifacts and continues from here.
    if cfg.generation.stop_after != stage:
        return
    info("")
    info(f"Stopped after {stage}. Edit the artifacts in {cfg.output_dir}, then rerun to continue.")
    raise SystemExit(0)


def _report_mix_space(cfg: Config, taxonomy: dict[str, Any], strategies: list[dict[str, Any]], attempts: int) -> None:
    # Sampling is independent per mix (with replacement), so a mix space near the number of mixes
    # drawn means repeated combinations; say how big the space is before paying for generation.
    space = sum(mix_space(taxonomy, strategy) for strategy in strategies)
    mixes = math.ceil(attempts / cfg.generation.meta_prompts_per_mix)
    info(f"[dim]Mix space: up to {space:,} distinct mixes; this run samples {mixes:,}[/dim]")
    if space < mixes:
        warn(f"only {space:,} distinct mixes for {mixes:,} samples, so mixes will repeat; widen or deepen the taxonomy for more variety.")


def _sample_lineage(cfg: Config, taxonomy: dict[str, Any], strategies: list[dict[str, Any]], mix_id: int, count: int) -> dict[str, Any]:
    # All generation randomness lives here: the strategy pick, the mix, then one complexify coin per
    # attempt sharing the mix. Seeded per mix (seed + mix_id), so reruns and resumes redraw identical
    # lineage; record/critic calls stay rng-free.
    rng = random.Random(cfg.seed + mix_id)
    strategy = choose_strategy(strategies, rng)
    mix = sample_mix(taxonomy, strategy, rng)
    harder = [rng.random() < cfg.generation.complexity_ratio for _ in range(count)]
    return {"strategy_id": strategy.get("id", "general"), "taxonomy_mix": mix, "harder": harder}


async def _build_meta_prompts(
    cfg: Config,
    router: ModelRouter,
    taxonomy: dict[str, Any],
    strategies: list[dict[str, Any]],
    attempts: int,
    *,
    quiet: bool,
) -> dict[int, dict[str, Any]]:
    """Produce one meta-prompt row per attempt index and persist them to meta_prompts.jsonl.

    One call writes meta_prompts_per_mix (K) distinct scenarios for one sampled mix, and every scenario
    becomes its own attempt: attempts mix_id*K .. mix_id*K+K-1 share that mix's lineage. Reuse-if-present
    like taxonomy/strategies: existing indexes are kept (so hand edits survive a rerun), only missing
    ones are generated; delete lines or the file to regenerate. A failed model call is stored with an
    `error` field and becomes a rejected dataset row downstream. Returns {attempt_index: row}.
    """
    path = artifact_path(cfg.output_dir, "meta_prompts")
    rows = {row["attempt_index"]: row for row in read_jsonl(path, tolerant=True) if isinstance(row.get("attempt_index"), int)}
    missing = [index for index in range(attempts) if index not in rows]
    if not missing:
        info(f"[dim]Reusing meta_prompts.jsonl ({len(rows)} prompts)[/dim]")
        return rows

    phase(f"Generating {len(missing)} meta prompts")
    k = cfg.generation.meta_prompts_per_mix
    limiter = asyncio.Semaphore(cfg.generation.concurrency)

    async def _mix_group(mix_ids: list[int]) -> list[dict[str, Any]]:
        # One scenario call covers meta_batch_size mixes (1 = today's one call per mix). Each mix's
        # attempts get their lineage first, so a failure still reports it; a mix whose sampling fails
        # only loses its own attempts.
        groups: list[tuple[list[int], dict[int, dict[str, Any]], dict[str, Any] | None]] = []
        for mix_id in mix_ids:
            indexes = list(range(mix_id * k, min(mix_id * k + k, attempts)))
            group = {index: {"attempt_index": index, "strategy_id": "error", "taxonomy_mix": [], "meta_prompt": "", "complexified": False} for index in indexes}
            try:
                lineage = _sample_lineage(cfg, taxonomy, strategies, mix_id, len(indexes))
                for position, index in enumerate(indexes):
                    group[index].update(strategy_id=lineage["strategy_id"], taxonomy_mix=lineage["taxonomy_mix"], complexified=lineage["harder"][position])
            except Exception as exc:  # noqa: BLE001 - e.g. unsatisfiable never_combine rules.
                lineage = None
                for row in group.values():
                    row["error"] = str(exc)
            groups.append((indexes, group, lineage))
        live = [(indexes, group, lineage) for indexes, group, lineage in groups if lineage is not None]
        try:
            async with limiter:
                scenario_lists = await _make_scenarios(cfg, router, [(lineage["taxonomy_mix"], len(indexes)) for indexes, _, lineage in live]) if live else []
                for (indexes, group, _), scenarios in zip(live, scenario_lists):
                    for position, index in enumerate(indexes):
                        row = group[index]
                        if position >= len(scenarios):
                            row["error"] = f"meta prompt call returned {len(scenarios)} of {len(indexes)} scenarios"
                            continue
                        try:
                            row["meta_prompt"] = await _complexify(cfg, router, scenarios[position]) if row["complexified"] else scenarios[position]
                        except Exception as exc:  # noqa: BLE001 - a failed complexify only loses its own attempt.
                            row["error"] = str(exc)
        except Exception as exc:  # noqa: BLE001 - a failed meta prompt call should not abort the phase.
            for _, group, _ in live:
                for row in group.values():
                    row["error"] = str(exc)
        return [row for _, group, _ in groups for index, row in group.items() if index not in rows]

    # Append rows in completion order from the main task, mirroring the generation loop's IO pattern.
    mix_ids = sorted({index // k for index in missing})
    size = cfg.generation.meta_batch_size
    async with asyncio.TaskGroup() as tg:
        tasks = [tg.create_task(_mix_group(mix_ids[start : start + size])) for start in range(0, len(mix_ids), size)]
        with track(len(missing), "Meta prompts", quiet=quiet) as advance:
            for future in asyncio.as_completed(tasks):
                for row in await future:
                    rows[row["attempt_index"]] = row
                    append_jsonl(path, row)
                    advance()
    return rows


def _run_fingerprint(cfg: Config, taxonomy: dict[str, Any], strategies: list[dict[str, Any]]) -> str:
    # Hash only the inputs that change a generated row's content or validity. target_size,
    # overgenerate_ratio, trim, concurrency, and checkpoint_every are deliberately excluded so a run can
    # be grown or re-paced and still resume. A prompt-module edit is caught via its file contents.
    # meta_prompts.jsonl is also excluded: growing a run appends new rows (which would spuriously
    # change the hash), and each dataset row records the meta_prompt it was actually built from.
    module_path = cfg.prompts.module_path
    generation = cfg.generation
    payload = {
        "description": cfg.description,
        "schema": cfg.schema,
        "seed": cfg.seed,
        "models": {role: cfg.data["models"][role]["model"] for role in ("strategic", "bulk", "critic")},
        "meta_prompts": generation.meta_prompts,
        "meta_prompts_per_mix": generation.meta_prompts_per_mix,
        "meta_batch_size": generation.meta_batch_size,
        "record_batch_size": generation.record_batch_size,
        "model_picked": generation.model_picked,
        "complexity_ratio": generation.complexity_ratio,
        "critic": generation.critic,
        "max_refine_attempts": generation.max_refine_attempts,
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
    meta_row: dict[str, Any],
    *,
    record: Any,
    schema_valid: bool,
    accepted: bool,
    rejection_reason: str | None,
    critic_verdicts: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    # Single source of truth for the dataset row shape (see the Artifact Contract in AGENTS.md).
    # Lineage (strategy, mix, meta prompt, complexify flag) always comes from the attempt's meta row,
    # so a failed attempt reports the same lineage a success would.
    index = meta_row["attempt_index"]
    return {
        "id": f"item-{index}-{uuid.uuid4().hex[:8]}",
        "attempt_index": index,
        "record": record,
        "output_format": cfg.output_format,
        "taxonomy_mix": meta_row.get("taxonomy_mix", []),
        "strategy_id": meta_row.get("strategy_id", "error"),
        "meta_prompt": meta_row.get("meta_prompt", ""),
        "complexified": bool(meta_row.get("complexified")),
        "generator_model": router.model_name("bulk"),
        "critic_verdicts": critic_verdicts or [],
        "schema_valid": schema_valid,
        "accepted": accepted,
        "rejection_reason": rejection_reason,
        "created_at": now_iso(),
    }


def _rejected(cfg: Config, router: ModelRouter, meta_row: dict[str, Any], reason: str) -> dict[str, Any]:
    return _build_row(cfg, router, meta_row, record="" if cfg.is_schema_free else {}, schema_valid=cfg.is_schema_free, accepted=False, rejection_reason=reason)


async def _generate_one_safe(cfg: Config, router: ModelRouter, meta_row: dict[str, Any]) -> dict[str, Any]:
    """Run one point generation from its meta-prompt row, converting failure into a rejected row.

    asyncio.TaskGroup cancels sibling tasks on the first unhandled exception, so this boundary stops a
    single bad point from aborting the whole batch.
    """
    # A meta row that failed (or was edited down to nothing) rejects without spending model calls.
    if meta_row.get("error") or not meta_row.get("meta_prompt"):
        return _rejected(cfg, router, meta_row, f"Meta prompt failed: {meta_row.get('error') or 'empty meta prompt'}")
    try:
        make = _make_text if cfg.is_schema_free else _make_record
        record, schema_valid, rejection_reason = await make(cfg, router, meta_row["meta_prompt"])
        return await _finish_row(cfg, router, meta_row, record, schema_valid, rejection_reason)
    except Exception as exc:  # noqa: BLE001 - point-level failures should checkpoint as rejected rows.
        return _rejected(cfg, router, meta_row, f"Generation failed: {exc}")


async def _scenario_batch(cfg: Config, router: ModelRouter, metas: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Meta-prompt path with record_batch_size > 1: one call writes the records for several scenarios.

    Each record keeps its own meta row (lineage + scenario); a failed meta row rejects without a call.
    """
    rows = [
        _rejected(cfg, router, meta_row, f"Meta prompt failed: {meta_row.get('error') or 'empty meta prompt'}")
        for meta_row in metas
        if meta_row.get("error") or not meta_row.get("meta_prompt")
    ]
    live = [meta_row for meta_row in metas if not meta_row.get("error") and meta_row.get("meta_prompt")]
    items = [{"id": f"r{position + 1}", "meta_prompt": meta_row["meta_prompt"]} for position, meta_row in enumerate(live)]
    if not items:
        return rows
    prompt = cfg.prompts.generate_record_batch_prompt(cfg.description, cfg.schema, items)
    return rows + await _write_batch(cfg, router, prompt, live, items, combined=False)


async def _combined_batch(
    cfg: Config,
    router: ModelRouter,
    taxonomy: dict[str, Any],
    strategies: list[dict[str, Any]],
    indexes: list[int],
) -> list[dict[str, Any]]:
    """Combined path: write one record per attempt in a single call, straight from the sampled mixes.

    No meta-prompt step: each attempt's lineage is sampled here (seeded per attempt, like the
    meta-prompt phase) and the model writes a one-line plan plus the record for every spec. The row's
    meta_prompt holds the plan + the sampled ingredients: what the record was built from, and what the
    critic judges it against. Factors in generation.model_picked are left out of the specs and offered
    as options instead; the model's pick becomes that factor's lineage entry ("picked_by": "model").
    The full mix is still sampled first, so code-picked factors draw exactly what they would otherwise.
    """
    picked = cfg.generation.model_picked
    choices = [{"factor": factor, "options": level_options(taxonomy, factor, level)} for factor, level in picked.items()]
    rows: list[dict[str, Any]] = []
    metas: list[dict[str, Any]] = []
    items: list[dict[str, Any]] = []
    for index in indexes:
        meta_row = {"attempt_index": index, "strategy_id": "error", "taxonomy_mix": [], "meta_prompt": "", "complexified": False}
        try:
            lineage = _sample_lineage(cfg, taxonomy, strategies, index, 1)
        except Exception as exc:  # noqa: BLE001 - e.g. unsatisfiable never_combine rules.
            rows.append(_rejected(cfg, router, meta_row, f"Mix sampling failed: {exc}"))
            continue
        meta_row.update(strategy_id=lineage["strategy_id"], taxonomy_mix=lineage["taxonomy_mix"], complexified=lineage["harder"][0])
        meta_row["taxonomy_mix"] = [entry for entry in meta_row["taxonomy_mix"] if entry["factor"] not in picked]
        metas.append(meta_row)
        items.append({"id": f"r{len(items) + 1}", "mix": meta_row["taxonomy_mix"], "harder": meta_row["complexified"]})
    if not items:
        return rows
    prompt = cfg.prompts.combined_batch_prompt(cfg.description, cfg.schema, items, choices)
    return rows + await _write_batch(cfg, router, prompt, metas, items, combined=True, choices=choices, taxonomy=taxonomy)


def _picked_entries(item: dict[str, Any], choices: list[dict[str, Any]]) -> list[dict[str, Any]]:
    # Lineage for model-picked factors: the option the model named, or the factor root if it named none.
    picks = item.get("picked") if isinstance(item.get("picked"), dict) else {}
    entries = []
    for choice in choices:
        factor, name = choice["factor"], str(picks.get(choice["factor"], "")).strip()
        option = find_option(choice["options"], name)
        if option is None:
            option = {"name": name or "unpicked", "level": 0, "path": [factor], "description": "model pick not among the offered options"}
        entries.append({"factor": factor, "node": option["name"], "level": option["level"], "path": option["path"],
                        "description": option["description"], "picked_by": "model"})
    return entries


async def _write_batch(
    cfg: Config,
    router: ModelRouter,
    prompt: str,
    metas: list[dict[str, Any]],
    items: list[dict[str, Any]],
    *,
    combined: bool,
    choices: list[dict[str, Any]] | None = None,
    taxonomy: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Send one batch prompt, then validate, repair and critique each returned record on its own.

    One bad item never sinks the batch: a record missing from (or malformed in) the response rejects
    only its own attempt. Returns one row per meta row, in order.
    """
    try:
        response = await router.complete("bulk", prompt, system=cfg.prompts.SYSTEM_JSON, task=TaskType.GENERATE)
        written = {str(item.get("id")): item for item in _batch_items(response, "record")}
        missing_reason = "Record missing from the batch response (or malformed in it)."
    except Exception as exc:  # noqa: BLE001 - a failed batch call rejects its attempts, not the run.
        written, missing_reason = {}, f"Batch generation failed: {exc}"

    async def finish(meta_row: dict[str, Any], spec: dict[str, Any]) -> dict[str, Any]:
        item = written.get(spec["id"])
        if item is None:
            return _rejected(cfg, router, meta_row, missing_reason)
        if combined:
            if choices:  # restore one lineage entry per factor, in taxonomy order
                entries = {entry["factor"]: entry for entry in [*meta_row["taxonomy_mix"], *_picked_entries(item, choices)]}
                meta_row["taxonomy_mix"] = [entries[factor["name"]] for factor in (taxonomy or {}).get("factors", []) if factor["name"] in entries]
            plan = str(item.get("plan") or "").strip()
            meta_row["meta_prompt"] = "\n".join(filter(None, [plan, "Sampled ingredients:", mix_text(meta_row["taxonomy_mix"])]))
        record = item.get("record")
        if cfg.is_schema_free and not (isinstance(record, str) and record.strip()):
            return _rejected(cfg, router, meta_row, "Batch item has no text record.")
        try:
            if cfg.is_schema_free:
                checked = (record.strip(), True, None)
            else:
                checked = await _check_record(cfg, router, record, json.dumps(item, ensure_ascii=False))
            return await _finish_row(cfg, router, meta_row, *checked)
        except Exception as exc:  # noqa: BLE001 - point-level failures should checkpoint as rejected rows.
            return _rejected(cfg, router, meta_row, f"Generation failed: {exc}")

    return list(await asyncio.gather(*(finish(meta_row, spec) for meta_row, spec in zip(metas, items))))


def _batch_items(text: str, field: str) -> list[dict[str, Any]]:
    """The {"id": ..., field: ...} items of a batch response (the first list in the JSON object).

    Long batch JSON sometimes breaks at one item (a dropped brace or opener) while every other item is
    fine, so a response that does not parse as a whole is salvaged item by item: each `{"id": ...`
    object is decoded on its own and a malformed item loses only itself, never the batch.
    """
    try:
        parsed = extract_json_object(text)
        items = next((value for value in parsed.values() if isinstance(value, list)), None) if isinstance(parsed, dict) else None
    except ValueError:
        items = None
    if not isinstance(items, list):
        decoder = json.JSONDecoder()
        items = []
        for match in re.finditer(r'\{\s*"id"\s*:', text):
            try:
                items.append(decoder.raw_decode(text, match.start())[0])
            except json.JSONDecodeError:
                continue
    return [item for item in items if isinstance(item, dict) and field in item]


async def _finish_row(
    cfg: Config,
    router: ModelRouter,
    meta_row: dict[str, Any],
    record: Any,
    schema_valid: bool,
    rejection_reason: str | None,
) -> dict[str, Any]:
    # Shared tail of both record paths: build the row, then critique/refine a structurally valid record
    # (or accept it as is when generation.critic is off). Applies to JSON and free-text records alike.
    row = _build_row(cfg, router, meta_row, record=record, schema_valid=schema_valid, accepted=False, rejection_reason=rejection_reason)
    if not schema_valid:
        return row
    if not cfg.generation.critic:
        row["accepted"] = True
        return row
    row["record"], row["accepted"], row["rejection_reason"], row["critic_verdicts"] = await _critic_loop(cfg, router, meta_row["meta_prompt"], record)
    if not cfg.is_schema_free:
        row["schema_valid"] = validate_record(cfg.schema, row["record"], validator=cfg.validator)[0]
        if not row["schema_valid"]:
            row["accepted"] = False
            row["rejection_reason"] = "Record failed schema validation after critique refinement."
    return row


async def _make_scenarios(cfg: Config, router: ModelRouter, specs: list[tuple[list[dict[str, Any]], int]]) -> list[list[str]]:
    """Scenario lists for (mix, count) specs: one meta_prompt call for a single mix, or one batched call
    covering every mix (meta_batch_size > 1). A mix missing from a batched reply gets no scenarios."""

    def clean(values: Any) -> list[str]:
        # Every non-blank scenario is kept (each one becomes its own attempt); blanks and non-strings drop.
        return [text.strip() for text in values or [] if isinstance(text, str) and text.strip()]

    if len(specs) == 1:
        mix, count = specs[0]
        prompt = cfg.prompts.meta_prompt_prompt(cfg.description, cfg.schema, mix, count)
        response = await router.complete_json("bulk", prompt, system=cfg.prompts.SYSTEM_JSON, task=TaskType.META_PROMPT)
        return [clean(response.get("meta_prompts"))]
    items = [{"id": f"m{position + 1}", "mix": mix, "count": count} for position, (mix, count) in enumerate(specs)]
    prompt = cfg.prompts.meta_prompt_batch_prompt(cfg.description, cfg.schema, items)
    response = await router.complete("bulk", prompt, system=cfg.prompts.SYSTEM_JSON, task=TaskType.META_PROMPT)
    written = {str(item.get("id")): item for item in _batch_items(response, "meta_prompts")}
    return [clean(written.get(item["id"], {}).get("meta_prompts")) for item in items]


async def _complexify(cfg: Config, router: ModelRouter, meta_prompt: str) -> str:
    # Sampled per attempt (the complexify coin) so the final set mixes simple and difficult items.
    response = await router.complete_json(
        "bulk",
        cfg.prompts.complexify_prompt(cfg.description, meta_prompt),
        system=cfg.prompts.SYSTEM_JSON,
        task=TaskType.COMPLEXIFY,
    )
    return response.get("meta_prompt") or meta_prompt


async def _make_record(cfg: Config, router: ModelRouter, meta_prompt: str) -> tuple[Any, bool, str | None]:
    """Generate a JSON record from a meta prompt; _check_record validates (and repairs) it."""
    raw_response = await router.complete(
        "bulk",
        cfg.prompts.generate_record_prompt(cfg.description, cfg.schema, meta_prompt),  # type: ignore[arg-type]
        system=cfg.prompts.SYSTEM_JSON,
        task=TaskType.GENERATE,
    )
    try:
        record = extract_json_object(raw_response)
    except Exception as exc:
        return await _check_record(cfg, router, None, raw_response, str(exc))
    return await _check_record(cfg, router, record, raw_response)


async def _check_record(cfg: Config, router: ModelRouter, record: Any, raw: str, error: str | None = None) -> tuple[Any, bool, str | None]:
    """Validate a parsed JSON record against the schema and give one repair attempt on failure.

    Returns (record, schema_valid, rejection_reason). `error` carries a parse failure (record None) into
    the repair prompt. The repair pass is the only place we re-ask the model after a parse/validation
    miss; persistent failures become a rejected row.
    """
    if error is None:
        valid, error = validate_record(cfg.schema, record, validator=cfg.validator)
        if valid:
            return record, True, None

    # Give the model one tightly scoped repair attempt before rejecting.
    try:
        repaired = await router.complete_json(
            "bulk",
            cfg.prompts.repair_json_prompt(cfg.schema, raw, error or "schema validation failed"),  # type: ignore[arg-type]
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
    """Critique, refine, and critique again, at most max_refine_attempts refines; shared by JSON and text.

    Every refined record is critiqued before it can be accepted, so the loop always ends on a verdict.
    A rejecting critic may also return "revised", a corrected record: when it is structurally valid it
    is accepted as is (the critic wrote it against its own findings), which skips the refine call and
    the re-critique. Such a fix shows up as an accepted row whose last verdict is "reject". The other
    mode-specific parts are the prompts and that a JSON refine re-validates against the schema (a
    refine that breaks the schema rejects the point). Returns (record, accepted, reason, verdicts).
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

    def usable(fix: Any) -> bool:
        if schema_free:
            return isinstance(fix, str) and bool(fix.strip())
        return fix is not None and validate_record(cfg.schema, fix, validator=cfg.validator)[0]

    verdicts: list[dict[str, Any]] = []
    current = record
    for refines in range(cfg.generation.max_refine_attempts + 1):
        verdict = await critique(current)
        fix = verdict.pop("revised", None)
        verdicts.append(verdict)
        if verdict.get("verdict") == "accept":
            return current, True, None, verdicts
        if usable(fix):
            return fix.strip() if schema_free else fix, True, None, verdicts
        if refines == cfg.generation.max_refine_attempts:
            break
        try:
            current, error = await refine(current, verdict.get("explanation", ""))
        except Exception as exc:  # noqa: BLE001 - a failed refine call rejects this point.
            return current, False, f"Refinement failed: {exc}", verdicts
        if error is not None:  # JSON refinement produced a schema-invalid record.
            return current, False, error, verdicts
    return current, False, verdicts[-1].get("explanation", "Critic rejected record."), verdicts
