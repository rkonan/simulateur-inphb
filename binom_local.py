from __future__ import annotations

from math import floor
from typing import Final


class _Binomiale:
    def __init__(self, n: int, p: float) -> None:
        self.n: Final[int] = int(n)
        self.p: Final[float] = float(p)

        if self.n < 0:
            raise ValueError("n doit être supérieur ou égal à 0.")

        if not 0.0 <= self.p <= 1.0:
            raise ValueError("p doit être compris entre 0 et 1.")

        self._probabilites = self._construire_distribution()
        self._cumul = self._construire_cumul()

    def _construire_distribution(self) -> list[float]:
        if self.p == 0.0:
            return [1.0] + [0.0] * self.n

        if self.p == 1.0:
            return [0.0] * self.n + [1.0]

        q = 1.0 - self.p
        mode = min(
            self.n,
            floor((self.n + 1) * self.p),
        )

        probabilites = [0.0] * (self.n + 1)
        probabilites[mode] = 1.0

        for k in range(mode, 0, -1):
            probabilites[k - 1] = (
                probabilites[k]
                * k
                / (self.n - k + 1)
                * q
                / self.p
            )

        for k in range(mode, self.n):
            probabilites[k + 1] = (
                probabilites[k]
                * (self.n - k)
                / (k + 1)
                * self.p
                / q
            )

        total = sum(probabilites)

        if total <= 0.0:
            raise ArithmeticError(
                "Impossible de normaliser la distribution binomiale."
            )

        return [
            valeur / total
            for valeur in probabilites
        ]

    def _construire_cumul(self) -> list[float]:
        cumul = []
        somme = 0.0

        for probabilite in self._probabilites:
            somme += probabilite
            cumul.append(min(somme, 1.0))

        return cumul

    def cdf(self, k: int | float) -> float:
        """Retourne P(X <= k)."""
        indice = floor(float(k))

        if indice < 0:
            return 0.0

        if indice >= self.n:
            return 1.0

        return float(self._cumul[indice])

    def ppf(self, quantile: float) -> float:
        """Retourne le plus petit k tel que P(X <= k) >= quantile."""
        q = float(quantile)

        if not 0.0 <= q <= 1.0:
            raise ValueError(
                "Le quantile doit être compris entre 0 et 1."
            )

        if q == 0.0:
            return 0.0

        for k, cumul in enumerate(self._cumul):
            if cumul >= q:
                return float(k)

        return float(self.n)


def binom(
    n: int,
    p: float,
) -> _Binomiale:
    """Remplacement local minimal de scipy.stats.binom."""
    return _Binomiale(n=n, p=p)