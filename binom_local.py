from __future__ import annotations

from dataclasses import dataclass
import math
import numpy as np


@dataclass(frozen=True)
class _BinomDistribution:
    n: int
    p: float

    def __post_init__(self) -> None:
        if self.n < 0:
            raise ValueError("n doit être positif ou nul")
        if not 0.0 <= self.p <= 1.0:
            raise ValueError("p doit être compris entre 0 et 1")

    def _probabilities(self) -> np.ndarray:
        n, p = int(self.n), float(self.p)
        probs = np.zeros(n + 1, dtype=float)
        if p == 0.0:
            probs[0] = 1.0
            return probs
        if p == 1.0:
            probs[n] = 1.0
            return probs

        mode = min(n, int(math.floor((n + 1) * p)))
        log_mode = (
            math.lgamma(n + 1)
            - math.lgamma(mode + 1)
            - math.lgamma(n - mode + 1)
            + mode * math.log(p)
            + (n - mode) * math.log1p(-p)
        )
        probs[mode] = math.exp(log_mode)

        ratio_down = (1.0 - p) / p
        for k in range(mode, 0, -1):
            probs[k - 1] = probs[k] * k / (n - k + 1) * ratio_down

        ratio_up = p / (1.0 - p)
        for k in range(mode, n):
            probs[k + 1] = probs[k] * (n - k) / (k + 1) * ratio_up

        total = float(probs.sum())
        if total > 0:
            probs /= total
        return probs

    def cdf(self, k: int | float) -> float:
        idx = int(math.floor(k))
        if idx < 0:
            return 0.0
        if idx >= self.n:
            return 1.0
        return float(self._probabilities()[: idx + 1].sum())

    def ppf(self, q: float) -> float:
        q = float(q)
        if q <= 0.0:
            return 0.0
        if q >= 1.0:
            return float(self.n)
        cumulative = np.cumsum(self._probabilities())
        return float(np.searchsorted(cumulative, q, side="left"))


def binom(n: int, p: float) -> _BinomDistribution:
    return _BinomDistribution(int(n), float(p))
