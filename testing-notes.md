# Testing Notes — Data Extraction Dataset (~1K rows)

Goal: build a ~1,000-row **text → narrow neat JSON** extraction dataset using `syndata`,
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
- Monitor: `python monitor.py [--watch N]` reads run_state/raw/llm_calls -> progress, accept rate,
  throughput+ETA, calls-by-task, key-set variety, reject reasons. (NOTE: llm_calls.jsonl still holds
  the earlier taxonomy/smoke calls; cost_summary.json resets per process so final cost is clean.)
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
- data_viewer.html: single-file, light theme, Tailwind+js-yaml CDN. Drag/drop dataset.final.jsonl
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
