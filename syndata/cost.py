from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class CostTracker:
    calls: dict[tuple[str, str, str], int] = field(default_factory=dict)
    in_tokens: dict[tuple[str, str, str], int] = field(default_factory=dict)
    out_tokens: dict[tuple[str, str, str], int] = field(default_factory=dict)
    durations: dict[tuple[str, str, str], float] = field(default_factory=dict)
    started: float = field(default_factory=time.time)

    def reset(self) -> None:
        self.calls.clear()
        self.in_tokens.clear()
        self.out_tokens.clear()
        self.durations.clear()
        self.started = time.time()

    def record(self, role: str, task: str, model: str, in_tokens: int, out_tokens: int, duration_seconds: float) -> None:
        key = (role, task, model)
        self.calls[key] = self.calls.get(key, 0) + 1
        self.in_tokens[key] = self.in_tokens.get(key, 0) + int(in_tokens)
        self.out_tokens[key] = self.out_tokens.get(key, 0) + int(out_tokens)
        self.durations[key] = self.durations.get(key, 0.0) + float(duration_seconds)

    def summary(self) -> dict[str, Any]:
        rows = []
        total_calls = total_in = total_out = 0
        total_duration = 0.0
        for role, task, model in sorted(self.calls):
            key = (role, task, model)
            calls = self.calls[key]
            in_toks = self.in_tokens.get(key, 0)
            out_toks = self.out_tokens.get(key, 0)
            duration = self.durations.get(key, 0.0)
            rows.append(
                {
                    "role": role,
                    "task": task,
                    "model": model,
                    "calls": calls,
                    "input_tokens": in_toks,
                    "output_tokens": out_toks,
                    "duration_seconds": round(duration, 3),
                }
            )
            total_calls += calls
            total_in += in_toks
            total_out += out_toks
            total_duration += duration
        return {
            "elapsed_seconds": round(time.time() - self.started, 3),
            "total_calls": total_calls,
            "total_input_tokens": total_in,
            "total_output_tokens": total_out,
            "total_duration_seconds": round(total_duration, 3),
            "by_role_task_model": rows,
        }


COST = CostTracker()
