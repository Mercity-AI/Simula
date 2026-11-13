"""Prompt overrides for the e-commerce search-query extraction dataset.

Each generated row is an extraction *example* shaped as:

    {
      "query":      "<a natural-language shopper search>",
      "extraction": { field_name: atomic_value, ... }   # the structured query
    }

The global YAML schema validates the envelope (query/extraction) and leaves
`extraction` as an open object, so each row can carry a DIFFERENT key set (people
search for different things). These overrides supply the real task rules the loose
envelope cannot express: narrow ATOMIC fields ready for a DB query, faithful
(non-hallucinated) extraction, length/size variation (3..20 fields), optional one-
level nesting for naturally grouped facts, and a strict critic.

Only the parameter *names* must match the built-ins in simula/prompts.py.
"""

from __future__ import annotations

import json
from typing import Any

from simula.prompts import choices_text, mix_text, situation_line, situation_note


# A catalog of GOOD narrow, atomic e-commerce attributes spanning many verticals. Listed in the
# prompts to anchor the model toward canonical, DB-queryable keys and away from compound junk like
# "size_and_color" or "price_range". No row uses all of these; invent similar atomic fields per domain.
_ATTRIBUTE_MENU = (
    # universal
    "category, product_type, brand, brand_excluded (array<string>), keywords (array<string>), "
    "gender (enum: men|women|unisex|kids|boys|girls), age_group (enum: adult|teen|kids|toddler|infant), "
    "condition (enum: new|used|refurbished|open_box), quantity (integer), "
    # price / deals (split ranges; never a 'price_range' string)
    "price_min (number), price_max (number), currency (e.g. USD), on_sale (boolean), "
    "discount_min_percent (number), "
    # ratings / social proof / availability / shipping
    "rating_min (number 0-5), reviews_min (integer), in_stock (boolean), free_shipping (boolean), "
    "delivery_by (date YYYY-MM-DD), seller, prime_eligible (boolean), "
    # apparel / footwear
    "size, size_system (enum: alpha|us|uk|eu|numeric), color, color_excluded (array<string>), "
    "material, pattern, fit (enum: slim|regular|relaxed|oversized), sleeve_length, neckline, "
    "occasion, season, waterproof (boolean), shoe_width (enum: narrow|regular|wide), "
    # electronics / appliances
    "storage_gb (integer), ram_gb (integer), screen_size_in (number), battery_mah (integer), "
    "refresh_rate_hz (integer), connectivity (array<string>), os, model_year (integer), "
    "energy_rating, warranty_years (number), compatible_with, "
    # home / grocery / beauty / generic specs
    "capacity_l (number), weight_kg (number), dimensions (group: length_cm/width_cm/height_cm), "
    "scent, flavor, organic (boolean), dietary (array<string>), spf (integer), "
    # sorting intent
    "sort_by (enum: price_asc|price_desc|rating|newest|popularity|discount)"
)

_TYPE_SPECS = (
    "string, number, integer, boolean, date (YYYY-MM-DD), an array of short strings, "
    "or a one-level group object whose own values are atomic"
)


def meta_prompt_prompt(description: str, schema: dict[str, Any] | None, mix: list[dict[str, Any]], k: int) -> str:
    # Each meta-prompt is a short instruction for ONE search inside the sampled mix: the product, how the
    # shopper phrases it, and HOW MANY atomic constraints (which sets both the JSON size and the query
    # length). It describes the search; the generator writes the query and picks the exact values.
    several = (
        f"\nAll {k} meta-prompts become separate examples built from these same requirements, so make them "
        "genuinely different searches (a different concrete product inside the vertical, buyer, context and "
        "phrasing), every one true to the sampled requirements."
        if k > 1
        else ""
    )
    return f"""
You are planning examples for an e-commerce search-query extraction dataset.

Dataset description:
{description}

Sampled taxonomy requirements (the product vertical, the shopper's intent, which attribute families
dominate, and the linguistic complexity / number of constraints):
{mix_text(mix)}

{_CONFLICT_RULE}

Write {k} meta-prompt{"s" if k > 1 else ""}. A meta-prompt is a short instruction (at most 70 words) for a generator and MUST:
{_META_PROMPT_RULES}{several}
Return JSON:
{{"meta_prompts": ["..."]}}
""".strip()


def meta_prompt_batch_prompt(description: str, schema: dict[str, Any] | None, items: list[dict[str, Any]]) -> str:
    # Batch mode A: meta-prompts for several sampled mixes in one call, under the same rules as meta_prompt_prompt.
    specs = "\n\n".join(f"{item['id']} ({item['count']} meta-prompt{'s' if item['count'] > 1 else ''}):\n{mix_text(item['mix'])}{situation_line(item)}" for item in items)
    return f"""
You are planning examples for an e-commerce search-query extraction dataset.

Dataset description:
{description}

Below are sets of sampled taxonomy requirements, one per id (the product vertical, the shopper's intent,
which attribute families dominate, and the linguistic complexity / number of constraints).

{_CONFLICT_RULE}

For each id, write the number of meta-prompts it asks for. A meta-prompt is a short instruction (at most 70 words)
for a generator and MUST:
{_META_PROMPT_RULES}
Several meta-prompts for one id must be genuinely different searches (a different concrete product inside
the vertical, buyer, context and phrasing).{situation_note(items)}

{_BATCH_VARIETY}

{specs}

Return JSON:
{{"mixes": [{{"id": "m1", "meta_prompts": ["..."]}}]}}
""".strip()


def complexify_prompt(description: str, meta_prompt: str) -> str:
    # "Harder" means harder to PARSE, not bigger: the constraint count is owned by the sampled
    # query_complexity node, so complexify must not inflate it.
    return f"""
Dataset description:
{description}

Meta-prompt:
{meta_prompt}

Rewrite this meta-prompt so the shopper's search is harder to parse correctly while keeping its product,
intent, phrasing style and TARGET NUMBER OF CONSTRAINTS. {_HARDER_RULE} Do not write the query itself.
Return JSON:
{{"meta_prompt": "..."}}
""".strip()


def complexify_record_prompt(description: str, schema: dict[str, Any] | None, meta_prompt: str, record: Any) -> str:
    # Combined path: complexify rewrites the finished example. Same meaning of "harder" as above
    # (harder to parse, not bigger), and the extraction must be rebuilt to match the rewritten query.
    return f"""
Dataset description:
{description}

Example (query + extraction):
{json.dumps(record, ensure_ascii=False)}

Rewrite the query so it is harder to parse correctly while keeping the same product, intent, phrasing
style and number of constraints. {_HARDER_RULE}
Then rebuild the extraction from the rewritten query: every field must be stated or clearly implied by
the new query, no stated constraint may be dropped, and every field stays atomic (split ranges into
min/max, put exclusions in *_excluded fields).
Return ONLY the JSON object with keys "query" and "extraction".
""".strip()


# The record contract shared by the one-record and the batch generate prompts.
_RECORD_RULES = f"""
Each example is a JSON object with EXACTLY these two top-level keys:

1. "query" — a realistic, natural English search a shopper would type or speak when looking for a
   product. It may be a terse keyword string ("mens running shoes size 11 under $80") or a chatty
   request ("I'm looking for a birthday gift for my mom, something like a cozy wool cardigan, navy or
   grey, medium, ideally under 60 dollars"). Write it the way a person actually searches, NOT like a
   filled form or JSON.

2. "extraction" — the structured query parsed from "query", ready to drive a database lookup. Rules:
   - Use NARROW, ATOMIC fields: each field holds exactly ONE fact. Snake_case canonical keys.
   - Allowed value types: {_TYPE_SPECS}.
   - Split every range and compound phrase into atomic fields: a budget "$50 to $100" becomes
     "price_min": 50 and "price_max": 100 (plus "currency"); "size XL" becomes "size": "XL"; a
     numeric size becomes "size": "42" with "size_system": "numeric". NEVER emit a "price_range",
     "size_and_color", "budget", or any value that packs two facts into one string.
   - Draw from these canonical atomic attributes (invent similar ones for the vertical when needed):
     {_ATTRIBUTE_MENU}.
   - Nesting is allowed but optional, only one level deep, ONLY for naturally grouped facts, and every
     leaf must still be atomic, e.g. "price": {{"min": 50, "max": 100, "currency": "USD"}} or
     "dimensions": {{"length_cm": 40, "width_cm": 30}}. Vary between the flat style (price_min/price_max)
     and the nested style across examples. Do not nest more than one level.
   - Multi-valued facts use arrays of short atomic strings: "colors": ["navy", "grey"],
     "brand_excluded": ["Nike"].
   - Encode ONLY constraints stated or unambiguously implied in "query". Normalize obvious cues
     ("cheap"/"budget" alone is NOT a number — do not invent one; "under $80" -> price_max 80;
     "highly rated" -> rating_min ~4; "on sale" -> on_sale true). Never invent attributes the shopper
     did not express — no default gender, size_system, currency, in_stock or condition unless the query
     says so.
""".strip()

_BATCH_VARIETY = """These are independent searches by different shoppers: they must not read like a batch. Vary openers,
sentence shapes, brands, price points, sizes and circumstances; never reuse a template or the same numbers."""

_SIZE_RULE = """- set the TARGET NUMBER OF CONSTRAINTS: small = 3-5 fields, medium = 6-11, large = 12-20. Use the
  size the query_complexity node names; when it names none, pick the size its phrasing implies (a terse
  fragment is small, a chatty multi-sentence request medium or large). The query's length scales with it;"""

# The four factors are sampled independently, so their leaves often conflict (a spec-heavy intent with a
# soft-attributes-only query shape, a packaging attribute on fresh meat). A fixed precedence lets the
# model resolve that in a sentence instead of deliberating, and keeps every record coherent.
_CONFLICT_RULE = """The sampled nodes are ingredients, not a checklist: they were drawn independently and often conflict.
Resolve conflicts with this precedence and do not deliberate: product_vertical (what is searched for) >
query_complexity (phrasing shape and field count) > shopper_intent > attribute_focus. Use a lower-priority
node only where it fits naturally; soften or drop it otherwise. A coherent, realistic search beats full
coverage."""

_HARDER_RULE = (
    "Add one or two parsing challenges: an exclusion/negation, a comparative (\"cheaper than my last one\"), "
    "a mild ambiguity, colloquial wording or a typo, or a range that must be split into min/max. Do not raise "
    "the number of constraints."
)

_META_PROMPT_RULES = f"""- name the concrete product being searched for, inside the sampled product vertical, and a realistic
  buying context that fits the sampled intent;
- set the phrasing style (terse keyword string, a normal sentence, or a chatty multi-sentence request);
{_SIZE_RULE}
- say which kinds of atomic attributes to feature, favoring the sampled attribute_focus;
- optionally a negation/exclusion or a mild ambiguity (required when query_complexity calls for one).
Describe the search; do NOT write the query or the extraction, and leave exact values (brand names,
prices, sizes) to the generator."""


def generate_record_prompt(description: str, schema: dict[str, Any], meta_prompt: str) -> str:
    # The envelope `schema` arg is intentionally loose; the real content rules live in _RECORD_RULES.
    return f"""
Create ONE example for an e-commerce search-query extraction dataset.

Dataset description:
{description}

{_RECORD_RULES}
   - The number of fields MUST match the meta-prompt's target (roughly 3-20), so size varies by row.

Meta-prompt (the specific instance to build):
{meta_prompt}

Return ONLY the JSON object with keys "query" and "extraction". No commentary.
""".strip()


def generate_record_batch_prompt(description: str, schema: dict[str, Any] | None, items: list[dict[str, Any]]) -> str:
    # Batch mode B: several examples per call, each from its own meta-prompt (same rules as one-by-one).
    specs = "\n\n".join(f"{item['id']}:\n{item['meta_prompt']}" for item in items)
    return f"""
Create examples for an e-commerce search-query extraction dataset, one per meta-prompt below.

Dataset description:
{description}

{_RECORD_RULES}
   - The number of fields MUST match each meta-prompt's target (roughly 3-20), so size varies by row.

{_BATCH_VARIETY}

Meta-prompts (each is the specific instance to build):
{specs}

Return JSON:
{{"records": [{{"id": "r1", "record": {{"query": "...", "extraction": {{...}}}}}}]}}
""".strip()


def combined_batch_prompt(description: str, schema: dict[str, Any] | None, items: list[dict[str, Any]], choices: list[dict[str, Any]]) -> str:
    # Batch mode C, the combined path: several examples per call straight from their sampled mixes, so
    # the planning rules of meta_prompt_prompt live here too. Static text first (cacheable prefix).
    # choices = model-picked factors (generation.model_picked): offered as options, reported in "picked".
    specs = "\n\n".join(f"{item['id']}:\n{mix_text(item['mix'])}{situation_line(item)}" for item in items)
    picked = ""
    if choices:
        picked = (
            "\n\nChoose these factors yourself for every search (they are not in the specs): pick the option that "
            "fits that product and shopper best, spread your picks across the searches roughly in the shares shown, "
            'and report them in "picked" by exact option name.\n' + choices_text(choices) + "\n"
        )
    pick_field = ', "picked": {' + ", ".join(f'"{choice["factor"]}": "..."' for choice in choices) + "}" if choices else ""
    return f"""
Create examples for an e-commerce search-query extraction dataset, one per spec below.

Dataset description:
{description}

{_RECORD_RULES}

Planning each example from its spec (the sampled taxonomy nodes):{picked}
{_CONFLICT_RULE}
- Pick a concrete product inside the sampled product_vertical and a buying context that fits the
  shopper_intent; feature attributes from the sampled attribute_focus.
{_SIZE_RULE.replace("- set the", "- Set the", 1)}
- Phrasing style follows query_complexity too: terse keyword string, a normal sentence, or a chatty
  multi-sentence request.
- First write a one-sentence "plan": the product, the buying context, the phrasing style and the target
  field count. Then write the example so it matches the plan.

The spec nodes are instructions, not content: never copy a node name, path or description into the
query or the extraction ("category" holds the shopper's own words, if anything).{situation_note(items)}

{_BATCH_VARIETY}

{specs}

Return JSON:
{{"records": [{{"id": "r1", "plan": "..."{pick_field}, "record": {{"query": "...", "extraction": {{...}}}}}}]}}
""".strip()


def critique_prompt(description: str, schema: dict[str, Any], meta_prompt: str, record: dict[str, Any]) -> str:
    # Strict gate: this is where compound fields, hallucinated constraints, and unnatural queries die.
    # On reject it also returns the corrected example, which saves the refine call and a re-critique.
    # The rules come first and the example last so the instruction block is a cacheable prefix.
    return f"""
Validate ONE example for an e-commerce search-query extraction dataset.

Return verdict "reject" if ANY check fails:
1. Top-level keys are not exactly "query" and "extraction".
2. "query" is not a realistic English shopper search, or reads like a filled form / JSON dump.
3. "extraction" has fewer than 3 atomic fields, or is empty.
4. Any field is compound / non-atomic: a range packed in one string ("50-100", "M-L"), multiple facts
   in one value ("XL navy cotton"), or a junk key like "price_range", "budget", "size_and_color".
5. A value has the wrong type (price as a string with currency symbols, a number written as words,
   an enum value outside a sensible set) or a group/object is nested more than one level deep or has a
   non-atomic leaf.
6. Any extracted value asserts a constraint NOT stated or clearly implied by "query", or contradicts
   it (hallucination) — including an invented price number when the query only said "cheap".
7. A constraint clearly present in "query" is missing from "extraction" (material omission), or a
   range was not split into atomic min/max fields.

Otherwise return "accept". Be strict but fair; natural normalization (under $80 -> price_max 80) is good.
If you reject, also return "revised": the example corrected so every check passes. Keep the query unless
the query itself is the problem, and change only what is needed (usually removing an unsupported field
or adding a missed one).

Example:
{json.dumps(record, ensure_ascii=False)}

Return JSON (omit "revised" when accepting):
{{"verdict": "accept" | "reject", "explanation": "...", "revised": {{"query": "...", "extraction": {{...}}}}}}
""".strip()
