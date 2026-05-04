import os
from functools import lru_cache

from sentence_transformers import SentenceTransformer

EMBED_MODEL_NAME = os.getenv("SOC_CLAW_EMBED_MODEL",
                              "BAAI/bge-small-en-v1.5")


@lru_cache(maxsize=1)
def _load_model() -> SentenceTransformer:
    return SentenceTransformer(EMBED_MODEL_NAME)


def embed(text: str) -> list[float]:
    text = (text or "").strip()
    if not text:
        return []
    model = _load_model()
    vector = model.encode([text], normalize_embeddings=True)[0]
    return vector.tolist()
