"""Showcase mode: a plain code file becomes a one-element ``SceneDoc``.

``codemotion render code.py`` (a file that doesn't import codemotion) builds the classic
code-card video: backdrop fades in, the card springs in, the code is revealed per the
timeline planner (SPEC 5.6), then holds.
"""

from __future__ import annotations

from codemotion import config
from codemotion.models import Phase, RenderRequest, Style
from codemotion.pipeline import plan_for, prepare
from codemotion.script.doc import Anim, CodeElement, SceneDoc

INTRO_EASE = f"spring:{config.INTRO_SPRING_STIFFNESS:g}:{config.INTRO_SPRING_DAMPING:g}"


def _phase(phases: list[Phase], name: str) -> Phase | None:
    return next((p for p in phases if p.name == name), None)


def showcase_doc(request: RenderRequest) -> tuple[SceneDoc, list[str]]:
    """The scene for ``request`` plus the planner's warnings (e.g. style fallbacks)."""
    # Plan at the full frame rate; previews render the same document at 15 fps.
    prepared = prepare(request.model_copy(update={"preview": False}))
    plan = plan_for(prepared)
    fps = plan.fps
    intro = _phase(plan.phases, "intro")
    assert intro is not None, "every plan starts with an intro"
    intro_s = intro.end / fps
    reveal = _phase(plan.phases, "reveal")
    if plan.effective_style is Style.fade_in:
        reveal_start, reveal_length = config.FADE_IN_DELAY_SECONDS, config.FADE_IN_SECONDS
    elif reveal is not None:
        reveal_start, reveal_length = reveal.start / fps, (reveal.end - reveal.start) / fps
    else:
        reveal_start, reveal_length = intro_s, 0.0

    card = CodeElement(
        id="code",
        code=request.code,
        filename=request.filename,
        reveal=plan.effective_style.value,
        reveal_start=reveal_start,
        reveal_length=reveal_length,
        anims=[
            Anim(
                prop="scale",
                start=0,
                length=intro_s,
                to=1.0,
                from_=config.INTRO_CARD_START_SCALE,
                ease=INTRO_EASE,
            ),
            Anim(prop="opacity", start=0, length=intro_s, to=1.0, from_=0.0, ease="easeOutCubic"),
        ],
    )
    doc = SceneDoc(
        duration=request.duration,
        aspect=request.aspect,
        fps=request.fps,
        theme=request.theme,
        fade_in=config.INTRO_BG_FADE_SECONDS,
        fade_out=config.OUTRO_FADE_SECONDS if request.outro == "fade" else 0.0,
        elements=[card],
    )
    return doc, [w.message for w in prepared.code_warnings] + plan.warnings
