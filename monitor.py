#!/usr/bin/env python3
"""Live monitor for a syndata run.

Reads the artifacts a run writes incrementally (run_state.json, dataset.raw.jsonl,
llm_calls.jsonl) and prints a progress / quality / throughput snapshot. No model calls.

Usage:
    python monitor.py                                  # one snapshot, default config
    python monitor.py --config examples/job_extraction.yaml
    python monitor.py --watch 20                       # refresh every 20s until target met
"""
from __future__ import annotations

import argparse
import json
import math
import time
from collections import Counter
from datetime import datetime
from pathlib import Path

import yaml


def _load_run_meta(config_path: Path) -> tuple[Path, int, int]:
    cfg = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    out_dir = Path(cfg["project"]["output_dir"])
    gen = cfg.get("generation", {})
    target = int(gen.get("target_size", 0))
    attempts = math.ceil(target * float(gen.get("overgenerate_ratio", 1.0)))
    return out_dir, target, attempts


def _iter_jsonl(path: Path):
    if not path.exists():
        return
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def _parse_ts(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value).timestamp()
    except ValueError:
        return None


def _fmt_dur(seconds: float) -> str:
    seconds = int(max(0, seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h{m:02d}m"
    if m:
        return f"{m}m{s:02d}s"
    return f"{s}s"


def _bar(frac: float, width: int = 30) -> str:
    frac = max(0.0, min(1.0, frac))
    filled = int(round(frac * width))
    return "[" + "#" * filled + "-" * (width - filled) + "]"


def snapshot(config_path: Path) -> bool:
    """Print one snapshot. Returns True when the final target appears to be met."""
    out_dir, target, attempts = _load_run_meta(config_path)

    raw = list(_iter_jsonl(out_dir / "dataset.raw.jsonl"))
    accepted_rows = [r for r in raw if r.get("accepted")]
    rejected_rows = [r for r in raw if not r.get("accepted")]
    n_raw = len(raw)
    n_acc = len(accepted_rows)

    # Throughput from raw-row timestamps (records written as each attempt completes).
    ts = sorted(t for t in (_parse_ts(r.get("created_at")) for r in raw) if t is not None)
    elapsed = (ts[-1] - ts[0]) if len(ts) >= 2 else 0.0
    rate_per_min = (n_raw / elapsed * 60) if elapsed > 0 else 0.0
    # Recent rate over the last up-to-50 rows for a fresher ETA.
    recent = ts[-50:]
    recent_rate = (len(recent) / (recent[-1] - recent[0]) * 60) if len(recent) >= 2 and recent[-1] > recent[0] else rate_per_min
    remaining_attempts = max(0, attempts - n_raw)
    eta = (remaining_attempts / recent_rate * 60) if recent_rate > 0 else 0.0

    # LLM call accounting (live file; parse only the light fields).
    by_task: Counter = Counter()
    in_tok = out_tok = 0
    n_calls = 0
    for row in _iter_jsonl(out_dir / "llm_calls.jsonl"):
        n_calls += 1
        by_task[row.get("task", "?")] += 1
        in_tok += row.get("in_tokens") or 0
        out_tok += row.get("out_tokens") or 0

    # Quality signals: key-set variety and average atomic-field count per extraction.
    keysets = Counter()
    keycount_total = 0
    for r in accepted_rows:
        rec = r.get("record") or {}
        extraction = rec.get("extraction") if isinstance(rec, dict) else None
        if isinstance(extraction, dict):
            keysets[tuple(sorted(extraction.keys()))] += 1
            keycount_total += len(extraction)
    avg_keys = (keycount_total / n_acc) if n_acc else 0.0
    models = Counter(r.get("generator_model", "?") for r in accepted_rows)

    acc_rate = (n_acc / n_raw * 100) if n_raw else 0.0
    reasons = Counter()
    for r in rejected_rows:
        reason = (r.get("rejection_reason") or "unknown").split(":")[0][:60]
        reasons[reason] += 1

    print("=" * 64)
    print(f"syndata monitor | {out_dir}  ({datetime.now().strftime('%H:%M:%S')})")
    print("-" * 64)
    print(f"attempts {_bar(n_raw / attempts if attempts else 0)} {n_raw}/{attempts}")
    print(f"accepted {_bar(n_acc / target if target else 0)} {n_acc}/{target}  ({acc_rate:.0f}% accept)")
    print(f"throughput: {recent_rate:.1f} rows/min (recent) | elapsed {_fmt_dur(elapsed)} | ETA ~{_fmt_dur(eta)}")
    print(f"llm calls: {n_calls}  in_tok={in_tok:,} out_tok={out_tok:,}")
    if by_task:
        print("  by task: " + ", ".join(f"{k}={v}" for k, v in by_task.most_common()))
    print(f"quality: {len(keysets)} distinct key-sets across {n_acc} accepted | avg {avg_keys:.1f} fields/extraction")
    if len(models) > 1 or (models and "?" not in models):
        print("  generator models: " + ", ".join(f"{m}={c}" for m, c in models.most_common()))
    if reasons:
        print("top reject reasons:")
        for reason, count in reasons.most_common(5):
            print(f"  {count:4d}  {reason}")
    print("=" * 64)

    return target > 0 and n_acc >= target


def main() -> int:
    ap = argparse.ArgumentParser(description="Live monitor for a syndata run.")
    ap.add_argument("--config", default="examples/job_extraction.yaml")
    ap.add_argument("--watch", type=float, default=0.0, help="Refresh interval in seconds (0 = one snapshot).")
    args = ap.parse_args()

    config_path = Path(args.config)
    if args.watch <= 0:
        snapshot(config_path)
        return 0
    while True:
        done = snapshot(config_path)
        if done:
            print("target reached.")
            return 0
        time.sleep(args.watch)


if __name__ == "__main__":
    raise SystemExit(main())
