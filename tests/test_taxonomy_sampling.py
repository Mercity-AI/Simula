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


def test_node_names_cannot_carry_the_path_separator() -> None:
    from simula.taxonomy import _child

    child = _child({"name": "A/V Receivers", "description": "d"}, 1, ["electronics"])
    assert child["name"] == "A-V Receivers"
    assert child["path"] == ["electronics", "A-V Receivers"]


def test_dotted_names_inside_slash_paths_stay_one_segment() -> None:
    taxonomy = {"factors": [_node("audio", ["audio"], [_node("5.1-Channel Systems", ["audio", "5.1-Channel Systems"])])]}
    paths = taxonomy_path_strings(taxonomy)
    assert "audio/5.1-Channel Systems" in paths
    assert _invalid_strategy_paths([{"taxonomy_roots": paths}], taxonomy) == []
    # Dot-only paths still work when there is no slash at all.
    assert _invalid_strategy_paths([{"taxonomy_roots": ["audio"]}], taxonomy) == []


def test_mix_space_counts_reachable_leaves() -> None:
    from simula.taxonomy import mix_space

    taxonomy = _lopsided_taxonomy()
    assert mix_space(taxonomy, {"taxonomy_roots": ["pov"]}) == 12
    assert mix_space(taxonomy, {"taxonomy_roots": ["pov/small"]}) == 2
    taxonomy["factors"][0]["children"][0]["weight"] = 0  # disable "big"
    assert mix_space(taxonomy, {"taxonomy_roots": []}) == 2


def test_level_options_carry_the_weighted_walk_share() -> None:
    from simula.taxonomy import level_options

    taxonomy = _lopsided_taxonomy()
    options = level_options(taxonomy, "pov", 1)
    assert [(o["name"], o["share"]) for o in options] == [("big", 0.5), ("small", 0.5)]
    taxonomy["factors"][0]["children"][0]["weight"] = 3.0  # "big" three times as likely as "small"
    leaves = level_options(taxonomy, "pov", 2)
    assert len(leaves) == 12 and abs(sum(o["share"] for o in leaves) - 1) < 1e-9
    assert abs(leaves[0]["share"] - 0.075) < 1e-9 and abs(leaves[-1]["share"] - 0.125) < 1e-9
    with pytest.raises(ValueError, match="unknown factor"):
        level_options(taxonomy, "missing", 1)


def test_code_level_stops_at_the_ancestor_with_exact_shares() -> None:
    from simula.taxonomy import mix_space

    taxonomy = _lopsided_taxonomy()
    taxonomy["factors"].append(_node("tone", ["tone"], [_node("warm", ["tone", "warm"]), _node("dry", ["tone", "dry"])]))
    strategy = {"taxonomy_roots": []}
    full = [sample_mix(taxonomy, strategy, random.Random(seed)) for seed in range(2000)]
    cut = [sample_mix(taxonomy, strategy, random.Random(seed), {"pov": 1}) for seed in range(2000)]
    for leaf_mix, cut_mix in zip(full, cut):
        # The cut entry is the drawn leaf's own level-1 ancestor, and the other factor's draw is unchanged.
        assert cut_mix[0]["path"] == leaf_mix[0]["path"][:2] and cut_mix[0]["level"] == 1
        assert cut_mix[0]["node"] in {"big", "small"} and cut_mix[0]["description"] == f"{cut_mix[0]['node']} node"
        assert cut_mix[1] == leaf_mix[1]
    big = sum(1 for mix in cut if mix[0]["node"] == "big")
    assert 0.45 < big / len(cut) < 0.55
    # A level at or below the leaves changes nothing; the mix space counts nodes at the cut level.
    assert sample_mix(taxonomy, strategy, random.Random(3), {"pov": 5}) == sample_mix(taxonomy, strategy, random.Random(3))
    assert mix_space(taxonomy, strategy) == 24 and mix_space(taxonomy, strategy, {"pov": 1}) == 4


def test_never_combine_is_checked_on_the_leaf_before_the_cut() -> None:
    taxonomy = _lopsided_taxonomy()
    taxonomy["factors"].append(_node("tone", ["tone"], [_node("warm", ["tone", "warm"]), _node("dry", ["tone", "dry"])]))
    strategy = {"taxonomy_roots": [], "never_combine": [["pov/small/small_leaf_0", "tone/dry"]]}
    full = [sample_mix(taxonomy, strategy, random.Random(seed)) for seed in range(500)]
    cut = [sample_mix(taxonomy, strategy, random.Random(seed), {"pov": 1}) for seed in range(500)]
    assert not any(mix[0]["node"] == "small_leaf_0" and mix[1]["node"] == "dry" for mix in full)
    assert [mix[1] for mix in cut] == [mix[1] for mix in full]  # same redraws, so the rule held on the leaf
