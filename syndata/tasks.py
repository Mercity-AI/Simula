from __future__ import annotations

from enum import StrEnum


class TaskType(StrEnum):
    FACTOR_DISCOVERY = "factor_discovery"
    NODE_EXPANSION = "node_expansion"
    TAXONOMY_CRITIC = "taxonomy_critic"
    LEVEL_PLAN = "level_plan"
    STRATEGY = "strategy"
    META_PROMPT = "meta_prompt"
    COMPLEXIFY = "complexify"
    GENERATE = "generate"
    REPAIR = "repair"
    SEMANTIC_CRITIC = "semantic_critic"
    REFINE = "refine"
    COMPLEXITY_SCORE = "complexity_score"
    NODE_ASSIGN = "node_assign"
