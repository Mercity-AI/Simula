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
    # Each meta-prompt nails one concrete shopping search: a product context, how the shopper phrases
    # it, and HOW MANY atomic constraints (which sets both the JSON size and the query length).
    return f"""
You are planning examples for an e-commerce search-query extraction dataset.

Dataset description:
{description}

Sampled taxonomy requirements (reflect the product vertical, the shopper's intent, which attribute
families dominate, and the linguistic complexity / number of constraints):
{json.dumps(mix, ensure_ascii=False)}

Write {k} diverse meta-prompts. Each meta-prompt is an instruction to a generator and MUST specify:
- the concrete product the shopper is searching for and a realistic buying context;
- the phrasing style (terse keyword string, a normal sentence, or a chatty multi-sentence request);
- the TARGET NUMBER OF CONSTRAINTS to encode, chosen to match the query_complexity node above:
  small = 3-5 fields (short query), medium = 6-11 fields, large = 12-20 fields (long, detailed query).
  The query's length MUST scale with this count;
- which atomic attributes to feature (size, color, material, brand, a price budget, rating, shipping,
  technical specs, etc.), favoring the sampled attribute_focus;
- optionally a negation/exclusion (e.g. "not red", "no Nike") and/or a mild ambiguity.

Vary the vertical, intent, phrasing, and constraint count across the {k} meta-prompts. Make queries
sound like how real people type or talk into a search box or shopping assistant.
Return JSON:
{{"meta_prompts": ["...", "..."]}}
""".strip()


def complexify_prompt(description: str, meta_prompt: str) -> str:
    # "More complex" here means a LONGER, more natural query carrying MORE atomic constraints, pushing
    # toward the large (12-20 field) end so the dataset spans the full size range.
    return f"""
Dataset description:
{description}

Meta-prompt:
{meta_prompt}

Rewrite this meta-prompt so the shopper's search is longer and more natural and carries MORE atomic
constraints (aim toward 12-20 extractable fields): add extra attributes such as a price budget
(min/max), color or material, brand preferences or exclusions, size, rating or review thresholds,
shipping/availability, and a sorting preference. Keep every added constraint atomic and realistic for
the product; do NOT introduce compound or contradictory requirements. Preserve the original intent.
Return JSON:
{{"meta_prompt": "..."}}
""".strip()


def generate_record_prompt(description: str, schema: dict[str, Any], meta_prompt: str) -> str:
    # The envelope `schema` arg is intentionally loose; the real content rules live here.
    return f"""
Create ONE example for an e-commerce search-query extraction dataset.

Dataset description:
{description}

Build a single JSON object with EXACTLY these two top-level keys:

1. "query" — a realistic, natural English search a shopper would type or speak when looking for a
   product. Follow the meta-prompt's phrasing style and length. It may be a terse keyword string
   ("mens running shoes size 11 under $80") or a chatty request ("I'm looking for a birthday gift for
   my mom, something like a cozy wool cardigan, navy or grey, medium, ideally under 60 dollars").
   Write it the way a person actually searches, NOT like a filled form or JSON.

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
     did not express.
   - The number of fields MUST match the meta-prompt's target (roughly 3-20), so size varies by row.

Meta-prompt (the specific instance to build):
{meta_prompt}

Return ONLY the JSON object with keys "query" and "extraction". No commentary.
""".strip()


def critique_prompt(description: str, schema: dict[str, Any], meta_prompt: str, record: dict[str, Any]) -> str:
    # Strict gate: this is where compound fields, hallucinated constraints, and unnatural queries die.
    return f"""
Validate ONE example for an e-commerce search-query extraction dataset.

Example:
{json.dumps(record, ensure_ascii=False)}

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
Return JSON: {{"verdict": "accept" | "reject", "explanation": "..."}}
""".strip()
