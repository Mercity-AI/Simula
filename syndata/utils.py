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
        matches = _jsonpath_matches(record, text_field)
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
    """Pull the first JSON object/array out of a model response."""
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, flags=re.DOTALL | re.IGNORECASE)
    if fenced:
        return json.loads(fenced.group(1).strip())

    starts = [i for i in [text.find("{"), text.find("[")] if i >= 0]
    if not starts:
        raise ValueError("No JSON object or array found in response.")
    start = min(starts)
    end = max(text.rfind("}"), text.rfind("]"))
    if end <= start:
        raise ValueError("Incomplete JSON object or array in response.")
    return json.loads(text[start : end + 1])


def _jsonpath_matches(record: Any, expression: str) -> list[Any]:
    # jsonpath-ng is a declared dependency; a malformed/unsupported expression returns no matches.
    from jsonpath_ng import parse

    try:
        return [match.value for match in parse(expression).find(record)]
    except Exception:
        return []
