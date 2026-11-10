from pathlib import Path

import asyncio
import json
import pytest
import yaml

from simula.cli import main
from simula.config import load_config
from simula.generate import _generate_one_safe, _subject_units, generate_dataset
from simula.models import ModelRouter
from simula.utils import artifact_path, read_jsonl, write_jsonl
from simula.taxonomy import build_strategies, build_taxonomy, sample_mix


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
            "strategic": {"model": "fake"},
            "bulk": {"model": "fake"},
            "critic": {"model": "fake"},
        },
        "taxonomy": {"depth": 1, "best_of_n": 1, "review_mode": "auto_accept", "children_per_node": 2},
        "generation": {"target_size": 5, "overgenerate_ratio": 1.2, "complexity_ratio": 0, "meta_prompts_per_mix": 2},
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


def test_trim_can_be_disabled_and_changed_on_resume(tmp_path: Path, monkeypatch) -> None:
    import simula.generate as generation

    cfg = load_config(write_config(tmp_path, {
        "generation": {"target_size": 2, "overgenerate_ratio": 2, "complexity_ratio": 0},
        "evaluation": {"dedupe": False},
    }))
    assert cfg.generation.trim is True
    router = ModelRouter(cfg.data)
    trimmed = asyncio.run(generate_dataset(cfg, router, quiet=True))
    asyncio.run(router.flush_logs())
    accepted = read_jsonl(artifact_path(cfg.output_dir, "accepted"))
    assert len(trimmed) == 2
    assert len(accepted) == 4
    calls_before = read_jsonl(artifact_path(cfg.output_dir, "llm_calls"))

    def unexpected_trim(*args):
        pytest.fail("Disabled trimming must bypass coverage selection entirely")

    monkeypatch.setattr(generation, "coverage_aware_trim", unexpected_trim)
    cfg.generation.trim = False
    untrimmed = asyncio.run(generate_dataset(cfg, router, quiet=True))
    asyncio.run(router.flush_logs())
    assert untrimmed == accepted
    assert read_jsonl(artifact_path(cfg.output_dir, "final")) == accepted
    assert read_jsonl(artifact_path(cfg.output_dir, "llm_calls")) == calls_before
    assert not artifact_path(cfg.output_dir, "eval").exists()


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
    from simula.generate import _make_record

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

    # Unmentioned factors sample their full tree instead of being dropped, so every mix covers
    # every factor.
    mix = sample_mix(_strategy_taxonomy(), {"taxonomy_roots": ["topic"]}, random.Random(1))
    assert {row["factor"] for row in mix} == {"topic", "other", "query_domain"}
    by_factor = {row["factor"]: row for row in mix}
    assert by_factor["topic"]["path"][0] == "topic"


def test_strategy_subtree_root_samples_only_that_subtree() -> None:
    import random

    for seed in range(10):
        mix = sample_mix(_strategy_taxonomy(), {"taxonomy_roots": ["topic.alpha"]}, random.Random(seed))
        by_factor = {row["factor"]: row for row in mix}
        assert by_factor["topic"]["path"][:2] == ["topic", "alpha"]


def test_strategy_leaf_root_returns_leaf() -> None:
    import random

    mix = sample_mix(_strategy_taxonomy(), {"taxonomy_roots": ["topic.alpha.leaf"]}, random.Random(2))
    by_factor = {row["factor"]: row for row in mix}
    assert by_factor["topic"] == {
        "factor": "topic",
        "node": "leaf",
        "level": 2,
        "path": ["topic", "alpha", "leaf"],
        "description": "Leaf branch",
    }


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

    # "query" must not prefix-match the "query_domain" factor; the unmatched root is ignored and
    # all factors still appear.
    mix = sample_mix(taxonomy, {"taxonomy_roots": ["query", "other"]}, random.Random(4))
    assert {row["factor"] for row in mix} == {"topic", "other", "query_domain"}


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


def test_write_then_edit_halts_before_generation(tmp_path: Path) -> None:
    cfg = load_config(
        write_config(tmp_path, {"taxonomy": {"depth": 1, "best_of_n": 1, "review_mode": "write_then_edit", "children_per_node": 2}})
    )
    router = ModelRouter(cfg.data)
    # write_then_edit must STOP the run after writing the taxonomy, not fall through to generation.
    with pytest.raises(SystemExit):
        asyncio.run(generate_dataset(cfg, router, quiet=True))
    assert artifact_path(cfg.output_dir, "taxonomy").exists()  # written so the user can edit it
    assert read_jsonl(artifact_path(cfg.output_dir, "raw")) == []  # nothing generated


def test_resume_rejects_changed_config(tmp_path: Path) -> None:
    base = {"generation": {"target_size": 3, "overgenerate_ratio": 1.0, "complexity_ratio": 0}}
    cfg = load_config(write_config(tmp_path, base))
    asyncio.run(generate_dataset(cfg, ModelRouter(cfg.data), quiet=True, resume=False))

    # Same output_dir, different seed (a fingerprint-changing input) must block resume.
    changed = dict(base)
    changed["project"] = {"name": "test", "output_dir": str(tmp_path / "run"), "seed": 999}
    cfg2 = load_config(write_config(tmp_path, changed))
    with pytest.raises(ValueError, match="--no-resume"):
        asyncio.run(generate_dataset(cfg2, ModelRouter(cfg2.data), quiet=True, resume=True))

    # --no-resume starts a clean run under the new config.
    rows = asyncio.run(generate_dataset(cfg2, ModelRouter(cfg2.data), quiet=True, resume=False))
    assert rows


def test_flush_logs_reports_write_failures(tmp_path: Path, capsys, monkeypatch) -> None:
    import simula.models as models_mod

    def boom(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(models_mod, "append_jsonl", boom)
    cfg = load_config(write_config(tmp_path))
    router = ModelRouter(cfg.data)

    # Schedule a log write and flush it in the SAME loop so the failing task is still pending.
    async def run() -> None:
        await router.complete("bulk", "generate something", "system", task="generate")
        await router.flush_logs()

    asyncio.run(run())
    assert "log write" in capsys.readouterr().err


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
            {"generation": {"target_size": 8, "overgenerate_ratio": 1.0, "complexity_ratio": 0, "meta_prompts_per_mix": 2, "concurrency": 2}},
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
    from simula.evaluate import run_evaluation

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

        async def complete(self, *args, **kwargs):
            raise ValueError("boom")

        async def complete_json(self, *args, **kwargs):
            raise ValueError("boom")

    cfg = load_config(write_config(tmp_path))
    meta_row = {"attempt_index": 0, "strategy_id": "general", "taxonomy_mix": [], "meta_prompt": "make one", "complexified": False}
    row = asyncio.run(_generate_one_safe(cfg, BadRouter(), meta_row))
    assert row["accepted"] is False
    assert row["attempt_index"] == 0
    assert "Generation failed" in row["rejection_reason"]


def test_failed_meta_row_rejects_without_model_calls(tmp_path: Path) -> None:
    class NoCallRouter:
        def model_name(self, role: str) -> str:
            return "none"

    cfg = load_config(write_config(tmp_path))
    meta_row = {"attempt_index": 1, "strategy_id": "general", "taxonomy_mix": [], "meta_prompt": "", "complexified": False, "error": "boom"}
    # A router with no complete/complete_json proves the failure path makes zero model calls.
    row = asyncio.run(_generate_one_safe(cfg, NoCallRouter(), meta_row))
    assert row["accepted"] is False
    assert "Meta prompt failed: boom" in row["rejection_reason"]


def test_stop_after_halts_each_stage_then_run_completes(tmp_path: Path) -> None:
    # Walk the staged workflow in one output dir: each rerun reuses earlier artifacts, builds the
    # next stage, and halts — then a final run without stop_after finishes generation.
    gen = {"target_size": 3, "overgenerate_ratio": 1.0, "complexity_ratio": 0}
    for stage in ("taxonomy", "strategies", "meta_prompts"):
        cfg = load_config(write_config(tmp_path, {"generation": {**gen, "stop_after": stage}}))
        with pytest.raises(SystemExit) as exit_info:
            asyncio.run(generate_dataset(cfg, ModelRouter(cfg.data), quiet=True))
        assert exit_info.value.code == 0
        assert artifact_path(cfg.output_dir, stage).exists()
        assert read_jsonl(artifact_path(cfg.output_dir, "raw")) == []  # nothing generated yet

    cfg = load_config(write_config(tmp_path, {"generation": gen}))
    rows = asyncio.run(generate_dataset(cfg, ModelRouter(cfg.data), quiet=True))
    assert rows


def test_cli_stop_after_overrides_config(tmp_path: Path) -> None:
    with pytest.raises(SystemExit) as exit_info:
        main(["generate", str(write_config(tmp_path)), "--stop-after", "taxonomy", "--quiet"])
    assert exit_info.value.code == 0
    assert (tmp_path / "run" / "taxonomy.json").exists()
    assert not (tmp_path / "run" / "strategies.json").exists()


def test_cli_stop_after_none_disables_config_stop(tmp_path: Path) -> None:
    gen = {"target_size": 3, "overgenerate_ratio": 1.0, "complexity_ratio": 0, "stop_after": "taxonomy"}
    path = write_config(tmp_path, {"generation": gen})
    # Without the override the config-set stop halts the run...
    with pytest.raises(SystemExit):
        main(["generate", str(path), "--quiet"])
    # ...and --stop-after none pushes through to a completed generation.
    assert main(["generate", str(path), "--stop-after", "none", "--quiet"]) == 0
    assert read_jsonl(tmp_path / "run" / "dataset.final.jsonl")


def test_unknown_stop_after_fails_validation(tmp_path: Path) -> None:
    path = write_config(tmp_path, {"generation": {"target_size": 3, "stop_after": "generation"}})
    with pytest.raises(ValueError, match="stop_after"):
        load_config(path)


def test_edited_meta_prompts_are_used_by_generation(tmp_path: Path) -> None:
    gen = {"target_size": 2, "overgenerate_ratio": 1.0, "complexity_ratio": 0}
    cfg = load_config(write_config(tmp_path, {"generation": {**gen, "stop_after": "meta_prompts"}}))
    with pytest.raises(SystemExit):
        asyncio.run(generate_dataset(cfg, ModelRouter(cfg.data), quiet=True))

    meta_path = artifact_path(cfg.output_dir, "meta_prompts")
    meta_rows = read_jsonl(meta_path)
    assert len(meta_rows) == 2 and all(row["meta_prompt"] for row in meta_rows)
    for row in meta_rows:
        row["meta_prompt"] = f"EDITED {row['attempt_index']}"
    write_jsonl(meta_path, meta_rows)

    # Rerun without the stop: edited prompts are reused (not regenerated) and land in row lineage.
    cfg2 = load_config(write_config(tmp_path, {"generation": gen}))
    rows = asyncio.run(generate_dataset(cfg2, ModelRouter(cfg2.data), quiet=True))
    assert rows and all(row["meta_prompt"].startswith("EDITED") for row in rows)


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


# --- optimizations: taxonomy call counts, critic loop, scenarios, combined batches ---


class _Recording(ModelRouter):
    """Fake router that records every task and can script the critic's responses (last one repeats)."""

    def __init__(self, config: dict, critic: list[str] | None = None) -> None:
        super().__init__(config)
        self.tasks: list[str] = []
        self.critic = critic or []

    async def complete(self, role, prompt, system, task="unknown"):  # type: ignore[override]
        self.tasks.append(str(task))
        if str(task) == "semantic_critic" and self.critic:
            return self.critic[min(self.tasks.count("semantic_critic"), len(self.critic)) - 1]
        return await super().complete(role, prompt, system, task=task)


def _gen(extra: dict) -> dict:
    return {"generation": {"target_size": 4, "overgenerate_ratio": 1.0, "complexity_ratio": 0, **extra}}


@pytest.mark.parametrize("best_of_n, critic, expansions, critics", [(1, True, 2, 2), (2, True, 4, 2), (1, False, 2, 0)])
def test_taxonomy_proposals_then_one_critic_refine_per_node(tmp_path: Path, best_of_n, critic, expansions, critics) -> None:
    taxonomy = {"depth": 1, "best_of_n": best_of_n, "critic": critic, "review_mode": "auto_accept", "children_per_node": 2}
    cfg = load_config(write_config(tmp_path, {"taxonomy": taxonomy}))
    router = _Recording(cfg.data)
    built = asyncio.run(build_taxonomy(cfg, router))
    # The fake discovers 2 factors, each root expands once at depth 1.
    assert router.tasks.count("node_expansion") == expansions
    assert router.tasks.count("taxonomy_critic") == critics
    assert all(factor["children"] for factor in built["factors"])


def test_critic_loop_always_ends_on_a_verdict(tmp_path: Path) -> None:
    from simula.generate import _critic_loop

    cfg = load_config(write_config(tmp_path, _gen({"max_refine_attempts": 1})))
    router = _Recording(cfg.data, critic=['{"verdict":"reject","explanation":"still wrong"}'])
    record, accepted, reason, verdicts = asyncio.run(_critic_loop(cfg, router, "make one", {"input": "q", "output": "a"}))
    # One refine, and the refined record is critiqued again (no trailing un-critiqued refine).
    assert router.tasks == ["semantic_critic", "refine", "semantic_critic"]
    assert accepted is False and reason == "still wrong" and len(verdicts) == 2


def test_critic_fix_is_accepted_without_refine(tmp_path: Path) -> None:
    from simula.generate import _critic_loop

    cfg = load_config(write_config(tmp_path))
    fix = '{"verdict":"reject","explanation":"wrong answer","revised":{"input":"q","output":"fixed"}}'
    router = _Recording(cfg.data, critic=[fix])
    record, accepted, reason, verdicts = asyncio.run(_critic_loop(cfg, router, "make one", {"input": "q", "output": "a"}))
    assert router.tasks == ["semantic_critic"]
    assert accepted is True and record == {"input": "q", "output": "fixed"}
    assert verdicts == [{"verdict": "reject", "explanation": "wrong answer"}]  # the fix is not duplicated

    # A schema-invalid fix is ignored and the normal refine path runs instead.
    broken = '{"verdict":"reject","explanation":"wrong","revised":{"input":"only"}}'
    router = _Recording(cfg.data, critic=[broken, '{"verdict":"accept","explanation":"ok"}'])
    _, accepted, _, _ = asyncio.run(_critic_loop(cfg, router, "make one", {"input": "q", "output": "a"}))
    assert router.tasks == ["semantic_critic", "refine", "semantic_critic"] and accepted is True


def test_critic_off_accepts_valid_records_without_critic_calls(tmp_path: Path) -> None:
    cfg = load_config(write_config(tmp_path, _gen({"critic": False})))
    router = _Recording(cfg.data)
    rows = asyncio.run(generate_dataset(cfg, router, quiet=True))
    assert len(rows) == 4 and all(row["accepted"] and row["critic_verdicts"] == [] for row in rows)
    assert "semantic_critic" not in router.tasks and "refine" not in router.tasks


def test_every_scenario_of_a_mix_becomes_an_attempt(tmp_path: Path) -> None:
    cfg = load_config(write_config(tmp_path, _gen({"meta_prompts_per_mix": 2})))
    router = _Recording(cfg.data)
    asyncio.run(generate_dataset(cfg, router, quiet=True))
    meta = {row["attempt_index"]: row for row in read_jsonl(artifact_path(cfg.output_dir, "meta_prompts"))}
    # 4 attempts from 2 mixes: one meta call per mix, both scenarios kept, the pair shares lineage.
    assert router.tasks.count("meta_prompt") == 2
    assert meta[0]["taxonomy_mix"] == meta[1]["taxonomy_mix"] and meta[2]["taxonomy_mix"] == meta[3]["taxonomy_mix"]
    assert meta[0]["meta_prompt"] != meta[1]["meta_prompt"] and all(row["meta_prompt"] for row in meta.values())


def test_blank_scenarios_are_dropped(tmp_path: Path) -> None:
    from simula.generate import _make_scenarios

    class Stub:
        async def complete_json(self, *args, **kwargs):
            return {"meta_prompts": ["  ", 7, " Keep me. "]}

    cfg = load_config(write_config(tmp_path))
    assert asyncio.run(_make_scenarios(cfg, Stub(), [([], 3)])) == [["Keep me."]]


def test_combined_batches_write_records_straight_from_mixes(tmp_path: Path) -> None:
    (tmp_path / "combined").mkdir()
    (tmp_path / "scenario").mkdir()
    combined = load_config(write_config(tmp_path / "combined", _gen({"meta_prompts": False, "record_batch_size": 3, "target_size": 7})))
    router = _Recording(combined.data)
    rows = asyncio.run(generate_dataset(combined, router, quiet=True))
    asyncio.run(router.flush_logs())
    # 7 attempts in batches of 3 -> 3 generate calls, no meta-prompt step or artifact.
    assert router.tasks.count("generate") == 3 and "meta_prompt" not in router.tasks
    assert not artifact_path(combined.output_dir, "meta_prompts").exists()
    assert len(rows) == 7 and all(row["accepted"] and row["taxonomy_mix"] for row in rows)
    assert all("Sampled ingredients:" in row["meta_prompt"] for row in rows)

    # Lineage is seeded per attempt exactly like the meta-prompt path, so both draw the same mixes.
    scenario = load_config(write_config(tmp_path / "scenario", _gen({"target_size": 7})))
    scenario_rows = asyncio.run(generate_dataset(scenario, ModelRouter(scenario.data), quiet=True))
    mixes = {row["attempt_index"]: row["taxonomy_mix"] for row in scenario_rows}
    assert all(row["taxonomy_mix"] == mixes[row["attempt_index"]] for row in rows)


def test_combined_batches_work_for_free_text(tmp_path: Path) -> None:
    cfg = load_config(write_config(tmp_path, {"schema": None, **_gen({"meta_prompts": False, "record_batch_size": 2})}))
    rows = asyncio.run(generate_dataset(cfg, ModelRouter(cfg.data), quiet=True))
    assert len(rows) == 4 and all(isinstance(row["record"], str) and row["record"] for row in rows)


def test_one_bad_batch_item_does_not_sink_the_batch(tmp_path: Path) -> None:
    from simula.generate import _combined_batch

    class Stub:
        def model_name(self, role: str) -> str:
            return "stub"

        async def complete(self, role, prompt, system, task="unknown"):
            return json.dumps({"records": [
                {"id": "r1", "plan": "A plan.", "record": {"input": "q", "output": "a"}},
                {"id": "r2", "plan": "Broken.", "record": {"input": "no output"}},
            ]})

        async def complete_json(self, role, prompt, system, task="unknown"):
            assert task == "repair"
            return {"input": "repaired", "output": "repaired"}

    cfg = load_config(write_config(tmp_path, _gen({"meta_prompts": False, "record_batch_size": 3, "critic": False})))
    strategies = [{"id": "general", "taxonomy_roots": []}]
    rows = asyncio.run(_combined_batch(cfg, Stub(), _strategy_taxonomy(), strategies, [5, 6, 7]))
    by_index = {row["attempt_index"]: row for row in rows}
    assert by_index[5]["accepted"] and by_index[5]["meta_prompt"].startswith("A plan.\nSampled ingredients:")
    assert by_index[6]["accepted"] and by_index[6]["record"] == {"input": "repaired", "output": "repaired"}
    assert not by_index[7]["accepted"] and by_index[7]["rejection_reason"] == "Record missing from the batch response (or malformed in it)."
    assert all(row["taxonomy_mix"] for row in rows)  # lineage survives failures


def test_malformed_batch_json_is_salvaged_item_by_item() -> None:
    from simula.generate import _batch_items

    # r2's object never closes (a dropped brace), so the whole response is invalid JSON; r1 and r3 are
    # still recovered, and r2 is lost on its own instead of sinking the batch.
    broken = (
        '{"records": [{"id": "r1", "plan": "a", "record": {"q": 1}},'
        ' {"id": "r2", "plan": "b", "record": {"q": 2},'
        ' {"id": "r3", "plan": "c", "record": {"q": 3}}]}'
    )
    assert [item["id"] for item in _batch_items(broken, "record")] == ["r1", "r3"]
    assert [item["id"] for item in _batch_items('{"records": [{"id": "r1", "record": "x"}, {"id": "r2"}]}', "record")] == ["r1"]


def test_combined_path_bounds_calls_and_critiques_each_batch_promptly(tmp_path: Path) -> None:
    class Tracking(_Recording):
        active = peak = 0

        async def complete(self, *args, **kwargs):  # type: ignore[override]
            Tracking.active += 1
            Tracking.peak = max(Tracking.peak, Tracking.active)
            try:
                await asyncio.sleep(0.01)
                return await super().complete(*args, **kwargs)
            finally:
                Tracking.active -= 1

    cfg = load_config(write_config(tmp_path, _gen({"meta_prompts": False, "record_batch_size": 2, "concurrency": 4, "target_size": 12})))
    router = Tracking(cfg.data)
    asyncio.run(build_strategies(cfg, router, asyncio.run(build_taxonomy(cfg, router))))
    router.tasks.clear()
    Tracking.peak = 0
    asyncio.run(generate_dataset(cfg, router, quiet=True))
    # 2 batches in flight x 2 critiques each never exceeds concurrency, and critiques start before the
    # last batch call instead of queueing behind every batch.
    assert Tracking.peak <= 4
    assert router.tasks.index("semantic_critic") < len(router.tasks) - router.tasks[::-1].index("generate") - 1


def test_failed_node_expansion_is_retried_once(tmp_path: Path) -> None:
    class Flaky(_Recording):
        async def complete(self, role, prompt, system, task="unknown"):  # type: ignore[override]
            if str(task) == "node_expansion" and self.tasks.count("node_expansion") == 0:
                self.tasks.append(str(task))
                return "not json"
            return await super().complete(role, prompt, system, task=task)

    taxonomy = {"depth": 1, "best_of_n": 1, "review_mode": "auto_accept", "children_per_node": 2}
    cfg = load_config(write_config(tmp_path, {"taxonomy": taxonomy}))
    built = asyncio.run(build_taxonomy(cfg, Flaky(cfg.data)))
    assert all(factor["children"] for factor in built["factors"])  # the first factor survived its bad response


def test_batch_mode_a_writes_scenarios_for_several_mixes_per_call(tmp_path: Path) -> None:
    cfg = load_config(write_config(tmp_path, _gen({"meta_batch_size": 3, "target_size": 7})))
    router = _Recording(cfg.data)
    rows = asyncio.run(generate_dataset(cfg, router, quiet=True))
    meta = {row["attempt_index"]: row for row in read_jsonl(artifact_path(cfg.output_dir, "meta_prompts"))}
    # 7 mixes in groups of 3 -> 3 scenario calls; each attempt keeps its own mix and gets its own scenario.
    assert router.tasks.count("meta_prompt") == 3
    assert len(rows) == 7 and all(row["accepted"] and row["taxonomy_mix"] for row in rows)
    assert len({row["meta_prompt"] for row in meta.values()}) == 7


def test_batch_mode_b_writes_records_for_several_scenarios_per_call(tmp_path: Path) -> None:
    cfg = load_config(write_config(tmp_path, _gen({"record_batch_size": 3, "target_size": 7})))
    router = _Recording(cfg.data)
    rows = asyncio.run(generate_dataset(cfg, router, quiet=True))
    meta = {row["attempt_index"]: row for row in read_jsonl(artifact_path(cfg.output_dir, "meta_prompts"))}
    # One scenario call per attempt, records in batches of 3; each row keeps its own scenario as lineage.
    assert router.tasks.count("meta_prompt") == 7 and router.tasks.count("generate") == 3
    assert len(rows) == 7 and all(row["accepted"] for row in rows)
    assert all(row["meta_prompt"] == meta[row["attempt_index"]]["meta_prompt"] for row in rows)


def test_batch_modes_a_and_b_together(tmp_path: Path) -> None:
    cfg = load_config(write_config(tmp_path, _gen({"meta_batch_size": 4, "record_batch_size": 4, "target_size": 8})))
    router = _Recording(cfg.data)
    rows = asyncio.run(generate_dataset(cfg, router, quiet=True))
    assert router.tasks.count("meta_prompt") == 2 and router.tasks.count("generate") == 2
    assert len(rows) == 8 and all(row["accepted"] for row in rows)


def test_model_picked_factor_lineage_comes_from_the_model(tmp_path: Path) -> None:
    from simula.generate import _combined_batch, _sample_lineage

    class Stub:
        def model_name(self, role: str) -> str:
            return "stub"

        async def complete(self, role, prompt, system, task="unknown"):
            specs = prompt.split("\nr1:")[1]
            assert "Choose these factors yourself" in prompt and "- alpha_extra (" in prompt  # options offered
            assert "- topic:" not in specs and "- other:" in specs  # the picked factor is left out of the specs
            return json.dumps({"records": [
                {"id": "r1", "plan": "A plan.", "picked": {"topic": "Alpha Extra"}, "record": {"input": "q", "output": "a"}},
                {"id": "r2", "plan": "A plan.", "picked": {"topic": "nonsense"}, "record": {"input": "q2", "output": "a2"}},
            ]})

    cfg = load_config(write_config(tmp_path, _gen({"meta_prompts": False, "record_batch_size": 2, "critic": False, "model_picked": {"topic": 1}})))
    taxonomy, strategies = _strategy_taxonomy(), [{"id": "general", "taxonomy_roots": []}]
    rows = {row["attempt_index"]: row for row in asyncio.run(_combined_batch(cfg, Stub(), taxonomy, strategies, [0, 1]))}
    mix = {entry["factor"]: entry for entry in rows[0]["taxonomy_mix"]}
    assert [entry["factor"] for entry in rows[0]["taxonomy_mix"]] == ["topic", "other", "query_domain"]
    assert mix["topic"]["path"] == ["topic", "alpha_extra"] and mix["topic"]["picked_by"] == "model"
    # Code-picked factors draw exactly what they would without model picking.
    sampled = {entry["factor"]: entry for entry in _sample_lineage(cfg, taxonomy, strategies, 0, 1)["taxonomy_mix"]}
    assert mix["other"] == sampled["other"] and mix["query_domain"] == sampled["query_domain"]
    # An unrecognised pick keeps a valid lineage entry at the factor root.
    unpicked = {entry["factor"]: entry for entry in rows[1]["taxonomy_mix"]}["topic"]
    assert unpicked["path"] == ["topic"] and unpicked["node"] == "nonsense"

    # A record missing from the response still gets one lineage entry per factor.
    class Short(Stub):
        async def complete(self, role, prompt, system, task="unknown"):
            return json.dumps({"records": [{"id": "r1", "plan": "p", "picked": {"topic": "alpha"}, "record": {"input": "q", "output": "a"}}]})

    short = {row["attempt_index"]: row for row in asyncio.run(_combined_batch(cfg, Short(), taxonomy, strategies, [0, 1]))}
    assert not short[1]["accepted"]
    assert [entry["factor"] for entry in short[1]["taxonomy_mix"]] == ["topic", "other", "query_domain"]
    assert short[1]["taxonomy_mix"][0]["node"] == "unpicked"
    assert "- topic: alpha_extra" in rows[0]["meta_prompt"]  # the critic sees the pick as an ingredient


def test_table_batch_prompt_describes_each_node_once() -> None:
    from simula.models import fake_complete
    from simula.prompts import combined_table_batch_prompt

    def entry(factor: str, *path: str) -> dict:
        return {"factor": factor, "node": path[-1], "path": [factor, *path], "description": f"{path[-1]} described"}

    items = [
        {"id": "r1", "mix": [entry("topic", "alpha"), entry("tone", "dry")]},
        {"id": "r2", "mix": [entry("topic", "beta"), entry("tone", "dry")]},
    ]
    prompt = combined_table_batch_prompt("Tiny QA.", None, items, [])
    assert prompt.count("dry described") == 1  # shared node: one glossary line, two references
    # "topic" and "tone" share an initial, so the second factor's ids use two letters.
    assert "r1 | T1 | TO1" in prompt and "r2 | T2 | TO1" in prompt
    records = json.loads(fake_complete(prompt, 1))["records"]
    assert [record["id"] for record in records] == ["r1", "r2"]


def test_unknown_model_picked_factor_fails_before_any_strategy_or_record_call(tmp_path: Path) -> None:
    cfg = load_config(write_config(tmp_path, _gen({"meta_prompts": False, "record_batch_size": 2, "model_picked": {"topicc": 1}})))
    router = _Recording(cfg.data)
    with pytest.raises(ValueError, match="'topicc', which is not in the taxonomy"):
        asyncio.run(generate_dataset(cfg, router, quiet=True))
    assert "strategy" not in router.tasks and "generate" not in router.tasks


def test_code_level_cuts_lineage_on_both_record_paths(tmp_path: Path) -> None:
    # depth 2 gives each fake factor leaves at level 2; code_level stops "topic" at level 1.
    taxonomy = {"depth": 2, "best_of_n": 1, "review_mode": "auto_accept", "children_per_node": 2}
    for name, extra in (("combined", {"meta_prompts": False, "record_batch_size": 2}), ("scenario", {})):
        (tmp_path / name).mkdir()
        cfg = load_config(write_config(tmp_path / name, {"taxonomy": taxonomy, **_gen({"code_level": {"topic": 1}, **extra})}))
        rows = asyncio.run(generate_dataset(cfg, ModelRouter(cfg.data), quiet=True))
        levels = [{entry["factor"]: entry["level"] for entry in row["taxonomy_mix"]} for row in rows]
        assert rows and all(level == {"topic": 1, "difficulty": 2} for level in levels)

    cfg = load_config(write_config(tmp_path, {"taxonomy": taxonomy, **_gen({"code_level": {"topicc": 1}})}))
    router = _Recording(cfg.data)
    with pytest.raises(ValueError, match="code_level names factor 'topicc'"):
        asyncio.run(generate_dataset(cfg, router, quiet=True))
    assert "strategy" not in router.tasks and "generate" not in router.tasks


def test_combined_path_complexifies_the_written_record_in_its_own_call(tmp_path: Path) -> None:
    class Stub(_Recording):
        async def complete(self, role, prompt, system, task="unknown"):  # type: ignore[override]
            if str(task) == "generate":
                assert "harder" not in prompt  # the batch prompt carries no complexify flag
            if str(task) == "complexify":
                self.tasks.append("complexify")
                assert '"input": "Question' in prompt  # the written record is the input
                return json.dumps({"input": "A harder question?", "output": "A harder answer."})
            return await super().complete(role, prompt, system, task=task)

    cfg = load_config(write_config(tmp_path, _gen({"meta_prompts": False, "record_batch_size": 2, "complexity_ratio": 1.0})))
    router = Stub(cfg.data)
    rows = asyncio.run(generate_dataset(cfg, router, quiet=True))
    asyncio.run(router.flush_logs())
    # 4 attempts in 2 batches: every record is complexified once, after generation and before its critique.
    assert router.tasks.count("generate") == 2 and router.tasks.count("complexify") == 4
    assert all(row["complexified"] and row["record"]["input"] == "A harder question?" for row in rows)
    first_critic = router.tasks.index("semantic_critic")
    assert "complexify" in router.tasks[:first_critic]

    # A failed complexify keeps the record as written and does not mark the row.
    class Failing(_Recording):
        async def complete(self, role, prompt, system, task="unknown"):  # type: ignore[override]
            if str(task) == "complexify":
                raise RuntimeError("boom")
            return await super().complete(role, prompt, system, task=task)

    (tmp_path / "failing").mkdir()
    cfg = load_config(write_config(tmp_path / "failing", _gen({"meta_prompts": False, "record_batch_size": 2, "complexity_ratio": 1.0})))
    rows = asyncio.run(generate_dataset(cfg, Failing(cfg.data), quiet=True))
    assert len(rows) == 4 and all(row["accepted"] and not row["complexified"] for row in rows)


def test_subject_units_group_by_subject() -> None:
    keyed = [(("topic", "a"), 0), (("topic", "b"), 1), (("topic", "a"), 2), (("topic", "a"), 3), (("topic", "b"), 4)]
    # Each batch holds one subject node.
    assert _subject_units(keyed, 2) == [(("topic", "a"), [0, 2]), (("topic", "a"), [3]), (("topic", "b"), [1, 4])]


def test_subject_must_be_a_code_sampled_taxonomy_factor(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="subject"):
        load_config(write_config(tmp_path, _gen({"meta_prompts": False, "subject": "topic", "model_picked": {"topic": 1}})))
    cfg = load_config(write_config(tmp_path, _gen({"meta_prompts": False, "record_batch_size": 2, "subject": "nope"})))
    with pytest.raises(ValueError, match="generation.subject"):
        asyncio.run(generate_dataset(cfg, _Recording(cfg.data), quiet=True))


@pytest.mark.parametrize("path", ["combined", "meta_prompts"])
def test_subject_batches_share_one_node(tmp_path: Path, path: str) -> None:
    class Capture(_Recording):
        prompts: dict[str, list[str]] = {}

        async def complete(self, role, prompt, system, task="unknown"):  # type: ignore[override]
            Capture.prompts.setdefault(str(task), []).append(prompt)
            return await super().complete(role, prompt, system, task=task)

    Capture.prompts = {}
    gen = {"meta_prompts": False, "record_batch_size": 2} if path == "combined" else {"meta_batch_size": 2}
    cfg = load_config(write_config(tmp_path, _gen({**gen, "subject": "topic", "target_size": 8})))
    rows = asyncio.run(generate_dataset(cfg, Capture(cfg.data), quiet=True))
    prompts = Capture.prompts["generate" if path == "combined" else "meta_prompt"]
    assert len(rows) == 8 and len(prompts) >= 4
    for prompt in prompts:
        # One subject node per batch, and the batch is told its records share it.
        assert len({line for line in prompt.splitlines() if line.startswith("- topic:")}) == 1
        assert "share the same subject" in prompt


def test_trim_does_not_depend_on_completion_order(tmp_path: Path) -> None:
    # Grouped batches finish node by node, so a trim that kept the first rows to finish would cut whole nodes.
    class SlowFirst(_Recording):
        async def complete(self, role, prompt, system, task="unknown"):  # type: ignore[override]
            if str(task) == "generate" and not self.__dict__.setdefault("slowed", False):
                self.slowed = True
                await asyncio.sleep(0.05)  # the first batch finishes last
            return await super().complete(role, prompt, system, task=task)

    gen = {"meta_prompts": False, "record_batch_size": 2, "subject": "topic", "target_size": 4, "overgenerate_ratio": 2, "concurrency": 8}
    kept = []
    for name, router in (("plain", _Recording), ("slow", SlowFirst)):
        (tmp_path / name).mkdir()
        cfg = load_config(write_config(tmp_path / name, _gen(gen)))
        kept.append([row["attempt_index"] for row in asyncio.run(generate_dataset(cfg, router(cfg.data), quiet=True))])
    assert len(kept[0]) == 4 and kept[0] == kept[1]


def test_without_subject_batch_prompts_are_unchanged(tmp_path: Path) -> None:
    cfg = load_config(write_config(tmp_path, _gen({"meta_prompts": False, "record_batch_size": 2})))
    router = _Recording(cfg.data)
    asyncio.run(generate_dataset(cfg, router, quiet=True))
    log = read_jsonl(Path(cfg.output_dir) / "llm_calls.jsonl")
    assert not any("share the same subject" in row["prompt"] for row in log if row["task"] == "generate")


def test_built_in_prompts_carry_the_context_rules() -> None:
    from simula import prompts as P

    factor = {"name": "topic", "description": "Subject"}
    node = {"name": "topic", "level": 0, "path": ["topic"]}
    assert P.TAXONOMY_CONTEXT_RULE in P.expand_prompt("d", factor, node, [], "plan", 3)
    assert P.TAXONOMY_CONTEXT_RULE in P.refine_nodes_prompt("d", node, [])
    mix = [{"factor": "topic", "node": "a", "path": ["topic", "a"], "description": ""}]
    schema = {"type": "object", "properties": {"q": {"type": "string"}}}
    for prompt in (P.meta_prompt_prompt("d", schema, mix, 1), P.meta_prompt_batch_prompt("d", schema, [{"id": "m1", "mix": mix, "count": 1}]),
                   P.generate_record_prompt("d", schema, "mp"), P.generate_record_batch_prompt("d", schema, [{"id": "r1", "meta_prompt": "mp"}]),
                   P.combined_batch_prompt("d", schema, [{"id": "r1", "mix": mix}], []), P.generate_text_prompt("d", "mp")):
        assert P.CONTEXT_RULE in prompt


def test_subject_pool_requires_a_subject(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="subject_pool requires"):
        load_config(write_config(tmp_path, _gen({"meta_prompts": False, "subject_pool": True})))


@pytest.mark.parametrize("path", ["combined", "meta_prompts"])
def test_subject_pool_gives_each_record_its_own_situation(tmp_path: Path, path: str, monkeypatch) -> None:
    import simula.generate as G

    monkeypatch.setattr(G, "SUBJECT_POOL_CHUNK", 2)  # several pool calls per node, so later ones see earlier ones
    gen = {"meta_prompts": False, "record_batch_size": 2} if path == "combined" else {"meta_batch_size": 2}
    cfg = load_config(write_config(tmp_path, _gen({**gen, "subject": "topic", "subject_pool": True, "target_size": 8, "concurrency": 8})))
    router = _Recording(cfg.data)
    rows = asyncio.run(generate_dataset(cfg, router, quiet=True))
    asyncio.run(router.flush_logs())
    pool = read_jsonl(artifact_path(cfg.output_dir, "subject_pool"))
    log = read_jsonl(Path(cfg.output_dir) / "llm_calls.jsonl")
    pool_prompts = [row["prompt"] for row in log if row["task"] == "subject_pool"]
    writer = [row["prompt"] for row in log if row["task"] == ("generate" if path == "combined" else "meta_prompt")]
    # One situation per attempt, never reused within a node; pool calls after the first on a node see earlier ones.
    assert len(rows) == 8 and len(pool) == 8
    by_node: dict[str, list[str]] = {}
    for row in pool:
        by_node.setdefault("/".join(row["subject"]), []).append(row["situation"])
    assert all(len(set(v)) == len(v) for v in by_node.values())
    assert any("Already listed for this subject" in prompt for prompt in pool_prompts)
    assert all("- situation: " in prompt for prompt in writer)
    if path == "combined":
        assert all(row["meta_prompt"].count("Situation: ") == 1 for row in rows)


def test_built_in_writer_prompts_carry_the_phrasing_rule() -> None:
    from simula import prompts as P

    mix = [{"factor": "topic", "node": "a", "path": ["topic", "a"], "description": ""}]
    schema = {"type": "object", "properties": {"q": {"type": "string"}}}
    for prompt in (P.meta_prompt_batch_prompt("d", schema, [{"id": "m1", "mix": mix, "count": 1}]),
                   P.generate_record_prompt("d", schema, "mp"), P.generate_record_batch_prompt("d", schema, [{"id": "r1", "meta_prompt": "mp"}]),
                   P.combined_batch_prompt("d", schema, [{"id": "r1", "mix": mix}], []), P.generate_text_prompt("d", "mp")):
        assert P.PHRASING_RULE in prompt
