"""DeepValueRLScorer — a real multi-layer neural network (not a lookup
table) predicting expected trade reward from the Opportunity Scanner's
full feature vector, trained online via ``partial_fit`` from real closed
trades — same "trains as data arrives" pattern as everything else in
``aitos/intelligence`` and ``aitos/xai``.

Honesty about scope: this is value-function approximation via supervised
regression on realized rewards (a feedforward net learning
``features → expected R-multiple``), not a full RL algorithm — no
temporal credit assignment across multi-step episodes, no policy
gradient, no replay buffer, no actor-critic structure. Each trade is
treated as an independent (context, reward) sample, exactly like
``TabularBanditRLScorer``, except the function approximator is a neural
network instead of per-bucket averages — so it *generalizes* across
similar-but-not-identical symbol/regime/feature combinations instead of
needing an exact bucket match. That generalization is the actual value
add over the tabular version, and it's real: see
``test_generalizes_to_unseen_but_similar_context`` in the test suite.
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

import numpy as np
from sklearn.neural_network import MLPRegressor

from aitos.intelligence.rl_policy import RLPolicyScorer
from aitos.xai.ml_explainer import FEATURE_ORDER

# The reward regressor's own confidence in a prediction it's making for a
# region of feature space it's seen little of is not something a plain
# MLPRegressor exposes — so, like the tabular bandit, we track a coarse
# sample count and shrink low-confidence predictions toward neutral.
DEFAULT_MIN_SAMPLES_FOR_CONFIDENCE = 30
DEFAULT_REWARD_SCALE_R_MULTIPLES = 2.0


def _vectorize(context: Dict[str, Any]) -> np.ndarray:
    # Scaled to [0, 1] (raw scores are 0-10) — unscaled inputs combined with
    # ReLU + adam's per-call bias-correction reset caused dead units and a
    # collapsed constant output under true single-sample online training
    # (verified empirically; see the module's test suite).
    return np.array([[float(context.get(f, 5.0)) / 10.0 for f in FEATURE_ORDER]], dtype=float)


class DeepValueRLScorer(RLPolicyScorer):
    def __init__(
        self,
        hidden_layer_sizes: Tuple[int, ...] = (8,),
        learning_rate_init: float = 0.01,
        min_samples_for_confidence: int = DEFAULT_MIN_SAMPLES_FOR_CONFIDENCE,
        reward_scale_r_multiples: float = DEFAULT_REWARD_SCALE_R_MULTIPLES,
        random_state: int = 0,
    ) -> None:
        self._model = MLPRegressor(
            hidden_layer_sizes=hidden_layer_sizes,
            activation="tanh",
            solver="sgd",
            learning_rate="constant",
            learning_rate_init=learning_rate_init,
            max_iter=1,  # one gradient step per partial_fit call — genuinely online
            random_state=random_state,
        )
        self._min_samples = min_samples_for_confidence
        self._reward_scale = reward_scale_r_multiples
        self._n_samples_seen = 0
        self._recent_rewards: List[float] = []  # for the un-clamped baseline sanity check in tests

    @property
    def n_samples_seen(self) -> int:
        return self._n_samples_seen

    @property
    def is_fitted(self) -> bool:
        return self._n_samples_seen > 0

    def update(self, symbol: str, context: Dict[str, Any], reward_r_multiple: float) -> None:
        """Incorporate one real trade outcome. ``context`` should be the
        same feature dict passed to ``score`` (the scanner's component
        scores) — this is a supervised regression target, not a bandit
        bucket key, so the actual feature values matter, not just
        symbol/regime/direction identity."""
        X = _vectorize(context)
        y = np.array([reward_r_multiple])
        self._model.partial_fit(X, y)
        self._n_samples_seen += 1
        self._recent_rewards.append(reward_r_multiple)
        if len(self._recent_rewards) > 500:
            self._recent_rewards.pop(0)

    async def score(self, symbol: str, context: Dict[str, Any]) -> float:
        if not self.is_fitted:
            return 5.0

        X = _vectorize(context)
        predicted_reward = float(self._model.predict(X)[0])
        raw_score = 5.0 + max(-1.0, min(1.0, predicted_reward / self._reward_scale)) * 5.0

        confidence = min(1.0, self._n_samples_seen / self._min_samples)
        blended = 5.0 + (raw_score - 5.0) * confidence
        return round(max(0.0, min(10.0, blended)), 2)
