"""Render the README's GIFs into ``docs/media/``: ``uv run python packaging/readme_media.py``.

Each example renders at preview quality, then converts to a small GIF (360 px short edge,
15 fps) so the README stays light.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from codemotion.pipeline import prepare_scene
from codemotion.render.encoder import export_video
from codemotion.render.renderer import render_video
from codemotion.script.runner import run_script

ROOT = Path(__file__).resolve().parent.parent
MEDIA = ROOT / "docs" / "media"
EXAMPLES = ("code_explainer", "terminal_demo", "request_flow")
SHORT_EDGE = 360


def main() -> None:
    MEDIA.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="codemotion-media-") as tmp:
        for name in EXAMPLES:
            doc = run_script(ROOT / "examples" / "scripts" / f"{name}.py").doc
            prepared = prepare_scene(doc, preview=True)
            video = render_video(prepared, Path(tmp) / f"{name}.mp4")
            size = (prepared.width, prepared.height)
            gif = export_video(video, "gif", size, doc.duration, gif_short_edge=SHORT_EDGE)
            target = MEDIA / f"{name}.gif"
            target.write_bytes(gif.read_bytes())
            print(f"{target.relative_to(ROOT)}: {target.stat().st_size / 1e6:.1f} MB")


if __name__ == "__main__":
    main()
