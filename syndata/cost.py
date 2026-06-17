from __future__ import annotations

from typing import Any


# Cost is accumulated on the ModelRouter as a plain dict keyed by (role, task, model)
# mapping to [calls, input_tokens, output_tokens, duration_seconds]. No class, no global:
# accounting belongs to the router instance that made the calls, so it stays reentrant.
CostKey = tuple[str, str, str]
CostStore = dict[CostKey, list[float]]


def record_cost(store: CostStore, role: str, task: str, model: str, in_tokens: int, out_tokens: int, duration_seconds: float) -> None:
    agg = store.setdefault((role, str(task), model), [0, 0, 0, 0.0])
    agg[0] += 1
    agg[1] += int(in_tokens)
    agg[2] += int(out_tokens)
    agg[3] += float(duration_seconds)


def summarize_cost(store: CostStore, elapsed_seconds: float) -> dict[str, Any]:
    rows = []
    total_calls = total_in = total_out = 0
    total_duration = 0.0
    for role, task, model in sorted(store):
        calls, in_toks, out_toks, duration = store[(role, task, model)]
        rows.append(
            {
                "role": role,
                "task": task,
                "model": model,
                "calls": int(calls),
                "input_tokens": int(in_toks),
                "output_tokens": int(out_toks),
                "duration_seconds": round(duration, 3),
            }
        )
        total_calls += int(calls)
        total_in += int(in_toks)
        total_out += int(out_toks)
        total_duration += duration
    return {
        "elapsed_seconds": round(elapsed_seconds, 3),
        "total_calls": total_calls,
        "total_input_tokens": total_in,
        "total_output_tokens": total_out,
        "total_duration_seconds": round(total_duration, 3),
        "by_role_task_model": rows,
    }
