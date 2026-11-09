"""Watch pilot arms: one line per arm when it finishes, errors, or STALLS (no model call logged for
`stall` seconds while it is still short of target). Re-alerts every `stall` seconds while stalled.
Exits when every arm has finished or errored.

usage: python analysis/watch_arms.py TARGET ARM [ARM ...] [--stall 180]
"""
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1] / "runs" / "pilot"
LOGS = REPO / "configs"
args = sys.argv[1:]
stall = 180
if "--stall" in args:
    i = args.index("--stall")
    stall = int(args[i + 1])
    args = args[:i] + args[i + 2 :]
target, arms = int(args[0]), args[1:]
done: set[str] = set()
last_alert: dict[str, float] = {}
while len(done) < len(arms):
    now = time.time()
    for arm in arms:
        if arm in done:
            continue
        raw = REPO / arm / "dataset.raw.jsonl"
        rows = sum(1 for _ in raw.open()) if raw.exists() else 0
        log = LOGS / f"{arm}.log"
        text = log.read_text(errors="ignore") if log.exists() else ""
        calls = REPO / arm / "llm_calls.jsonl"
        idle = now - calls.stat().st_mtime if calls.exists() else 0
        if rows >= target:
            done.add(arm)
            print(f"{arm} finished generation ({rows} rows)", flush=True)
        elif "Traceback" in text or "Error:" in text:
            done.add(arm)
            print(f"{arm} log error: {[l for l in text.splitlines() if 'Error' in l][:1]}", flush=True)
        elif idle > stall and now - last_alert.get(arm, 0) > stall:
            last_alert[arm] = now
            print(f"STALL {arm}: no model call for {idle:.0f}s at {rows}/{target} rows", flush=True)
    time.sleep(15)
