from __future__ import annotations

import itertools

import pytest

from codemotion import config
from codemotion.errors import UserFacingError
from codemotion.models import Style
from codemotion.timeline.easing import (
    EASINGS,
    ease_in_out_cubic,
    ease_out_back,
    ease_out_cubic,
    ease_out_expo,
    linear,
    spring,
)
from codemotion.timeline.keyframes import Keyframe, Track
from codemotion.timeline.planner import (
    DROP_FOCUS_WARNING,
    DROP_OUTPUT_WARNING,
    cascade_timing,
    make_plan,
)

SAMPLES = [i / 200 for i in range(201)]
MONOTONIC = [
    linear,
    ease_in_out_cubic,
    ease_out_cubic,
    ease_out_expo,
    spring(170, 26),
    spring(100, 40),
]

# --------------------------------------------------------------------------- easing


@pytest.mark.parametrize(
    "ease", [*EASINGS.values(), spring(180, 20), spring(170, 26), spring(50, 30)]
)
def test_easing_endpoints(ease: object) -> None:
    assert callable(ease)
    assert ease(0.0) == pytest.approx(0.0, abs=1e-9)
    assert ease(1.0) == 1.0
    assert ease(-0.5) == ease(0.0)  # clamped
    assert ease(1.5) == 1.0


@pytest.mark.parametrize("ease", MONOTONIC)
def test_monotonic_easings(ease: object) -> None:
    assert callable(ease)
    values = [ease(p) for p in SAMPLES]
    assert all(b >= a - 1e-12 for a, b in itertools.pairwise(values))


def test_ease_out_back_overshoots() -> None:
    assert max(ease_out_back(p) for p in SAMPLES) > 1.0


def test_underdamped_spring_overshoots_then_settles() -> None:
    ease = spring(180, 12)
    values = [ease(p) for p in SAMPLES]
    assert max(values) > 1.0
    assert values[-2] == pytest.approx(1.0, abs=2e-3)


def test_critically_damped_spring_does_not_overshoot() -> None:
    ease = spring(100, 20)  # zeta = 1
    assert max(ease(p) for p in SAMPLES) <= 1.0 + 1e-9


def test_spring_is_pure() -> None:
    assert [spring(180, 20)(p) for p in SAMPLES] == [spring(180, 20)(p) for p in SAMPLES]


def test_spring_rejects_bad_parameters() -> None:
    with pytest.raises(ValueError):
        spring(0, 10)


# --------------------------------------------------------------------------- keyframes


def test_track_interpolates_and_holds() -> None:
    track = Track.of((10, 0.0), (20, 100.0))
    assert track.at(0) == 0.0
    assert track.at(15) == pytest.approx(50.0)
    assert track.at(25) == 100.0


def test_track_uses_easing_of_arriving_keyframe() -> None:
    track = Track((Keyframe(0, 0.0), Keyframe(10, 1.0, ease_out_cubic)))
    assert track.at(5) == pytest.approx(ease_out_cubic(0.5))


def test_track_multi_segment_and_constant() -> None:
    track = Track.of((0, 0.0), (10, 1.0), (20, 0.0))
    assert track.at(10) == 1.0
    assert track.at(15) == pytest.approx(0.5)
    assert Track.constant(3.0).at(1234) == 3.0


def test_track_validation() -> None:
    with pytest.raises(ValueError):
        Track(())
    with pytest.raises(ValueError):
        Track.of((10, 0.0), (5, 1.0))


# --------------------------------------------------------------------------- planner

DURATIONS = [3.0 + 0.5 * i for i in range(19)]


@pytest.mark.parametrize("fps", [15, 30, 60])
@pytest.mark.parametrize("style", list(Style))
@pytest.mark.parametrize(("focus", "output"), list(itertools.product([False, True], repeat=2)))
@pytest.mark.parametrize("chars", [10, 400, 2900])
def test_phases_always_sum_to_total(
    fps: int, style: Style, focus: bool, output: bool, chars: int
) -> None:
    for duration in DURATIONS:
        plan = make_plan(
            duration=duration, fps=fps, style=style, chars=chars, lines=max(1, chars // 40),
            focus=focus, output=output,
        )  # fmt: skip
        assert plan.total_frames == round(duration * fps)
        assert sum(p.end - p.start for p in plan.phases) == plan.total_frames
        assert plan.phases[0].start == 0
        assert plan.phases[-1].end == plan.total_frames
        assert all(a.end == b.start for a, b in itertools.pairwise(plan.phases))
        names = [p.name for p in plan.phases]
        assert names[0] == "intro" and names[-1] == "hold"
        hold = plan.phases[-1]
        assert hold.end - hold.start >= round(config.HOLD_SHRUNK_SECONDS * fps)


def test_default_plan_shape() -> None:
    plan = make_plan(duration=8, fps=30, style=Style.typewriter, chars=170, lines=10)
    assert [(p.name, p.start, p.end) for p in plan.phases] == [
        ("intro", 0, 24),
        ("reveal", 24, 210),
        ("hold", 210, 240),
    ]
    assert plan.effective_style is Style.typewriter
    assert plan.warnings == []


def test_focus_and_output_phases_in_order() -> None:
    plan = make_plan(
        duration=12, fps=30, style=Style.typewriter, chars=200, lines=10, focus=True, output=True
    )
    names = [p.name for p in plan.phases]
    assert names == ["intro", "reveal", "focus", "output", "hold"]
    lengths = {p.name: p.end - p.start for p in plan.phases}
    assert lengths["focus"] == 54
    assert lengths["output"] == 48


def test_short_duration_drops_output_then_focus() -> None:
    plan = make_plan(
        duration=3, fps=30, style=Style.typewriter, chars=20, lines=2, focus=True, output=True
    )
    names = [p.name for p in plan.phases]
    assert "output" not in names
    assert "focus" not in names
    assert plan.warnings == [DROP_OUTPUT_WARNING, DROP_FOCUS_WARNING]


def test_output_dropped_before_focus() -> None:
    # 4 s: intro 24 + hold 30 + focus 54 + output 48 leaves -36; dropping output leaves 12
    # (< 18 min reveal) so focus goes too. At 4.5 s, dropping output alone is enough.
    plan = make_plan(
        duration=4.5, fps=30, style=Style.typewriter, chars=20, lines=2, focus=True, output=True
    )
    assert [p.name for p in plan.phases] == ["intro", "reveal", "focus", "hold"]
    assert plan.warnings == [DROP_OUTPUT_WARNING]


def test_typewriter_speed_cap_switches_to_cascade() -> None:
    plan = make_plan(duration=4, fps=30, style=Style.typewriter, chars=500, lines=30)
    assert plan.effective_style is Style.line_cascade
    assert plan.warnings == ["Too much code to type in 2.2s; using line cascade instead."]


def test_typewriter_at_cap_is_kept() -> None:
    reveal_seconds = (240 - 24 - 30) / 30
    chars = int(config.TYPEWRITER_MAX_CPS * reveal_seconds)
    plan = make_plan(duration=8, fps=30, style=Style.typewriter, chars=chars, lines=20)
    assert plan.effective_style is Style.typewriter


def test_short_code_types_at_min_speed_and_holds_longer() -> None:
    plan = make_plan(duration=12, fps=30, style=Style.typewriter, chars=30, lines=2)
    reveal = next(p for p in plan.phases if p.name == "reveal")
    assert reveal.end - reveal.start == 60  # 30 chars at 15 chars/s
    assert plan.phases[-1].end - plan.phases[-1].start == 360 - 24 - 60


def test_fade_in_has_no_reveal_phase() -> None:
    plan = make_plan(duration=6, fps=30, style=Style.fade_in, chars=999, lines=40)
    assert [p.name for p in plan.phases] == ["intro", "hold"]
    assert plan.effective_style is Style.fade_in


def test_cascade_reveal_fits_all_lines() -> None:
    plan = make_plan(duration=5, fps=30, style=Style.line_cascade, chars=999, lines=55)
    reveal = next(p for p in plan.phases if p.name == "reveal")
    seconds = (reveal.end - reveal.start) / 30
    stagger, line_s = cascade_timing(seconds, 55)
    assert stagger * 54 + line_s <= seconds + 1e-9


@pytest.mark.parametrize("seconds", [0.6, 1.0, 3.0, 9.0])
@pytest.mark.parametrize("lines", [1, 3, 25, 60])
def test_cascade_timing_rules(seconds: float, lines: int) -> None:
    stagger, line_s = cascade_timing(seconds, lines)
    assert stagger <= config.CASCADE_MAX_STAGGER
    assert stagger <= config.CASCADE_STAGGER_SHARE * seconds / lines + 1e-12
    assert 0 < line_s <= config.CASCADE_LINE_SECONDS
    assert stagger * (lines - 1) + line_s <= seconds + 1e-9


def test_layout_warnings_and_metadata_pass_through() -> None:
    plan = make_plan(
        duration=8, fps=60, style=Style.typewriter, chars=50, lines=3, font_size=26.857,
        scroll=True, warnings=["Code is long; it will scroll."],
    )  # fmt: skip
    assert plan.fps == 60
    assert plan.font_size == 26.86
    assert plan.scroll
    assert plan.warnings[0] == "Code is long; it will scroll."


def test_impossible_plan_is_a_friendly_error() -> None:
    with pytest.raises(UserFacingError, match="too short") as info:
        make_plan(duration=1.0, fps=30, style=Style.typewriter, chars=10, lines=1)
    assert info.value.field == "duration"
