# Custom prompts

The config says what shape to produce; a prompt module says how the model is asked. It is how you
enforce rules the config cannot express: atomic fields, how absent values are written, what the critic
must reject. Simple datasets do not need one.

## Pointing the config at a module

```yaml
prompts:
  module: "my_prompts.py"   # resolved relative to this YAML file
```

The module may override any subset of the prompt functions in `simula/prompts.py`, plus the
`SYSTEM_JSON` and `SYSTEM_TEXT` system prompts; everything else falls back to the built-ins.

```python
# my_prompts.py
SYSTEM_JSON = "Return valid JSON only."


def strategy_prompt(description, taxonomy, valid_paths, guidance=None, count=None):
    return f"""
Dataset description:
{description}

Taxonomy:
{taxonomy}

Use only these taxonomy paths, verbatim:
{valid_paths}

Create only combinations that make semantic sense for this dataset.
Return JSON with a strategies array.
""".strip()
```

Each override must keep its built-in's parameter names exactly. Read them in `simula/prompts.py` rather
than guessing: `simula validate` imports the module and rejects a missing file, an import error, a
non-string system prompt or a mismatched signature before any model call.

## Which functions matter

| Function | What it controls |
|---|---|
| `combined_batch_prompt` | The main lever on the combined path: receives several records' mixes and says how to plan and write each one. |
| `complexify_record_prompt` | What "harder" means for a written record (combined path). |
| `meta_prompt_prompt`, `meta_prompt_batch_prompt` | The main lever on the meta-prompt path. A meta-prompt should describe the record, not contain it: a finished record inside a meta-prompt gets copied, invented details included. |
| `generate_record_prompt`, `generate_record_batch_prompt`, `generate_text_prompt` | Writing records from meta-prompts. |
| `situations_prompt` | How situations for a subject node are listed. Override it only to say what a situation is for an unusual dataset; keep it about subject matter alone. |
| `critique_prompt`, `critique_text_prompt` | What the critic rejects. On rejection it may return the corrected record as `"revised"`, which is accepted directly when it is valid. |
| `refine_record_prompt`, `refine_text_prompt` | How a rejected record is fixed. |
| `expand_prompt`, `refine_nodes_prompt`, `strategy_prompt` | How the taxonomy and strategies are drafted. |

## Keep the shared rules

The built-in prompts share a few rules, defined as constants at the top of `simula/prompts.py`. An
override replaces the whole prompt, so import the rules that apply and include them. Each one is there
because a run went wrong without it:

| Rule | Goes in | What happens without it |
|---|---|---|
| `TAXONOMY_CONTEXT_RULE` | `expand_prompt`, `refine_nodes_prompt` | Node descriptions copy the description's named details as "such as" examples, and those land in most records. |
| `CONTEXT_RULE` | every writer prompt | The description's few concrete details show up in a large share of records. |
| `PHRASING_RULE` | every writer prompt | Records announce their ingredients in the same stock phrases ("I'm the admin", "third time writing"). |
| `DIVERSITY_RULE` | writer prompts that produce several records per call | Records of one call converge on one template. |
| `REVISE_RULE` | `critique_prompt` (its `"revised"` record), `refine_record_prompt`, `refine_text_prompt` | The critic fixes a mismatch by changing the label instead of the text, and label shares drift. |

```python
from simula.prompts import CONTEXT_RULE, DIVERSITY_RULE, PHRASING_RULE
```

`PHRASING_RULE` and the others are worded for any dataset; add your own dataset's stock phrases next to
them.

### With a subject

When `generation.subject` is set, an override of `combined_batch_prompt` or `meta_prompt_batch_prompt`
must render each item's situation: add `situation_line(item)` to the item's spec and
`situation_note(items)` to the instructions (both in `simula/prompts.py`). An override that leaves them
out silently drops every situation.

On the meta-prompt path a subject also means `meta_prompt_batch_prompt` is called even for one mix per
call, so a module that overrides only `meta_prompt_prompt` must add the batch version too.
`examples/stories_freetext_v2_prompts.py` shows both sharing one rule block.

## Three rules learned from bad runs

- **Never ask the model to vary something code already sampled.** "Vary the product across the
  candidates" produced a dataset where about 60% of rows were not about the product in their lineage.
- **Say what wins when ingredients clash.** Factors are drawn independently. Give a precedence (subject
  first) and permission to soften or drop a texture ingredient that does not fit. Never give that
  permission for a label or anything else stored in the record: those must hold exactly, so say which
  ingredient decides which field (for a ticket, the facts decide priority and the wording carries mood).
- **Tell the writer the nodes are instructions, not content,** or taxonomy names leak into records.

## Where to start

- `examples/ecommerce_search_extraction_prompts.py`: every prompt for the combined path.
- `examples/job_extraction_prompts.py`: atomic fields, omitting absent fields, a strict critic.
- `examples/support_tickets_prompts.py`: both paths, rendering situations, a label-focused critic.
- `examples/stories_freetext_v2_prompts.py`: free text, one rule block shared by the single and batch
  meta-prompt functions.
