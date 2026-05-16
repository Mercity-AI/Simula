from __future__ import annotations

import asyncio
import random
from typing import Any

from . import prompts
from .config import Config
from .models import ModelRouter
from .tasks import TaskType
from .utils import artifact_path, read_json, write_json


async def build_taxonomy(cfg: Config, router: ModelRouter) -> dict[str, Any]:
    tax_cfg = cfg.data["taxonomy"]
    factors = tax_cfg.get("factors") or await _discover_factors(cfg, router)
    taxonomy = {"description": cfg.description, "factors": []}

    # Expand each factor breadth-first so all branches stay at comparable depth.
    for factor in factors:
        root = {"name": factor["name"], "description": factor.get("description", ""), "level": 0, "children": []}
        queue = [root]
        plan = "Expand into useful, balanced child categories."
        for level in range(1, int(tax_cfg["depth"]) + 1):
            next_queue: list[dict[str, Any]] = []
            tasks: list[tuple[dict[str, Any], asyncio.Task[list[dict[str, Any]]]]] = []

            # Expand all nodes in this level concurrently while preserving BFS ordering.
            async with asyncio.TaskGroup() as tg:
                for node in queue:
                    siblings = [s["name"] for s in queue if s is not node]
                    task = tg.create_task(_expand_one_node(cfg, router, factor, node, siblings, plan, level))
                    tasks.append((node, task))

            # Attach successful child lists; failed nodes have already degraded into leaves.
            for node, task in tasks:
                node["children"] = task.result()
                next_queue.extend(node["children"])
            if level < int(tax_cfg["depth"]):
                plan_data = await router.complete_json(
                    "strategic",
                    prompts.level_plan_prompt(cfg.description, next_queue),
                    system=prompts.SYSTEM_JSON,
                    task=TaskType.LEVEL_PLAN,
                )
                plan = plan_data.get("plan", plan)
            queue = next_queue
        taxonomy["factors"].append(root)

    taxonomy = _review_taxonomy(cfg, taxonomy)
    write_json(artifact_path(cfg.output_dir, "taxonomy"), taxonomy)
    return taxonomy


async def load_or_build_taxonomy(cfg: Config, router: ModelRouter) -> dict[str, Any]:
    path = artifact_path(cfg.output_dir, "taxonomy")
    existing = read_json(path)
    return existing if existing else await build_taxonomy(cfg, router)


async def build_strategies(cfg: Config, router: ModelRouter, taxonomy: dict[str, Any]) -> list[dict[str, Any]]:
    path = artifact_path(cfg.output_dir, "strategies")
    existing = read_json(path)
    if existing:
        return existing["strategies"]
    response = await router.complete_json(
        "strategic",
        prompts.strategy_prompt(cfg.description, taxonomy),
        system=prompts.SYSTEM_JSON,
        task=TaskType.STRATEGY,
    )
    strategies = response.get("strategies") or [{"id": "general", "description": "Sample all taxonomies.", "taxonomy_roots": [f["name"] for f in taxonomy["factors"]], "weight": 1.0}]
    write_json(path, {"strategies": strategies})
    return strategies


def sample_mix(taxonomy: dict[str, Any], strategy: dict[str, Any], rng: random.Random) -> list[dict[str, Any]]:
    roots = strategy.get("taxonomy_roots") or [f["name"] for f in taxonomy["factors"]]
    mix = []
    for factor in taxonomy["factors"]:
        node = _strategy_node_for_factor(factor, roots, rng)
        if node is None:
            continue
        mix.append({"factor": factor["name"], "node": node["name"], "level": node.get("level", 0), "path": node.get("path", [node["name"]]), "description": node.get("description", "")})
    if not mix:
        for factor in taxonomy["factors"]:
            node = _sample_descendant(factor, rng)
            mix.append({"factor": factor["name"], "node": node["name"], "level": node.get("level", 0), "path": node.get("path", [node["name"]]), "description": node.get("description", "")})
    return mix


def choose_strategy(strategies: list[dict[str, Any]], rng: random.Random) -> dict[str, Any]:
    weights = [float(s.get("weight", 1.0)) for s in strategies]
    return rng.choices(strategies, weights=weights, k=1)[0]


def taxonomy_nodes_by_level(taxonomy: dict[str, Any]) -> dict[str, dict[int, set[str]]]:
    coverage: dict[str, dict[int, set[str]]] = {}
    for factor in taxonomy.get("factors", []):
        coverage[factor["name"]] = {}
        for node in walk_nodes(factor):
            coverage[factor["name"]].setdefault(int(node.get("level", 0)), set()).add("/".join(node.get("path", [node["name"]])))
    return coverage


def walk_nodes(node: dict[str, Any]) -> list[dict[str, Any]]:
    rows = [node]
    for child in node.get("children", []):
        rows.extend(walk_nodes(child))
    return rows


def taxonomy_to_text(node: dict[str, Any], indent: int = 0) -> str:
    prefix = "  " * indent
    description = f" - {node.get('description', '')}" if node.get("description") else ""
    lines = [f"{prefix}- {node['name']}{description}"]
    for child in node.get("children", []):
        lines.append(taxonomy_to_text(child, indent + 1))
    return "\n".join(lines)


async def _discover_factors(cfg: Config, router: ModelRouter) -> list[dict[str, Any]]:
    response = await router.complete_json(
        "strategic",
        prompts.factor_prompt(cfg.description, cfg.data["taxonomy"].get("factors")),
        system=prompts.SYSTEM_JSON,
        task=TaskType.FACTOR_DISCOVERY,
    )
    return response.get("factors", [])


async def _expand_one_node(
    cfg: Config,
    router: ModelRouter,
    factor: dict[str, Any],
    node: dict[str, Any],
    siblings: list[str],
    plan: str,
    level: int,
) -> list[dict[str, Any]]:
    tax_cfg = cfg.data["taxonomy"]
    try:
        raw_children: list[dict[str, Any]] = []

        # Best-of-N asks for several candidate child lists before the critic refines them.
        for _ in range(int(tax_cfg["best_of_n"])):
            response = await router.complete_json(
                "strategic",
                prompts.expand_prompt(cfg.description, factor, node, siblings, plan, int(tax_cfg.get("children_per_node", 4))),
                system=prompts.SYSTEM_JSON,
                task=TaskType.NODE_EXPANSION,
            )
            raw_children.extend(response.get("children", []))

        # A separate refinement call keeps child categories coherent and low-duplication.
        refined = await router.complete_json(
            "strategic",
            prompts.refine_nodes_prompt(cfg.description, node, raw_children),
            system=prompts.SYSTEM_JSON,
            task=TaskType.TAXONOMY_CRITIC,
        )
        return [_child(child, level, node.get("path", [node["name"]])) for child in refined.get("children", [])]
    except Exception as exc:  # noqa: BLE001 - malformed provider output should only prune one branch.
        print(f"[warn] node expansion failed for {node.get('name')}: {exc}")
        return []


def _child(child: dict[str, Any], level: int, parent_path: list[str]) -> dict[str, Any]:
    return {
        "name": child["name"],
        "description": child.get("description", ""),
        "level": level,
        "path": [*parent_path, child["name"]],
        "children": [],
    }


def _sample_descendant(root: dict[str, Any], rng: random.Random) -> dict[str, Any]:
    nodes = walk_nodes(root)
    return rng.choice(nodes)


def _strategy_node_for_factor(factor: dict[str, Any], roots: list[str], rng: random.Random) -> dict[str, Any] | None:
    factor_key = _path_key([factor["name"]])
    subtree_roots: list[tuple[str, ...]] = []

    # Strategy roots are dot-separated taxonomy paths, not free-form string prefixes.
    for root in roots:
        root_key = _root_key(root)
        if not root_key or root_key[0] != factor_key[0]:
            continue
        if root_key == factor_key:
            return _sample_descendant(factor, rng)
        subtree_roots.append(root_key)

    if not subtree_roots:
        return None

    # Deeper strategy roots must exactly name an existing node path.
    candidates = [
        node
        for node in walk_nodes(factor)
        if _path_key(node.get("path", [node["name"]])) in subtree_roots
    ]
    if not candidates:
        return None
    return _sample_descendant(rng.choice(candidates), rng)


def _root_key(root: str) -> tuple[str, ...]:
    return tuple(segment for segment in (_norm_segment(part) for part in str(root).split(".")) if segment)


def _path_key(path: list[str]) -> tuple[str, ...]:
    return tuple(_norm_segment(part) for part in path)


def _norm_segment(value: str) -> str:
    return "".join(ch for ch in value.casefold() if ch.isalnum())


def _review_taxonomy(cfg: Config, taxonomy: dict[str, Any]) -> dict[str, Any]:
    mode = cfg.data["taxonomy"].get("review_mode", "auto_accept")
    if mode == "auto_accept":
        return taxonomy

    path = artifact_path(cfg.output_dir, "taxonomy")
    write_json(path, taxonomy)
    if mode == "write_then_edit":
        print(f"Taxonomy written to {path}. Edit it, then rerun generation.")
        return taxonomy

    answer = input("Accept generated taxonomy? [Y/n] ").strip().lower()
    if answer in {"", "y", "yes"}:
        return taxonomy
    print(f"Taxonomy left at {path}. Edit it before generation.")
    raise SystemExit(1)
