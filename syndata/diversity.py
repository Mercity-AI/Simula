"""Embedding-based diversity scoring (optional eval enrichment).

The heavy numerical deps (numpy, scikit-learn, sentence-transformers) are the optional `[diversity]`
extra, imported at module top behind one guard. `run_evaluation` only imports this module when
diversity is enabled, so a base install that never enables diversity never pays for these imports.
"""

from __future__ import annotations

import hashlib
import random
from functools import lru_cache
from pathlib import Path
from typing import Any

try:
    import numpy as np
    from sentence_transformers import SentenceTransformer
    from sklearn.metrics.pairwise import cosine_distances
except ImportError as exc:  # pragma: no cover - exercised only without the optional extra installed.
    raise ImportError(
        "Embedding diversity needs the optional deps (numpy, scikit-learn, sentence-transformers). "
        "Install them with: pip install 'syndata[diversity]'"
    ) from exc

from .utils import ensure_dir


def embedding_diversity(
    texts: list[str],
    model_name: str,
    cache_path: Path,
    *,
    sample_cap: int = 1000,
    k_local: int = 10,
) -> dict[str, float | int | str]:
    """Score global and local diversity over `texts` using cosine distance between embeddings."""
    if len(texts) < 2:
        return {"global_diversity": 0.0, "local_diversity": 0.0, "sample_size": len(texts), "cache_path": str(cache_path)}

    # Diversity is O(n^2); sample deterministically past the cap. The metric is then an estimate
    # from this subset (reported via sample_size/sample_cap).
    if len(texts) > sample_cap:
        print(f"[diversity] {len(texts)} texts exceed sample_cap={sample_cap}; computing on a random sample of {sample_cap}.")
        texts = random.Random(0).sample(texts, sample_cap)

    embeddings = _load_embeddings(texts, model_name, cache_path)
    distances = cosine_distances(embeddings)
    n = distances.shape[0]

    # global_diversity: mean pairwise distance over all off-diagonal entries (~np.eye masks the
    # zero self-distances on the diagonal).
    global_div = float(distances[~np.eye(n, dtype=bool)].mean())
    # local_diversity: mean distance to each row's k nearest neighbours. Setting the diagonal to inf
    # drops self-matches before sorting so the k smallest are genuine neighbours.
    np.fill_diagonal(distances, np.inf)
    local_k = min(k_local, n - 1)
    local_div = float(np.sort(distances, axis=1)[:, :local_k].mean())
    return {
        "global_diversity": global_div,
        "local_diversity": local_div,
        "sample_size": n,
        "sample_cap": sample_cap,
        "k_local": local_k,
        "cache_path": str(cache_path),
    }


def _load_embeddings(texts: list[str], model_name: str, cache_path: Path) -> Any:
    # Load cached vectors, embed only the cache misses, persist the merged sidecar, and return the
    # full matrix in `texts` order. Caching makes repeated evaluate runs cheap.
    cache = _read_cache(cache_path)
    keys = [_cache_key(model_name, text) for text in texts]
    missing = [text for key, text in zip(keys, texts) if key not in cache]
    if missing:
        vectors = _embed(model_name, missing).astype("float32")
        for text, vector in zip(missing, vectors):
            cache[_cache_key(model_name, text)] = vector
        _write_cache(cache_path, cache)
    return np.vstack([cache[key] for key in keys]).astype("float32")


@lru_cache(maxsize=1)
def _load_model(model_name: str) -> Any:
    # Load the embedding model once per process and reuse it across calls. Lazy (first _embed only,
    # never at import); diversity always needs the model, so caching it has no downside. Tests patch
    # _embed directly, so this loader stays offline in the suite.
    return SentenceTransformer(model_name)


def _embed(model_name: str, texts: list[str]) -> Any:
    return _load_model(model_name).encode(texts, convert_to_numpy=True, show_progress_bar=False)


def _cache_key(model_name: str, text: str) -> str:
    return hashlib.sha256(f"{model_name}\0{text}".encode("utf-8")).hexdigest()


def _read_cache(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    data = np.load(path, allow_pickle=False)
    keys = [str(key) for key in data["keys"]]
    embeddings = data["embeddings"]
    return {key: embeddings[index] for index, key in enumerate(keys)}


def _write_cache(path: Path, cache: dict[str, Any]) -> None:
    ensure_dir(path.parent)
    # Keys are sha256 hexdigests (always 64 chars); a fixed-width unicode dtype avoids object arrays
    # (which np.load(allow_pickle=False) refuses) without truncating keys.
    keys = np.array(list(cache.keys()), dtype="<U64")
    embeddings = np.vstack(list(cache.values())).astype("float32") if cache else np.empty((0, 0), dtype="float32")
    np.savez_compressed(path, keys=keys, embeddings=embeddings)
