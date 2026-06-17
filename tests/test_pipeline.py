from pathlib import Path

import asyncio
import yaml

from syndata.cli import main
from syndata.config import load_config
from syndata.generate import _generate_one_safe, generate_dataset
from syndata.models import ModelRouter
from syndata.utils import artifact_path, read_jsonl
from syndata.taxonomy import build_strategies, build_taxonomy, sample_mix


def write_config(tmp_path: Path, extra: dict | None = None) -> Path:
    data = {
        "project": {"name": "test", "output_dir": str(tmp_path / "run"), "seed": 3},
        "description": "Generate tiny QA examples.",
        "schema": {
            "type": "object",
            "required": ["input", "output"],
            "properties": {"input": {"type": "string"}, "output": {"type": "string"}},
        },
        "models": {
            "strategic": {"base_url": "fake", "model": "fake"},
            "bulk": {"base_url": "fake", "model": "fake"},
            "critic": {"base_url": "fake", "model": "fake"},
        },
        "taxonomy": {"depth": 1, "best_of_n": 1, "review_mode": "auto_accept", "children_per_node": 2},
        "generation": {"target_size": 5, "overgenerate_ratio": 1.2, "complexity_ratio": 0, "scenarios_per_mix": 2},
    }
    if extra:
        data.update(extra)
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return path


def test_cli_validate(tmp_path: Path) -> None:
    assert main(["validate", str(write_config(tmp_path))]) == 0


def test_fake_model_end_to_end(tmp_path: Path) -> None:
    cfg = load_config(write_config(tmp_path))
    router = ModelRouter(cfg.data)
    rows = asyncio.run(generate_dataset(cfg, router, quiet=True))
    asyncio.run(router.flush_logs())
    assert len(rows) >= 1
    assert rows[0]["schema_valid"] is True
    assert rows[0]["accepted"] is True
    assert rows[0]["output_format"] == "json"
    assert "attempt_index" in rows[0]


def test_sampling_override_is_logged_in_llm_calls(tmp_path: Path) -> None:
    cfg = load_config(write_config(tmp_path, {"sampling": {"tasks": {"generate": {"temperature": 1.3, "min_p": 0.07}}}}))
    router = ModelRouter(cfg.data)
    asyncio.run(generate_dataset(cfg, router, quiet=True))
    asyncio.run(router.flush_logs())
    calls = read_jsonl(artifact_path(cfg.output_dir, "llm_calls"))
    generate_calls = [c for c in calls if c["task"] == "generate"]
    assert generate_calls, "expected at least one generate call logged"
    assert all(c["sampling"]["temperature"] == 1.3 for c in generate_calls)  # task override applied
    assert all(c["extra_body"].get("min_p") == 0.07 for c in generate_calls)  # provider param via extra_body
    # An untargeted task keeps the default temperature, proving the override is task-scoped.
    meta_calls = [c for c in calls if c["task"] == "meta_prompt"]
    assert meta_calls and all(c["sampling"]["temperature"] == 0.7 for c in meta_calls)


def test_malformed_json_repair_path(tmp_path: Path) -> None:
    from syndata.generate import _make_record

    class RepairRouter:
        def __init__(self) -> None:
            self.calls = 0

        async def complete(self, role: str, prompt: str, system: str | None = None, task: str = "unknown") -> str:
            self.calls += 1
            return "not json"

        async def complete_json(self, role: str, prompt: str, system: str | None = None, task: str = "unknown"):
            return {"input": "fixed", "output": "fixed"}

    cfg = load_config(write_config(tmp_path))
    record, valid, error = asyncio.run(_make_record(cfg, RepairRouter(), "make a record"))
    assert record == {"input": "fixed", "output": "fixed"}
    assert valid is True
    assert error is None


def test_sampling_is_deterministic(tmp_path: Path) -> None:
    cfg = load_config(write_config(tmp_path))
    router = ModelRouter(cfg.data)
    taxonomy = asyncio.run(build_taxonomy(cfg, router))
    strategy = {"taxonomy_roots": ["topic", "difficulty"]}
    import random

    a = sample_mix(taxonomy, strategy, random.Random(4))
    b = sample_mix(taxonomy, strategy, random.Random(4))
    assert a == b


def test_strategy_factor_root_samples_within_factor() -> None:
    import random

    mix = sample_mix(_strategy_taxonomy(), {"taxonomy_roots": ["topic"]}, random.Random(1))
    assert len(mix) == 1
    assert mix[0]["factor"] == "topic"
    assert mix[0]["path"][0] == "topic"


def test_strategy_subtree_root_samples_only_that_subtree() -> None:
    import random

    for seed in range(10):
        mix = sample_mix(_strategy_taxonomy(), {"taxonomy_roots": ["topic.alpha"]}, random.Random(seed))
        assert len(mix) == 1
        assert mix[0]["path"][:2] == ["topic", "alpha"]


def test_strategy_leaf_root_returns_leaf() -> None:
    import random

    mix = sample_mix(_strategy_taxonomy(), {"taxonomy_roots": ["topic.alpha.leaf"]}, random.Random(2))
    assert mix == [
        {
            "factor": "topic",
            "node": "leaf",
            "level": 2,
            "path": ["topic", "alpha", "leaf"],
            "description": "Leaf branch",
        }
    ]


def test_invalid_strategy_root_falls_back_to_all_factors() -> None:
    import random

    mix = sample_mix(_strategy_taxonomy(), {"taxonomy_roots": ["missing.branch"]}, random.Random(3))
    assert {row["factor"] for row in mix} == {"topic", "other", "query_domain"}


def test_strategy_matching_respects_path_segments_and_overlapping_names() -> None:
    import random

    taxonomy = _strategy_taxonomy()
    for seed in range(10):
        mix = sample_mix(taxonomy, {"taxonomy_roots": ["topic.alpha", "other"]}, random.Random(seed))
        by_factor = {row["factor"]: row for row in mix}
        assert by_factor["topic"]["path"][:2] == ["topic", "alpha"]
        assert by_factor["topic"]["path"][:2] != ["topic", "alpha_extra"]

    mix = sample_mix(taxonomy, {"taxonomy_roots": ["query", "other"]}, random.Random(4))
    assert {row["factor"] for row in mix} == {"other"}


def test_schema_free_fake_model_end_to_end(tmp_path: Path) -> None:
    path = write_config(tmp_path, {"schema": None})
    cfg = load_config(path)
    router = ModelRouter(cfg.data)
    rows = asyncio.run(generate_dataset(cfg, router, quiet=True))
    asyncio.run(router.flush_logs())
    assert rows
    assert isinstance(rows[0]["record"], str)
    assert rows[0]["output_format"] == "text"
    assert rows[0]["schema_valid"] is True


def test_resume_skips_completed_attempts(tmp_path: Path) -> None:
    cfg = load_config(write_config(tmp_path, {"generation": {"target_size": 3, "overgenerate_ratio": 1.0, "complexity_ratio": 0}}))
    router = ModelRouter(cfg.data)
    asyncio.run(generate_dataset(cfg, router, quiet=True, resume=False))
    raw_count = len(read_jsonl(artifact_path(cfg.output_dir, "raw")))
    asyncio.run(generate_dataset(cfg, router, quiet=True, resume=True))
    assert len(read_jsonl(artifact_path(cfg.output_dir, "raw"))) == raw_count


def test_generation_concurrency_is_bounded(tmp_path: Path) -> None:
    # generation.concurrency must cap in-flight attempts; otherwise every attempt launches at once.
    class TrackingRouter(ModelRouter):
        def __init__(self, config: dict) -> None:
            super().__init__(config)
            self.active = 0
            self.max_active = 0

        async def complete(self, *args, **kwargs):  # type: ignore[override]
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            try:
                await asyncio.sleep(0.01)  # widen the window so overlap is observable
                return await super().complete(*args, **kwargs)
            finally:
                self.active -= 1

    cfg = load_config(
        write_config(
            tmp_path,
            {"generation": {"target_size": 8, "overgenerate_ratio": 1.0, "complexity_ratio": 0, "scenarios_per_mix": 2, "concurrency": 2}},
        )
    )
    router = TrackingRouter(cfg.data)
    # Build taxonomy + strategies first so the counter reflects only the bounded generation phase.
    taxonomy = asyncio.run(build_taxonomy(cfg, router))
    asyncio.run(build_strategies(cfg, router, taxonomy))
    router.max_active = 0
    asyncio.run(generate_dataset(cfg, router, quiet=True))
    asyncio.run(router.flush_logs())
    # 8 attempts with a limit of 2 must never exceed 2 concurrent calls, and should reach it.
    assert router.max_active == 2


def test_evaluate_writes_its_own_artifact_and_preserves_final(tmp_path: Path) -> None:
    from syndata.evaluate import run_evaluation

    cfg = load_config(write_config(tmp_path))
    router = ModelRouter(cfg.data)
    asyncio.run(build_taxonomy(cfg, router))
    asyncio.run(generate_dataset(cfg, router, quiet=True))
    final_before = read_jsonl(artifact_path(cfg.output_dir, "final"))
    asyncio.run(run_evaluation(cfg, router, quiet=True))
    asyncio.run(router.flush_logs())
    # evaluate must not rewrite the generator's final dataset; it writes a separate artifact.
    assert read_jsonl(artifact_path(cfg.output_dir, "final")) == final_before
    evaluated = read_jsonl(artifact_path(cfg.output_dir, "evaluated"))
    assert isinstance(evaluated, list) and len(evaluated) <= len(final_before)


def test_point_failure_becomes_rejected_row(tmp_path: Path) -> None:
    class BadRouter:
        def model_name(self, role: str) -> str:
            return "bad"

        async def complete_json(self, role: str, prompt: str, system: str | None = None, task: str = "unknown"):
            raise ValueError("boom")

    cfg = load_config(write_config(tmp_path))
    taxonomy = {"factors": [{"name": "topic", "level": 0, "path": ["topic"], "children": []}]}
    strategies = [{"id": "general", "taxonomy_roots": ["topic"], "weight": 1.0}]
    import random

    row = asyncio.run(_generate_one_safe(cfg, BadRouter(), taxonomy, strategies, random.Random(0), 0))
    assert row["accepted"] is False
    assert row["attempt_index"] == 0
    assert "Generation failed" in row["rejection_reason"]


def _strategy_taxonomy() -> dict:
    return {
        "factors": [
            {
                "name": "topic",
                "description": "Topic factor",
                "level": 0,
                "path": ["topic"],
                "children": [
                    {
                        "name": "alpha",
                        "description": "Alpha branch",
                        "level": 1,
                        "path": ["topic", "alpha"],
                        "children": [
                            {
                                "name": "leaf",
                                "description": "Leaf branch",
                                "level": 2,
                                "path": ["topic", "alpha", "leaf"],
                                "children": [],
                            }
                        ],
                    },
                    {
                        "name": "alpha_extra",
                        "description": "Overlapping name",
                        "level": 1,
                        "path": ["topic", "alpha_extra"],
                        "children": [],
                    },
                ],
            },
            {
                "name": "other",
                "description": "Other factor",
                "level": 0,
                "path": ["other"],
                "children": [{"name": "plain", "description": "Plain branch", "level": 1, "path": ["other", "plain"], "children": []}],
            },
            {
                "name": "query_domain",
                "description": "Query domain factor",
                "level": 0,
                "path": ["query_domain"],
                "children": [{"name": "travel", "description": "Travel branch", "level": 1, "path": ["query_domain", "travel"], "children": []}],
            },
        ]
    }
