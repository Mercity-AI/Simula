from __future__ import annotations

import hashlib
import random
from pathlib import Path
from typing import Any

from .utils import ensure_dir


class EmbeddingClient:
    def __init__(self, model_name: str):
        self.model_name = model_name
        self._model: Any = None

    def _load(self) -> Any:
        if self._model is None:
            try:
                from sentence_transformers import SentenceTransformer
            except ImportError as exc:
                raise ImportError(
                    "Embedding diversity needs sentence-transformers. "
                    "Install it with: pip install 'syndata[diversity]'"
                ) from exc
            self._model = SentenceTransformer(self.model_name)
        return self._model

    def encode(self, texts: list[str]) -> Any:
        return self._load().encode(texts, convert_to_numpy=True, show_progress_bar=False)


def embedding_diversity(
    texts: list[str],
    model_name: str,
    cache_path: Path,
    *,
    sample_cap: int = 1000,
    k_local: int = 10,
    embedder: EmbeddingClient | None = None,
) -> dict[str, float | int | str]:
    try:
        import numpy as np
        from sklearn.metrics.pairwise import cosine_distances
    except ImportError as exc:  # diversity is optional; its heavy deps live in an optional extra.
        raise ImportError(
            "Embedding diversity needs numpy and scikit-learn. "
            "Install them with: pip install 'syndata[diversity]'"
        ) from exc

    if len(texts) < 2:
        return {"global_diversity": 0.0, "local_diversity": 0.0, "sample_size": len(texts), "cache_path": str(cache_path)}

    # Diversity can be expensive, so sample deterministically after text extraction. The reported
    # metric is then an estimate from this subset (see sample_size/sample_cap in the result).
    if len(texts) > sample_cap:
        print(f"[diversity] {len(texts)} texts exceed sample_cap={sample_cap}; computing on a random sample of {sample_cap}.")
        texts = random.Random(0).sample(texts, sample_cap)

    cache = _load_cache(cache_path)
    keys = [_cache_key(model_name, text) for text in texts]
    missing = [text for key, text in zip(keys, texts) if key not in cache]

    # Encode only cache misses and persist the merged sidecar for future evaluate runs.
    if missing:
        client = embedder or EmbeddingClient(model_name)
        embeddings = client.encode(missing).astype("float32")
        for text, embedding in zip(missing, embeddings):
            cache[_cache_key(model_name, text)] = embedding
        _save_cache(cache_path, cache)

    embs = np.vstack([cache[key] for key in keys]).astype("float32")
    dists = cosine_distances(embs)
    n = dists.shape[0]
    global_div = float(dists[~np.eye(n, dtype=bool)].mean())
    np.fill_diagonal(dists, np.inf)
    local_k = min(k_local, n - 1)
    local_div = float(np.sort(dists, axis=1)[:, :local_k].mean())
    return {
        "global_diversity": global_div,
        "local_diversity": local_div,
        "sample_size": n,
        "sample_cap": sample_cap,
        "k_local": local_k,
        "cache_path": str(cache_path),
    }


def _cache_key(model_name: str, text: str) -> str:
    return hashlib.sha256(f"{model_name}\0{text}".encode("utf-8")).hexdigest()


def _load_cache(path: Path) -> dict[str, Any]:
    import numpy as np

    if not path.exists():
        return {}
    data = np.load(path, allow_pickle=False)
    keys = [str(key) for key in data["keys"]]
    embeddings = data["embeddings"]
    return {key: embeddings[index] for index, key in enumerate(keys)}


def _save_cache(path: Path, cache: dict[str, Any]) -> None:
    import numpy as np

    ensure_dir(path.parent)
    key_list = list(cache.keys())
    # Size the unicode dtype to the longest key so keys are never silently truncated (and we avoid
    # object arrays, which np.load(allow_pickle=False) would refuse). sha256 keys are 64 chars today.
    width = max((len(key) for key in key_list), default=1)
    keys = np.array(key_list, dtype=f"<U{width}")
    embeddings = np.vstack(list(cache.values())).astype("float32") if cache else np.empty((0, 0), dtype="float32")
    np.savez_compressed(path, keys=keys, embeddings=embeddings)
