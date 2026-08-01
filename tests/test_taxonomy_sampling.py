import random

import pytest

from simula.taxonomy import _invalid_strategy_paths, sample_mix, taxonomy_path_strings


def _node(name: str, path: list[str], children: list[dict] | None = None, weight: float | None = None) -> dict:
    node = {"name": name, "description": f"{name} node", "level": len(path) - 1, "path": path, "children": children or []}
    if weight is not None:
        node["weight"] = weight
    return node


def _lopsided_taxonomy() -> dict:
    # Branch "big" has 10 leaves, branch "small" has 2. A flat draw over all nodes would land in
    # "big" ~5x more often; the level-wise walk must keep the branches even.
    big_leaves = [_node(f"big_leaf_{i}", ["pov", "big", f"big_leaf_{i}"]) for i in range(10)]
    small_leaves = [_node(f"small_leaf_{i}", ["pov", "small", f"small_leaf_{i}"]) for i in range(2)]
    return {
        "factors": [
            _node("pov", ["pov"], [_node("big", ["pov", "big"], big_leaves), _node("small", ["pov", "small"], small_leaves)])
        ]
    }


def _draw_many(taxonomy: dict, strategy: dict, n: int = 4000) -> list[list[dict]]:
    rng = random.Random(7)
    return [sample_mix(taxonomy, strategy, rng) for _ in range(n)]


def test_branch_probability_ignores_leaf_count() -> None:
    mixes = _draw_many(_lopsided_taxonomy(), {"taxonomy_roots": ["pov"]})
    big = sum(1 for mix in mixes if mix[0]["path"][1] == "big")
    assert 0.45 < big / len(mixes) < 0.55  # flat walk would give ~0.85


def test_node_weights_set_branch_probability() -> None:
    taxonomy = _lopsided_taxonomy()
    taxonomy["factors"][0]["children"][0]["weight"] = 0.05  # big
    taxonomy["factors"][0]["children"][1]["weight"] = 1.0  # small
    mixes = _draw_many(taxonomy, {"taxonomy_roots": ["pov"]})
    big = sum(1 for mix in mixes if mix[0]["path"][1] == "big")
    assert big / len(mixes) < 0.10  # ~0.048 expected


def test_zero_weight_disables_branch() -> None:
    taxonomy = _lopsided_taxonomy()
    taxonomy["factors"][0]["children"][0]["weight"] = 0.0
    mixes = _draw_many(taxonomy, {"taxonomy_roots": ["pov"]}, n=500)
    assert all(mix[0]["path"][1] == "small" for mix in mixes)


def test_only_leaves_are_sampled() -> None:
    for mix in _draw_many(_lopsided_taxonomy(), {"taxonomy_roots": ["pov"]}, n=500):
        assert len(mix[0]["path"]) == 3  # never the factor root or a branch header


def test_slash_separated_roots_match() -> None:
    for seed in range(10):
        mix = sample_mix(_lopsided_taxonomy(), {"taxonomy_roots": ["pov/small"]}, random.Random(seed))
        assert mix[0]["path"][:2] == ["pov", "small"]


def _two_factor_taxonomy() -> dict:
    return {
        "factors": [
            _node("pov", ["pov"], [_node("plain", ["pov", "plain"]), _node("weird", ["pov", "weird"])]),
            _node("structure", ["structure"], [_node("arc", ["structure", "arc"]), _node("frame", ["structure", "frame"])]),
        ]
    }


def test_never_combine_is_enforced() -> None:
    strategy = {"taxonomy_roots": ["pov", "structure"], "never_combine": [["pov/weird", "structure/frame"]]}
    for mix in _draw_many(_two_factor_taxonomy(), strategy, n=2000):
        by_factor = {row["factor"]: row for row in mix}
        assert not (by_factor["pov"]["node"] == "weird" and by_factor["structure"]["node"] == "frame")


def test_unsatisfiable_never_combine_raises() -> None:
    taxonomy = {
        "factors": [
            _node("pov", ["pov"], [_node("weird", ["pov", "weird"])]),
            _node("structure", ["structure"], [_node("frame", ["structure", "frame"])]),
        ]
    }
    strategy = {"id": "stuck", "taxonomy_roots": ["pov", "structure"], "never_combine": [["pov/weird", "structure/frame"]]}
    with pytest.raises(ValueError, match="never_combine"):
        sample_mix(taxonomy, strategy, random.Random(1))


def test_taxonomy_path_strings_lists_every_node() -> None:
    paths = taxonomy_path_strings(_two_factor_taxonomy())
    assert paths == ["pov", "pov/plain", "pov/weird", "structure", "structure/arc", "structure/frame"]


def test_invalid_strategy_paths_flags_unknown_refs_only() -> None:
    taxonomy = _two_factor_taxonomy()
    good = {"taxonomy_roots": ["pov", "structure.arc"], "never_combine": [["pov/weird", "structure/frame"]]}
    assert _invalid_strategy_paths([good], taxonomy) == []
    bad = {"taxonomy_roots": ["pov/scifi", "structure"], "never_combine": [["pov/weird", "structure/framed"]]}
    assert _invalid_strategy_paths([bad], taxonomy) == ["pov/scifi", "structure/framed"]


def test_child_nodes_carry_prevalence_weight() -> None:
    from simula.taxonomy import _child

    assert _child({"name": "x", "weight": 0.05}, 1, ["f"])["weight"] == 0.05
    assert _child({"name": "x"}, 1, ["f"])["weight"] == 1.0
    assert _child({"name": "x", "weight": "junk"}, 1, ["f"])["weight"] == 1.0
