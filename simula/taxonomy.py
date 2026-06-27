from __future__ import annotations

import asyncio
import random
from typing import Any

from .config import Config
from .console import console, info, phase, spinner, taxonomy_logger, warn
from .data_models import TaskType
from .models import ModelRouter
from .utils import artifact_path, read_json, write_json


async def build_taxonomy(cfg: Config, router: ModelRouter) -> dict[str, Any]:
    """Breadth-first expand each factor into a depth-bounded taxonomy tree, then write + review it.

    Per level: expand every node concurrently (best_of_n proposals + a critic refine), then ask for one
    global plan that guides the next level. The review step may halt the run so the user can edit.
    """
    depth = cfg.taxonomy.depth
    taxonomy = {"description": cfg.description, "factors": []}

    phase("Building taxonomy")
    # Surface nodes as they are generated, in the configured style (tree/light). The logger is driven
    # with the JSON node dicts; a tree logger's live display closes before the review prompt below
    # (only one rich live display at a time).
    with taxonomy_logger(style=cfg.taxonomy.log_style) as log:
        factors = cfg.taxonomy.factors or await _discover_factors(cfg, router)

        # Expand each factor breadth-first so all branches stay at comparable depth.
        for factor in factors:
            root = {"name": factor["name"], "description": factor.get("description", ""), "level": 0, "children": []}
            log.add_factor(root)
            queue = [root]
            plan = "Expand into useful, balanced child categories."
            for level in range(1, depth + 1):
                next_queue: list[dict[str, Any]] = []
                tasks: list[tuple[dict[str, Any], asyncio.Task[list[dict[str, Any]]]]] = []

                # Expand all nodes in this level concurrently while preserving BFS ordering.
                async with asyncio.TaskGroup() as tg:
                    for node in queue:
                        siblings = [s["name"] for s in queue if s is not node]
                        task = tg.create_task(_expand_one_node(cfg, router, factor, node, siblings, plan, level))
                        tasks.append((node, task))

                # Attach successful child lists (failed nodes already degraded into leaves) and log
                # each expanded node as its children land.
                for node, task in tasks:
                    node["children"] = task.result()
                    log.add_children(node, node["children"])
                    next_queue.extend(node["children"])
                if level < depth:
                    plan_data = await router.complete_json(
                        "strategic",
                        cfg.prompts.level_plan_prompt(cfg.description, next_queue),
                        system=cfg.prompts.SYSTEM_JSON,
                        task=TaskType.LEVEL_PLAN,
                    )
                    plan = plan_data.get("plan", plan)
                queue = next_queue
            log.finish_factor(root)
            taxonomy["factors"].append(root)

    write_json(artifact_path(cfg.output_dir, "taxonomy"), taxonomy)
    info(f"[dim]Taxonomy: {len(taxonomy['factors'])} factors, depth {depth}[/dim]")
    _review_taxonomy(cfg)
    return taxonomy


async def load_or_build_taxonomy(cfg: Config, router: ModelRouter) -> dict[str, Any]:
    """Reuse taxonomy.json if present (so a resumed run keeps an edited tree), else build it."""
    path = artifact_path(cfg.output_dir, "taxonomy")
    existing = read_json(path)
    if existing:
        info(f"[dim]Reusing taxonomy.json ({len(existing.get('factors', []))} factors)[/dim]")
        return existing
    return await build_taxonomy(cfg, router)


async def build_strategies(cfg: Config, router: ModelRouter, taxonomy: dict[str, Any]) -> list[dict[str, Any]]:
    """Reuse strategies.json if present, else ask the model for weighted sampling strategies."""
    info("")  # separate the strategies step from the taxonomy block above
    path = artifact_path(cfg.output_dir, "strategies")
    existing = read_json(path)
    if existing:
        info(f"[dim]Reusing strategies.json ({len(existing['strategies'])} strategies)[/dim]")
        return existing["strategies"]
    with spinner("Building strategies"):
        response = await router.complete_json(
            "strategic",
            cfg.prompts.strategy_prompt(cfg.description, taxonomy, cfg.strategy.guidance),
            system=cfg.prompts.SYSTEM_JSON,
            task=TaskType.STRATEGY,
        )
        strategies = response.get("strategies") or [{"id": "general", "description": "Sample all taxonomies.", "taxonomy_roots": [f["name"] for f in taxonomy["factors"]], "weight": 1.0}]
        write_json(path, {"strategies": strategies})
    return strategies


def sample_mix(taxonomy: dict[str, Any], strategy: dict[str, Any], rng: random.Random) -> list[dict[str, Any]]:
    """Sample one taxonomy node per factor under the strategy's roots; one lineage entry per factor.

    If no factor matches the strategy's roots, fall back to sampling every factor so the returned mix
    (and thus row lineage) is never empty.
    """
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
    """Weighted-random pick of one strategy (higher weight -> sampled more often)."""
    weights = [float(s.get("weight", 1.0)) for s in strategies]
    return rng.choices(strategies, weights=weights, k=1)[0]


def taxonomy_nodes_by_level(taxonomy: dict[str, Any]) -> dict[str, dict[int, set[str]]]:
    """Map factor -> level -> set of node paths; the denominator for coverage ratios."""
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
        cfg.prompts.factor_prompt(cfg.description, cfg.taxonomy.factors),
        system=cfg.prompts.SYSTEM_JSON,
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
    try:
        raw_children: list[dict[str, Any]] = []

        # Best-of-N asks for several candidate child lists before the critic refines them.
        for _ in range(cfg.taxonomy.best_of_n):
            response = await router.complete_json(
                "strategic",
                cfg.prompts.expand_prompt(cfg.description, factor, node, siblings, plan, cfg.taxonomy.children_per_node),
                system=cfg.prompts.SYSTEM_JSON,
                task=TaskType.NODE_EXPANSION,
            )
            raw_children.extend(response.get("children", []))

        # A separate refinement call keeps child categories coherent and low-duplication.
        refined = await router.complete_json(
            "strategic",
            cfg.prompts.refine_nodes_prompt(cfg.description, node, raw_children),
            system=cfg.prompts.SYSTEM_JSON,
            task=TaskType.TAXONOMY_CRITIC,
        )
        return [_child(child, level, node.get("path", [node["name"]])) for child in refined.get("children", [])]
    except Exception as exc:  # noqa: BLE001 - malformed provider output should only prune one branch.
        warn(f"node expansion failed for {node.get('name')}: {exc}")
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
    """Pick a node inside `factor` for a strategy whose roots are dot-separated taxonomy paths.

    Strict subtree matching: a root that exactly names the factor samples anywhere in its tree; a
    deeper root must exactly name an existing node path and samples that node's subtree. Roots that
    match no path (or belong to another factor) contribute nothing here, so the caller can fall back.
    """
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


def _review_taxonomy(cfg: Config) -> None:
    """Gate generation on the review mode after taxonomy.json is written.

    write_then_edit and a rejected interactive_confirm STOP the process (not just print) so the user
    can edit the file before generating — otherwise generation runs on the unedited tree.
    """
    mode = cfg.taxonomy.review_mode
    if mode == "auto_accept":
        return

    path = artifact_path(cfg.output_dir, "taxonomy")
    if mode == "write_then_edit":
        info(f"Taxonomy written to {path}. Edit it, then rerun to generate.")
        raise SystemExit(0)

    answer = console.input("Accept generated taxonomy? [Y/n] ").strip().lower()
    if answer in {"", "y", "yes"}:
        return
    info(f"Taxonomy left at {path}. Edit it before generation.")
    raise SystemExit(1)
