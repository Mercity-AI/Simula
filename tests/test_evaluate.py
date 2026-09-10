from pathlib import Path
import random

import pytest
from jsonschema import Draft202012Validator
from simula.utils import ngrams_for_text, record_to_text

from simula.evaluate import (
    _dedupe_prefix_keys,
    _complexity_schedule,
    coverage_aware_trim,
    coverage_report,
    decontaminate_rows,
    dedupe_rows,
    validate_record,
)


def test_validate_record() -> None:
    schema = {"type": "object", "required": ["x"], "properties": {"x": {"type": "string"}}}
    assert validate_record(schema, {"x": "ok"}) == (True, None)
    assert validate_record(schema, {"x": 1})[0] is False


def test_validate_record_uses_precompiled_validator() -> None:
    schema = {"type": "object", "required": ["x"], "properties": {"x": {"type": "string"}}}
    validator = Draft202012Validator(schema)
    # A precompiled validator is reused and takes precedence over the schema argument.
    assert validate_record(None, {"x": "ok"}, validator=validator) == (True, None)
    assert validate_record(None, {"x": 1}, validator=validator)[0] is False


def test_complexity_schedule_keeps_every_row_without_singletons() -> None:
    # 3 rows in batches of 2 would leave a trailing batch of 1; it must merge, not drop.
    rows = [{"id": str(i), "record": {}} for i in range(3)]
    schedule = _complexity_schedule(rows, batch_size=2, appearances=1)
    assert all(len(batch) >= 2 for batch in schedule)
    assert {row["id"] for batch in schedule for row in batch} == {"0", "1", "2"}


def test_dedupe_rows() -> None:
    rows = [{"id": "a", "record": {"x": "same"}}, {"id": "b", "record": {"x": "same"}}]
    kept, removed = dedupe_rows(rows)
    assert [row["id"] for row in kept] == ["a"]
    assert removed == ["b"]


def test_dedupe_does_not_collapse_records_without_word_tokens() -> None:
    # `{}` and `[]` both render to text with no word tokens, so they yield empty n-grams.
    # They must NOT be treated as duplicates of each other (the old jaccard(set(),set())==1.0 bug).
    rows = [{"id": "a", "record": {}}, {"id": "b", "record": []}]
    kept, removed = dedupe_rows(rows)
    assert [row["id"] for row in kept] == ["a", "b"]
    assert removed == []


@pytest.mark.parametrize("threshold", [0, 0.3, 0.8, 1.0])
@pytest.mark.parametrize("n", [1, 3, 13])
def test_indexed_dedupe_matches_exhaustive_comparisons(threshold, n) -> None:
    rng = random.Random(71)
    records = [{}, [], "!!!", "short", "short"]
    for _ in range(25):
        words = [f"word{rng.randrange(40)}" for _ in range(rng.randrange(15, 70))]
        record = {"messages": [{"role": "customer", "content": " ".join(words)}], "product": "sample"}
        records.extend([record, dict(reversed(list(record.items())))])
        words[rng.randrange(len(words))] = "changed"
        records.append({"messages": [{"role": "customer", "content": " ".join(words)}], "product": "sample"})
    rows = [{"id": str(i), "record": record} for i, record in enumerate(records)]
    kept, removed, gram_sets = [], [], []
    for row in rows:
        grams = ngrams_for_text(record_to_text(row["record"]), n)
        duplicate = bool(grams) and any(
            len(grams & other) / max(1, len(grams | other)) >= threshold for other in gram_sets
        )
        if duplicate:
            removed.append(row["id"])
        else:
            kept.append(row)
            gram_sets.append(grams)
    assert dedupe_rows(rows, n=n, threshold=threshold) == (kept, removed)


def test_dedupe_hash_collisions_do_not_remove_distinct_records(monkeypatch) -> None:
    monkeypatch.setattr("simula.evaluate._dedupe_gram_key", lambda gram: b"collision")
    rows = [
        {"id": "a", "record": "alpha beta gamma delta"},
        {"id": "b", "record": "one two three four"},
        {"id": "c", "record": "alpha beta gamma delta"},
    ]
    kept, removed = dedupe_rows(rows, n=2)
    assert [row["id"] for row in kept] == ["a", "b"]
    assert removed == ["c"]


def test_dedupe_checks_exact_threshold_and_only_kept_rows() -> None:
    # A/B overlap is exactly 0.8; B/C overlap is also 0.8, but A/C is below it.
    # B is removed and must not cause C to be removed through the index.
    rows = [
        {"id": "a", "record": "a b c d e f g h i"},
        {"id": "b", "record": "a b c d e f g h j"},
        {"id": "c", "record": "a b c d e f g j k"},
    ]
    kept, removed = dedupe_rows(rows, n=1, threshold=0.8)
    assert [row["id"] for row in kept] == ["a", "c"]
    assert removed == ["b"]


def test_prefix_filter_never_excludes_a_match_exhaustively() -> None:
    # Every nonempty subset of seven distinct 13-word sequences, paired with every other subset.
    # This checks the no-false-negative property independently of the greedy dedupe loop.
    universe = [tuple(f"sequence{i}word{j}" for j in range(13)) for i in range(7)]
    sets = [{gram for i, gram in enumerate(universe) if mask & (1 << i)} for mask in range(1, 128)]
    for threshold in (0.3, 0.5, 0.8, 1.0):
        prefixes = [set(_dedupe_prefix_keys(grams, threshold)) for grams in sets]
        for i, left in enumerate(sets):
            for j, right in enumerate(sets):
                if len(left & right) / len(left | right) >= threshold:
                    assert prefixes[i] & prefixes[j], (threshold, left, right)


@pytest.mark.parametrize("reverse", [False, True])
def test_jaccard13_containment_at_threshold_in_both_orders(reverse) -> None:
    # Unique tokens yield exactly 100 and 125 distinct 13-grams: similarity is exactly 0.8.
    words = [f"word{i}" for i in range(137)]
    rows = [{"id": "short", "record": " ".join(words[:112])},
            {"id": "long", "record": " ".join(words)}]
    if reverse:
        rows.reverse()
    assert dedupe_rows(rows) == ([rows[0]], [rows[1]["id"]])


def test_jaccard13_long_record_spans_index_query_chunks() -> None:
    # More than 900 prefix keys exercises the chunked SQLite lookup with the real n=13 default.
    text = " ".join(f"word{i}" for i in range(5000))
    rows = [{"id": "a", "record": text}, {"id": "b", "record": text}]
    assert dedupe_rows(rows) == ([rows[0]], ["b"])


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
    # A leaf/branch sample also covers its ancestors: the root level must not read as uncovered.
    assert report["topic"]["0"]["ratio"] == 1.0


def test_coverage_aware_trim_prefers_new_branches() -> None:
    rows = [
        {"id": "a", "taxonomy_mix": [{"factor": "topic", "node": "x", "path": ["topic", "x"]}]},
        {"id": "b", "taxonomy_mix": [{"factor": "topic", "node": "x", "path": ["topic", "x"]}]},
        {"id": "c", "taxonomy_mix": [{"factor": "topic", "node": "y", "path": ["topic", "y"]}]},
    ]
    trimmed = coverage_aware_trim(rows, 2)
    assert {row["id"] for row in trimmed} == {"a", "c"}
