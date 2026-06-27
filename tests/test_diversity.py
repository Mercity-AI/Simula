from pathlib import Path

import numpy as np

from simula import diversity
from simula.diversity import embedding_diversity
from simula.utils import record_to_text


def _fake_embed(model_name: str, texts: list[str]):
    return np.array([[float(len(text)), float(index)] for index, text in enumerate(texts)], dtype="float32")


def test_record_to_text_uses_dotted_field() -> None:
    record = {"query": "find hotels", "extraction": {"intent": "travel"}}
    assert record_to_text(record, "query") == "find hotels"  # plain top-level key
    assert record_to_text(record, "extraction.intent") == "travel"  # nested
    assert record_to_text(record, "$.query") == "find hotels"  # leading $. tolerated


def test_record_to_text_unmatched_field_falls_back_to_json() -> None:
    record = {"query": "find hotels"}
    assert record_to_text(record, "missing") == '{"query": "find hotels"}'


def test_embedding_cache_reuses_existing_vectors(tmp_path: Path, monkeypatch) -> None:
    cache_path = tmp_path / "embeddings.cache.npz"
    calls = {"n": 0}

    def counting_embed(model_name: str, texts: list[str]):
        calls["n"] += 1
        return _fake_embed(model_name, texts)

    # Patch the model loader so the test stays offline; the cache must mean it runs only once.
    monkeypatch.setattr(diversity, "_embed", counting_embed)
    report = embedding_diversity(["alpha", "beta", "gamma"], "fake-embed", cache_path, sample_cap=3)
    assert report["sample_size"] == 3
    assert calls["n"] == 1

    report = embedding_diversity(["alpha", "beta", "gamma"], "fake-embed", cache_path, sample_cap=3)
    assert report["sample_size"] == 3
    assert calls["n"] == 1
