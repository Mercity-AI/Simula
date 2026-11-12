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

    Per level: expand every node concurrently (best_of_n proposals, then a critic refine call unless
    taxonomy.critic is off), then ask for one global plan that guides the next level. The review step may halt
    the run so the user can edit.
    """
    depth = cfg.taxonomy.depth
    taxonomy = {"description": cfg.description, "factors": []}

    phase("Building taxonomy")
    # Nodes within a level expand concurrently; bound in-flight expansion calls like generation does.
    limiter = asyncio.Semaphore(cfg.generation.concurrency)
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
                        task = tg.create_task(_expand_one_node(cfg, router, factor, node, siblings, plan, level, limiter))
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


AUTO_SUBJECT = "auto"  # generation.subject value that lets the model pick the subject factor


async def load_or_build_taxonomy(cfg: Config, router: ModelRouter) -> dict[str, Any]:
    """Reuse taxonomy.json if present (so a resumed run keeps an edited tree), else build it. With
    generation.subject: auto, the model's pick of the subject factor is made once and kept in the artifact
    ("subject": a factor name or null), so reruns reuse it and it can be edited like the tree."""
    path = artifact_path(cfg.output_dir, "taxonomy")
    taxonomy = read_json(path)
    if taxonomy:
        info(f"[dim]Reusing taxonomy.json ({len(taxonomy.get('factors', []))} factors)[/dim]")
    else:
        taxonomy = await build_taxonomy(cfg, router)
    if cfg.generation.subject == AUTO_SUBJECT and "subject" not in taxonomy:
        taxonomy["subject"] = await _pick_subject(cfg, router, taxonomy)
        write_json(path, taxonomy)
    return taxonomy


def subject_factor(cfg: Config, taxonomy: dict[str, Any]) -> str | None:
    """The factor whose nodes get situations (generation.subject): the named factor, or with "auto" the pick kept
    in taxonomy.json. None = no subject."""
    subject = cfg.generation.subject
    return taxonomy.get("subject") if subject == AUTO_SUBJECT else subject


async def _pick_subject(cfg: Config, router: ModelRouter, taxonomy: dict[str, Any]) -> str | None:
    # Model-picked factors are not code-sampled, so they cannot be the subject.
    eligible = [factor for factor in taxonomy["factors"] if factor["name"] not in cfg.generation.model_picked]
    with spinner("Picking the subject factor"):
        prompt = cfg.prompts.subject_prompt(cfg.description, eligible)
        response = await router.complete_json("strategic", prompt, system=cfg.prompts.SYSTEM_JSON, task=TaskType.SUBJECT)
    picked = response.get("subject")
    subject = next((f["name"] for f in eligible if isinstance(picked, str) and _norm_segment(f["name"]) == _norm_segment(picked)), None)
    info(f'[dim]Subject factor: {subject or "none"} (the model\'s pick; edit "subject" in taxonomy.json to change it)[/dim]')
    return subject


async def build_strategies(cfg: Config, router: ModelRouter, taxonomy: dict[str, Any]) -> list[dict[str, Any]]:
    """Reuse strategies.json if present, else ask the model for weighted sampling strategies.

    The response is validated before use: every taxonomy_roots / never_combine path must name a real
    taxonomy node (one retry with the offending strings quoted back, then a hard error), so a typo'd
    or hallucinated path fails loudly here instead of silently sampling the full tree on every mix.
    """
    info("")  # separate the strategies step from the taxonomy block above
    path = artifact_path(cfg.output_dir, "strategies")
    existing = read_json(path)
    if existing:
        info(f"[dim]Reusing strategies.json ({len(existing['strategies'])} strategies)[/dim]")
        return existing["strategies"]
    with spinner("Building strategies"):
        prompt = cfg.prompts.strategy_prompt(
            cfg.description, taxonomy, taxonomy_path_strings(taxonomy), cfg.strategy.guidance, cfg.strategy.count
        )
        response = await router.complete_json("strategic", prompt, system=cfg.prompts.SYSTEM_JSON, task=TaskType.STRATEGY)
        strategies = _strategies_or_fallback(response, taxonomy)
        invalid = _invalid_strategy_paths(strategies, taxonomy)
        if invalid:
            retry_prompt = (
                prompt
                + "\n\nYour previous attempt used paths that do not exist in the taxonomy: "
                + ", ".join(sorted(set(invalid)))
                + "\nRegenerate the strategies using only paths from the valid list, verbatim."
            )
            response = await router.complete_json("strategic", retry_prompt, system=cfg.prompts.SYSTEM_JSON, task=TaskType.STRATEGY)
            strategies = _strategies_or_fallback(response, taxonomy)
            invalid = _invalid_strategy_paths(strategies, taxonomy)
        # Written even when invalid so the paths can be fixed by hand before rerunning.
        write_json(path, {"strategies": strategies})
    if invalid:
        raise ValueError(f"strategies reference unknown taxonomy paths {sorted(set(invalid))}; fix {path} by hand, then rerun.")
    _warn_uncovered_factors(strategies, taxonomy)
    return strategies


def _strategies_or_fallback(response: dict[str, Any], taxonomy: dict[str, Any]) -> list[dict[str, Any]]:
    return response.get("strategies") or [
        {"id": "general", "description": "Sample all taxonomies.", "taxonomy_roots": [f["name"] for f in taxonomy["factors"]], "weight": 1.0}
    ]


def taxonomy_path_strings(taxonomy: dict[str, Any]) -> list[str]:
    """Every node path as a slash-joined string; the vocabulary strategies may reference."""
    return ["/".join(node.get("path", [node["name"]])) for factor in taxonomy["factors"] for node in walk_nodes(factor)]


def _invalid_strategy_paths(strategies: list[dict[str, Any]], taxonomy: dict[str, Any]) -> list[str]:
    valid = {_path_key(node.get("path", [node["name"]])) for factor in taxonomy["factors"] for node in walk_nodes(factor)}
    invalid = []
    for strategy in strategies:
        referenced = list(strategy.get("taxonomy_roots") or [])
        for pair in strategy.get("never_combine") or []:
            if isinstance(pair, (list, tuple)):
                referenced.extend(pair)
        invalid.extend(str(ref) for ref in referenced if _root_key(str(ref)) not in valid)
    return invalid


def _warn_uncovered_factors(strategies: list[dict[str, Any]], taxonomy: dict[str, Any]) -> None:
    factor_names = {_norm_segment(f["name"]): f["name"] for f in taxonomy["factors"]}
    for strategy in strategies:
        covered = {key[0] for root in strategy.get("taxonomy_roots") or [] if (key := _root_key(str(root)))}
        missing = [name for key, name in factor_names.items() if key not in covered]
        if missing:
            warn(f"strategy {strategy.get('id', '?')!r} does not mention factors {missing}; they will sample their full tree.")


_MAX_MIX_REDRAWS = 100


def sample_mix(
    taxonomy: dict[str, Any],
    strategy: dict[str, Any],
    rng: random.Random,
    code_depth: dict[str, int] | None = None,
) -> list[dict[str, Any]]:
    """Sample one taxonomy leaf per factor under the strategy's roots; one lineage entry per factor.

    Every factor always appears in the mix: factors the strategy leaves out (or whose roots match
    nothing) sample their full tree rather than being dropped. The strategy's never_combine path
    pairs are enforced by redrawing the whole mix; a ruleset the roots cannot satisfy raises after a
    bounded number of redraws instead of spinning.

    code_depth {factor: level} stops code at that level for those factors: the leaf is still drawn
    (same rng sequence, and never_combine is checked on it), then replaced by its ancestor at the
    level, so every node there keeps its exact share and the writer chooses the specifics beneath it.
    """
    roots = strategy.get("taxonomy_roots") or []
    rules = _never_combine_rules(strategy)
    levels = code_depth or {}
    for _ in range(_MAX_MIX_REDRAWS):
        nodes = [_strategy_node_for_factor(factor, roots, rng) or _sample_descendant(factor, rng) for factor in taxonomy["factors"]]
        if not _mix_violates([_mix_entry(factor, node) for factor, node in zip(taxonomy["factors"], nodes)], rules):
            return [_mix_entry(factor, _at_level(factor, node, levels.get(factor["name"]))) for factor, node in zip(taxonomy["factors"], nodes)]
    raise ValueError(
        f"never_combine rules for strategy {strategy.get('id', '?')!r} still violated after "
        f"{_MAX_MIX_REDRAWS} redraws; loosen the rules or widen the strategy's roots."
    )


def _mix_entry(factor: dict[str, Any], node: dict[str, Any]) -> dict[str, Any]:
    return {"factor": factor["name"], "node": node["name"], "level": node.get("level", 0), "path": node.get("path", [node["name"]]), "description": node.get("description", "")}


def _at_level(factor: dict[str, Any], node: dict[str, Any], level: int | None) -> dict[str, Any]:
    # The node's ancestor at `level` (the node itself when it is already at or above that level).
    if level is None or node.get("level", 0) <= level:
        return node
    current = factor
    for name in node["path"][1 : level + 1]:
        current = next(child for child in current["children"] if child["name"] == name)
    return current


def mix_space(taxonomy: dict[str, Any], strategy: dict[str, Any], code_depth: dict[str, int] | None = None) -> int:
    """Upper bound on the distinct mixes a strategy can draw: reachable leaves per factor (under its
    roots, else the full tree; zero-weight branches excluded), multiplied. A factor in code_depth counts
    its nodes at that level instead of its leaves. never_combine is ignored."""
    roots = {_root_key(str(root)) for root in strategy.get("taxonomy_roots") or []}
    total = 1
    for factor in taxonomy["factors"]:
        stop = (code_depth or {}).get(factor["name"])
        matched = [node for node in walk_nodes(factor) if _path_key(node.get("path", [node["name"]])) in roots]
        total *= sum(_leaf_count(node, stop) for node in matched) or _leaf_count(factor, stop)
    return total


def restricting_strategies(strategies: list[dict[str, Any]], factor_name: str) -> list[str]:
    """Ids of the strategies whose taxonomy_roots limit this factor to some of its branches."""
    factor_key = _norm_segment(factor_name)
    return [
        strategy.get("id", "general")
        for strategy in strategies
        if any(len(key := _root_key(str(root))) > 1 and key[0] == factor_key for root in strategy.get("taxonomy_roots") or [])
    ]


def _leaf_count(node: dict[str, Any], stop: int | None = None) -> int:
    if node.get("children") and (stop is None or node.get("level", 0) < stop):
        return sum(_leaf_count(child, stop) for child in node["children"] if _node_weight(child) > 0)
    return 1


def level_options(taxonomy: dict[str, Any], factor_name: str, level: int) -> list[dict[str, Any]]:
    """A factor's nodes at `level` (or leaves above it), each with the share the weighted level-wise walk
    would give it (shares sum to 1). These are the options offered when the model picks the factor."""
    factor = next((f for f in taxonomy["factors"] if f["name"] == factor_name), None)
    if factor is None:
        raise ValueError(f"generation.model_picked names unknown factor {factor_name!r}.")
    options: list[dict[str, Any]] = []

    def descend(node: dict[str, Any], share: float) -> None:
        children = [child for child in node.get("children", []) if _node_weight(child) > 0]
        if node is not factor and (node.get("level", 0) >= level or not children):
            options.append({"name": node["name"], "description": node.get("description", ""), "level": node.get("level", level),
                            "path": node.get("path", [factor["name"], node["name"]]), "share": share})
            return
        total = sum(_node_weight(child) for child in children)
        for child in children:
            descend(child, share * _node_weight(child) / total)

    descend(factor, 1.0)
    return options


def find_option(options: list[dict[str, Any]], name: str) -> dict[str, Any] | None:
    """The option a model named, matched like strategy paths (case and punctuation ignored)."""
    return next((option for option in options if _norm_segment(option["name"]) == _norm_segment(name)), None)


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
    limiter: asyncio.Semaphore,
) -> list[dict[str, Any]]:
    async def ask(prompt: str, task: TaskType) -> Any:
        async with limiter:
            return await router.complete_json("strategic", prompt, system=cfg.prompts.SYSTEM_JSON, task=task)

    # Best-of-N candidate child lists, requested concurrently, then (taxonomy.critic, default on) one
    # critic call that refines them for completeness and low duplication. The critic is not just a merge:
    # even with a single proposal it fills in children the node's description implies and drops
    # duplicates (measured: without it a depth-4 tree kept 3 of 21 described verticals and 5x the repeated
    # names). critic: false (only with best_of_n 1) uses the proposal as is, 1 call per node. Malformed
    # output (~6% of expansions on DeepSeek v4 pro) gets one retry; a second failure prunes only this branch.
    expand = cfg.prompts.expand_prompt(cfg.description, factor, node, siblings, plan, cfg.taxonomy.children_per_node)
    for attempt in range(2):
        try:
            proposals = await asyncio.gather(*(ask(expand, TaskType.NODE_EXPANSION) for _ in range(cfg.taxonomy.best_of_n)))
            children = [child for response in proposals for child in response.get("children", [])]
            if cfg.taxonomy.critic:
                refined = await ask(cfg.prompts.refine_nodes_prompt(cfg.description, node, children), TaskType.TAXONOMY_CRITIC)
                children = refined.get("children", [])
            return [_child(child, level, node.get("path", [node["name"]])) for child in children]
        except Exception as exc:  # noqa: BLE001 - malformed provider output should only prune one branch.
            if attempt:
                warn(f"node expansion failed for {node.get('name')}: {exc}")
    return []


def _child(child: dict[str, Any], level: int, parent_path: list[str]) -> dict[str, Any]:
    # "/" is the path separator strategies use (taxonomy_path_strings), so it cannot live inside a name.
    name = str(child["name"]).replace("/", "-")
    return {
        "name": name,
        "description": child.get("description", ""),
        # Prevalence weight, consumed by _sample_descendant; kept explicit so it is hand-tunable in
        # the reviewed taxonomy.json.
        "weight": _node_weight(child),
        "level": level,
        "path": [*parent_path, name],
        "children": [],
    }


def _sample_descendant(root: dict[str, Any], rng: random.Random) -> dict[str, Any]:
    """Weighted level-by-level descent to a leaf.

    A node's optional `weight` (default 1.0, 0 disables the branch) sets how often it is picked
    relative to its siblings, so a branch's probability never depends on how finely it happens to be
    subdivided — a flat draw over all nodes would make the most-enumerated branch the most likely.
    """
    node = root
    while node.get("children"):
        node = _weighted_pick(node["children"], rng)
    return node


def _weighted_pick(nodes: list[dict[str, Any]], rng: random.Random) -> dict[str, Any]:
    weights = [_node_weight(node) for node in nodes]
    if sum(weights) <= 0:
        weights = [1.0] * len(nodes)
    return rng.choices(nodes, weights=weights, k=1)[0]


def _node_weight(node: dict[str, Any]) -> float:
    try:
        return max(0.0, float(node.get("weight", 1.0)))
    except (TypeError, ValueError):
        return 1.0


def _never_combine_rules(strategy: dict[str, Any]) -> list[tuple[tuple[str, ...], tuple[str, ...]]]:
    rules = []
    for pair in strategy.get("never_combine") or []:
        if isinstance(pair, (list, tuple)) and len(pair) == 2:
            rules.append((_root_key(str(pair[0])), _root_key(str(pair[1]))))
    return rules


def _mix_violates(mix: list[dict[str, Any]], rules: list[tuple[tuple[str, ...], tuple[str, ...]]]) -> bool:
    if not rules:
        return False
    keys = [_path_key(row["path"]) for row in mix]

    def _hit(rule: tuple[str, ...]) -> bool:
        return any(key[: len(rule)] == rule for key in keys)

    return any(_hit(first) and _hit(second) for first, second in rules)


def _strategy_node_for_factor(factor: dict[str, Any], roots: list[str], rng: random.Random) -> dict[str, Any] | None:
    """Pick a leaf inside `factor` for a strategy whose roots are dot- or slash-separated taxonomy paths.

    Strict subtree matching: a root that exactly names the factor samples anywhere in its tree; a
    deeper root must exactly name an existing node path and samples that node's subtree. Roots that
    match no path (or belong to another factor) contribute nothing here, so the caller can fall back.
    """
    factor_key = _path_key([factor["name"]])
    subtree_roots: list[tuple[str, ...]] = []

    # Strategy roots are dot-/slash-separated taxonomy paths, not free-form string prefixes.
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
    return _sample_descendant(_weighted_pick(candidates, rng), rng)


def _root_key(root: str) -> tuple[str, ...]:
    # "/" is the canonical separator (the valid-path list uses it); "." is accepted only for paths
    # with no "/" at all, so a name like "5.1-Channel Systems" inside a slash path stays one segment.
    root = str(root)
    parts = root.split("/") if "/" in root else root.split(".")
    return tuple(segment for segment in (_norm_segment(part) for part in parts) if segment)


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
