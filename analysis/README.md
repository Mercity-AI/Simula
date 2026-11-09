# Pilot analysis

Scripts behind the cost/quality tables in `plan.md` §0 (the e-commerce pilots of 2026-11-08/09). Not
part of the `simula` package. Run everything from the repo root: API keys come from the root `.env`
(none are stored here), and all run data lands in the gitignored `runs/pilot/`.

Needs the dev extras (`pip install -e ".[dev]"` for the diversity deps) plus `tiktoken` for
`prompt_tokens.py`. Diversity uses `simula.diversity.embedding_diversity`, so its numbers match what
`simula evaluate` reports for the same texts, embedding model and sample size.

## Rerun a pilot

```bash
python analysis/pilot_configs.py --main-worktree /path/to/main-checkout   # writes runs/pilot/configs/*.yaml
MAIN_WORKTREE=/path/to/main-checkout analysis/run_arms.sh C5_combined R5_recommended
python analysis/watch_arms.py 500 C5_combined R5_recommended               # alerts on finish, errors, stalls
```

Every arm copies the 10K run's taxonomy and strategies (`runs/v0_ecommerce_search_extraction`) and uses
seed 31, so arms draw the same mixes per attempt index. `B0*` arms run `main`'s code (the pre-change
baseline) and need a checkout of it. Arm definitions are the `ARMS` table in `pilot_configs.py`.

Real model calls cost money: only run arms with the user's go-ahead. On stalls (no call logged for a few
minutes) kill the arm and rerun it; resume only redoes the missing attempts.

## Score an arm

```bash
python analysis/analyze.py runs/pilot/R5_recommended --n 400   # cost, critic, shape, diversity -> analysis.json
python analysis/judge.py runs/pilot/R5_recommended             # judge the final records   -> judge2.jsonl
python analysis/judge.py runs/pilot/R5_recommended --pre       # judge the pre-critic records ("critic off")
python analysis/report.py B0_baseline R5_recommended           # markdown table of both
```

- **`analyze.py`** (no model calls): cost from `llm_calls.jsonl`, both at list price (token counts × the
  price table in the script) and as billed (`cost` logged per call); calls per accepted row; critic
  first-pass accepts and critic-fixed share; extraction field counts and query word counts; size-class
  fidelity (rows whose `query_complexity` node names small/medium/large, and whether the field count lands
  in 3–5 / 6–11 / 12–20); taxonomy-path leaks; opener/brand/price repetition. Pre-critic records are
  rebuilt from the logged generate calls, so "critic off" is scored on the same generations.
  Diversity on query text: Vendi, local (k=10) and global diversity on 400 accepted queries
  (`all-MiniLM-L6-v2`), plus the same on 90 length-matched (8–25-word) queries.
- **`judge.py`**: blind judge `anthropic/claude-haiku-5.5`, reasoning effort medium, prompt version v2
  (explicit product-fit and normalization rules; v1 mis-scored obvious matches and was dropped). Scores
  lineage fit (product, complexity, intent, attribute), unsupported / missing / non-atomic fields,
  realism and coherence. Sample: 150 attempt indexes drawn from 0–499 with `random.Random(7)`, the same
  indexes in every arm; `--first200` uses 100 indexes from 0–199 (the 200-attempt v4-pro arms). Cost ≈
  $0.08 per 150 records. Treat it as a relative measure: absolute rates are noisy.
- **`report.py`**: merges `analysis.json` and `judge2.jsonl` per arm (`--max-index=200` for the paired
  first-200 sample).
- **`prompt_tokens.py`** (no model calls): input tokens of the inline combined prompt vs the NODES/ID
  table, random vs subject-grouped batches, calibrated to the provider's logged counts.
