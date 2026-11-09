# Optimizations plan

Branch: `optimizations` (cut from `main` at `c27f83b9`). Goal: bring Simula's per-row cost toward
Bespoke Curator levels without losing what the taxonomy, lineage and quality gates buy us.

Status (2026-11-08, evening): **phases 0–3 implemented and piloted** (uncommitted on this branch);
results in §0. Sections 1–4 below are the original design, kept as written; §0 records where the
implementation deviated and why. Follow `AGENTS.md` (compact code, `pytest -q`, fake-model tests,
update `CONFIG.md`/`template.yaml`/README when config changes, no real model calls without asking).

---

## 0. Implementation status and pilot results (2026-11-08)

### What landed

| Item | State | Notes |
|---|---|---|
| 5 Vendi score | done | `vendi` next to global/local diversity (`simula/diversity.py`). |
| 8 Reasoning / cached tokens | done | Plus provider-billed `cost` (OpenRouter returns `usage.cost`) on every `llm_calls.jsonl` row and in `cost_summary.json`. |
| 1 Taxonomy critic | **changed** | Default `best_of_n` 1, but the critic refine **always runs** (2 calls/node instead of 3). Skipping it was measured to wreck the tree: with one proposal and no critic, `product_vertical` kept 3 of its 21 described verticals and repeated node names rose from 4% to 21%. The critic is a completeness/dedupe pass, not just a merge. One proposal + critic recovered all 21 verticals and v0's fan-out (4.1 vs 3.8 children/node). |
| 2 Parallel proposals, bounded fan-out | done | Plus one retry per failed node expansion: v4-pro returned bad JSON on ~6% of expansions, silently pruning branches (once a whole factor). |
| 9 Per-task reasoning effort | **dropped** | User decision: reasoning stays on at the run's normal effort. Also measured: DeepSeek treats `minimal` as `low` and ignores `reasoning.max_tokens`. |
| 10 Critic fixes | done (3 of 4) | Off-by-one fixed (every refine is re-critiqued); `generation.critic: false`; a rejecting critic may return `"revised"` and a schema-valid fix is accepted directly (built-in JSON critic + e-commerce critic do this). **Mechanical-check hook not built:** 70% of baseline critic rejections are semantic hallucinations, ~15% mechanical. |
| 11 Small fixes | done | `/` replaced in node names; dotted names inside slash paths round-trip; blank and non-string scenarios dropped (the 10K run had 4 dict-valued `meta_prompt`s). |
| 3 Keep every scenario | done | `scenarios_per_mix` default 1; K>1 = K attempts share one mix. Built-in and e-commerce meta prompts now describe the record instead of writing it. |
| 4 Mix-space report | done | Printed before generation; warns when the space is smaller than the mixes drawn. |
| 6 Batching | done (A, B, C) | Two knobs instead of a mode list: `meta_batch_size` (A: mixes per scenario call) and `record_batch_size` (records per record call: B with meta prompts on, C = the combined path with `meta_prompts: false`); A+B = both. Malformed batch JSON is salvaged item by item. Concurrency bounds whole units (`concurrency // record_batch_size`) so critiques never queue behind other batches. Results in round 3 below. |
| 7 Code- vs model-picked factors | built (minimal), not recommended | `generation.model_picked: {factor: level}` on the combined path: those factors are offered as options (with the share code sampling would give each) and the model's pick becomes lineage (`"picked_by": "model"`). No automatic per-factor decision step. Round 3: the model skews the distribution and mislabels its own picks; most of the readability gain comes from coarser specs, which code-picking at the same level gets too. |

E-commerce prompt module fixes: no more "vary the vertical across the K"; complexify keeps the size class
(was "12–20 fields"); explicit conflict precedence for the four independently sampled nodes
(product_vertical > query_complexity > shopper_intent > attribute_focus); critic rules first (cacheable
prefix) and returns fixes; batch prompt; "never copy taxonomy names into the record".

### Pilot design

500 attempts per arm, seed 31, the v0 taxonomy + strategies, so **every arm draws the same 500 mixes**
(lineage is seeded per attempt on both paths). Bulk + critic = `deepseek-v4-flash`, reasoning effort low.
Baseline arm `B0` = `main` run from a worktree. Cost from `llm_calls.jsonl` (list price from tokens;
billed = provider-reported). Quality: a blind judge (`claude-haiku-5.5`, reasoning medium) on a fixed
150-attempt sample, scoring the final record and the pre-critic record (rebuilt from the log) for
lineage fit, unsupported/missing/non-atomic fields, realism and coherence. Diversity: Vendi + local
(all-MiniLM, k=10) on 400 queries, and on 90 length-matched (8–25-word) queries.

### Results (deepseek-v4-flash, 500 attempts each)

| Arm | $/1K rows list | billed | calls/row | product fit | complexity fit | faithful | unsupported | size in class | Vendi (n=400) | Vendi len-matched |
|---|---|---|---|---|---|---|---|---|---|---|
| B0 today's `main` | 3.18 | ≈6.7* | 4.30 | 38% | 21% | 60% | 14% | 39% | 53.6 | 35.4 |
| M1 meta path, fixed | 2.08 | 4.42 | 3.34 | 94% | 64% | 64% | 10% | 83% | 79.0 | 44.8 |
| C1 combined, 1/call | 1.43 | 2.95 | 2.00 | 95% | 80% | 71% | 8% | 91% | 78.4 | 40.7 |
| C5 combined, 5/call | 0.91 | 1.98 | 1.20 | 95% | 73% | 77% | 7% | 82% | 78.8 | 41.8 |
| C10 combined, 10/call | 0.76 | 1.64 | 1.10 | 91% | 69% | 75% | 4% | 79% | 79.1 | 42.5 |
| **R5** = C5 on default routing | 0.55 | **0.40** | 1.22 | 90% | 74% | 70% | 7% | 81% | 79.0 | 42.3 |
| C10, critic off | 0.30 | — | 0.10 | 91% | 75% | 63% | 14% | 76% | — | — |

\* `main` does not log billed cost; ≈ list × 2.1, the `:nitro` ratio measured on every other arm.
"Faithful" = no unsupported field and no missing constraint. Acceptance was 93% (B0) and 99% elsewhere.
Strong model (`deepseek-v4-pro`, 200 attempts, paired 100-row judge sample): today's `main` 79% product
fit / 47% complexity / 59% faithful at $5.94/1K list; combined-5 93% / 73% / 78% at $1.94/1K list.

### Findings

- **Today's lineage is mostly wrong, and that also kills diversity.** B0 drifts off its sampled product
  in ~60% of rows (pacifiers → a tent, QLED TVs → green tea, office chairs → headphones) toward a few
  favourite products ("Sony" in 4% of rows), and its queries share one long template (42% open with the
  top-3 phrases in the 10K run). Fixing the prompts fixes both.
- **Hidden reasoning does not shrink with batching.** A 10-record batch reasons about as much per record
  as ten single calls (~1–2K tokens each at effort low). Batching saves calls, repeated prompt input and
  (on the combined path) the scenario step — not reasoning.
- **Batching drifts on shape, not product.** Product fit holds (95% → 91%), complexity fit slides
  (80% → 73% → 69% at 1/5/10 per call), and a "large multi-need" spec can come back as a 5-word query.
  Batch size 5 is the sweet spot.
- **The critic earns its cost.** It halves unsupported fields in every arm (e.g. 17% → 7%) and lifts
  faithfulness by ~10–15 points; its fixes are mostly surgical (drop an invented `category`, a guessed
  delivery date; add an omitted constraint). Critic-off is the cheapest arm ($0.30/1K) but 1 in 3 rows
  is unfaithful.
- **Routing is the largest single lever.** `:nitro` bills ~2.1× list and its provider reasons ~2× more
  per call. Default routing billed **below** list (45% of input hit the prompt cache, thanks to the
  static-prefix prompt order) — 5× cheaper for the same configuration, but slower and with a slightly
  more lenient critic (70% vs 62% first-pass accepts; faithfulness 70% vs 77%, within judge noise).
  The strong model's default route bills ~2.3× list.
- **The judge is a relative measure.** Haiku's first prompt mis-scored obvious product matches; v2 (explicit
  normalization rules, medium reasoning) fixed the spot-checked errors, but absolute rates stay noisy.

### Round 3: batch modes A/B and model-picked factors (default routing, 500 attempts each)

All on plain `deepseek-v4-flash` (default routing), same 500 mixes; compare within this table only
(default routing reasons less per call than `:nitro`, so it is not comparable to the round-1 table).

| Arm | $/1K billed | calls/row | product fit | complexity fit | faithful | realism | coherence | Vendi | Vendi len-matched |
|---|---|---|---|---|---|---|---|---|---|
| Scenario path, unbatched | 0.84 | 3.33 | 92% | 64% | 52% | 4.05 | 4.24 | 80.4 | 44.0 |
| A: 5 mixes per scenario call | 0.74 | 2.56 | 95% | 58% | 55% | 3.97 | 4.21 | 80.9 | 44.3 |
| B: 5 records per record call | 0.70 | 2.54 | 95% | 64% | 49% | 3.89 | 4.11 | 80.0 | 41.4 |
| A+B | 0.55 | 1.72 | 96% | 64% | 58% | 3.91 | 4.25 | 83.6 | 45.3 |
| **C: combined, 5 per call (R5)** | **0.40** | 1.22 | 90% | 74% | **71%** | 3.90 | 4.22 | 79.0 | 42.3 |
| C, intent/attribute cut to level 2 (code-picked) | 0.44 | 1.22 | 92% | 79% | 66% | 4.11 | 4.31 | 81.3 | 43.5 |
| C, intent/attribute **model-picked** at level 2 | 0.40 | 1.21 | 91% | 80% | 64% | 4.25 | 4.53 | 84.4 | 44.5 |

- **A and B work and hold quality.** A saves 12%, B 17%, A+B 35% against the unbatched scenario path,
  with product fit unchanged or better. B's first drafts drop more stated constraints (32% faithful
  before the critic vs 43%); the critic repairs most of it. Scenario-path arms miss more constraints
  than C overall (their queries are longer). C stays the cheapest and most faithful: half A+B's cost.
- **Model-picked skews and mislabels.** Picks concentrate on typical options (`category_browsing` 26%,
  `exact_product_search` 22% against 8–11% targets; evenness 0.85 vs 0.99 for code), rare options vanish
  (`bulk_quantity_purchaser` 0%), 2–3% of picks match no offered option, and the judge finds the record
  matches the model's own intent label only 21% of the time (43% for code-picked). Unsupported fields
  rise to 15% (7% code-picked). Records do read better (realism 4.25 vs 3.90).
- **Most of that readability is specificity, not who picks.** Code-picking the same two factors at the
  same level 2 recovers all of the complexity-fit gain and about half the realism/coherence gain, with
  exact distributions and reliable labels. Leaf-level intent/attribute nodes ("rating_max",
  "color_code") force odd records; that is a taxonomy-shape issue, fixable by a shallower tree for those
  factors.
- **Recommendation:** combined path, batch 5, code-picked (as in the example config). Use A+B when
  reviewable scenarios matter. Keep `model_picked` for factors that do not need an exact label or
  distribution (tone, style); do not use it for labelled factors.
- **Hangs:** default routing occasionally hangs a request. The OpenAI SDK's own retries stacked under
  ours (one stuck request could hold an attempt for over an hour); the client now runs with
  `max_retries=0`. Our loop still allows 8 tries x `timeout_seconds`, so watch long runs for stalls and
  resume.

### What a 10K e-commerce dataset costs now

| | rows | taxonomy (v4-pro, once) | total |
|---|---|---|---|
| Today's `main` (13K attempts, `:nitro`) | ≈$38 list / ≈$80 billed | $2.72 list | **≈$41 list / ≈$85 billed** |
| Recommended (combined, batch 5, critic on, default routing, 10.5K attempts) | ≈$5.8 list / **≈$4.2 billed** | ≈$1.85 list / ≈$4.3 billed | **≈$7.6 list / ≈$8.5 billed** |

Per-record cost is now $0.40 per 1K rows billed (Curator's benchmark run: $0.25/1K on Haiku, different
task and model) while keeping code-picked lineage and the critic. The taxonomy is now the biggest share
of a 10K run; cheaper routing for the strategic role is the next untested lever.

### Open

- Item 7 beyond the minimal version: an automatic per-factor code/model decision and a validate warning.
- Backlog §4: provider batch pricing, retryable infra failures on resume, response cache, sibling-only
  expansion prompts, sending the strategy call the path list instead of the whole taxonomy.
- Critic leniency differs by provider; re-check judge numbers on a larger sample before a big run.

---

## 1. Context

### Where the numbers come from

- **Our baseline** — measured from our own 10K e-commerce run, `runs/v0_ecommerce_search_extraction`
  (gitignored, local only): `llm_calls.jsonl` aggregated per task and priced at Nov-2026 OpenRouter list
  prices (`deepseek-v4-pro` $0.546 in / $1.091 out per M tokens; `deepseek-v4-flash` $0.091 / $0.182;
  `:nitro` priced as flash). `cost_summary.json` is useless here — it only reflects the last command.
- **The benchmark** — external reports (not in this repo) from a separate `synth-bench` project. They ran
  Simula at commit `c27f83b` (same as our main), Curator, and four other libraries on one task
  (support-ticket triage, 10K rows each, all on `claude-haiku-5-5`). One run per library, no confidence
  intervals: treat small differences as noise, big ones as real.

### Glossary

- **Factor** — one dimension of variation (e.g. `product_vertical`). Each factor is its own tree in
  `taxonomy.json`.
- **Node / leaf / expanded node** — a node is any tree entry; a leaf has no children and is what gets
  sampled; an expanded node is a parent whose children were generated by model calls.
- **Mix** — one sampled leaf per factor for one record (e.g. Cowboy Boots × low-spec bulk × capacity
  quantity × small). Sampled in code, no model call.
- **Strategy (lane)** — limits which branches each factor may draw from, picked by weight per attempt.
- **Scenario = meta-prompt** — the paragraph a model writes from a mix, telling the generator exactly
  what record to make. Same thing; the benchmark says "scenario", the code says `meta_prompt`.
- **Complexify** — a separate call that rewrites the *scenario* (not the record) to be harder.
- **Critic / refine / repair** — critic judges a record (accept/reject); refine rewrites a rejected
  record; repair fixes broken JSON or a schema failure right after generate (independent of the critic).
- **Code-picked / model-picked factor** — who chooses a factor's value: code (sampled into the mix) or
  the model (offered with weights inside a batch prompt). See item 7.
- **Vendi score** — "effective number of distinct records" (1 = all identical, n = all unrelated):
  exp(entropy of the eigenvalues of the cosine-similarity matrix / n). See item 5.

### Benchmark headline (Simula vs Curator, 10K support tickets, Haiku)

| | Simula | Curator |
|---|---|---|
| Cost / API time | $65.59 / 81 min | $2.47 / 5 min |
| Calls per row | 4.83 | 0.11 |
| Vendi (distinct tickets) | 42.8 | 78.9 |
| Local diversity (10-NN) | 0.262 | 0.320 |
| Category correctness (blind re-label) | 91.6% | 86.1% |
| Rows carrying their intended labels | 99% | n/a (model picks labels) |
| Body length p90 | 254 words | 87 words |
| Repeated persona ("Dana") | 40% of tickets | 2% |

Why Curator wins on cost and diversity: (1) a topic tree whose 963 leaves are concrete *situations*
("company migrated Google Workspace domains, old login no longer works"), built for $0.06 in 104 calls;
(2) one call writes 11 tickets for one subtopic, so the model contrasts them and the prompt is amortized;
(3) the other dimensions (priority, sentiment, tier, persona) are listed with a target mix in that prompt
and the model assigns them per ticket; (4) no critic. Simula wins on label control, borderline/hard
cases, length spread and lineage — the critic and code-picked labels.

Their side experiment on Simula (their own loop around our private functions, 500 rows per arm, one run
each): finer leaves + several records per generate call → Vendi 39 → 52 (Curator 68.5 on the same
subset), local diversity 0.376 → 0.454, "Dana" 43% → 7%, cost −28%, critic acceptance and category
agreement held; batched tickets got shorter (median 147 → 123 words at 6 per call). Direction clearly
real; exact size not established.

---

## 2. Baseline anatomy (our 10K e-commerce run)

13,000 attempts → 10,985 accepted → 10,000 final. 50,476 calls, 48.2M input / 75.8M output tokens.
**Total ≈ $23.31 ≈ $2.33 per 1K final rows.**

| Stage | Call | Triggered by | Count | Cost | Share |
|---|---|---|---|---|---|
| Taxonomy (once) | `node_expansion` | 2× per expanded node (`best_of_n: 2`) | 1,244 | $3.93 | 16.9% |
| | `taxonomy_critic` | 1× per expanded node | 621 | $2.03 | 8.7% |
| | `level_plan` | 1 per factor per level | 12 | $0.05 | 0.2% |
| | `strategy` | once | 1 | $0.14 | 0.6% |
| Per attempt | `meta_prompt` | every attempt (writes 3, keeps 1) | 11,996 | $5.77 | 24.8% |
| | `complexify` | 30% coin flip | 3,576 | $0.98 | 4.2% |
| | `generate` | every attempt | 11,919 | $4.99 | 21.4% |
| | `repair` | broken JSON / schema failure | 3 | ~$0 | 0% |
| | `semantic_critic` | every valid record | 16,068 | $3.94 | 16.9% |
| | `refine` | critic rejects | 5,036 | $1.47 | 6.3% |

Taxonomy ≈ 26% of dollars (strategic role on `v4-pro`, ~6× flash) but is a fixed cost; per-record
calls ≈ 74%.

**Hidden reasoning** (`reasoning: {effort: low, exclude: true}` on every role):

| Call | Billed output / call | Visible | Hidden |
|---|---|---|---|
| `semantic_critic` | ~1,069 | ~79 | 93% |
| `generate` | ~1,549 | ~180 | 88% |
| `refine` | ~1,275 | ~196 | 85% |
| `complexify` | ~1,198 | ~327 | 73% |
| `meta_prompt` | ~2,131 | ~646 | 70% |

**Per-attempt patterns:** critic accepts first draft 41% (3 calls); complexified + accepted 18% (4);
reject → refine → accept 25% (5–6); reject twice 7% (6–7); failed before the critic 9%.

**Taxonomy shape:** 4 factors, depth 4, 2,358 nodes = 618 expanded + 1,740 leaves (leaves cost no calls).
`product_vertical` alone: 1 → 21 → 84 → 350 → 1,269 (456 expansions, 1,368 calls). Expansion prompts list
*every* other node at the same level (349 names at level 3), so input grows 1,027 → 1,260 → 3,022 tokens
per call by level. The `strategy` prompt pasted the whole 1 MB taxonomy (260K input tokens) — and all 5
strategies came back as bare roots, i.e. identical sampling: the call changed nothing (this run predates
the August strategy fixes).

**Problems found (beyond cost):**

- **Lineage is wrong for ~60% of rows.** The ecommerce prompt module tells `meta_prompt` to "vary the
  vertical across the 3"; one scenario is kept at random. Only ~40% of kept scenarios mention their
  sampled product (word-match heuristic). The complexify override hard-codes "12–20 fields": 72% of
  rows sampled as "small (3–5 fields)" have >8 fields.
- **The scenario writes the record.** Scenarios include a full example query + extraction; `generate`
  copies it, including invented details (traced: complexify invented a "Tony Lama" brand exclusion,
  generate copied it, critic + refine spent 2 more calls removing it).
- **Off-by-one in `_critic_loop`** (`simula/generate.py`): after the last rejection it refines once more
  and exits without critiquing → 870 wasted refines here; the rejected row stores an un-critiqued record.
- **Sticky failures:** 1,073 attempts died on OpenRouter "Key limit exceeded" 403s after their
  meta-prompt was paid; failed attempts are never retried on resume.
- **Overgeneration waste:** 985 accepted rows (all calls paid) were trimmed away to hit 10,000.

---

## 3. Work items

Suggested order: **phase 0** (measure) → **phase 1** (cheap wins) → **phase 2** (meta-prompts) →
**phase 3** (batching) → **phase 4** (code- vs model-picked). Items are numbered for reference, not order.

### Phase 0 — measure first

#### 5. Vendi score in evaluation diversity

- **Why:** the benchmark's own metric; we need it to judge batching / finer leaves against today.
- **Change:** in `embedding_diversity` (`simula/diversity.py`) add `vendi` next to `global_diversity` and
  `local_diversity`, reusing the embeddings already computed. Compute from the d×d matrix XᵀX of the
  normalised embeddings (same nonzero eigenvalues as the n×n similarity matrix, cheap); ~5 lines numpy.
- **Caveats:** depends on sample size and embedding model — compare only runs with the same `sample_cap`
  and `embedding_model`. The benchmark found mean-pairwise `global_diversity` unstable across embedding
  models (Spearman 0.03) while Vendi and local diversity held (0.83–1.00): decide on those two.
- **Tests/docs:** `tests/test_diversity.py` (identical texts → 1, orthogonal → n); diversity docs.

#### 8. Log reasoning and cached tokens per call

- **Why:** we can't see whether prompt caching happens or how much each change cuts reasoning.
- **Change:** in `ModelRouter._account` (`simula/models.py`) record
  `usage.completion_tokens_details.reasoning_tokens` and `usage.prompt_tokens_details.cached_tokens`
  when the provider returns them (on the `llm_calls.jsonl` row and in the cost roll-up).
- Dollar accounting stays offline (price table over `llm_calls.jsonl`, as for the baseline) unless we
  decide otherwise.

#### Pilot protocol (for every change below that affects quality)

500 rows per arm, same seed / taxonomy / strategies; arms = today vs the change. Report: $ per accepted
row, calls per row, Vendi + local diversity (same `sample_cap`, length-matched), critic acceptance,
lineage fidelity (record matches its sampled mix), length and field-count distribution. Run on one strong
and one cheap model. Real calls cost money — get the user's go-ahead first.

### Phase 1 — cheap wins

#### 1. Taxonomy: skip the critic when `best_of_n` is 1, and default to 1

- **Why:** `_expand_one_node` (`simula/taxonomy.py`) always makes `best_of_n` proposal calls **plus** a
  `taxonomy_critic` merge call, so `best_of_n: 1` still costs 2 calls per expanded node. With one proposal
  there is nothing to merge. In the baseline the critic often added nothing (two identical proposals) or
  just picked one list.
- **Change:** if `best_of_n == 1`, use the proposal's children directly (1 call per expansion).
  `best_of_n >= 2` keeps proposals + critic merge. Default `best_of_n` 2 → 1 (`data_models.py`).
  No new knob.
- **Effect:** baseline taxonomy $6.16 → ~$2.14 ≈ **−17% of the whole run** (shrinks as target_size grows).
- **Note:** `children_per_node` is only a request; today the critic sometimes returns more (asked 3, got 5).
- **Tests/docs:** taxonomy tests with a fake router counting calls; `CONFIG.md`, `template.yaml`,
  AGENTS.md taxonomy section.

#### 2. Taxonomy: run the `best_of_n` proposals in parallel, and bound fan-out

- **Why:** proposals run one after another; speed only, no cost change (matters when `best_of_n >= 2`).
- **Change:** run them concurrently (TaskGroup/gather), then the critic. Nodes within a level are
  already expanded concurrently with **no bound**, so also cap in-flight taxonomy calls (reuse
  `generation.concurrency` or a semaphore).

#### 9. Per-task reasoning effort (config only — test first)

- **Why:** the largest untouched lever: 70–93% of billed output is hidden reasoning, every task runs at
  the same effort. The critic spends ~1,000 tokens to write ~80.
- **Already supported:** `sampling.tasks.<task>` keys outside the OpenAI params merge into `extra_body`
  and replace the role's value (`resolve_sampling`, `simula/models.py`), e.g.
  `sampling.tasks.semantic_critic: {reasoning: {effort: low, exclude: true}}` (replaces the whole
  `reasoning` dict — repeat `exclude`). Try the lowest effort, reasoning off, or a reasoning token cap.
- **Effect (rough, untested):** ~15–25% of today's run; under batching, most of what remains is the
  critic, so this matters even more there. Needs the pilot to check critic quality.

#### 10. Critic fixes: off-by-one, critic-off switch, critic + fix in one call

- **Off-by-one** (bug): `_critic_loop` must critique after the final refine or not refine at all
  (870 wasted refines in the baseline). The fake critic always accepts, so add a rejecting fake.
- **Critic-off switch:** today `max_refine_attempts: 0` still critiques once; the "critic off" estimates
  below assume a real switch (e.g. `generation.critic: false`). Critic off removes `semantic_critic` and
  `refine`; `repair` stays (it follows schema validation, not the critic).
- **Critic returns the fix:** on reject, the critic returns a corrected record, skipping the refine call
  and often the second critique (~$1.5–2.5 on the baseline). Combine with the next item.
- **Mechanical checks in code first:** much of a typical critic checklist is code-checkable (exact keys,
  min field count, no compound keys, value types, "every extracted string appears in the query" — which
  would have caught "Tony Lama"). Where a schema field maps one-to-one to a code-picked factor, fill it
  from the mix in code (the benchmark's largest rejection class, 878 of 1,883, was label mismatch). Shape
  of the hook (prompt module? config?) is open.

#### 11. Pending small library fixes (carried over)

- Taxonomy node names containing `/` or `.` break strategy path round-trip (`taxonomy_path_strings` joins
  on `/`, `_root_key` re-splits on `/` and `.`). Sanitize in `_child` (`simula/taxonomy.py`).
- `_make_meta_prompt` (`simula/generate.py`) keeps blank-string and non-string options
  (`rng.choice(options)`); filter to non-empty strings. Folds into item 3.

### Phase 2 — meta-prompts

#### 3. Never throw scenarios away

- **Why:** one `meta_prompt` call writes `scenarios_per_mix` (default 3) scenarios for one mix in one
  response and keeps one at random — paying for and discarding two. The Simula paper (arXiv 2603.29791)
  does this to avoid mode collapse when *one mix is reused for several records* ("generate multiple
  simultaneously, then sub-sample the required fraction"); we sample a fresh mix per attempt, so that
  reason doesn't apply. The discarded drafts also invite drift away from the mix (see lineage problem).
- **Change:** keep every scenario. `scenarios_per_mix: K` = one call writes K scenarios for one mix and
  all K become attempts sharing that mix. Default `1`. Prompt asks for genuinely distinct scenarios.
- **Work through:** mixes sampled = ceil(attempts / K); one meta row per attempt with shared lineage;
  per-mix RNG; resume/fingerprint; trim/coverage with shared mixes. Applies to today's path and batch
  mode A only.
- **Also:** scenarios should describe *what to make*, not write the record; cap their length (they are
  re-sent to generate/critic/refine).

#### 4. Report the size of the mix space

- **Why:** sampling is independent per attempt (weighted, with replacement); nothing tracks repeats or
  says how many mixes are possible.
- **Change:** compute reachable leaves per factor per strategy, multiply, subtract `never_combine`
  exclusions; print at the strategies checkpoint. Baseline: 1,269 × 163 × 187 × 121 ≈ 4.7 billion
  mixes for 13,000 attempts (benchmark: 9,996 of ~10K mixes unique).

### Phase 3 — batching

#### 6. Batching modes (several records per call)

Three batch targets, selected as a list so A and B can coexist:

| Mode | What one call does | Config (sketch) |
|---|---|---|
| A: batch the meta step | M mixes → M scenarios | `generation.batch: [meta]` |
| B: batch the generate step | M scenarios → M records (benchmark experiment) | `generation.batch: [generate]` |
| A + B | both, as two steps | `generation.batch: [meta, generate]` |
| C: combined (most promising) | M mixes → M records directly; meta-prompt phase skipped | `generation.batch: [combined]` |

- Empty list = today. `combined` cannot be combined with `meta`/`generate` (`validate` error). Today's
  path stays for users who want to review/edit `meta_prompts.jsonl`.
- **Batch size** is a dial (records per call); conservative default, `1` = today's behaviour. Bounded by
  record length: batched records come out shorter (the model sizes the whole answer like one reply), so
  long records (stories) want small batches, short ones (queries) larger ones. Weaker models may drift,
  mislabel or break the JSON as batches grow — the pilot finds each model's comfortable size.
- **Prompt shape (compact):** instructions + description once; a glossary where each distinct node is
  described once; a table with one row of node IDs per record; under C a one-line `plan` per record;
  an instruction to invent distinct names, people and situations across the batch (labels already differ
  — the repetition is in invented details); length as a per-record category (terse/standard/long), since
  models follow word counts poorly; complexity as a per-record flag (replaces the complexify call).
  Example:

  ```text
  <instructions + dataset description, once>
  NODES (each described once):
  P1  product_vertical = Transmission Fluid Additives: additives that improve shifting, condition seals…
  S1  shopper_intent = brand_attribute: a brand plus several attributes, no price or rating…
  …
  RECORDS TO WRITE:
  id | product_vertical | shopper_intent | attribute_focus | query_complexity
  r1 | P1 | S1 | A1 | Q1
  r2 | P2 | S2 | A2 | Q2
  …
  Return JSON: {"records": [{"id": "r1", "plan": "...", "query": "...", "extraction": {...}}, ...]}
  ```

- **Measured** on 5 real mixes: description + mix input 3,613 → 1,665 tokens (46%); with the ~1,000-token
  instruction block, per-record input ≈ 30% of today at 5/call, ≈ 23% at 10/call. Grouping mixes that
  share a subject branch shrinks the glossary and forces contrast between near-neighbours (Curator's
  effect); random grouping is the alternative — test both.
- **Validate each record separately**; one bad item must not sink the batch. Resume unit becomes a batch
  (handle partial batches). Each record keeps its own mix as lineage; under C `plan` takes the role of
  `meta_prompt` (row-shape contract: check `README.md` §Artifacts and tests).

Estimated savings on the baseline (batches of 10; biggest unknown is reasoning per record inside a
batched call, bracketed at 250–500 tokens):

| Setup | Run cost | Saving |
|---|---|---|
| Today | $23.31 | — |
| C only | ~$13.2–13.7 | ~41–43% |
| C + taxonomy change (item 1) | ~$9.1–9.7 | ~59–61% |
| C + taxonomy change + critic off | ~$3.7–4.2 | ~82–84% |

(Complexify is already gone in the C rows. Today with complexify off: $22.33, ~−4%.)

### Phase 4 — who picks each factor

#### 7. Code-picked vs model-picked factors (open — needs its own design)

Per factor: **code** chooses the value (sampled into the mix, as today) or **the model** chooses it
(listed with weights in the batch prompt — "sentiment: neutral 40%, frustrated 30%… spread across the
batch" — and returns its choice per record for lineage and the critic). The model combines every factor
into the record either way; only who chooses differs. Curator ≈ one code-picked factor + the rest
model-picked; current Simula = all code-picked. A per-factor setting, not a mode; only meaningful with
batching (B or C).

| Model-picked fits | Code-picked fits |
|---|---|
| Small closed sets (≈ ≤6 options) | Deep/large trees that can't be listed in a prompt |
| Coherence-sensitive factors (the model avoids silly combinations) | Rare values (weight < ~1/batch size is lost in per-call quotas) |
| Batch size ≥ number of options (at batch size 1 the model collapses to the most typical option) | Labels whose distribution must be exact |
| | Open-ended content (situations, personas) — the model converges when left to invent |

- Needs its own prompt and thinking; most likely a strategic-step call (alongside strategies) that
  decides per factor, writes the decision to a reviewable artifact (seen at `stop_after: strategies`),
  with `validate` warning when the rules above are violated.
- Open: the batch-prompt section for model-picked factors; how model-picked values enter `taxonomy_mix`
  (row-shape contract change); how the critic checks them.

---

## 4. Backlog (not yet planned in detail)

- **Tree shape:** wider + shallower gives ~one leaf per child per call (uniform tree: `children_per_node:
  6`, `depth: 3` ≈ ⅓ of today's calls for ~2.7× the leaves). The benchmark's diversity gain came from
  finer leaves on the *subject* factor — ideally concrete situations, not labels (Curator's subtopics).
- **Taxonomy prompt trimming:** list only a node's own siblings, not the whole level; send the strategy
  call the valid-path list instead of the full taxonomy JSON (1 MB today — will overflow context on
  bigger trees).
- **Provider batch pricing** (~50% off): each phase as one batch job; OpenRouter lists `:batch` DeepSeek
  flash variants — check whether a model id alone gets the price. Batch jobs take hours per phase.
- **Prompt caching:** keep static text first in every prompt; verify with item 8's cached-token logging
  (DeepSeek caches prefixes automatically; cached input ≈ 1/5 price). Less important once batching
  sends the prefix once per batch.
- **Stop paying for discarded rows:** generate for the expected accept rate and top up only the shortfall
  (resume already supports growing a run); make infrastructure failures (rate limits, key limits)
  retryable on resume instead of permanent rejections.
- **Response cache** (Curator's content-addressed cache): makes re-running pilots and prompt tweaks nearly
  free; dev-time win, not first-run.

Other defects the benchmark's code review found (not cost items; verify before fixing): `--no-resume`
keeps stale `meta_prompts.jsonl`; hand-edited `strategies.json` is never validated; nullable schema types
(`["string","null"]`) crash config load; `cost_summary.json` is overwritten by every command; ~~OpenAI SDK
retries stack with ours~~ (fixed: `max_retries=0`); evaluation LLM stages have no per-item
isolation; below-target runs finish silently.
