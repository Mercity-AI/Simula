from __future__ import annotations

import hashlib
import importlib.util
import inspect
import json
from pathlib import Path
from types import ModuleType
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


PROMPT_FUNCTION_NAMES = (
    "factor_prompt",
    "expand_prompt",
    "refine_nodes_prompt",
    "level_plan_prompt",
    "strategy_prompt",
    "meta_prompt_prompt",
    "complexify_prompt",
    "generate_record_prompt",
    "generate_text_prompt",
    "repair_json_prompt",
    "critique_prompt",
    "critique_text_prompt",
    "refine_record_prompt",
    "refine_text_prompt",
    "complexity_prompt",
    "node_assign_prompt",
)


class PromptSet:
    def __init__(self, module: ModuleType | None = None, module_path: Path | None = None):
        self.module = module
        self.module_path = module_path

    def __getattr__(self, name: str) -> Any:
        if name in {"SYSTEM_JSON", "SYSTEM_TEXT"}:
            if self.module is not None and hasattr(self.module, name):
                return getattr(self.module, name)
            return globals()[name]
        if name in PROMPT_FUNCTION_NAMES:
            if self.module is not None and hasattr(self.module, name):
                return getattr(self.module, name)
            return globals()[name]
        raise AttributeError(name)


def load_prompt_set(config_path: Path, prompt_config: Any) -> PromptSet:
    # Prompt modules are optional; missing functions fall back to the built-in prompt set.
    module_ref = _prompt_module_ref(prompt_config)
    if module_ref is None:
        return PromptSet()

    module_path = Path(module_ref)
    if not module_path.is_absolute():
        module_path = config_path.parent / module_path
    module_path = module_path.resolve()
    if not module_path.is_file():
        raise ValueError(f"prompts.module does not exist: {module_path}")

    module = _load_module(module_path)
    _validate_prompt_module(module, module_path)
    return PromptSet(module, module_path)


def _prompt_module_ref(prompt_config: Any) -> str | None:
    if prompt_config is None or prompt_config == {}:
        return None
    if isinstance(prompt_config, str):
        return prompt_config
    if isinstance(prompt_config, dict):
        module_ref = prompt_config.get("module")
        if module_ref is None:
            return None
        if not isinstance(module_ref, str) or not module_ref.strip():
            raise ValueError("prompts.module must be a non-empty string path.")
        return module_ref
    raise ValueError("prompts must be a module path string or mapping with prompts.module.")


def _load_module(module_path: Path) -> ModuleType:
    digest = hashlib.sha1(str(module_path).encode("utf-8")).hexdigest()[:12]
    spec = importlib.util.spec_from_file_location(f"syndata_user_prompts_{digest}", module_path)
    if spec is None or spec.loader is None:
        raise ValueError(f"Could not load prompt module: {module_path}")
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception as exc:  # noqa: BLE001 - surface user-module import errors during validation.
        raise ValueError(f"Failed to import prompt module {module_path}: {exc}") from exc
    return module


def _validate_prompt_module(module: ModuleType, module_path: Path) -> None:
    # Fail early when an override cannot be called like the built-in function it replaces.
    for system_name in ("SYSTEM_JSON", "SYSTEM_TEXT"):
        if hasattr(module, system_name) and not isinstance(getattr(module, system_name), str):
            raise ValueError(f"{module_path}: {system_name} must be a string.")

    for name in PROMPT_FUNCTION_NAMES:
        if not hasattr(module, name):
            continue
        override = getattr(module, name)
        if not callable(override):
            raise ValueError(f"{module_path}: {name} must be callable.")
        expected = _parameter_names(globals()[name])
        actual = _parameter_names(override)
        if actual != expected:
            raise ValueError(f"{module_path}: {name} must accept parameters {expected}; got {actual}.")


def _parameter_names(func: Any) -> list[str]:
    return list(inspect.signature(func).parameters)


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


def strategy_prompt(description: str, taxonomy: dict[str, Any], guidance: str | None = None) -> str:
    # Optional user guidance steers which roots combine and how weights emphasize/de-emphasize branches.
    guidance_block = ""
    if guidance and guidance.strip():
        guidance_block = (
            "\nUser guidance (honor these preferences when choosing taxonomy roots and weights):\n"
            f"{guidance.strip()}\n"
        )
    return f"""
Dataset description:
{description}

Taxonomy:
{json.dumps(taxonomy, ensure_ascii=False)}
{guidance_block}
Create 2-5 sampling strategies. Each strategy lists compatible taxonomy roots and a weight.
A higher weight makes a strategy sampled more often; use weights to emphasize common combinations and de-emphasize rare ones.
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
