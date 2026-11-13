#!/bin/zsh
# Run pilot arms in parallel from the repo root (API keys come from the root .env).
# usage: analysis/run_arms.sh ARM [ARM ...]      (B0* arms also need MAIN_WORKTREE=/path/to/main/checkout)
# Logs go to runs/pilot/configs/<arm>.log. Watch them with analysis/watch_arms.py (alerts on stalls).
cd "$(dirname "$0")/.."
CONFIGS=runs/pilot/configs
for arm in "$@"; do
  if [[ $arm == B0* ]]; then
    : "${MAIN_WORKTREE:?B0 arms run main's code: set MAIN_WORKTREE}"
    python -c "import sys; sys.path.insert(0, '$MAIN_WORKTREE'); from simula.cli import main; raise SystemExit(main(['generate', '$CONFIGS/$arm.yaml', '--quiet']))" > "$CONFIGS/$arm.log" 2>&1 &
  else
    python -m simula.cli generate "$CONFIGS/$arm.yaml" --quiet > "$CONFIGS/$arm.log" 2>&1 &
  fi
done
wait
