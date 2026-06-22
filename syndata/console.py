"""Human-facing console output via rich.

This is the CLI's presentation layer (status, warnings, the taxonomy review prompt) — deliberately
separate from the machine-readable `llm_calls.jsonl` audit log, which stays plain JSONL. Both
consoles resolve sys.stdout/stderr lazily at print time, so they cooperate with pytest's capture.
"""

from __future__ import annotations

from rich.console import Console

console = Console()
err_console = Console(stderr=True)


def info(message: str) -> None:
    console.print(message)


def warn(message: str) -> None:
    err_console.print(f"[yellow]Warning:[/yellow] {message}")


def error(message: str) -> None:
    err_console.print(f"[red]Error:[/red] {message}")
