"""Timeline planner (SPEC 5.6): deterministic, in integer frames."""

from __future__ import annotations

import math

from codemotion import config
from codemotion.errors import UserFacingError
from codemotion.models import Phase, Plan, Style

DROP_OUTPUT_WARNING = "Not enough time for the output panel; it was left out."
DROP_FOCUS_WARNING = "Not enough time for the focus highlight; it was left out."
TOO_SHORT_ERROR = "The video is too short for this much code; choose a longer duration."


def cascade_timing(reveal_seconds: float, lines: int) -> tuple[float, float]:
    """(stagger, per-line duration) in seconds so every line finishes within the reveal."""
    lines = max(1, lines)
    stagger = min(config.CASCADE_MAX_STAGGER, config.CASCADE_STAGGER_SHARE * reveal_seconds / lines)
    line_seconds = min(config.CASCADE_LINE_SECONDS, reveal_seconds - stagger * (lines - 1))
    return stagger, max(line_seconds, 1e-6)


def make_plan(
    *,
    duration: float,
    fps: int,
    style: Style,
    chars: int,
    lines: int,
    focus: bool = False,
    output: bool = False,
    font_size: float = 0.0,
    scroll: bool = False,
    warnings: tuple[str, ...] | list[str] = (),
) -> Plan:
    """Split ``round(duration * fps)`` frames into intro/reveal/focus/output/hold phases.

    ``chars`` is the number of characters a typewriter would type (no leading indentation).
    """

    def frames(seconds: float) -> int:
        return round(seconds * fps)

    total = frames(duration)
    out = list(warnings)
    intro = frames(config.INTRO_SECONDS)
    hold_min = frames(config.HOLD_MIN_SECONDS)
    focus_f = frames(config.PHASE_FOCUS_SECONDS) if focus else 0
    output_f = frames(config.PHASE_OUTPUT_SECONDS) if output else 0
    min_reveal = frames(config.REVEAL_MIN_SECONDS) if style is not Style.fade_in else 0

    def available() -> int:
        return total - intro - hold_min - focus_f - output_f

    if available() < min_reveal and output_f:
        output_f = 0
        out.append(DROP_OUTPUT_WARNING)
    if available() < min_reveal and focus_f:
        focus_f = 0
        out.append(DROP_FOCUS_WARNING)
    if available() < min_reveal:
        intro = frames(config.INTRO_SHRUNK_SECONDS)
        hold_min = frames(config.HOLD_SHRUNK_SECONDS)
    if available() < min_reveal:
        raise UserFacingError(TOO_SHORT_ERROR, field="duration")

    reveal_avail = available()
    reveal_seconds = reveal_avail / fps
    effective = style
    reveal = 0
    if style is Style.typewriter:
        if chars / reveal_seconds > config.TYPEWRITER_MAX_CPS:
            effective = Style.line_cascade
            out.append(
                f"Too much code to type in {reveal_seconds:.1f}s; using line cascade instead."
            )
        else:
            slowest = math.ceil(chars / config.TYPEWRITER_MIN_CPS * fps)
            reveal = min(reveal_avail, max(min_reveal, slowest))
    if effective is Style.line_cascade:
        stagger, line_seconds = cascade_timing(reveal_seconds, lines)
        needed = math.ceil((stagger * (max(1, lines) - 1) + line_seconds) * fps)
        reveal = min(reveal_avail, max(min_reveal, needed))

    hold = total - intro - reveal - focus_f - output_f  # leftover frames go to the hold
    phases: list[Phase] = []
    cursor = 0
    for name, length in (
        ("intro", intro),
        ("reveal", reveal),
        ("focus", focus_f),
        ("output", output_f),
        ("hold", hold),
    ):
        if length > 0:
            phases.append(Phase(name=name, start=cursor, end=cursor + length))
            cursor += length
    assert cursor == total, f"phase frames {cursor} != {total}"

    return Plan(
        total_frames=total,
        fps=fps,
        effective_style=effective,
        font_size=round(font_size, 2),
        scroll=scroll,
        phases=phases,
        warnings=out,
    )
