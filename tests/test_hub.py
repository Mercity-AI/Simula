from pathlib import Path

import pytest
import yaml

from simula import __version__
from simula.cli import main
from simula.hub import DATA_FILE, build_card, hub_tags, push_to_hub, size_category, stage_for_hub
from simula.utils import artifact_path, read_jsonl
from tests.test_pipeline import write_config


def _card_metadata(card: str) -> dict:
    # The card opens with a YAML block between two --- lines.
    _, front, _ = card.split("---\n", 2)
    return yaml.safe_load(front)


def test_card_carries_the_simula_library_tag() -> None:
    card = build_card(
        repo_id="me/qa",
        pretty_name="qa",
        description="Tiny QA pairs.\n\nSecond paragraph.",
        output_format="json",
        models={"strategic": "fake", "bulk": "fake", "critic": "fake"},
        rows=[{"id": "item-0"}] * 3,
        source_name="dataset.final.jsonl",
        taxonomy={"factors": [{"name": "topic"}, {"name": "tone"}]},
        eval_report={"count": 3, "dedupe": {"removed_count": 1, "removed_ids": ["x"]}},
        license_id="mit",
    )
    meta = _card_metadata(card)
    assert meta["tags"] == ["simula", f"simula-{__version__}", "synthetic"]
    assert meta["license"] == "mit"
    assert meta["size_categories"] == ["n<1K"]
    assert meta["configs"][0]["data_files"][0]["path"] == DATA_FILE
    assert 'load_dataset("me/qa", split="train")' in card
    assert "topic, tone" in card
    assert "1 near-duplicates" in card
    assert "> Second paragraph." in card


def test_hub_tags_lead_with_the_library_key() -> None:
    # The Hub matches the plain `simula` tag to the registered library; keep it first and unversioned.
    assert hub_tags()[0] == "simula"


@pytest.mark.parametrize(
    ("count", "label"), [(0, "n<1K"), (999, "n<1K"), (1_000, "1K<n<10K"), (250_000, "100K<n<1M"), (10**8, "n>10M")]
)
def test_size_category(count: int, label: str) -> None:
    assert size_category(count) == label


def test_dry_run_stages_card_and_data_but_not_logs(tmp_path: Path) -> None:
    config = write_config(tmp_path)
    assert main(["run", str(config), "--quiet"]) == 0
    assert main(["push", str(config), "--repo", "me/qa", "--dry-run"]) == 0

    stage = tmp_path / "run" / "hub"
    staged = {str(path.relative_to(stage)) for path in stage.rglob("*") if path.is_file()}
    assert {"README.md", DATA_FILE, "taxonomy.json", "strategies.json", "eval_report.json"} <= staged
    assert not any("llm_calls" in name or "run_state" in name or ".env" in name for name in staged)
    # The evaluated file is shared when it exists, and its rows keep their lineage.
    rows = read_jsonl(stage / DATA_FILE)
    assert rows == read_jsonl(artifact_path(tmp_path / "run", "evaluated"))
    assert rows and rows[0]["taxonomy_mix"]
    assert "simula" in _card_metadata((stage / "README.md").read_text(encoding="utf-8"))["tags"]


def test_push_needs_a_dataset(tmp_path: Path) -> None:
    config = write_config(tmp_path)
    assert main(["push", str(config), "--repo", "me/qa", "--dry-run"]) == 1


def test_push_rejects_a_bad_repo_id(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="OWNER/NAME"):
        stage_for_hub(
            output_dir=tmp_path, repo_id="no-owner", pretty_name="x", description="x",
            output_format="json", models={},
        )


def test_push_uploads_the_staged_folder(tmp_path: Path) -> None:
    calls = []

    class FakeApi:
        def create_repo(self, **kwargs):
            calls.append(("create_repo", kwargs))

        def upload_folder(self, **kwargs):
            calls.append(("upload_folder", kwargs))

    stage = tmp_path / "hub"
    stage.mkdir()
    url = push_to_hub(stage, "me/qa", private=True, api=FakeApi())
    assert url == "https://huggingface.co/datasets/me/qa"
    assert calls[0] == ("create_repo", {"repo_id": "me/qa", "repo_type": "dataset", "private": True, "exist_ok": True})
    assert calls[1][1]["folder_path"] == str(stage)
    assert calls[1][1]["repo_type"] == "dataset"
