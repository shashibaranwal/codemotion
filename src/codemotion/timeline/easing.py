"""Easing functions (SPEC 5.5): pure ``f(p) -> float`` on [0, 1] with f(0) = 0 and f(1) = 1."""

from __future__ import annotations

import math
from collections.abc import Callable
from functools import lru_cache

Easing = Callable[[float], float]

_SETTLE_EPSILON = 1e-3


def _clamp01(p: float) -> float:
    return 0.0 if p <= 0.0 else 1.0 if p >= 1.0 else p


def linear(p: float) -> float:
    return _clamp01(p)


def ease_in_out_cubic(p: float) -> float:
    p = _clamp01(p)
    return 4 * p**3 if p < 0.5 else 1 - (-2 * p + 2) ** 3 / 2


def ease_out_cubic(p: float) -> float:
    p = _clamp01(p)
    return 1 - (1 - p) ** 3


def ease_out_back(p: float) -> float:
    p = _clamp01(p)
    c1 = 1.70158
    c3 = c1 + 1
    return 1 + c3 * (p - 1) ** 3 + c1 * (p - 1) ** 2


def ease_out_expo(p: float) -> float:
    p = _clamp01(p)
    return 1.0 if p == 1.0 else 1 - 2 ** (-10 * p)


def spring(stiffness: float, damping: float) -> Easing:
    """Closed-form damped spring (unit mass) from 0 to 1, time-scaled to settle at p = 1.

    The analytic solution is evaluated directly, so there's no integration drift. The
    small residual at the settle time is spread linearly so f(1) is exactly 1.
    """
    if stiffness <= 0 or damping <= 0:
        raise ValueError("spring stiffness and damping must be positive")
    w0 = math.sqrt(stiffness)
    zeta = damping / (2 * w0)

    if zeta < 1:
        wd = w0 * math.sqrt(1 - zeta**2)
        k = zeta * w0 / wd

        def x(t: float) -> float:
            return 1 - math.exp(-zeta * w0 * t) * (math.cos(wd * t) + k * math.sin(wd * t))

        def bound(t: float) -> float:
            return math.exp(-zeta * w0 * t) * math.sqrt(1 + k * k)

    elif zeta == 1:

        def x(t: float) -> float:
            return 1 - math.exp(-w0 * t) * (1 + w0 * t)

        def bound(t: float) -> float:
            return math.exp(-w0 * t) * (1 + w0 * t)

    else:
        root = math.sqrt(zeta**2 - 1)
        r1, r2 = -w0 * (zeta - root), -w0 * (zeta + root)

        def x(t: float) -> float:
            return 1 + (r2 * math.exp(r1 * t) - r1 * math.exp(r2 * t)) / (r1 - r2)

        def bound(t: float) -> float:
            return abs(r2 * math.exp(r1 * t) - r1 * math.exp(r2 * t)) / abs(r1 - r2)

    step = 1 / (w0 * 64)
    settle = step
    while bound(settle) > _SETTLE_EPSILON:
        settle += step
    residual = 1 - x(settle)

    def ease(p: float) -> float:
        p = _clamp01(p)
        if p == 1.0:
            return 1.0
        return x(p * settle) + p * residual

    return ease


EASINGS: dict[str, Easing] = {
    "linear": linear,
    "easeInOutCubic": ease_in_out_cubic,
    "easeOutCubic": ease_out_cubic,
    "easeOutBack": ease_out_back,
    "easeOutExpo": ease_out_expo,
}


def resolve_ease(name: str) -> Easing:
    """An easing by name: one of ``EASINGS`` or ``"spring:<stiffness>:<damping>"``."""
    if name in EASINGS:
        return EASINGS[name]
    kind, _, params = name.partition(":")
    if kind == "spring":
        stiffness, _, damping = params.partition(":")
        return _spring_cached(float(stiffness), float(damping))
    raise ValueError(f"unknown easing '{name}'")


@lru_cache(maxsize=64)
def _spring_cached(stiffness: float, damping: float) -> Easing:
    return spring(stiffness, damping)
