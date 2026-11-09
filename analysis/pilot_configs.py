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


def prune_to_level_two(name: str, factors: tuple[str, ...]) -> None:
    # The CP5 control: cut these factors' trees at level 2 so code samples level-2 nodes as leaves.
    import json

    path = RUNS / name / "taxonomy.json"
    taxonomy = json.loads(path.read_text())
    for factor in taxonomy["factors"]:
        if factor["name"] in factors:
            for child in factor["children"]:
                for grandchild in child["children"]:
                    grandchild["children"] = []
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
    prune_to_level_two("CP5_code_picked_level2", tuple(MODEL_PICKED))


if __name__ == "__main__":
    main()
