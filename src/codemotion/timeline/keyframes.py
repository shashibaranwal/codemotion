"""Keyframe tracks: a property's value as a function of the frame index."""

from __future__ import annotations

import bisect
import itertools
from dataclasses import dataclass, field

from codemotion.timeline.easing import Easing, linear


@dataclass(frozen=True)
class Keyframe:
    frame: float
    value: float
    easing: Easing = linear  # shapes the segment arriving at this keyframe


@dataclass(frozen=True)
class Track:
    keyframes: tuple[Keyframe, ...]
    _frames: tuple[float, ...] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        if not self.keyframes:
            raise ValueError("a track needs at least one keyframe")
        frames = tuple(k.frame for k in self.keyframes)
        if any(b < a for a, b in itertools.pairwise(frames)):
            raise ValueError("keyframes must be in frame order")
        object.__setattr__(self, "_frames", frames)

    @classmethod
    def of(cls, *keys: tuple[float, float] | tuple[float, float, Easing]) -> Track:
        return cls(tuple(Keyframe(*k) for k in keys))

    @classmethod
    def constant(cls, value: float) -> Track:
        return cls((Keyframe(0, value),))

    def at(self, frame: float) -> float:
        """Value at ``frame``; holds the first/last value outside the keyframe range."""
        i = bisect.bisect_right(self._frames, frame)
        if i == 0:
            return self.keyframes[0].value
        if i == len(self.keyframes):
            return self.keyframes[-1].value
        a, b = self.keyframes[i - 1], self.keyframes[i]
        span = b.frame - a.frame
        p = 1.0 if span <= 0 else (frame - a.frame) / span
        return a.value + (b.value - a.value) * b.easing(p)


@dataclass(frozen=True)
class Animation:
    """One animated change of a property, in seconds (see ``compile_track``)."""

    start: float
    length: float
    to: float | None = None
    by: float | None = None
    from_: float | None = None
    ease: Easing = linear


def compile_track(base: float, animations: list[Animation], fps: int) -> Track:
    """Turn a property's animations into a track, applying them in start order.

    Each animation runs from the property's value when it starts (or ``from_``) to ``to``
    (or that value + ``by``). The value before the first animation is its ``from_`` if
    given, otherwise ``base``. An animation that starts while another is still running
    takes over from the in-progress value.
    """
    ordered = sorted(animations, key=lambda a: a.start)
    first_from = ordered[0].from_ if ordered else None
    keys = [Keyframe(0.0, base if first_from is None else first_from)]
    for anim in ordered:
        start = anim.start * fps
        end = start + max(0.0, anim.length) * fps
        so_far = Track(tuple(keys))
        current = so_far.at(start)
        keys = [k for k in keys if k.frame <= start]
        if keys[-1].frame < start:
            keys.append(Keyframe(start, current))  # cut a running animation short
        begin = current if anim.from_ is None else anim.from_
        target = anim.to if anim.to is not None else begin + (anim.by or 0.0)
        keys.append(Keyframe(start, begin))
        keys.append(Keyframe(end, target, anim.ease))
    return Track(tuple(keys))
