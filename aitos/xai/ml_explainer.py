"""TradeOutcomeClassifier — closes the gap flagged in
``xai_techniques.py``: SHAP/feature-importance explanations need a
trained model, and none existed. This is a real (if simple) one: an
online logistic classifier (``SGDClassifier(loss="log_loss")``) predicting
win/loss from the Opportunity Scanner's component scores, retrained
incrementally via ``partial_fit`` as trades close — same "trains as data
arrives" pattern as ``TabularBanditRLScorer``.

Explanations use ``shap.LinearExplainer``, which is exact (not sampled)
for linear/logistic models and cheap enough to run per-trade. Until
enough labeled trades have accumulated, ``is_ready`` is False and
``explain`` returns an empty result rather than a misleadingly confident
one from a barely-trained model — small-sample SHAP values are noise, not
insight.

Attention visualization and saliency maps remain unimplemented — both
need an actual neural network (transformer / CNN respectively), which is
a different kind of model than this one and out of scope here.
"""

from __future__ import annotations

from typing import Dict, List, Optional

import numpy as np
from sklearn.linear_model import SGDClassifier

# Fixed feature order — must match across partial_fit/predict/explain calls.
# Mirrors the Opportunity Scanner's DEFAULT_WEIGHTS keys (spec §32.1's ten dimensions).
FEATURE_ORDER: List[str] = [
    "trend_strength", "liquidity_quality", "order_flow_bias", "auction_context",
    "volatility", "market_regime", "lead_lag", "funding_rate", "open_interest_trend", "rl_confidence",
]

DEFAULT_MIN_SAMPLES = 30
BACKGROUND_BUFFER_SIZE = 200


def _vectorize(component_scores: Dict[str, float]) -> np.ndarray:
    return np.array([[component_scores.get(f, 5.0) for f in FEATURE_ORDER]], dtype=float)


class TradeOutcomeClassifier:
    def __init__(self, min_samples_for_ready: int = DEFAULT_MIN_SAMPLES) -> None:
        self._model = SGDClassifier(loss="log_loss", random_state=0)
        self._min_samples = min_samples_for_ready
        self._n_samples_seen = 0
        self._classes_seen: set = set()
        self._background: List[List[float]] = []  # recent feature vectors, for the SHAP background/masker

    @property
    def n_samples_seen(self) -> int:
        return self._n_samples_seen

    @property
    def is_ready(self) -> bool:
        """Both classes must have been observed at least once (SGDClassifier
        can't predict probabilities for a single-class fit) and the sample
        count must clear the confidence threshold."""
        return self._n_samples_seen >= self._min_samples and self._classes_seen == {0, 1}

    def partial_fit(self, component_scores: Dict[str, float], won: bool) -> None:
        X = _vectorize(component_scores)
        y = np.array([1 if won else 0])

        if self._n_samples_seen == 0:
            self._model.partial_fit(X, y, classes=np.array([0, 1]))
        else:
            self._model.partial_fit(X, y)

        self._n_samples_seen += 1
        self._classes_seen.add(int(y[0]))
        self._background.append(X[0].tolist())
        if len(self._background) > BACKGROUND_BUFFER_SIZE:
            self._background.pop(0)

    def predict_win_probability(self, component_scores: Dict[str, float]) -> Optional[float]:
        if not self.is_ready:
            return None
        X = _vectorize(component_scores)
        proba = self._model.predict_proba(X)[0]
        # predict_proba's column order follows self._model.classes_, not a fixed [0,1] assumption.
        win_index = list(self._model.classes_).index(1)
        return round(float(proba[win_index]), 4)

    def explain(self, component_scores: Dict[str, float]) -> Dict[str, float]:
        """Real per-feature SHAP contributions to the win-probability
        prediction (in log-odds space, via ``LinearExplainer``). Returns an
        empty dict if the model isn't ``is_ready`` yet — see module
        docstring for why that's the honest answer rather than a guess."""
        if not self.is_ready:
            return {}

        import shap  # imported lazily — only needed once a model actually exists to explain

        background = np.array(self._background[-50:])  # a sample is plenty for a linear model's background
        explainer = shap.LinearExplainer(self._model, background)
        X = _vectorize(component_scores)
        shap_values = explainer.shap_values(X)[0]
        return {feature: round(float(value), 4) for feature, value in zip(FEATURE_ORDER, shap_values)}
