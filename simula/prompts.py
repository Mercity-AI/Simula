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


class PromptSet:
    def __init__(self, module: ModuleType | None = None, module_path: Path | None = None):
        self.module = module
        self.module_path = module_path

    def __getattr__(self, name: str) -> Any:
        # Resolve an overridable name to the user module's version if present, else the built-in.
        if name in OVERRIDABLE_NAMES:
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
    spec = importlib.util.spec_from_file_location(f"simula_user_prompts_{digest}", module_path)
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
Give each child a "weight": its expected prevalence in realistic data for this dataset (dominant ~1.0, common ~0.5, uncommon ~0.15, rare ~0.03). Weight is prevalence, not how interesting the category is to enumerate; rare forms sit an order of magnitude below common ones.
Return JSON:
{{"children": [{{"name": "...", "description": "...", "weight": 1.0}}]}}
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
Keep each child's "weight" (its expected prevalence in realistic data, not its interestingness): preserve incoming weights unless a merge or split makes an adjustment necessary, and give any new child a weight on the same scale (dominant ~1.0, common ~0.5, uncommon ~0.15, rare ~0.03).
Return JSON:
{{"children": [{{"name": "...", "description": "...", "weight": 1.0}}]}}
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


def strategy_prompt(
    description: str,
    taxonomy: dict[str, Any],
    valid_paths: list[str],
    guidance: str | None = None,
    count: int | None = None,
) -> str:
    # Optional user guidance steers which roots combine and how weights emphasize/de-emphasize branches.
    guidance_block = ""
    if guidance and guidance.strip():
        guidance_block = (
            "\nUser guidance (honor these preferences when choosing taxonomy roots and weights):\n"
            f"{guidance.strip()}\n"
        )
    paths_block = (
        "\nValid taxonomy paths (use these strings verbatim in taxonomy_roots and never_combine; "
        "any other string is invalid):\n" + "\n".join(valid_paths) + "\n"
    )
    return f"""
Dataset description:
{description}

Taxonomy:
{json.dumps(taxonomy, ensure_ascii=False)}
{paths_block}{guidance_block}
Create {count if count else "2-5"} sampling strategies. Each strategy is a thematic lane: mixes are sampled by drawing one node per factor from within its taxonomy_roots.
- Every strategy must address every factor. A bare factor name means "sample that factor's full tree"; use it whenever the strategy has no opinion about that factor. Never omit a factor.
- A higher weight makes a strategy sampled more often. Spread weights across a real range (common, broadly useful strategies near 1.0; rare or specialised ones near 0.1) instead of clustering everything near 1.0.
- Before bundling roots together, think about what records their combination would actually produce. Avoid bundles whose combinations would force incoherent, contradictory, or over-constructed records — a combination that is individually sensible can still be strange in aggregate.
- Spread rare or unusual branches thinly across strategies instead of concentrating them into one strategy; a strategy made only of unusual roots makes every record it produces unusual in several ways at once.
- never_combine lists pairs of paths that must never appear in the same mix: combinations that would be contradictory, redundant (the same idea imposed by two factors), or impossible at the record's scale. Only add pairs you are confident about; an empty list is fine.
Return JSON:
{{"strategies": [{{"id": "general", "description": "...", "taxonomy_roots": ["..."], "weight": 1.0, "never_combine": [["...", "..."]]}}]}}
""".strip()


def mix_text(mix: list[dict[str, Any]]) -> str:
    """One line per sampled node: `- factor: branch > ... > leaf — description` (path minus the factor root)."""
    lines = []
    for entry in mix:
        path = entry.get("path") or [entry["node"]]
        description = f" — {entry['description']}" if entry.get("description") else ""
        lines.append(f"- {entry['factor']}: {' > '.join(path[1:] or path)}{description}")
    return "\n".join(lines)


def choices_text(choices: list[dict[str, Any]]) -> str:
    """Model-picked factors for a batch prompt: per factor, its options with target shares."""
    blocks = []
    for choice in choices:
        lines = [f"{choice['factor']}:"]
        for option in choice["options"]:
            description = f" — {option['description']}" if option.get("description") else ""
            lines.append(f"- {option['name']} ({option['share']:.0%}){description}")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def meta_prompt_prompt(description: str, schema: dict[str, Any] | None, mix: list[dict[str, Any]], k: int) -> str:
    # k > 1: every scenario becomes its own record sharing this mix, so they must differ for real.
    format_instruction = "The final data point will be free-form text." if schema is None else f"Output JSON Schema:\n{schema_text(schema)}"
    several = (
        f"\nAll {k} meta-prompts become separate records built from these same ingredients, so make them genuinely distinct records — different situations, details and angles — not rewordings of one idea."
        if k > 1
        else ""
    )
    return f"""
Dataset description:
{description}

{format_instruction}

Sampled ingredients for this record (not a strict checklist):
{json.dumps(mix, ensure_ascii=False)}

Write {k} meta-prompt{"s" if k > 1 else ""}. A meta-prompt tells a generator what record to create: the situation and the content the record must carry, in at most 80 words. Describe the record; do not write it.{several}
Think about what this combination of ingredients implies for the finished record. Full coverage of the ingredients, although important, is not the goal by itself — coherence, and being smart about what the combination should produce, is just as important, if not more. If ingredients conflict or their sum would be incoherent, build the meta-prompt around the load-bearing ones and soften or drop the rest.
Return JSON:
{{"meta_prompts": ["..."]}}
""".strip()


def meta_prompt_batch_prompt(description: str, schema: dict[str, Any] | None, items: list[dict[str, Any]]) -> str:
    # Batch mode A (generation.meta_batch_size > 1): scenarios for several sampled mixes in one call.
    # items = [{"id": "m1", "mix": [...], "count": k}]. Static text first, specs last (cacheable prefix).
    format_instruction = "The final data points will be free-form text." if schema is None else f"Output JSON Schema:\n{schema_text(schema)}"
    specs = "\n\n".join(f"{item['id']} ({item['count']} meta-prompt{'s' if item['count'] > 1 else ''}):\n{mix_text(item['mix'])}" for item in items)
    return f"""
Dataset description:
{description}

{format_instruction}

Below are sets of sampled ingredients (not strict checklists), one per id. For each id, write the number of
meta-prompts it asks for. A meta-prompt tells a generator what record to create: the situation and the
content the record must carry, in at most 80 words. Describe the record; do not write it.
- Think about what each combination of ingredients implies for the finished record. Full coverage of the ingredients, although important, is not the goal by itself — coherence, and being smart about what the combination should produce, is just as important, if not more. If ingredients conflict or their sum would be incoherent, build around the load-bearing ones and soften or drop the rest.
- The ids are unrelated records: give each its own situation, people, names and details; never reuse a template across ids. Several meta-prompts for one id must be genuinely different records, not rewordings.

{specs}

Return JSON:
{{"mixes": [{{"id": "m1", "meta_prompts": ["..."]}}]}}
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


def generate_record_batch_prompt(description: str, schema: dict[str, Any] | None, items: list[dict[str, Any]]) -> str:
    # Batch mode B (generation.record_batch_size > 1 with meta-prompts on): records for several meta-prompts in
    # one call. items = [{"id": "r1", "meta_prompt": "..."}]. Static text first, specs last.
    record_format = (
        "Each record is free-form text, given as a JSON string."
        if schema is None
        else f"Each record is a JSON object matching this JSON Schema:\n{schema_text(schema)}"
    )
    specs = "\n\n".join(f"{item['id']}:\n{item['meta_prompt']}" for item in items)
    return f"""
Dataset description:
{description}

{record_format}

Write one record per meta-prompt below; each meta-prompt says what its record must be.
- The records are independent examples of one dataset. Keep each true to its own meta-prompt, and do not let them converge on a shared template or shared details.
- Give every record the length and depth it would have if it were written on its own.

{specs}

Return JSON:
{{"records": [{{"id": "r1", "record": ...}}]}}
""".strip()


def combined_batch_prompt(description: str, schema: dict[str, Any] | None, items: list[dict[str, Any]], choices: list[dict[str, Any]]) -> str:
    # Batch mode C, the combined path (generation.meta_prompts: false): several records per call, straight from their
    # sampled mixes. items = [{"id": "r1", "mix": [...], "harder": bool}]. Static text first, specs
    # last, so providers that cache prompt prefixes can reuse the instruction block across calls.
    record_format = (
        "Each record is free-form text, given as a JSON string."
        if schema is None
        else f"Each record is a JSON object matching this JSON Schema:\n{schema_text(schema)}"
    )
    specs = "\n\n".join(f"{item['id']}{' (harder)' if item['harder'] else ''}:\n{mix_text(item['mix'])}" for item in items)
    picked = ""
    if choices:  # generation.model_picked: these factors are chosen by the model per record
        picked = (
            "\n\nChoose these factors yourself for every record (they are not in the specs): pick the option that "
            "fits that record best, spread your picks across records roughly in the shares shown, and report them "
            'in "picked" by exact option name.\n' + choices_text(choices)
        )
    pick_field = ', "picked": {' + ", ".join(f'"{choice["factor"]}": "..."' for choice in choices) + "}" if choices else ""
    return f"""
Dataset description:
{description}

{record_format}{picked}

Write one record per spec below. Each spec lists the sampled ingredients for its record (taxonomy nodes; not a strict checklist).
- First write a one-sentence "plan": what the record will be, given what its ingredients imply together. Coverage of the ingredients matters, but coherence matters as much: if ingredients conflict or their sum would be incoherent, build around the load-bearing ones and soften or drop the rest.
- A spec marked (harder) gets a record that is more complex or challenging than a typical one, while staying true to its ingredients.
- The records are independent examples of one dataset. Make each one distinct: different names, people, places, numbers, details and phrasing; never reuse a template across the batch.
- Give every record the length and depth it would have if it were written on its own.
- The ingredients are instructions to you, not content: never copy their names, paths or descriptions into a record.

{specs}

Return JSON:
{{"records": [{{"id": "r1", "plan": "..."{pick_field}, "record": ...}}]}}
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
If you reject it, also return "revised": the record corrected to fix every problem you found (change only what is needed).
Return JSON (omit "revised" when accepting):
{{"verdict": "accept" | "reject", "explanation": "...", "revised": {{...}}}}
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


# Overridable prompt builders are every public module-level function ending in `_prompt`, derived
# here so adding a builder never requires updating a hand-maintained list. Defined at module end so
# all builders are already in globals().
PROMPT_FUNCTION_NAMES = tuple(
    name
    for name, obj in sorted(globals().items())
    if name.endswith("_prompt") and not name.startswith("_") and callable(obj)
)
OVERRIDABLE_NAMES = frozenset(PROMPT_FUNCTION_NAMES) | {"SYSTEM_JSON", "SYSTEM_TEXT"}
