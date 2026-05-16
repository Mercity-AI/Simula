from pathlib import Path

from syndata.utils import append_jsonl, ngrams_for_text, read_jsonl, record_to_text


def test_jsonl_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "rows.jsonl"
    append_jsonl(path, {"a": 1})
    append_jsonl(path, {"b": 2})
    assert read_jsonl(path) == [{"a": 1}, {"b": 2}]


def test_record_to_text_jsonpath() -> None:
    assert record_to_text({"x": {"y": "hello"}}, "$.x.y") == "hello"


def test_ngrams_for_short_text() -> None:
    assert ngrams_for_text("Hello   world") == {("hello", "world")}
