"""Latent address representation.

The hand features describe a pair's address evidence with string statistics
(Jaccard, cosine). What they cannot express is that two *different* strings are the
same place: "100 EXAMPLE AVE" vs "100 EXAMPLE AVENUE", or two renderings of
one block. A latent representation of the street string can, because it is learned
from co-occurrence rather than from edits.

The encoder is unsupervised (character n-gram TF-IDF -> truncated SVD) and is fitted
on the *training* window only, so attaching it to later records stays prefix-safe.
"""
from __future__ import annotations

from typing import Dict, Optional, Sequence

import numpy as np


class StreetEncoder:
    def __init__(self, n_components: int = 48, min_df: int = 3, seed: int = 13):
        from sklearn.decomposition import TruncatedSVD
        from sklearn.feature_extraction.text import TfidfVectorizer
        self.vec = TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 4),
                                   min_df=min_df, sublinear_tf=True)
        self.svd = TruncatedSVD(n_components=n_components, random_state=seed)
        self.ready = False

    def fit(self, streets: Sequence[str]) -> "StreetEncoder":
        uniq = sorted({s for s in streets if s})
        if len(uniq) < 50:
            return self
        X = self.vec.fit_transform(uniq)
        self.svd.fit(X)
        Z = self.svd.transform(X)
        norms = np.linalg.norm(Z, axis=1, keepdims=True) + 1e-9
        Z = Z / norms
        self.table: Dict[str, np.ndarray] = {s: Z[i] for i, s in enumerate(uniq)}
        self.ready = True
        return self

    def embed(self, street: str) -> Optional[np.ndarray]:
        if not self.ready or not street:
            return None
        v = self.table.get(street)
        if v is not None:
            return v
        # unseen street: project on the fly
        try:
            z = self.svd.transform(self.vec.transform([street]))[0]
        except Exception:
            return None
        n = float(np.linalg.norm(z)) + 1e-9
        return z / n


def attach(records, encoder: StreetEncoder) -> int:
    """Attach a unit-norm street embedding to every record that has a street."""
    n = 0
    for r in records:
        v = encoder.embed(r.addr.street)
        if v is not None:
            r.emb = v
            n += 1
    return n
