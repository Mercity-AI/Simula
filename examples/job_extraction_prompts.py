"""Prompt overrides for the schema-guided job-posting extraction dataset.

Each generated row is an extraction *example* shaped as:

    {
      "schema":     { field_name: type_spec, ... },   # what to extract (varies per row)
      "text":       "<a realistic English job posting>",
      "extraction": { field_name: atomic_value, ... }  # only fields actually in `text`
    }

The global YAML schema validates the envelope (schema/text/extraction) and leaves
`schema`/`extraction` as open objects so each row can carry a DIFFERENT key set. These
overrides supply the real task rules the loose envelope cannot express: narrow atomic
fields, faithful (non-hallucinated) extraction, omit-when-absent, and strict critique.

Only the parameter *names* must match the built-ins in syndata/prompts.py.
"""

from __future__ import annotations

import json
from typing import Any


# Catalog of good NARROW, atomic fields. Listed in the prompt to anchor the model away
# from compound junk like "salary_range" or "location". Not every row uses every field.
_FIELD_MENU = (
    "title, seniority (enum: intern|junior|mid|senior|lead|principal|manager|director), "
    "employment_type (enum: full_time|part_time|contract|internship|temporary), "
    "location_city, location_country, remote (boolean), hybrid (boolean), "
    "relocation_offered (boolean), salary_min (integer), salary_max (integer), "
    "salary_currency, salary_period (enum: hour|day|month|year), equity_offered (boolean), "
    "bonus_offered (boolean), skills (array<string>), min_years_experience (integer), "
    "education_level (enum: none|high_school|bachelor|master|phd), "
    "visa_sponsorship (boolean), security_clearance_required (boolean), team, department, "
    "start_date (date YYYY-MM-DD), application_deadline (date YYYY-MM-DD), "
    "travel_required (boolean), contact_email, company_name, num_openings (integer)"
)

_TYPE_SPECS = (
    '"string", "integer", "number", "boolean", "date (YYYY-MM-DD)", '
    '"array<string>", or "enum: a|b|c"'
)


def meta_prompt_prompt(description: str, schema: dict[str, Any] | None, mix: list[dict[str, Any]], k: int) -> str:
    # Each meta-prompt nails down one concrete extraction instance: a job context, a
    # DISTINCT narrow target schema, and which requested fields the text will/won't state.
    return f"""
You are planning examples for a schema-guided job-posting extraction dataset.

Dataset description:
{description}

Sampled taxonomy requirements (reflect these in the job posting's domain, seniority,
writing style, and which facts are present/absent):
{json.dumps(mix, ensure_ascii=False)}

Write {k} diverse meta-prompts. Each meta-prompt is an instruction to a generator and MUST specify:
- the kind of job and company context to write a realistic posting about;
- a NARROW target schema of 4-9 atomic fields to request (choose a DIFFERENT subset for each
  meta-prompt; never request a field that bundles multiple facts);
- a deliberate gap: at least one requested field that the posting will NOT state (so the
  extraction must omit it) and optionally one fact in the posting that is NOT requested.

Vary seniority, domain, region, currency, and posting style across the {k} meta-prompts.
Return JSON:
{{"meta_prompts": ["...", "..."]}}
""".strip()


def generate_record_prompt(description: str, schema: dict[str, Any], meta_prompt: str) -> str:
    # The envelope `schema` arg is intentionally loose; the real content rules live here.
    return f"""
Create ONE example for a schema-guided information-extraction dataset about JOB POSTINGS.

Dataset description:
{description}

Build a single JSON object with EXACTLY these three top-level keys:

1. "schema" — an object mapping snake_case field names to a short type spec. This is the
   extraction target requested for THIS example. Requirements:
   - Use NARROW, ATOMIC fields only. Each field holds ONE fact.
   - Allowed type specs: {_TYPE_SPECS}.
   - Choose 4-9 fields. Vary the field set from other examples.
   - Good atomic fields to draw from (invent similar ones when sensible):
     {_FIELD_MENU}.
   - NEVER use compound fields such as "salary_range", "location", "date_range", or any
     field whose value would pack two or more facts into one string. Split them
     (salary_min/salary_max, location_city/location_country, start_date/application_deadline).

2. "text" — a realistic, natural English job posting consistent with the meta-prompt below.
   1-6 sentences or a short bulleted ad. It may state some, all, or extra facts beyond the
   requested schema. Write it like a real ad, NOT like a filled form or JSON.

3. "extraction" — the result of applying "schema" to "text". Rules:
   - Use ONLY information explicitly stated in "text". Never invent, infer, or guess.
   - Values must be ATOMIC and correctly typed per the field's spec: numbers as JSON
     numbers, booleans as true/false, dates as "YYYY-MM-DD", arrays as JSON arrays of
     short strings, enums as one allowed token.
   - If "text" does not state a requested field, OMIT that key entirely. Do not null-pad
     and do not guess. So "extraction" keys are a SUBSET of "schema" keys and vary per row.
   - Never add a key that is not in "schema".

Meta-prompt (the specific instance to build):
{meta_prompt}

Return ONLY the JSON object with keys "schema", "text", and "extraction". No commentary.
""".strip()


def critique_prompt(description: str, schema: dict[str, Any], meta_prompt: str, record: dict[str, Any]) -> str:
    # Strict gate: this is where compound fields, hallucinations, and bad omissions die.
    return f"""
Validate ONE example for a schema-guided JOB-POSTING extraction dataset.

Example:
{json.dumps(record, ensure_ascii=False)}

Return verdict "reject" if ANY check fails:
1. Top-level keys are not exactly "schema", "text", "extraction".
2. "schema" contains a compound / non-atomic field (a field that would hold a range,
   multiple facts, or free-form prose — e.g. "salary_range", "location", "date_range",
   "description", "requirements").
3. Any "extraction" value is non-atomic (a range string like "70-90k", a sentence packing
   2+ facts) or has the wrong type for its declared spec.
4. Any "extraction" key is missing from "schema".
5. Any "extraction" value asserts a fact NOT explicitly supported by "text", or contradicts
   "text" (hallucination).
6. A requested field that "text" clearly does NOT state was included in "extraction" anyway
   (it should have been omitted).
7. "text" is not a realistic English job posting, or reads like a filled form / JSON dump.

Otherwise return "accept". Be strict but fair.
Return JSON: {{"verdict": "accept" | "reject", "explanation": "..."}}
""".strip()
