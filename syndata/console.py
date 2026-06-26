"""Human-facing console output via rich.

This is the CLI's presentation layer (the run header, phase markers, spinners, progress bars,
warnings, and the taxonomy review prompt) — deliberately separate from the machine-readable
`llm_calls.jsonl` audit log, which stays plain JSONL. Both consoles resolve sys.stdout/stderr lazily
at print time, so they cooperate with pytest's capture.

Progress is rendered with rich (there is no tqdm dependency). Only one rich live display may run at a
time, so callers run phases sequentially: a `spinner()` or `track()` block must close before the next
one opens or before prompting for input.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager

from rich.console import Console
from rich.live import Live
from rich.markup import escape
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
)
from rich.tree import Tree

console = Console()
err_console = Console(stderr=True)

# Quiet mode is process-global, set once by the CLI from --quiet. It suppresses only the animated
# live elements (spinners, bars); static phase markers, the run header, warnings, and the final
# summary still print so a redirected log keeps its structure. Functions with their own `quiet`
# parameter (generate_dataset, run_evaluation) pass it through explicitly and take precedence.
_quiet = False


def set_quiet(value: bool) -> None:
    global _quiet
    _quiet = value


def info(message: str) -> None:
    console.print(message)


def warn(message: str) -> None:
    err_console.print(f"[yellow]Warning:[/yellow] {message}")


def error(message: str) -> None:
    err_console.print(f"[red]Error:[/red] {message}")


def phase(title: str) -> None:
    # A phase marker with a leading blank line so each section gets breathing room above it.
    # Printed even under --quiet so piped logs keep their shape.
    console.print("")
    console.print(f"[bold cyan]›[/bold cyan] {title}")


def _live(quiet: bool | None) -> bool:
    # Animated displays only make sense on a real terminal and when not silenced. Off a TTY (piped /
    # CI / pytest capture) we fall back to plain lines so no escape codes land in the captured stream.
    silenced = _quiet if quiet is None else quiet
    return not silenced and console.is_terminal


@contextmanager
def spinner(title: str, *, quiet: bool | None = None) -> Iterator[Callable[[str], None]]:
    """Indeterminate-phase status (taxonomy, strategies). Yields `update(subtitle)` to retitle it.

    Falls back to one plain `title…` line when silenced or off a TTY, so the phase is still announced
    without animation. Must close before `track()` opens or before prompting for input.
    """
    if not _live(quiet):
        console.print(f"  {title}…")
        yield lambda _subtitle: None
        return
    with console.status(f"{title}…", spinner="dots") as status:
        yield lambda subtitle: status.update(f"{title} · {subtitle}…")


@contextmanager
def track(total: int, description: str, *, quiet: bool | None = None) -> Iterator[Callable[..., None]]:
    """Determinate progress bar (the generation loop). Yields `advance(step=1, description=None)`.

    Silent when silenced or off a TTY — callers emit a `phase()` marker first, so the phase is still
    visible there. Renders spinner + description + bar + N/total + elapsed. One live display at a time,
    so it must not be nested inside `spinner()`.
    """
    if not _live(quiet):
        yield lambda step=1, description=None: None
        return
    columns = (
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
        TimeElapsedColumn(),
    )
    with Progress(*columns, console=console) as progress:
        task = progress.add_task(description, total=total)

        def advance(step: int = 1, description: str | None = None) -> None:
            # description=None leaves the label unchanged (rich only updates fields it is given).
            progress.update(task, advance=step, description=description)

        yield advance


def _tree_label(name: str, description: str = "", *, bold: bool = False) -> str:
    # escape() so model-generated names containing [] don't get parsed as rich markup.
    label = f"[bold]{escape(name)}[/bold]" if bold else escape(name)
    if description:
        short = description if len(description) <= 60 else description[:59] + "…"
        label += f" [dim]— {escape(short)}[/dim]"
    return label


def _node_path(node: dict) -> str:
    return "/".join(node.get("path", [node["name"]]))


class TaxonomyLogger:
    """Surfaces taxonomy nodes as they are generated. The caller drives it with the JSON node dicts:
    `add_factor(root)` when a factor starts, `add_children(parent, children)` as each node's children
    are attached, `finish_factor(root)` when its subtree is done.

    This no-op base is also the --quiet logger. Subclasses render the two styles:
      - `tree`  (TaxonomyTreeLogger): a live-growing tree on a TTY, printed once off a TTY.
      - `light` (TaxonomyLightLogger): flat per-node breadcrumb lines (`+ path → k children`).
    Style is chosen by `taxonomy.log_style`. Only one rich live display runs at a time, so a tree
    logger must close before another live display opens or before the review prompt.
    """

    def start(self) -> None: ...
    def add_factor(self, node: dict) -> None: ...
    def add_children(self, parent: dict, children: list[dict]) -> None: ...
    def finish_factor(self, node: dict) -> None: ...
    def close(self) -> None: ...


class TaxonomyLightLogger(TaxonomyLogger):
    def add_factor(self, node: dict) -> None:
        desc = node.get("description", "")
        suffix = f" [dim]— {escape(desc[:60])}[/dim]" if desc else ""
        console.print(f"[bold]▸ {escape(node['name'])}[/bold]{suffix}")

    def add_children(self, parent: dict, children: list[dict]) -> None:
        console.print(f"[dim]  + {escape(_node_path(parent))} → {len(children)} children[/dim]")


class TaxonomyTreeLogger(TaxonomyLogger):
    def __init__(self, *, live_enabled: bool) -> None:
        self._tree = Tree("[bold]Taxonomy[/bold]")
        self._branch: dict[int, Tree] = {}  # id(node) -> its rich branch; kept off the JSON node
        # vertical_overflow="visible" keeps a deep tree from being cropped to the terminal height.
        self._live = Live(self._tree, console=console, auto_refresh=False, vertical_overflow="visible") if live_enabled else None

    def start(self) -> None:
        if self._live is not None:
            self._live.start()
            self._live.refresh()

    def add_factor(self, node: dict) -> None:
        # The "(expanding…)" marker is cleared by finish_factor once the subtree is complete.
        self._branch[id(node)] = self._tree.add(f"{_tree_label(node['name'], node.get('description', ''), bold=True)} [dim](expanding…)[/dim]")
        self._refresh()

    def add_children(self, parent: dict, children: list[dict]) -> None:
        branch = self._branch.get(id(parent))
        if branch is None:
            return
        for child in children:
            self._branch[id(child)] = branch.add(_tree_label(child["name"], child.get("description", "")))
        self._refresh()

    def finish_factor(self, node: dict) -> None:
        branch = self._branch.get(id(node))
        if branch is not None:
            branch.label = _tree_label(node["name"], node.get("description", ""), bold=True)
            self._refresh()

    def _refresh(self) -> None:
        if self._live is not None:
            self._live.refresh()

    def close(self) -> None:
        if self._live is not None:
            self._live.stop()  # leaves the final frame in place
        else:
            console.print(self._tree)  # off a TTY: render the finished tree once


@contextmanager
def taxonomy_logger(*, style: str = "tree", quiet: bool | None = None) -> Iterator[TaxonomyLogger]:
    """Yield the taxonomy logger for `style` (`tree`/`light`); --quiet yields the no-op base."""
    silenced = _quiet if quiet is None else quiet
    if silenced:
        logger: TaxonomyLogger = TaxonomyLogger()
    elif style == "light":
        logger = TaxonomyLightLogger()
    else:
        logger = TaxonomyTreeLogger(live_enabled=console.is_terminal)
    logger.start()
    try:
        yield logger
    finally:
        logger.close()
