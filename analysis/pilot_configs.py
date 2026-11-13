"""Write the e-commerce pilot arm configs (plan.md §0) to runs/pilot/configs/, one output dir per arm.

usage: python analysis/pilot_configs.py [--main-worktree PATH]

Every arm uses the 10K run's description, schema, seed (31), taxonomy and strategies (copied from
runs/v0_ecommerce_search_extraction), so all arms draw the same mixes per attempt index. Baseline arms
(B0*) need a checkout of `main` for its prompt module and code (pass --main-worktree; they are skipped
otherwise). Run arms with analysis/run_arms.sh.
"""

from __future__ import annotations

import argparse
import copy
import shutil
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[1]
RUNS = REPO / "runs" / "pilot"
CONFIGS = RUNS / "configs"
MODULE = REPO / "examples" / "ecommerce_search_extraction_prompts.py"
NITRO, FLASH, PRO = "deepseek/deepseek-v4-flash:nitro", "deepseek/deepseek-v4-flash", "deepseek/deepseek-v4-pro"
FAST = "deepseek/deepseek-v4.1-flash-1017:nitro"
COMBINED = {"meta_prompts": False}
MODEL_PICKED = {"shopper_intent": 2, "attribute_focus": 2}

# name: (generation overrides, bulk/critic model, attempts, uses main's code + prompt module)
ARMS = {
    # Round 1: :nitro routing, as in the 10K baseline run.
    "B0_baseline": ({"scenarios_per_mix": 3}, NITRO, 500, True),
    "M1_meta_fixed": ({}, NITRO, 500, False),
    "C1_combined": ({**COMBINED, "record_batch_size": 1}, NITRO, 500, False),
    "C5_combined": ({**COMBINED, "record_batch_size": 5}, NITRO, 500, False),
    "C10_combined": ({**COMBINED, "record_batch_size": 10}, NITRO, 500, False),
    # Round 2: default routing, and the strong model.
    "R5_recommended": ({**COMBINED, "record_batch_size": 5, "concurrency": 80}, FLASH, 500, False),
    "B0_pro": ({"scenarios_per_mix": 3}, PRO, 200, True),
    "C5_pro": ({**COMBINED, "record_batch_size": 5, "concurrency": 80}, PRO, 200, False),
    # Round 3: batch modes A/B and model-picked factors, default routing (compare with R5).
    "M1d_meta_unbatched": ({"concurrency": 60}, FLASH, 500, False),
    "A5_meta_batched": ({"meta_batch_size": 5, "concurrency": 60}, FLASH, 500, False),
    "B5_record_batched": ({"record_batch_size": 5, "concurrency": 60}, FLASH, 500, False),
    "AB5_both_batched": ({"meta_batch_size": 5, "record_batch_size": 5, "concurrency": 60}, FLASH, 500, False),
    "MP5_model_picked": ({**COMBINED, "record_batch_size": 5, "model_picked": MODEL_PICKED, "concurrency": 60}, FLASH, 500, False),
    # Control: code-picked, with shopper_intent/attribute_focus cut to level 2 in its taxonomy copy.
    "CP5_code_picked_level2": ({**COMBINED, "record_batch_size": 5, "concurrency": 60}, FLASH, 500, False),
    # Round 4: where code stops picking each factor (analysis/factor_roles.py), all on FAST. Leaves = the
    # recommended setup; "model" = the cut levels the decision prompt chose (majority of 3 runs); "hand" =
    # the hand-checked cut levels (CP5's design).
    "V5_leaves": ({**COMBINED, "record_batch_size": 5, "concurrency": 60}, FAST, 500, False),
    "V5_cut_model": ({**COMBINED, "record_batch_size": 5, "concurrency": 60}, FAST, 500, False),
    "V5_cut_hand": ({**COMBINED, "record_batch_size": 5, "concurrency": 60}, FAST, 500, False),
}
# Arms whose taxonomy copy is cut: {factor: deepest level code samples}. Nodes at that level become leaves.
CUTS = {
    "CP5_code_picked_level2": {"shopper_intent": 2, "attribute_focus": 2},
    "V5_cut_model": {"shopper_intent": 1, "attribute_focus": 1, "query_complexity": 2},
    "V5_cut_hand": {"shopper_intent": 2, "attribute_focus": 2},
}


def write_arm(name: str, generation: dict, model: str, attempts: int, module: Path) -> Path:
    cfg = copy.deepcopy(yaml.safe_load((REPO / "examples" / "ecommerce_search_extraction.yaml").read_text()))
    out = RUNS / name
    out.mkdir(parents=True, exist_ok=True)
    for artifact in ("taxonomy.json", "strategies.json"):
        if not (out / artifact).exists():
            shutil.copy(REPO / "runs" / "v0_ecommerce_search_extraction" / artifact, out / artifact)
    cfg["project"] = {"name": name, "output_dir": str(out), "seed": 31}
    cfg["prompts"] = {"module": str(module)}
    for role in ("bulk", "critic"):
        cfg["models"][role]["model"] = model
    cfg["models"]["bulk"]["max_tokens"] = 65536  # a 10-record batch reasons ~20-25K tokens at effort low
    cfg["generation"] = {"target_size": attempts, "overgenerate_ratio": 1.0, "trim": False, "complexity_ratio": 0.3,
                         "max_refine_attempts": 1, "concurrency": 24, "checkpoint_every": 50, **generation}
    cfg["evaluation"] = {"dedupe": True, "coverage": False}
    CONFIGS.mkdir(parents=True, exist_ok=True)
    path = CONFIGS / f"{name}.yaml"
    path.write_text(yaml.safe_dump(cfg, sort_keys=False))
    return path


def cut_taxonomy(name: str, levels: dict[str, int]) -> None:
    # "Code to a level": cut these factors' trees at the given level so code samples those nodes as leaves
    # (the same shares as sampling a leaf and keeping its ancestor at that level).
    import json

    def cut(node: dict, level: int) -> None:
        if node.get("level", 0) >= level:
            node["children"] = []
        for child in node["children"]:
            cut(child, level)

    path = RUNS / name / "taxonomy.json"
    taxonomy = json.loads(path.read_text())
    for factor in taxonomy["factors"]:
        if factor["name"] in levels:
            cut(factor, levels[factor["name"]])
    path.write_text(json.dumps(taxonomy, indent=2, ensure_ascii=False))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--main-worktree", type=Path, help="checkout of main, for the B0* baseline arms")
    args = parser.parse_args()
    for name, (generation, model, attempts, baseline) in ARMS.items():
        if baseline and not args.main_worktree:
            print(f"skip {name} (needs --main-worktree)")
            continue
        module = args.main_worktree / "examples" / MODULE.name if baseline else MODULE
        print("wrote", write_arm(name, generation, model, attempts, module))
    for name, levels in CUTS.items():
        cut_taxonomy(name, levels)


if __name__ == "__main__":
    main()
