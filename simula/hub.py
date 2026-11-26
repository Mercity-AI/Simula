"""Share a run on the Hugging Face Hub: stage the dataset and a dataset card, then upload them.

`stage_for_hub` is pure local work: it copies the dataset file and the run's plan into
`<output_dir>/hub/` and writes a `README.md` dataset card. The card's metadata carries the `simula`
tag, which is how the Hub knows which library made a dataset (the Hub's dataset-library registry
maps a card tag to a library name, badge and links). `push_to_hub` uploads that folder.

`huggingface_hub` is optional (the `[hub]` extra) and imported only when uploading, so `--dry-run`
and the tests need nothing beyond the base install.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import yaml
from dotenv import dotenv_values

from . import __version__
from .utils import ARTIFACTS, artifact_path, ensure_dir, read_json, read_jsonl

HUB_DIR = "hub"
DATA_FILE = "data/train.jsonl"
SIMULA_URL = "https://github.com/Mercity-AI/Simula"
DOCS_URL = "https://simula.readthedocs.io/en/latest/"

# Run files that travel with the dataset. llm_calls.jsonl (full prompts and replies), run_state.json
# and the raw/accepted files stay local: they are working data, and the first can hold anything
# the description or the generated text holds.
PLAN_FILES = ("taxonomy", "strategies", "eval")

# The Hub's size_categories buckets, smallest first.
_SIZE_BUCKETS = [
    (1_000, "n<1K"),
    (10_000, "1K<n<10K"),
    (100_000, "10K<n<100K"),
    (1_000_000, "100K<n<1M"),
    (10_000_000, "1M<n<10M"),
]


def hub_tags() -> list[str]:
    """Card tags. `simula` is the library tag the Hub matches; the versioned tag finds datasets
    made by one release; `synthetic` is the Hub's common tag for generated data."""
    return ["simula", f"simula-{__version__}", "synthetic"]


def size_category(count: int) -> str:
    for bound, label in _SIZE_BUCKETS:
        if count < bound:
            return label
    return "n>10M"


def dataset_source(output_dir: Path) -> Path:
    """The file to share: the evaluated dataset when `evaluate` has run, else the generator's output."""
    for name in ("evaluated", "final"):
        path = artifact_path(output_dir, name)
        if path.exists():
            return path
    raise FileNotFoundError(
        f"No dataset to push in {output_dir}: run `simula generate` (or `simula run`) first."
    )


def build_card(
    *,
    repo_id: str,
    pretty_name: str,
    description: str,
    output_format: str,
    models: dict[str, str],
    rows: list[dict[str, Any]],
    source_name: str,
    taxonomy: dict[str, Any] | None,
    eval_report: dict[str, Any] | None,
    license_id: str | None = None,
) -> str:
    """Render the README.md dataset card: YAML metadata, then a short human-readable page."""
    # Metadata block: the tags name simula; configs point the Hub's loader at the data file.
    metadata: dict[str, Any] = {
        "pretty_name": pretty_name,
        "tags": hub_tags(),
        "size_categories": [size_category(len(rows))],
        "configs": [{"config_name": "default", "data_files": [{"split": "train", "path": DATA_FILE}]}],
    }
    if license_id:
        metadata["license"] = license_id
    front = yaml.safe_dump(metadata, sort_keys=False, allow_unicode=True).strip()

    factors = [factor.get("name", "?") for factor in (taxonomy or {}).get("factors", [])]
    lines = [
        "---",
        front,
        "---",
        "",
        f"# {pretty_name}",
        "",
        f"This dataset was generated with [simula]({SIMULA_URL}) {__version__}, a library for synthetic "
        "data generation that maps out what a dataset should cover, samples each record's ingredients "
        "in code, and has every record checked by a critic model.",
        "",
        "## How it was made",
        "",
        "The description the dataset was generated from:",
        "",
        *[f"> {line}" if line.strip() else ">" for line in description.strip().splitlines()],
        "",
        f"- **Records:** {len(rows):,} ({output_format}), from `{source_name}`",
        f"- **Models:** strategic `{models.get('strategic', '?')}`, bulk `{models.get('bulk', '?')}`, "
        f"critic `{models.get('critic', '?')}`",
    ]
    if factors:
        lines.append(f"- **Factors of variation:** {', '.join(factors)} (the full tree is in `taxonomy.json`)")
    if eval_report:
        removed = [
            f"{eval_report[key]['removed_count']} {label}"
            for key, label in (("dedupe", "near-duplicates"), ("decontamination", "contaminated rows"))
            if isinstance(eval_report.get(key), dict) and isinstance(eval_report[key].get("removed_count"), int)
        ]
        if removed:
            lines.append(f"- **Evaluation:** removed {' and '.join(removed)}; the full report is in `eval_report.json`")
    lines += [
        "",
        "## Use it",
        "",
        "```python",
        "from datasets import load_dataset",
        "",
        f'ds = load_dataset("{repo_id}", split="train")',
        "```",
        "",
        "## Fields",
        "",
        "Each row holds the generated `record` and its lineage: `taxonomy_mix` (one node per factor, the "
        "ingredients the record was built from), `strategy_id`, `meta_prompt`, `generator_model`, "
        "`critic_verdicts` and `created_at`. The row shape is documented in "
        f"[Artifacts]({DOCS_URL}reference/artifacts.html).",
        "",
        "## Files",
        "",
        f"- `{DATA_FILE}`: the dataset",
        "- `taxonomy.json` and `strategies.json`: the plan the records were sampled from",
    ]
    if eval_report:
        lines.append("- `eval_report.json`: coverage, dedupe and decontamination results")
    return "\n".join(lines) + "\n"


def stage_for_hub(
    *,
    output_dir: Path,
    repo_id: str,
    pretty_name: str,
    description: str,
    output_format: str,
    models: dict[str, str],
    license_id: str | None = None,
) -> Path:
    """Write `<output_dir>/hub/` (the card, the dataset file, the plan files) and return its path."""
    owner, _, name = repo_id.partition("/")
    if not owner or not name or "/" in name:
        raise ValueError(f"--repo must be OWNER/NAME, got {repo_id!r}.")
    source = dataset_source(output_dir)
    rows = read_jsonl(source)
    if not rows:
        raise ValueError(f"{source.name} is empty; there is nothing to push.")

    # Start from an empty folder so a file left by an earlier push is never uploaded by mistake.
    stage = output_dir / HUB_DIR
    if stage.exists():
        shutil.rmtree(stage)
    ensure_dir(stage / Path(DATA_FILE).parent)
    shutil.copyfile(source, stage / DATA_FILE)
    for name in PLAN_FILES:
        path = artifact_path(output_dir, name)
        if path.exists():
            shutil.copyfile(path, stage / ARTIFACTS[name])

    card = build_card(
        repo_id=repo_id,
        pretty_name=pretty_name,
        description=description,
        output_format=output_format,
        models=models,
        rows=rows,
        source_name=source.name,
        taxonomy=read_json(artifact_path(output_dir, "taxonomy")),
        eval_report=read_json(artifact_path(output_dir, "eval")),
        license_id=license_id,
    )
    (stage / "README.md").write_text(card, encoding="utf-8")
    return stage


def resolve_hf_token() -> str | None:
    # Same rule as the provider key: read from the project-root .env. When it is not there,
    # huggingface_hub falls back to the login saved by `hf auth login`.
    return dotenv_values(Path.cwd() / ".env").get("HF_TOKEN")


def push_to_hub(stage: Path, repo_id: str, *, private: bool = False, api: Any = None) -> str:
    """Create the dataset repo if needed and upload the staged folder. Returns the dataset URL.
    `api` is a test seam: anything with huggingface_hub.HfApi's create_repo and upload_folder."""
    if api is None:
        try:
            from huggingface_hub import HfApi
        except ImportError as exc:
            raise RuntimeError(
                "simula push needs huggingface_hub: pip install \"simula-gen[hub]\""
            ) from exc
        api = HfApi(token=resolve_hf_token())
    api.create_repo(repo_id=repo_id, repo_type="dataset", private=private, exist_ok=True)
    api.upload_folder(
        folder_path=str(stage),
        repo_id=repo_id,
        repo_type="dataset",
        commit_message=f"Upload dataset generated with simula {__version__}",
    )
    return f"https://huggingface.co/datasets/{repo_id}"
