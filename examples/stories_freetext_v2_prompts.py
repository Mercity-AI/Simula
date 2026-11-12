"""Per-run prompt overrides for stories_freetext_v2.

Only the meta-prompt is overridden (one mix per call, or several with meta_batch_size; with generation.subject set
the batched version is always used, and it renders each story's situation). The goal: SHORT, evocative meta-prompts that seed a story
rather than over-specify it. v1's meta-prompts were ~150-200 words each and dictated POV,
setting, plot beats, and "such as ..." example menus — which produced mechanical, same-shaped
stories with stacked devices. Here a meta-prompt is a seed: the premise plus the one or two
ingredients that most define this particular story, and nothing else. Diversity across the
corpus comes from WHICH ingredient leads varying story to story, not from cramming all seven
factors into every prompt.

Signature must match simula/prompts.py:meta_prompt_prompt exactly (description, schema, mix, k).
"""
from __future__ import annotations

import json
from typing import Any

from simula.prompts import DIVERSITY_RULE, PHRASING_RULE, situation_line, situation_note

RULES = """- Keep it to 1-3 sentences, roughly 40-90 words. Shorter is better.
- Give the premise and only the 1-2 ingredients that most define THIS story; let the writer invent the rest. Do not enumerate every ingredient.
- Always convey the rough length (from the length ingredient) in a few words.
- If the mix includes an unusual form — second person, a letter/diary (epistolary), a documentary/archive presentation, a framed/nested telling, or a nonlinear timeline — that form is the SPINE of this story: build the meta-prompt around it and state it plainly. It is not optional and must never be dropped or softened away. If the mix is ordinary narrated prose, don't mention form at all.
- Do NOT dictate the opening, the plot beats, the ending, or a "final image", and do NOT give "such as ..." example menus.
- Do NOT add craft adjectives ("vivid", "emotionally resonant", "no filler", "distinct voice") — trust the writer.
- If the THEMATIC ingredients (genre, tone, setting) clash, reconcile them — soften or drop the weaker one; coherence beats coverage. This never applies to the unusual form above, which always stays.
- Do NOT restate the invariant format rules (no title, plain prose, safe-for-work) — those are handled elsewhere."""


def _ingredients(mix: list[dict[str, Any]]) -> str:
    # Compact ingredient list: the sampled value per factor plus its short gloss.
    return json.dumps([{"factor": m["factor"], "value": m["node"], "note": m.get("description", "")} for m in mix], ensure_ascii=False)


def meta_prompt_prompt(description: str, schema: dict[str, Any] | None, mix: list[dict[str, Any]], k: int) -> str:
    ingredients = _ingredients(mix)
    return f"""
Dataset: {description}

Ingredients sampled for this story (a guide, NOT a checklist):
{ingredients}

Write {k} SHORT meta-prompt{'s' if k > 1 else ''}{', each a genuinely different story' if k > 1 else ''}. A meta-prompt is a short instruction handed to a story writer — a seed, not an outline.

Rules for every meta-prompt:
{RULES}

Return JSON:
{{"meta_prompts": ["..."]}}
""".strip()


def meta_prompt_batch_prompt(description: str, schema: dict[str, Any] | None, items: list[dict[str, Any]]) -> str:
    # The same rules for several stories per call (generation.meta_batch_size), each with its situation when set.
    specs = "\n\n".join(f"{item['id']} ({item['count']} meta-prompt{'s' if item['count'] > 1 else ''}):\n{_ingredients(item['mix'])}{situation_line(item)}"
                         for item in items)
    return f"""
Dataset: {description}

Below are ingredient sets sampled for separate stories, one per id (a guide, NOT a checklist). For each id, write the
number of SHORT meta-prompts it asks for. A meta-prompt is a short instruction handed to a story writer — a seed, not
an outline.
{DIVERSITY_RULE} Give each story its own premise, characters and names.{situation_note(items)}
{PHRASING_RULE}

Rules for every meta-prompt:
{RULES}

{specs}

Return JSON:
{{"mixes": [{{"id": "m1", "meta_prompts": ["..."]}}]}}
""".strip()
