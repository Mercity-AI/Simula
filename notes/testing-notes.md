# Testing Notes — Data Extraction Dataset (~1K rows)

Goal: build a ~1,000-row **text → narrow neat JSON** extraction dataset using `simula`,
with `deepseek/deepseek-v4-pro` for every model role (strategic, bulk, critic).

Design principle the user emphasized: **fields must be narrow/atomic**.
- Good: `{"before": "2026-11-26", "after": "2026-12-02", "name": "xyz"}`
- Bad:  `{"date_range": "before 26-11-26 and after 12-02-25"}` (compound/lousy)

## Status log

### 2026-06-14 — Recon
- Read AGENTS.md, config.py, models.py, generate.py, tasks.py, example configs.
- Per-item call flow: meta_prompt (1) → complexify (maybe) → generate (1) → repair (maybe)
  → semantic_critic (1) → refine (maybe, up to max_refine_attempts). ~3–6 calls/item.
- For ~1K rows w/ overgeneration ≈ 3.5K–5.5K calls. Plan: smoke test first, then scale.
- `_reasoning_extras` auto-detects "deepseek" → sends `{"reasoning":{"effort":"low","exclude":true}}`
  via extra_body. Good (faster/cheaper, no reasoning tokens in output).

### BLOCKER: API key
- `env` shows NO `OPENROUTER_API_KEY` and there is no `.env` file.
- `deepseek/deepseek-v4-pro` is an OpenRouter-style id → needs `OPENROUTER_API_KEY`.
- Real calls will fail with "Missing API key for model role ..." until provided.
- Resolution plan: user puts key in gitignored `.env` (`OPENROUTER_API_KEY=...`);
  runs source it inline. (.env is already in .gitignore.)

## SMOKE RESULT (2026-06-14) — PASS, high quality
- 20 attempts -> 18 accepted (90%) -> 15 final. Cost ~$0.30. Wall ~24 min (~16 was one-time taxonomy).
- Quality: narrow atomic fields (salary_min/max/currency, location_city/country/state split, dates
  YYYY-MM-DD). NO compound salary_range junk. Keys VARY per row. Extraction omits requested-but-absent
  fields (subset of schema keys). Critic caught a real hallucination (location_state "Texas" from "Austin").
- Nits: ~1/20 incomplete-JSON parse failures (overgen absorbs); occasional mildly-compound enum value
  (employment_type "Permanent, full-time"). Acceptable.
- FIX applied: generate.py now bounds generation with asyncio.Semaphore(concurrency). New test
  test_generation_concurrency_is_bounded. Full suite: 46 passed.

## Full run plan (pending user GO)
- Same output_dir -> reuses taxonomy.json + strategies.json + the 20 smoke rows (resume).
- Set target_size=1000, overgenerate_ratio~1.35, concurrency~25. Est ~$10, ~1-1.5 hr.

## FULL RUN (2026-06-14) — in progress
- Switched data generation to deepseek/deepseek-v4-flash:nitro (all roles). Taxonomy + strategies
  reused from the pro build (files on disk). Launched with --no-resume so all rows are flash.
  target_size=1000, overgenerate=1.4 (1400 attempts), concurrency=25.
- Sanity-checked flash:nitro: narrow fields hold (salary split, omits absent fields), ~5s/call w/
  reasoning low (~$0.0003), ~2.5s without (~$0.0001). Est full run ~$1-2, ~30-45 min.
- ~100/1400 in: 97% accept, ~39 rows/min, 92 distinct key-sets / 96 rows. Critic correctly rejecting
  compound fields (benefits_description, salary_per_year range string) -> accepted rows stay narrow.

## FULL RUN COMPLETE (2026-06-14) — 1000 rows delivered
- Generated 1206 attempts -> 1157 accepted (96%) before the tail stalled. The last ~194 overgen
  attempts jammed on OpenRouter rate-limiting flash:nitro (SDK 600s timeouts; some single calls 800s+,
  14% of calls >40s in bad windows). Throughput was bursty (39 -> 3.7 -> 35 rows/min).
- Decision: we already had 1157 accepted >> 1000 target (the tail would be trimmed anyway), so killed
  the stalled proc and finalized from accepted rows (no extra calls).
- CLEANUP pass: dropped 22 of 1157 accepted that had prose/compound fields (benefits_summary,
  hybrid_work_description, schedule_details, etc.) or compound values (e.g. shift '...12-hour shifts',
  experience '1-2'); backfilled from clean surplus -> 1135 pristine -> coverage-trimmed to 1000.
- FINAL dataset.final.jsonl = 1000 rows. Audit: 0 compound field names, 0 compound values,
  841 distinct extraction key-sets, avg 6.7 fields, all flash:nitro, 0 empty lineage, 0 dupes.
- eval_report.json: 100% taxonomy leaf coverage on all 4 factors (16/16, 28/28, 29/29, 20/20).
- Cost (token-based estimate, rates approx): flash data gen ~$2.3, pro taxonomy+smoke ~$0.4, ~$2.6 total.
  NOTE: cost_summary.json may be stale (proc was SIGKILLed, skipping the finally-block write).

## FOLLOW-UPS / known issues for the repo
- ~~generation has no per-attempt timeout~~ RESOLVED (code-refactor branch): each real call now has a
  180s default timeout (`models.<role>.timeout_seconds`) so a hung connection fails fast.
- The critic lets ~2% prose/compound fields through; could add an explicit "no *_summary/_description/
  _details fields" rule. Handled here via a post-hoc cleanup filter.

## DATA VIEWER (2026-06-14)
- viz-tooling/data_viewer.html: single-file, light theme, Tailwind+js-yaml CDN. Drag/drop dataset.final.jsonl
  (+ optional cost_summary.json, strategies.json, *.yaml config; auto-detected). 10 records/page,
  search + strategy filter + accepted-only. Each card: schema|text|extraction + collapsible provenance
  (strategy, taxonomy lineage, meta-prompt, critic verdicts, LLM config/timing). Has a built-in demo.
- Verified headless against real data: 1000 rows, 10/page, summary + provenance render, search works.
- Rebuilt cost_summary.json from llm_calls.jsonl (the evaluate run had clobbered it to zeros because
  the SIGKILLed full-run never wrote its own, and evaluate's finally-block wrote an empty COST summary).
  Now: 4440 calls, 3.43M in / 5.57M out tokens, ~84 min wall.

## Decisions (locked)
1. Domain: **Job postings**.
2. Each row = schema-guided extraction example with VARYING keys:
   `{schema: {field->type}, text: "<job ad>", extraction: {field->atomic value}}`.
   Inner schema/extraction are open objects → keys differ per row (user's requirement).
3. Plan: smoke ~15 rows → review → full 1K.

## Build (2026-06-14)
- examples/job_extraction.yaml — envelope schema (schema/text/extraction), deepseek/deepseek-v4-pro
  for all 3 roles via OPENROUTER_API_KEY, explicit job-posting taxonomy factors, strategy.guidance.
  target_size=15 (smoke); bump to 1000 + resume for full run.
- examples/job_extraction_prompts.py — overrides meta_prompt_prompt / generate_record_prompt /
  critique_prompt. Enforces narrow atomic fields (no salary_range/location/date_range), faithful
  extraction (only facts in text), omit-when-absent (→ varying keys), strict critic.
- `validate` PASSED offline. Est smoke calls: taxonomy=17, generation=80.

## BUG FOUND: generation.concurrency not enforced (2026-06-14)
- `generation.concurrency` is honored in evaluate.py (Semaphore) but NOT in generate.py.
  generate_dataset creates a TaskGroup task for EVERY attempt index at once -> unbounded concurrency.
- Smoke (20 attempts) = harmless. Full 1K (~1300 attempts) would launch ~1300 concurrent reasoning
  calls -> httpx pool exhaustion + 429 storms + cost spike.
- FIX before full run: wrap _generate_one_safe in asyncio.Semaphore(concurrency). Compact, makes the
  documented knob actually work. Add/extend a pipeline test.

## Latency / scale analysis (2026-06-14, during smoke)
- Per-call latency is HIGH: 26-93s (reasoning emits 1k-3.6k tokens even at effort low).
- Taxonomy call count = 4 factors x 16 + 1 strategy = ~65 calls. Factors expand SEQUENTIALLY
  (for factor in factors), so effective parallelism is low -> taxonomy ~20 min. One-time, reused.
- Generation IS concurrent at generation.concurrency. ~4 calls/row sequential.
  At concurrency 6 + ~30s/call, 1.3k attempts ≈ ~7 HOURS. Too slow.
  PLAN for full run: raise generation.concurrency to ~30 (watch for 429s; router retries them).
  Taxonomy/strategies are reused from smoke (same output_dir), so full run skips the ~65 taxonomy calls.

## Key + model verified (2026-06-14)
- User pasted key; written to gitignored .env. Auth OK, `deepseek/deepseek-v4-pro` resolves on OpenRouter.
- IMPORTANT: deepseek-v4-pro is a REASONING model. Hidden reasoning tokens count against max_tokens.
  Measured on a generate-style prompt (max_tokens=1500):
    * reasoning {effort:low, exclude:true}  -> ~1150 reasoning toks, GOOD narrow fields (salary_min/max)
    * reasoning {enabled:false}             -> 0 reasoning, but WORSE: emitted compound "salary_range"
    * no reasoning key (default)            -> ~530 reasoning, good
- Decision: keep reasoning low+exclude (repo auto-adds for deepseek) for field quality; RAISE max_tokens
  so reasoning+JSON both fit. Set strategic=3000, bulk=4000, critic=2500. (critic also reasons -> needs
  headroom or verdict JSON truncates and the row gets rejected as "Generation failed".)
- Cost ref: ~$2.2e-5 for a 32-token call; reasoning adds ~1k toks/call but deepseek is cheap.

## REFACTOR (2026-06-17, `code-refactor` branch — not yet merged)

Cleanup/hardening pass driven by a review. Notes above are historical; several observations there
are now superseded by this work. Key changes (see commits on `code-refactor`):

- **.env is now auto-loaded.** `load_config` calls `load_env_files` (python-dotenv), so the
  earlier "API key BLOCKER / .env not read" notes no longer apply — drop the key in a gitignored
  `.env` and it resolves. An exported shell var still wins.
- **Reasoning auto-detection REMOVED.** The old `_reasoning_extras` substring sniffing
  (`deepseek`/`o1`/...) is gone; set `extra_body: {reasoning: {effort: low, exclude: true}}`
  explicitly per role. `examples/job_extraction.yaml` was updated to do this.
- **Per-request timeout added** (180s default, `models.<role>.timeout_seconds`).
- **`min_interval_seconds` pacing removed** — rate control is `generation.concurrency` + 429
  retry/backoff. Lower concurrency if a provider rate-limits.
- **Typed config** (`cfg.generation`/`cfg.taxonomy`/`cfg.evaluation`); `validate` now range-checks
  knobs and warns on a missing real API key.
- **`evaluate` no longer rewrites `dataset.final.jsonl`** — it writes `dataset.evaluated.jsonl`.
- **Dependencies trimmed:** `jsonpath-ng` dropped (`text_field` is plain dotted dict access now);
  `python-dotenv` added. Cost tracking simplified (no `CostTracker` class / global). `numpy`,
  `scikit-learn`, and `sentence-transformers` moved to an optional `[diversity]` extra (they are
  only used by the off-by-default diversity metric); `httpx` dropped (transitive via `openai`).
- **Token logging:** `llm_calls.jsonl` always records in/out tokens (estimated when the provider
  omits usage), matching `cost_summary.json`. Logs always go to `<output_dir>/llm_calls.jsonl`;
  the `SYNDATA_LLM_LOG` env override was removed, and `flush_logs` now warns (stderr) on a failed
  log write instead of swallowing it.

---

# E-COMMERCE SEARCH-QUERY EXTRACTION DATASET (target ~10K rows) — 2026-06-24

New task on the refactored (`code-refactor`) branch: a dataset to exercise the post-refactor
pipeline end-to-end while producing something useful. Domain: **e-commerce search → structured
query extraction**. People search in natural language ("I want a shirt, XL, navy, under $40") and we
extract the product + attributes into ATOMIC, DB-queryable JSON. Same spirit as job_extraction but
input is a search query and the envelope is `{query, extraction}` (no per-row `schema` field).

## Decisions (locked)
1. **Row shape `{query, extraction}`** — query = NL shopper search; extraction = open object of atomic
   fields. No declared schema: key variation comes from what each query mentions (user requirement:
   "we don't want the same schema everywhere"). Envelope validates structure; atomicity/faithfulness
   live in the prompt module + critic.
2. **Atomic + nesting** — one fact per leaf; ranges split (price_min/price_max, never "price_range");
   units normalized ("under $80" -> price_max 80; "cheap" alone -> NO invented number). ONE level of
   nesting allowed for grouped facts (`price.min/price.max`, `dimensions.*`); flat and nested styles
   varied across rows. Confirmed nested objects validate against the open `extraction` schema.
3. **Size/length spread 3..20 fields** — steered upstream by the `query_complexity` taxonomy factor +
   meta-prompts (query length scales with constraint count), NOT hard-enforced by the critic (critic
   only rejects <3 fields / compound junk), so we get the spread without mass rejections.
4. **Models (user spec):** strategic = `deepseek/deepseek-v4-pro` (strategy phase ONLY: taxonomy +
   strategies, one-time, reused); bulk + critic = `deepseek/deepseek-v4-flash`. temps: bulk 0.75,
   critic 0.1, strategic 0.5. All roles: `reasoning {effort:low, exclude:true}`, max_tokens 16384
   (reasoning auto-detection was removed in the refactor, so it MUST be set explicitly per role).
5. **Taxonomy: depth 4** (user: "4 node deep"), 4 factors (product_vertical, shopper_intent,
   attribute_focus, query_complexity). `children_per_node=3` -> ~81 leaves/factor and ~492 pro calls;
   the default 4 would give ~256 leaves/factor (~1032 pro calls) and 27->81-wide concurrent bursts at
   the deep levels — overkill for 10K and a rate-limit risk. best_of_n=2.

## Files
- `examples/ecommerce_search_extraction.yaml` — config (smoke-sized; bump target_size for full run).
- `examples/ecommerce_search_extraction_prompts.py` — overrides meta_prompt_prompt, complexify_prompt
  (pushes toward the large 12-20 field end), generate_record_prompt, critique_prompt. Carries an
  atomic-attribute menu spanning many verticals.

## Fingerprint discipline (smoke -> full resume)
Resume fingerprints description/schema/seed/model-ids/scenarios_per_mix/complexity_ratio/
max_refine_attempts/prompts/taxonomy/strategies — these MUST stay constant smoke->full. target_size,
overgenerate_ratio, concurrency, checkpoint_every are NOT fingerprinted -> safe to change. So the full
run = edit those four only + `generate --resume` in the same output_dir (reuses taxonomy/strategies/
smoke rows). Locked-constant: scenarios_per_mix=3, complexity_ratio=0.3, max_refine_attempts=1.

## Per-attempt call flow (from generate.py)
meta_prompt(1, bulk; produces scenarios_per_mix options, one is picked — does NOT multiply calls)
-> maybe complexify (~30%) -> generate(1, bulk) -> maybe repair(rare) -> critic loop: critique(1,
critic) + up to max_refine_attempts refine(+critique). Accept-first-try ~3.3 calls/attempt.
10K @ overgen 1.3 = 13K attempts ~= 45K flash calls. Rough est: data gen ~$10-20, taxonomy ~$1-3,
~3-5 h wall at concurrency ~24. (To be refined with real smoke numbers.)

## Status log
### 2026-06-24 — Build + offline checks: PASS
- Wrote config + prompts. `validate` PASS. Offline: flat/nested/2-field records validate against the
  envelope (min-field-count is correctly the critic's job, not the schema). Prompts render clean.
- Verified auth + both model ids resolve on OpenRouter with tiny test calls (3 stray `generate` rows
  in llm_calls.jsonl are from this check, pre-taxonomy).
- NEXT: build depth-4 taxonomy on pro (one-time), review tree, then smoke ~24 rows, then full 10K.

### 2026-06-24 — Taxonomy build (depth 4 on pro), IN PROGRESS
- GOTCHA: `children_per_node` is a REQUEST to expand_prompt, NOT a hard cap. The refine step
  (`refine_nodes_prompt`) consolidates the best_of_n candidate lists but is never told to cap the
  count, so the model keeps ~5 children/node instead of 3. Effective branching ~5 -> ~5^4 leaves/
  factor (~600 nodes/factor) instead of ~81, and ~1750+ pro calls instead of my ~492 estimate.
  Tree is just richer than planned (fine/better for 10K diversity); cost ~$4-5 for taxonomy, one-time.
  LEVER for a genuinely smaller tree next time: lower `depth`, or add a refine prompt that enforces
  the child count. User confirmed spend is fine -> let it finish.
- Health: ~1 node-expansion failure out of ~580 nodes (that branch degrades to a leaf — negligible).
- DONE: 4 factors, 2358 total nodes, depth 4 on all. Lopsided — product_vertical = 1725 nodes / 1269
  leaves (level-1 fanned to 21 because the description enumerated ~21 verticals), other 3 factors ~165-
  252 nodes. Fine for sampling (one node/factor, random over the tree -> finer product granularity =
  more diversity). Leaf quality excellent (OLED TVs, retro consoles; query_complexity split into
  constraint_count{small/med/large} x utterance_shape x special_expressions — drives the size spread).

### 2026-06-24 — SMOKE (24 rows on plain flash): PASS, high quality
- 36 attempts -> 35 accepted (97%) -> 24 final. The 1 reject was the critic correctly catching a
  hallucination ("stylus" extracted as a product when it was a bundled feature). Wall ~14 min @ conc 8.
- Field-count spread 5..25 (median 13); 24/24 distinct key-sets; nesting in 10/24, arrays in 19/24;
  budget->price_min/max, "no polyester"->material_excluded, "not an HP"->brand_excluded, size+size_system.
- LATENCY profile (cost_summary by-task): meta_prompt is the hog at ~73s/call (46% of compute) because
  scenarios_per_mix=3 writes 3 elaborate prompts and uses 1; generate ~26s, critic ~30s, refine ~22s.
  Per attempt ~157 compute-seconds. deepseek v4 reasoning is the latency driver (~38s/call avg).
- KEY-NAMING: open schema -> same concept gets synonym keys across rows (free_shipping vs shipping_free,
  reviews_min vs review_count_min, weight_max_lb vs weight_kg_max, sort_by "rating" vs "rating_desc").
  USER DECISION: leave as-is (max variety). Not canonicalized. (If a future run wants DB-clean keys,
  add a canonical-vocab preference to generate_record_prompt — would also pull the >20-field outliers
  back toward ~20.)

### 2026-06-24 — FULL 10K RUN: LAUNCHED
- Pricing (OpenRouter, web-checked): flash $0.09/M in, $0.18/M out; pro $0.435/M in, $0.87/M out.
- SPEND so far (sunk): taxonomy ~$4.79 (1877 pro calls, 3.28M in/3.87M out) + smoke ~$0.16 = ~$5.
- 10K estimate: scale smoke per-attempt (3752 in / 5265 out, incl. reasoning + 3-scenario meta) x 13000
  attempts (10K/0.97 accept, overgen 1.3) -> ~48.8M in + ~68.4M out -> ~$16.7; with nitro/variance margin
  ~$17-25. Total project ~$22-30.
- USER DECISIONS: nitro on (bulk+critic = deepseek-v4-flash:nitro), scenarios_per_mix=3 kept (max
  variety, spend/time not a constraint), concurrency 48, keys as-is. strategic stays pro but is NOT
  re-called (strategies.json reused). Launched `generate --no-resume` (model id change invalidated the
  smoke-row fingerprint; taxonomy.json + strategies.json reused from disk).
- Config block now: target_size 10000, overgenerate 1.3, scenarios_per_mix 3, complexity_ratio 0.3,
  max_refine_attempts 1, concurrency 48, checkpoint_every 50.
- Early health (90s in): 48 raw / 47 accepted (98%), generator_model=flash:nitro, no errors. Throughput
  ~32 attempts/min @ conc 48 -> ETA ~5-7 h. Background task bvoqgvq6q notifies on exit (success or crash).
- NEXT (on completion): audit final 10K (accept rate, size dist, atomicity, dupes, empty lineage),
  then `evaluate` for coverage report (dataset.evaluated.jsonl + eval_report.json), then summarize.

### 2026-06-25 — FULL 10K RUN: COMPLETE
- 13,000 attempts -> 10,985 accepted -> trimmed to dataset.final.jsonl = 10,000 rows. Wall ~5h @ conc 48.
- TAIL THROTTLE (known pattern, infra not quality): last 1500 attempts accepted only 384 — 1083 of the
  rejects were OpenRouter timeouts/rate-limits, just 33 critic-rejects. Overgen 1.3 absorbed it (had
  10,985 accepted >> 10,000), so the final set is unaffected. If pushing higher targets, either lower
  concurrency for the tail or raise overgenerate_ratio.
- ACTUAL COST (cost_summary.json, this process only — excludes the separate taxonomy build): 48,447
  calls, 44.49M in / 71.73M out -> ~$16.9 (flash:nitro $0.09/$0.18). + sunk ~$5 (taxonomy+smoke) =
  ~$22 total. Matched the pre-run estimate ($17-25).
- FINAL AUDIT (all 10,000): all accepted + schema_valid + nitro; 0 empty lineage; envelope exactly
  {query,extraction} on every row; 5 exact-dup queries (0.05%); 1 junk compound key (price_range) in
  10K. Variety: 9,625 distinct key-sets (96% unique), 8,241 distinct keys total. Size buckets: 3-5=1230,
  6-11=2882, 12-20=4417, 21+=1459, <3=12; median 14 fields, p90 22, max 58. Query length 1-288 words
  (median 45). Nesting 52%, arrays 78%. Both flat price_min/max and nested price{min,max} coexist.
- Artifacts in runs/ecommerce_search_extraction/: dataset.final.jsonl (deliverable), dataset.raw/
  accepted.jsonl, taxonomy.json, strategies.json, llm_calls.jsonl, cost_summary.json, run_state.json.
- Refactored pipeline validated end-to-end (taxonomy -> strategy -> generate -> critic -> trim) on a
  real 10K run. No code changes were needed; config + prompt-module overrides only.
