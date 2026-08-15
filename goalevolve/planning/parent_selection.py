from __future__ import annotations

from dataclasses import dataclass
from math import exp
from random import Random
from typing import Sequence

from ..core.models import Parent


DEFAULT_TAG_COMPATIBILITY: dict[str, dict[str, float]] = {
    "timing": {"timing": 1.00, "power": 0.30, "area": 0.45, "integration": 0.75, "unknown": 0.50},
    "power": {"timing": 0.30, "power": 1.00, "area": 0.60, "integration": 0.75, "unknown": 0.50},
    "area": {"timing": 0.45, "power": 0.60, "area": 1.00, "integration": 0.75, "unknown": 0.50},
    "integration": {"timing": 0.75, "power": 0.75, "area": 0.75, "integration": 1.00, "unknown": 0.50},
    "unknown": {"timing": 0.50, "power": 0.50, "area": 0.50, "integration": 0.50, "unknown": 0.50},
}


@dataclass(frozen=True)
class ParentSelectionSettings:
    portfolio_size: int = 3
    plateau_rounds: int = 3
    improvement_threshold: float = 1.0e-6
    history_window: int = 10
    beta: float = 0.25
    gamma: float = 0.50
    temperature_initial: float = 0.75
    temperature_decay: float = 0.85
    temperature_floor: float = 0.15
    seed: int = 0


@dataclass(frozen=True)
class ParentPortfolioEntry:
    parent: Parent
    mechanism_family: str

    @property
    def tag(self) -> str:
        return normalize_tag(self.mechanism_family)


def normalize_tag(value: str) -> str:
    """Map repository mechanism vocabulary to the fixed Eq. 5 tag alphabet."""
    tag = value.lower()
    if "integrat" in tag:
        return "integration"
    if "power" in tag or "leak" in tag or "dynamic" in tag:
        return "power"
    if "area" in tag or "rmp" in tag:
        return "area"
    if "timing" in tag or "tns" in tag or "setup" in tag:
        return "timing"
    return "unknown"


class ParentSelectionPolicy:
    """Equation 4 sampler over a portfolio of already validated parents."""

    def __init__(self, settings: ParentSelectionSettings | None = None) -> None:
        self.settings = settings or ParentSelectionSettings()

    def temperature(self, plateau_iteration: int) -> float:
        return max(
            self.settings.temperature_floor,
            self.settings.temperature_initial
            * self.settings.temperature_decay ** max(0, plateau_iteration),
        )

    def probabilities(
        self,
        *,
        portfolio: Sequence[ParentPortfolioEntry],
        bottleneck_tag: str,
        selection_history: Sequence[str],
        plateau_iteration: int,
    ) -> dict[str, float]:
        if not portfolio:
            return {}
        champion_distance = min(entry.parent.goal_distance for entry in portfolio)
        history = tuple(selection_history[-self.settings.history_window :])
        bottleneck = normalize_tag(bottleneck_tag)
        temperature = self.temperature(plateau_iteration)
        scores: dict[str, float] = {}
        for entry in portfolio:
            tag = entry.tag
            count = sum(normalize_tag(previous) == tag for previous in history)
            diversity = 1.0 - count / self.settings.history_window
            alignment = DEFAULT_TAG_COMPATIBILITY[bottleneck][tag]
            score = (
                exp(-(entry.parent.goal_distance - champion_distance) / temperature)
                * (1.0 + self.settings.beta * diversity)
                * (1.0 + self.settings.gamma * alignment)
            )
            scores[entry.parent.parent_id] = score
        normalizer = sum(scores.values())
        return {
            parent_id: score / normalizer
            for parent_id, score in scores.items()
        }

    def sample(
        self,
        *,
        portfolio: Sequence[ParentPortfolioEntry],
        bottleneck_tag: str,
        selection_history: Sequence[str],
        plateau_iteration: int,
        random_source: Random,
    ) -> ParentPortfolioEntry:
        probabilities = self.probabilities(
            portfolio=portfolio,
            bottleneck_tag=bottleneck_tag,
            selection_history=selection_history,
            plateau_iteration=plateau_iteration,
        )
        threshold = random_source.random()
        cumulative = 0.0
        for entry in portfolio:
            cumulative += probabilities[entry.parent.parent_id]
            if threshold <= cumulative:
                return entry
        return portfolio[-1]
