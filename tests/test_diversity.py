from pathlib import Path

import numpy as np

from syndata.diversity import embedding_diversity
from syndata.utils import record_to_text


class FakeEmbedder:
    def __init__(self) -> None:
        self.calls = 0

    def encode(self, texts: list[str]):
        self.calls += 1
        return np.array([[float(len(text)), float(index)] for index, text in enumerate(texts)], dtype="float32")


def test_record_to_text_uses_dotted_field() -> None:
    record = {"query": "find hotels", "extraction": {"intent": "travel"}}
    assert record_to_text(record, "query") == "find hotels"  # plain top-level key
    assert record_to_text(record, "extraction.intent") == "travel"  # nested
    assert record_to_text(record, "$.query") == "find hotels"  # leading $. tolerated


def test_record_to_text_unmatched_field_falls_back_to_json() -> None:
    record = {"query": "find hotels"}
    assert record_to_text(record, "missing") == '{"query": "find hotels"}'


def test_embedding_cache_reuses_existing_vectors(tmp_path: Path) -> None:
    cache_path = tmp_path / "embeddings.cache.npz"
    embedder = FakeEmbedder()
    report = embedding_diversity(["alpha", "beta", "gamma"], "fake-embed", cache_path, embedder=embedder, sample_cap=3)
    assert report["sample_size"] == 3
    assert embedder.calls == 1

    report = embedding_diversity(["alpha", "beta", "gamma"], "fake-embed", cache_path, embedder=embedder, sample_cap=3)
    assert report["sample_size"] == 3
    assert embedder.calls == 1
