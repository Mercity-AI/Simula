from pathlib import Path

from syndata.evaluate import coverage_aware_trim, coverage_report, decontaminate_rows, dedupe_rows, validate_record


def test_validate_record() -> None:
    schema = {"type": "object", "required": ["x"], "properties": {"x": {"type": "string"}}}
    assert validate_record(schema, {"x": "ok"}) == (True, None)
    assert validate_record(schema, {"x": 1})[0] is False


def test_dedupe_rows() -> None:
    rows = [{"id": "a", "record": {"x": "same"}}, {"id": "b", "record": {"x": "same"}}]
    kept, removed = dedupe_rows(rows)
    assert [row["id"] for row in kept] == ["a"]
    assert removed == ["b"]


def test_decontaminate_rows(tmp_path: Path) -> None:
    ref = tmp_path / "ref.jsonl"
    ref.write_text('{"record":"alpha beta gamma"}\n', encoding="utf-8")
    rows = [{"id": "a", "record": "alpha beta gamma"}, {"id": "b", "record": "different text"}]
    kept, removed = decontaminate_rows(rows, [str(ref)], n=2, threshold=0.8)
    assert [row["id"] for row in kept] == ["b"]
    assert removed == ["a"]


def test_coverage_report_from_lineage() -> None:
    taxonomy = {"factors": [{"name": "topic", "level": 0, "path": ["topic"], "children": [{"name": "alpha", "level": 1, "path": ["topic", "alpha"], "children": []}]}]}
    rows = [{"taxonomy_mix": [{"factor": "topic", "node": "alpha", "level": 1, "path": ["topic", "alpha"]}]}]
    report = coverage_report(taxonomy, rows)
    assert report["topic"]["1"]["ratio"] == 1.0


def test_coverage_aware_trim_prefers_new_branches() -> None:
    rows = [
        {"id": "a", "taxonomy_mix": [{"factor": "topic", "node": "x", "path": ["topic", "x"]}]},
        {"id": "b", "taxonomy_mix": [{"factor": "topic", "node": "x", "path": ["topic", "x"]}]},
        {"id": "c", "taxonomy_mix": [{"factor": "topic", "node": "y", "path": ["topic", "y"]}]},
    ]
    trimmed = coverage_aware_trim(rows, 2)
    assert {row["id"] for row in trimmed} == {"a", "c"}
