from __future__ import annotations

import json
from typing import Any


SYSTEM_JSON = (
    "You are a careful synthetic data generation assistant. "
    "Always think, write, label fields, and produce content in English. "
    "Return valid JSON only, with no markdown or surrounding commentary."
)

SYSTEM_TEXT = (
    "You are a careful synthetic data generation assistant. "
    "Always think and write in English. "
    "Produce the requested content directly with no markdown wrapping or preamble."
)


def schema_text(schema: dict[str, Any] | None) -> str:
    if schema is None:
        return "No JSON schema is configured; generate free-form text."
    return json.dumps(schema, indent=2, ensure_ascii=False)


def factor_prompt(description: str, existing_factors: list[str] | None = None) -> str:
    extra = ""
    if existing_factors:
        extra = f"\nThe user suggested these factors. Improve or preserve them: {existing_factors}"
    return f"""
Dataset description:
{description}
{extra}

Identify 3-6 prime factors of variation that should be covered when generating this dataset.
Return JSON:
{{"factors": [{{"name": "...", "description": "..."}}]}}
""".strip()


def expand_prompt(description: str, factor: dict[str, Any], node: dict[str, Any], siblings: list[str], plan: str, count: int) -> str:
    return f"""
Dataset description:
{description}

Factor:
{json.dumps(factor, ensure_ascii=False)}

Current node:
{json.dumps(node, ensure_ascii=False)}

Sibling node names:
{siblings}

Expansion plan:
{plan}

Propose {count} useful, non-overlapping child nodes for the current node.
Return JSON:
{{"children": [{{"name": "...", "description": "..."}}]}}
""".strip()


def refine_nodes_prompt(description: str, node: dict[str, Any], raw_children: list[dict[str, Any]]) -> str:
    return f"""
Dataset description:
{description}

Parent node:
{json.dumps(node, ensure_ascii=False)}

Raw proposed children:
{json.dumps(raw_children, ensure_ascii=False)}

Refine this list for completeness, soundness, specificity, and low duplication.
Return JSON:
{{"children": [{{"name": "...", "description": "..."}}]}}
""".strip()


def level_plan_prompt(description: str, nodes: list[dict[str, Any]]) -> str:
    return f"""
Dataset description:
{description}

Nodes just created at this taxonomy level:
{json.dumps(nodes, ensure_ascii=False)}

Write one compact plan for expanding the next level across all listed nodes.
The plan should apply to every node at this level, even when the sibling nodes cover different domains.
Prefer abstract granularity guidance over branch-specific examples.
Avoid examples or criteria that only make sense for one listed node.
Preserve flexibility for each node's own domain while keeping same-level children comparable.
Return JSON:
{{"plan": "..."}}
""".strip()


def strategy_prompt(description: str, taxonomy: dict[str, Any]) -> str:
    return f"""
Dataset description:
{description}

Taxonomy:
{json.dumps(taxonomy, ensure_ascii=False)}

Create 2-5 sampling strategies. Each strategy lists compatible taxonomy roots and a weight.
Return JSON:
{{"strategies": [{{"id": "general", "description": "...", "taxonomy_roots": ["..."], "weight": 1.0}}]}}
""".strip()


def meta_prompt_prompt(description: str, schema: dict[str, Any] | None, mix: list[dict[str, Any]], k: int) -> str:
    format_instruction = "The final data point will be free-form text." if schema is None else f"Output JSON Schema:\n{schema_text(schema)}"
    return f"""
Dataset description:
{description}

{format_instruction}

Sampled taxonomy requirements:
{json.dumps(mix, ensure_ascii=False)}

Generate {k} diverse meta-prompts. Each meta-prompt should tell a generator exactly what record to create.
Return JSON:
{{"meta_prompts": ["...", "..."]}}
""".strip()


def complexify_prompt(description: str, meta_prompt: str) -> str:
    return f"""
Dataset description:
{description}

Meta-prompt:
{meta_prompt}

Make this meta-prompt more complex while preserving the original requirements.
Return JSON:
{{"meta_prompt": "..."}}
""".strip()


def generate_record_prompt(description: str, schema: dict[str, Any], meta_prompt: str) -> str:
    return f"""
Dataset description:
{description}

Output JSON Schema:
{schema_text(schema)}

Meta-prompt:
{meta_prompt}

Generate exactly one synthetic data record matching the schema. Return the record as JSON only.
""".strip()


def generate_text_prompt(description: str, meta_prompt: str) -> str:
    return f"""
Dataset description:
{description}

Meta-prompt:
{meta_prompt}

Generate exactly one synthetic data point matching the meta-prompt.
Return only the data point itself. No JSON wrapping. No preamble.
""".strip()


def repair_json_prompt(schema: dict[str, Any], bad_response: str, error: str) -> str:
    return f"""
The previous response did not parse or validate.

Schema:
{schema_text(schema)}

Error:
{error}

Bad response:
{bad_response}

Return a corrected JSON record only.
""".strip()


def critique_prompt(description: str, schema: dict[str, Any], meta_prompt: str, record: dict[str, Any]) -> str:
    return f"""
Dataset description:
{description}

Schema:
{schema_text(schema)}

Meta-prompt:
{meta_prompt}

Generated record:
{json.dumps(record, ensure_ascii=False)}

Does the record satisfy the meta-prompt and schema intent? Explain briefly.
Return JSON:
{{"verdict": "accept" | "reject", "explanation": "..."}}
""".strip()


def critique_text_prompt(description: str, meta_prompt: str, text: str) -> str:
    return f"""
Dataset description:
{description}

Meta-prompt:
{meta_prompt}

Generated output:
---
{text}
---

Does the output satisfy the meta-prompt? Be strict but fair.
Return JSON:
{{"verdict": "accept" | "reject", "explanation": "..."}}
""".strip()


def refine_record_prompt(schema: dict[str, Any], meta_prompt: str, record: dict[str, Any], critique: str) -> str:
    return f"""
Schema:
{schema_text(schema)}

Meta-prompt:
{meta_prompt}

Rejected record:
{json.dumps(record, ensure_ascii=False)}

Critique:
{critique}

Revise the record to satisfy the meta-prompt and schema. Return JSON only.
""".strip()


def refine_text_prompt(description: str, meta_prompt: str, text: str, critique: str) -> str:
    return f"""
Dataset description:
{description}

Meta-prompt:
{meta_prompt}

Rejected output:
---
{text}
---

Critique:
{critique}

Produce a new, better version that addresses the critique.
Return only the data point itself. No JSON wrapping. No preamble.
""".strip()


def complexity_prompt(description: str, rows: list[dict[str, Any]]) -> str:
    return f"""
Dataset description:
{description}

Records:
{json.dumps(rows, ensure_ascii=False)}

Score each record's relative complexity from 1 to 10 within this batch.
Return JSON:
{{"scores": [{{"id": "...", "score": 1, "reason": "..."}}]}}
""".strip()


def node_assign_prompt(taxonomy_text: str, factor_name: str, data_point: str) -> str:
    return f"""
Taxonomy factor:
{factor_name}

Taxonomy tree:
{taxonomy_text}

Data point:
{data_point}

Choose the single most appropriate node name in this taxonomy for the data point.
Return JSON:
{{"node_name": "..."}}
""".strip()
