"""Logistic scorer with the same call interface as GBT.

The deferral effect has been measured with one model family. A reviewer is entitled to
ask whether it is a property of a boosted tree's calibration rather than of the
mechanism, so this gives a second family with identical plumbing: same features, same
candidate distribution, same thresholds, same replay. Only the scorer changes.
"""
from __future__ import annotations

import numpy as np


class Logit:
    def __init__(self, X, y, seed=13):
        from sklearn.linear_model import LogisticRegression
        from sklearn.pipeline import make_pipeline
        from sklearn.preprocessing import StandardScaler

        self.m = make_pipeline(
            StandardScaler(),
            LogisticRegression(max_iter=4000, C=1.0, random_state=seed,
                               class_weight=None, solver="lbfgs"),
        )
        self.m.fit(np.asarray(X, dtype=np.float64), np.asarray(y))

    def __call__(self, X):
        return self.m.predict_proba(np.asarray(X, dtype=np.float64))[:, 1]
