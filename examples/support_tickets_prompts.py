"""Prompt module for examples/support_tickets.yaml: a support-ticket triage dataset for Pulseboard, a fictional B2B
project-management SaaS. Each record is one customer ticket plus its triage labels (category, priority, sentiment,
escalate).

This is the setup measured in the November 2026 benchmark (10,000 tickets on GLM 5.3 Flash, about $0.85 per 1,000):
the combined path, 5 tickets per call, `subject: category` (one situation per ticket), and the shared rules from
simula/prompts.py. What each override is for:

- expand_prompt, refine_nodes_prompt: give the tree builder the product context and pin level 1 of the four
  labelled factors to the exact label names and target shares, so code samples the labels at the right rates.
- combined_batch_prompt: the main writer (combined path). Renders each ticket's situation.
- complexify_record_prompt: makes a written ticket harder to triage without changing its labels.
- critique_prompt, refine_record_prompt: a label-focused checklist; fixes change the text, not the labels.
- meta_prompt_prompt, meta_prompt_batch_prompt, complexify_prompt, generate_record_batch_prompt: the same rules
  for the meta-prompt path (`meta_prompts: true`). With a subject set, that path always calls
  meta_prompt_batch_prompt, so it is the one that renders situations.

The writer prompts get the product brief and the triage guidelines; the critic gets the guidelines only.
"""
from __future__ import annotations

import json
from typing import Any

from simula.prompts import (
    CONTEXT_RULE,
    DIVERSITY_RULE,
    PHRASING_RULE,
    REVISE_RULE,
    TAXONOMY_CONTEXT_RULE,
    choices_text,
    mix_text,
    situation_line,
    situation_note,
)

# Product facts the writer may use. Kept to facts: an earlier version listed recent incidents and error codes as
# "texture", and they ended up in a quarter of all tickets.
BRIEF = """PULSEBOARD (fictional B2B SaaS): project management + delivery analytics for agencies, software, marketing
and ops teams. Workspace → projects → boards → cards. Modules: Boards (Kanban/List/
Calendar/Timeline), Sprints, Goals/OKRs, Insights dashboards, Automations (each
execution = a "run" against a monthly quota), Forms, Pulse AI add-on ($6/user/mo). Web + mobile apps.
Plans: Free (5 members, 3 projects, 100 runs/mo, email ~72 h). Pro ($12/user/mo annual, $15 monthly, up from
$14 on 1 Nov 2026; 2,500 runs, chat, 24 h). Enterprise (custom; SAML SSO, SCIM, audit log, EU/US region,
50k runs, 99.9% SLA, 4 h / 1 h urgent, CSM).
Billing: seat-based, added seats prorated, removed seats credited at renewal; refunds only on annual plans
within 14 days; failed payment → read-only after 10 days.
Access: password, Google, SSO (can be enforced), 2FA, roles Owner/Admin/Member/Guest.
Integrations: Slack, Teams, Jira two-way sync (5–15 min), GitHub/GitLab, Google, Zapier,
Salesforce (Enterprise); REST API v2 (600 req/min), API v1 sunsets 31 Jan 2027."""

GUIDELINES = """TRIAGE LABELS
category (one dominant issue):
- billing: charges, invoices, refunds, seats, plan/price changes, failed payments
- account_access: login, password, 2FA, SSO errors, roles/permissions, ownership, inviting/removing users
- bug_report: a feature works incorrectly or crashes (not slowness)
- outage_performance: down, very slow, timeouts, delayed notifications
- how_to: how to do something that already exists
- feature_request: something Pulseboard doesn't do, or a change to how it works
- integrations_api: Slack/Teams/Jira/GitHub/Google/Zapier/Salesforce, API, webhooks, tokens
- security_privacy: suspected compromise, phishing, data exposure, GDPR/deletion, compliance docs
priority:
- low: no impact on work (questions, ideas, invoice copies)
- medium: one person or non-critical workflow blocked; workaround exists
- high: a team blocked, deadline at risk, wrong charge, or many users affected
- urgent: workspace-wide outage, data loss, security incident, or Enterprise fully blocked
sentiment (tone, not severity): positive (friendly) · neutral (matter-of-fact) · frustrated (annoyed,
repeated contact) · angry (hostile, threatens to cancel)
escalate = true if priority is urgent, OR category is security_privacy, OR (enterprise AND high); else false.

TICKET RULES
Labels must be inferable from the text (+ tier/channel); never state them ("Priority: high"). Realistic:
one line to ~250 words, typos, rambling, mixed issues; chat is short and informal. Stay consistent with the
Pulseboard brief. No real people, companies or contact details; use .example domains."""

WRITER_SPEC = f"<pulseboard_brief>\n{BRIEF}\n</pulseboard_brief>\n\n<triage_guidelines>\n{GUIDELINES}\n</triage_guidelines>"
JUDGE_SPEC = f"<triage_guidelines>\n{GUIDELINES}\n</triage_guidelines>"

# Ticket-specific stock phrases, added to the library's general phrasing rule.
TICKET_PHRASING = (
    PHRASING_RULE + " For tickets in particular: do not open with the writer's role, tenure or team size unless the "
    "ticket needs it, and do not label the tone or the history (\"third time writing\", \"I hope this finds you well\")."
)

# Level 1 of these factors is the label itself, at the dataset's target shares (the weights are the shares).
PINNED = {
    "category": [("billing", 1.0), ("account_access", 1.0), ("bug_report", 1.0), ("outage_performance", 1.0),
                 ("how_to", 1.0), ("feature_request", 1.0), ("integrations_api", 1.0), ("security_privacy", 1.0)],
    "priority": [("low", 0.30), ("medium", 0.35), ("high", 0.25), ("urgent", 0.10)],
    "sentiment": [("positive", 0.15), ("neutral", 0.40), ("frustrated", 0.30), ("angry", 0.15)],
    "customer_context": [("free", 0.40), ("pro", 0.40), ("enterprise", 0.20)],
}

# How the sampled ingredients map to the record's fields (shared by the writer, critic and refine prompts).
INGREDIENTS_NOTE = """In the sampled ingredients, the first step after "category:", "priority:" and "sentiment:" is the
intended label, and the first step after "customer_context:" is the customer_tier. Deeper steps and ticket_style
describe the writer, the impact and the ticket's shape."""

TICKET_RULES = """- The labels are category, priority and sentiment from the ingredients; customer_tier is the customer_context tier.
- Choose the channel (email, chat or web_form; chat only for pro/enterprise) and the length/shape from ticket_style.
- Decide who is writing and the concrete Pulseboard problem or request, with specifics that make the labels
  inferable from the text.
- The facts of the ticket decide its priority (what happened, how many people it affects, the plan they are on);
  the wording carries the sentiment. An angry customer can have a minor problem.
- escalate follows the rule in the guidelines.
- If ingredients conflict, keep category, priority and sentiment as given and adapt the situation so they are believable."""


def _child_rule(factor_name: str, level: int) -> str:
    if level == 0 and factor_name in PINNED:
        names = json.dumps([{"name": n, "weight": w} for n, w in PINNED[factor_name]])
        return (f"This is a pinned level. The children MUST be exactly these, with these exact names and "
                f"weights, in this order (write only the descriptions): {names}")
    return "Propose 3-5 children." if level == 0 else "Propose 2-3 children (never more than 3)."


# --- Taxonomy ---

def expand_prompt(description: str, factor: dict[str, Any], node: dict[str, Any], siblings: list[str], plan: str, count: int) -> str:
    return f"""
Dataset description:
{description}

Product context:
<pulseboard_brief>
{BRIEF}
</pulseboard_brief>

Factor:
{json.dumps(factor, ensure_ascii=False)}

Current node:
{json.dumps({k: v for k, v in node.items() if k != "children"}, ensure_ascii=False)}

Sibling node names:
{siblings}

Expansion plan:
{plan}

{_child_rule(factor["name"], int(node.get("level", 0)))}
{TAXONOMY_CONTEXT_RULE}
Children must be useful, non-overlapping, about Pulseboard support tickets, and narrower than the current node.
Give each child a "weight": its expected prevalence in realistic support tickets (dominant ~1.0, common ~0.5, uncommon ~0.15, rare ~0.03).
Return JSON:
{{"children": [{{"name": "...", "description": "...", "weight": 1.0}}]}}
""".strip()


def refine_nodes_prompt(description: str, node: dict[str, Any], raw_children: list[dict[str, Any]]) -> str:
    level = int(node.get("level", 0))
    factor_name = node["name"] if level == 0 else (node.get("path") or [node["name"]])[0]
    return f"""
Dataset description:
{description}

Parent node:
{json.dumps({k: v for k, v in node.items() if k != "children"}, ensure_ascii=False)}

Raw proposed children:
{json.dumps(raw_children, ensure_ascii=False)}

Refine this list for completeness, soundness, specificity, and low duplication.
{TAXONOMY_CONTEXT_RULE}
{_child_rule(factor_name, level)} Merge or drop children to respect this limit.
Keep each child's "weight" (expected prevalence in realistic tickets) unless a merge makes an adjustment necessary.
Return JSON:
{{"children": [{{"name": "...", "description": "...", "weight": 1.0}}]}}
""".strip()


# --- Combined path (meta_prompts: false): several tickets per call, straight from their sampled mixes ---

def combined_batch_prompt(description: str, schema: dict[str, Any] | None, items: list[dict[str, Any]], choices: list[dict[str, Any]]) -> str:
    # Static text first (cacheable prefix), specs last. choices = model-picked factors, if any.
    specs = "\n\n".join(f"{item['id']}:\n{mix_text(item['mix'])}{situation_line(item)}" for item in items)
    picked = ""
    if choices:
        picked = ("\n\nChoose these factors yourself for every ticket (they are not in the specs): pick the option that fits "
                  "that ticket best, spread your picks across tickets roughly in the shares shown, and report them in "
                  '"picked" by exact option name.\n' + choices_text(choices) + "\n")
    pick_field = ', "picked": {' + ", ".join(f'"{choice["factor"]}": "..."' for choice in choices) + "}" if choices else ""
    return f"""
Create examples for a Pulseboard support-ticket triage dataset, one per spec below.

Dataset description:
{description}

{WRITER_SPEC}

Each example is a JSON object matching this JSON Schema:
{json.dumps(schema, indent=2)}

Planning each example from its spec (the sampled taxonomy ingredients):{picked}
{INGREDIENTS_NOTE}
{TICKET_RULES}
- First write a one-sentence "plan": who writes, the concrete problem, the channel and the shape. Then write the
  ticket so it matches the plan.

Writing each ticket:{situation_note(items)}
- Write it exactly as the customer would send it (subject + body). Set the labels to the correct triage labels
  for the text you wrote; they should match the intended labels. Never state labels in the ticket.
- The ingredients are instructions, not content: never copy a node name, path or description into a ticket.
- {CONTEXT_RULE}
- {TICKET_PHRASING}
- {DIVERSITY_RULE}
- Give every ticket the length and depth it would have if it were written on its own.

{specs}

Return JSON:
{{"records": [{{"id": "r1", "plan": "..."{pick_field}, "record": {{"ticket": {{...}}, "labels": {{...}}}}}}]}}
""".strip()


def complexify_record_prompt(description: str, schema: dict[str, Any] | None, meta_prompt: str, record: Any) -> str:
    return f"""
Dataset description:
{description}

{WRITER_SPEC}

What this ticket was built from:
{meta_prompt}
{INGREDIENTS_NOTE}

Ticket:
{json.dumps(record, ensure_ascii=False)}

Rewrite this ticket so it is harder to triage while its correct labels stay exactly the same: for example add a
secondary, less important issue, indirect impact cues instead of explicit urgency, rambling context, partial
misdiagnosis by the customer, or a tone that is easy to misread. It must stay the same ticket: the category,
priority, sentiment, tier, channel and escalate value must not change and must remain inferable from the text.
Return the rewritten record as JSON only, matching this JSON Schema:
{json.dumps(schema, indent=2)}
""".strip()


# --- Critic and refine ---

def critique_prompt(description: str, schema: dict[str, Any], meta_prompt: str, record: dict[str, Any]) -> str:
    return f"""
Validate ONE example for a support-ticket triage dataset (fictional product: Pulseboard, a B2B
project-management SaaS).

{JUDGE_SPEC}

What the example was built from:
{meta_prompt}
{INGREDIENTS_NOTE}

Example:
{json.dumps(record, ensure_ascii=False)}

Return verdict "reject" if ANY check fails:
1. A label is not supported by the ticket text (+ tier/channel): an independent triager reading only the
   ticket would pick a different category, priority or sentiment.
2. The ticket has more than one plausible dominant category and the label is not clearly the dominant one.
3. Label leakage: the ticket states a label outright ("Priority: high", "this is a billing issue",
   "I'm frustrated" used as a tag) or reads like it was written to a label checklist.
4. escalate does not follow the rule (urgent, or security_privacy, or enterprise + high).
5. The labels or customer_tier differ from the intended ones stated above.
6. The ticket is unrealistic: reads like marketing copy or a test case, wrong channel conventions (chat that
   reads like a formal letter, chat from a free-tier customer), or contradicts the guidelines' ticket rules.

Otherwise return "accept". Be strict but fair: tickets may be messy, short or ambiguous as long as the labels
are still the best reading.
If you reject, also return "revised": the example corrected so every check passes, changing only what is needed.
{REVISE_RULE}
Return JSON (omit "revised" when accepting):
{{"verdict": "accept" | "reject", "explanation": "...", "revised": {{"ticket": {{...}}, "labels": {{...}}}}}}
""".strip()


def refine_record_prompt(schema: dict[str, Any], meta_prompt: str, record: dict[str, Any], critique: str) -> str:
    return f"""
{WRITER_SPEC}

Schema:
{json.dumps(schema, indent=2)}

What the record was built from:
{meta_prompt}
{INGREDIENTS_NOTE}

Rejected record:
{json.dumps(record, ensure_ascii=False)}

Critique:
{critique}

Revise the record to fix every problem in the critique. {REVISE_RULE}
Return the full JSON record only.
""".strip()


# --- Meta-prompt path (meta_prompts: true) ---

META_PROMPT_RULES = """Each meta-prompt is an instruction to a ticket writer, at most about 100 words, and MUST specify:
- the intended labels: category, priority and sentiment (taken from the ingredients) and the customer_tier;
- the channel (email, chat or web_form; chat only for pro/enterprise) and the length/shape from ticket_style;
- who is writing and the concrete Pulseboard problem or request, with specifics that make the labels
  inferable from the text (the facts decide the priority; the wording carries the sentiment);
- the escalate value implied by the rule in the guidelines.
If ingredients conflict, keep category, priority and sentiment as given and adapt the situation so they are
believable. Describe the ticket; do not write it. The ingredients are instructions, not content: never copy
their names, paths or descriptions.
""" + CONTEXT_RULE


def meta_prompt_prompt(description: str, schema: dict[str, Any] | None, mix: list[dict[str, Any]], k: int) -> str:
    return f"""
You are planning examples for a support-ticket triage dataset.

Dataset description:
{description}

{WRITER_SPEC}

Sampled taxonomy ingredients for this record:
{mix_text(mix)}
{INGREDIENTS_NOTE}

Write {k} meta-prompt(s).
{META_PROMPT_RULES}
Return JSON:
{{"meta_prompts": ["..."]}}
""".strip()


def meta_prompt_batch_prompt(description: str, schema: dict[str, Any] | None, items: list[dict[str, Any]]) -> str:
    specs = "\n\n".join(f"{item['id']} ({item['count']} meta-prompt{'s' if item['count'] > 1 else ''}):\n{mix_text(item['mix'])}{situation_line(item)}"
                         for item in items)
    return f"""
You are planning examples for a support-ticket triage dataset, one or more per id below.

Dataset description:
{description}

{WRITER_SPEC}

{INGREDIENTS_NOTE}
{META_PROMPT_RULES}
- {DIVERSITY_RULE}{situation_note(items)}

{specs}

Return JSON:
{{"mixes": [{{"id": "m1", "meta_prompts": ["..."]}}]}}
""".strip()


def complexify_prompt(description: str, meta_prompt: str) -> str:
    return f"""
Dataset description:
{description}

{WRITER_SPEC}

Meta-prompt:
{meta_prompt}

Rewrite this meta-prompt so the ticket is harder to triage while its correct labels stay exactly the same:
for example add a secondary, less important issue, indirect impact cues instead of explicit urgency, rambling
context, partial misdiagnosis by the customer, or a tone that is easy to misread. The intended category,
priority, sentiment, tier, channel and escalate value must not change and must remain inferable from the text.
Keep it to about 100 words.
Return JSON:
{{"meta_prompt": "..."}}
""".strip()


def generate_record_batch_prompt(description: str, schema: dict[str, Any] | None, items: list[dict[str, Any]]) -> str:
    specs = "\n\n".join(f"{item['id']}:\n{item['meta_prompt']}" for item in items)
    return f"""
Create examples for a Pulseboard support-ticket triage dataset, one per meta-prompt below.

Dataset description:
{description}

{WRITER_SPEC}

Each example is a JSON object matching this JSON Schema:
{json.dumps(schema, indent=2)}

Write each ticket exactly as the customer would send it (subject + body), set channel and customer_tier, and set
the labels to the correct triage labels for the text you wrote (they should match the meta-prompt's intended
labels). escalate must follow the rule in the guidelines. Never state labels in the ticket. Keep each ticket true
to its own meta-prompt, and give each the length it would have if written alone.
- {CONTEXT_RULE}
- {TICKET_PHRASING}
- {DIVERSITY_RULE}

{specs}

Return JSON:
{{"records": [{{"id": "r1", "record": {{"ticket": {{...}}, "labels": {{...}}}}}}]}}
""".strip()
