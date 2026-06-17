from __future__ import annotations

from typing import Any


def summarize_cost(cost: dict[tuple[str, str, str], list[float]], elapsed_seconds: float) -> dict[str, Any]:
    # cost is the ModelRouter's accumulator: (role, task, model) -> [calls, in_tokens, out_tokens, duration].
    rows = []
    total_calls = total_in = total_out = 0
    total_duration = 0.0
    for role, task, model in sorted(cost):
        calls, in_toks, out_toks, duration = cost[(role, task, model)]
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
