"""Shared helpers: artifact filenames, JSON/JSONL IO, timestamps, text extraction, cost summary."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


ARTIFACTS = {
    "taxonomy": "taxonomy.json",
    "strategies": "strategies.json",
    "raw": "dataset.raw.jsonl",
    "accepted": "dataset.accepted.jsonl",
    "final": "dataset.final.jsonl",
    "evaluated": "dataset.evaluated.jsonl",
    "eval": "eval_report.json",
    "state": "run_state.json",
    "llm_calls": "llm_calls.jsonl",
    "cost": "cost_summary.json",
    "embedding_cache": "embeddings.cache.npz",
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def artifact_path(output_dir: Path, name: str) -> Path:
    return output_dir / ARTIFACTS[name]


def read_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, data: Any) -> None:
    ensure_dir(path.parent)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def read_jsonl(path: Path, *, tolerant: bool = False) -> list[dict[str, Any]]:
    # tolerant=True skips blank/corrupt lines, which matters for files appended to live
    # (a SIGKILL mid-append can leave a torn final line). Strict mode raises on bad JSON.
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            if tolerant:
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
            else:
                rows.append(json.loads(line))
    return rows


def append_jsonl(path: Path, row: dict[str, Any]) -> None:
    ensure_dir(path.parent)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    ensure_dir(path.parent)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def record_to_text(record: Any, text_field: str | None = None) -> str:
    if isinstance(record, str):
        return record
    if text_field:
        matches = _field_value(record, text_field)
        if matches:
            return "\n".join(str(match) for match in matches)
    return json.dumps(record, sort_keys=True, ensure_ascii=False)


def ngrams_for_text(text: str, n: int = 13) -> set[tuple[str, ...]]:
    tokens = re.findall(r"\w+", text.casefold())
    if not tokens:
        return set()
    if len(tokens) < n:
        return {tuple(tokens)}
    return {tuple(tokens[i : i + n]) for i in range(len(tokens) - n + 1)}


def load_completed_attempt_indexes(path: Path) -> set[int]:
    indexes: set[int] = set()
    for row in read_jsonl(path, tolerant=True):
        if isinstance(row.get("attempt_index"), int):
            indexes.add(row["attempt_index"])
            continue
        match = re.match(r"item-(\d+)-", str(row.get("id", "")))
        if match:
            indexes.add(int(match.group(1)))
    return indexes


def extract_json_object(text: str) -> Any:
    """Parse a model response as JSON, unwrapping a ```json fence if the model added one.

    The system prompt already demands raw JSON with no commentary, so we deliberately do NOT try
    to slice JSON out of surrounding prose: a first-brace-to-last-brace heuristic splices unrelated
    braces together and corrupts otherwise-recoverable output. Record generation has its own repair
    pass for the rare model that ignores the instruction.
    """
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, flags=re.DOTALL | re.IGNORECASE)
    if fenced:
        return json.loads(fenced.group(1).strip())
    raise ValueError("Response was not valid JSON.")


def _field_value(record: Any, field: str) -> list[Any]:
    # Direct nested-key access into a record dict: "query" or "extraction.intent" (a leading "$."
    # is tolerated). Records share a fixed shape, so this is all the field-targeting we need — no
    # JSONPath engine. Returns [value] when the path resolves, else [] (caller falls back to JSON).
    current = record
    for part in field.lstrip("$.").split("."):
        if isinstance(current, dict) and part in current:
            current = current[part]
        else:
            return []
    return [current]


def summarize_cost(cost: dict[tuple[str, str, str], list[float]], elapsed_seconds: float) -> dict[str, Any]:
    """Roll up the ModelRouter's per-(role, task, model) accumulator into cost_summary.json.

    `cost` maps (role, task, model) -> [calls, in_tokens, out_tokens, duration]; this flattens it
    into sorted per-key rows plus run-wide totals.
    """
    rows = []
    total_calls = total_in = total_out = 0
    total_duration = 0.0
    for role, task, model in sorted(cost):
        calls, in_toks, out_toks, duration = cost[(role, task, model)]
        rows.append(
            {
                "role": role,
                "task": task,
                "model": model,
                "calls": int(calls),
                "input_tokens": int(in_toks),
                "output_tokens": int(out_toks),
                "duration_seconds": round(duration, 3),
            }
        )
        total_calls += int(calls)
        total_in += int(in_toks)
        total_out += int(out_toks)
        total_duration += duration
    return {
        "elapsed_seconds": round(elapsed_seconds, 3),
        "total_calls": total_calls,
        "total_input_tokens": total_in,
        "total_output_tokens": total_out,
        "total_duration_seconds": round(total_duration, 3),
        "by_role_task_model": rows,
    }
