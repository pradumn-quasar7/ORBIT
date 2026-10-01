"""Deterministic hashing embedder (signed feature hashing of unigrams + bigrams).

Uses a stable digest rather than Python's ``hash`` (which is salted per process), so
the same text embeds identically across runs — required for reproducible benchmarks.
"""
import hashlib
import math
import re
from typing import List, Sequence

from backend.app.providers.base import EmbeddingProvider

_TOKEN = re.compile(r"[a-z0-9]+")
_STOP = frozenset("the a an of to is was at on in it and or for from by with what where did do".split())


def tokens(text: str) -> List[str]:
    return [t for t in _TOKEN.findall(text.lower().replace("_", " ")) if t not in _STOP]


class HashingEmbeddingProvider(EmbeddingProvider):
    name = "hashing-bow-v1"

    def __init__(self, dim: int = 512):
        self.dim = dim

    def _bucket(self, feature: str):
        digest = hashlib.md5(feature.encode()).digest()
        index = int.from_bytes(digest[:4], "little") % self.dim
        sign = 1.0 if digest[4] & 1 else -1.0
        return index, sign

    def embed(self, texts: Sequence[str]) -> List[List[float]]:
        out = []
        for text in texts:
            vec = [0.0] * self.dim
            toks = tokens(text)
            for feature in toks + [f"{a}_{b}" for a, b in zip(toks, toks[1:])]:
                i, sign = self._bucket(feature)
                vec[i] += sign
            norm = math.sqrt(sum(v * v for v in vec)) or 1.0
            out.append([v / norm for v in vec])
        return out
