import json
from pathlib import Path

import pytest

from simula.utils import append_jsonl, ngrams_for_text, read_jsonl, record_to_text


def test_jsonl_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "rows.jsonl"
    append_jsonl(path, {"a": 1})
    append_jsonl(path, {"b": 2})
    assert read_jsonl(path) == [{"a": 1}, {"b": 2}]


def test_read_jsonl_tolerant_skips_torn_line(tmp_path: Path) -> None:
    # A crash mid-append can leave a torn final line; tolerant reads recover the good rows.
    path = tmp_path / "torn.jsonl"
    path.write_text('{"a": 1}\n{"b": 2\n', encoding="utf-8")
    assert read_jsonl(path, tolerant=True) == [{"a": 1}]
    with pytest.raises(json.JSONDecodeError):
        read_jsonl(path)


def test_record_to_text_dotted_field() -> None:
    assert record_to_text({"x": {"y": "hello"}}, "x.y") == "hello"
    assert record_to_text({"x": {"y": "hello"}}, "$.x.y") == "hello"  # leading $. tolerated


def test_ngrams_for_short_text() -> None:
    assert ngrams_for_text("Hello   world") == {("hello", "world")}


def test_summarize_cost_rolls_up_usage_extras() -> None:
    from simula.utils import summarize_cost

    cost = {
        ("bulk", "generate", "m"): [2, 100, 50, 1.5, 30, 10, 0.002],
        ("critic", "semantic_critic", "m"): [1, 40, 20, 0.5, 15, 0, 0.0005],
    }
    summary = summarize_cost(cost, 3.0)
    assert summary["total_calls"] == 3
    assert summary["total_input_tokens"] == 140 and summary["total_output_tokens"] == 70
    assert summary["total_reasoning_tokens"] == 45 and summary["total_cached_tokens"] == 10
    assert summary["total_cost"] == 0.0025
    assert summary["by_role_task_model"][0]["reasoning_tokens"] == 30
