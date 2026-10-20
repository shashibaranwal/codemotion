from __future__ import annotations

import hashlib
import json
from pathlib import Path

import imageio_ffmpeg
import numpy as np
import pytest
from conftest import EXAMPLES_DIR, code_scene, read_source
from typer.testing import CliRunner

from codemotion.cli import app
from codemotion.errors import UserFacingError
from codemotion.pipeline import PreparedScene
from codemotion.render.encoder import FFmpegEncoder, temp_path_for
from codemotion.render.renderer import (
    CancelToken,
    Progress,
    RenderCancelledError,
    StillRenderer,
    default_output,
    render_frames,
    render_still,
    render_video,
    sidecar_path,
)

runner = CliRunner()


def probe(path: Path) -> tuple[int, str, tuple[int, int], float]:
    frames, _ = imageio_ffmpeg.count_frames_and_secs(str(path))
    gen = imageio_ffmpeg.read_frames(str(path))
    meta = next(gen)
    gen.close()
    return frames, meta["codec"], tuple(meta["size"]), meta["fps"]


# --------------------------------------------------------------------------- encoder


def test_encoder_writes_exact_frame_count(tmp_path: Path) -> None:
    out = tmp_path / "solid.mp4"
    frame = np.full((64, 32, 4), 128, dtype=np.uint8)
    with FFmpegEncoder(out, 32, 64, 15) as enc:
        for _ in range(7):
            enc.write(memoryview(frame).cast("B"))
        enc.finish()
    assert probe(out) == (7, "h264", (32, 64), 15.0)
    assert not temp_path_for(out).exists()


def test_encoder_failure_is_friendly_and_cleans_up(tmp_path: Path) -> None:
    out = tmp_path / "bad.mp4"
    # Odd width: yuv420p rejects it.
    with (
        pytest.raises(UserFacingError, match="Video encoding failed"),
        FFmpegEncoder(out, 33, 64, 15) as enc,
    ):
        enc.write(np.zeros(33 * 64 * 4, dtype=np.uint8).tobytes())
        enc.finish()
    assert not out.exists()
    assert not temp_path_for(out).exists()


def test_encoder_abort_removes_partial_file(tmp_path: Path) -> None:
    out = tmp_path / "x.mp4"
    with pytest.raises(RuntimeError), FFmpegEncoder(out, 32, 32, 15) as enc:
        enc.write(np.zeros(32 * 32 * 4, dtype=np.uint8).tobytes())
        raise RuntimeError("boom")
    assert not out.exists()
    assert not temp_path_for(out).exists()


# --------------------------------------------------------------------------- renderer


def _prepared(code: str = "x = 1\nprint(x)", **settings: object) -> PreparedScene:
    return code_scene(code, duration=settings.pop("duration", 3), **settings)


def test_render_is_deterministic() -> None:
    code = read_source(EXAMPLES_DIR / "fib.py")

    def hashes() -> list[str]:
        out: list[str] = []
        render_frames(_prepared(code), lambda px: out.append(hashlib.sha256(px).hexdigest()))
        return out

    first = hashes()
    assert len(first) == 45
    assert first == hashes()
    assert len(set(first)) > 10  # it actually animates


def test_progress_reports_every_frame() -> None:
    seen: list[Progress] = []
    render_frames(_prepared(), lambda px: None, progress=seen.append)
    assert [p.done for p in seen] == list(range(1, 46))
    assert seen[-1].fraction == 1.0
    assert seen[-1].eta == pytest.approx(0.0)
    assert seen[0].eta is None


def test_cancel_stops_render_and_removes_partial(tmp_path: Path) -> None:
    prepared = _prepared()
    token = CancelToken()
    out = tmp_path / "c.mp4"

    def on_progress(p: Progress) -> None:
        if p.done == 5:
            token.cancel()

    with pytest.raises(RenderCancelledError):
        render_video(prepared, out, progress=on_progress, cancel=token)
    assert not out.exists()
    assert not temp_path_for(out).exists()
    assert not sidecar_path(out).exists()


def test_render_video_writes_sidecar(tmp_path: Path) -> None:
    video = render_video(_prepared(), tmp_path / "v.mp4", source="x.py", warnings=["w"])
    data = json.loads(sidecar_path(video).read_text(encoding="utf-8"))
    assert data["video"] == "v.mp4"
    assert data["source"] == "x.py"
    assert data["scene"]["duration"] == 3
    assert data["frames"] == 45
    assert data["warnings"] == ["w"]


# --------------------------------------------------------------------------- integration

FIXTURES = [
    ("hello.py", "9:16", "typewriter", 4.0),
    ("fib.py", "1:1", "typewriter", 6.5),
    ("fastapi_app.py", "16:9", "fade_in", 5.0),
    ("dataclass.py", "9:16", "typewriter", 8.0),
    ("long_script.py", "16:9", "typewriter", 3.0),  # falls back to line cascade
]


@pytest.mark.parametrize(
    ("name", "aspect", "style", "duration"), FIXTURES, ids=[f[0] for f in FIXTURES]
)
def test_fixture_renders_at_preview_quality(
    tmp_path: Path, name: str, aspect: str, style: str, duration: float
) -> None:
    prepared = _prepared(read_source(EXAMPLES_DIR / name), aspect=aspect, style=style,
                         duration=duration)  # fmt: skip
    video = render_video(prepared, tmp_path / "out.mp4")
    frames, codec, size, fps = probe(video)
    assert frames == round(duration * 15)
    assert codec == "h264"
    assert size == (prepared.width, prepared.height)
    assert min(size) == 540
    assert fps == 15.0


# --------------------------------------------------------------------------- CLI


def test_cli_render(tmp_path: Path) -> None:
    out = tmp_path / "r.mp4"
    result = runner.invoke(
        app,
        ["render", str(EXAMPLES_DIR / "hello.py"), "--duration", "3", "--preview", "-o", str(out)],
    )
    assert result.exit_code == 0, result.output
    assert "45 frames" in result.output
    assert probe(out)[0] == 45


def test_cli_render_default_output_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["render", "-", "--duration", "3", "--preview"], input="x = 1\n")
    assert result.exit_code == 0, result.output
    videos = list((tmp_path / "output").glob("snippet-9x16-preview-*.mp4"))
    assert len(videos) == 1
    assert sidecar_path(videos[0]).exists()


def test_cli_render_requires_duration_without_tty(tmp_path: Path) -> None:
    result = runner.invoke(app, ["render", "-", "-o", str(tmp_path / "x.mp4")], input="x = 1\n")
    assert result.exit_code == 1
    assert "Duration is required" in result.output


def test_cli_render_rejects_non_mp4(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        [
            "render",
            str(EXAMPLES_DIR / "hello.py"),
            "--duration",
            "3",
            "-o",
            str(tmp_path / "x.gif"),
        ],
    )
    assert result.exit_code == 1
    assert ".mp4" in result.output


def test_cli_plan_table_and_json() -> None:
    long_script = str(EXAMPLES_DIR / "long_script.py")
    result = runner.invoke(app, ["plan", long_script, "--duration", "4"])
    assert result.exit_code == 0, result.output
    assert "line_cascade" in result.output
    assert "using line cascade instead" in result.output

    result = runner.invoke(app, ["plan", long_script, "--duration", "4", "--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    [code] = data["plan"]["elements"]
    assert code["reveal"] == "line_cascade"
    assert code["scroll"] is True
    assert data["plan"]["total_frames"] == 120
    assert data["scene"]["elements"][0]["type"] == "code"


def test_still_renderer_matches_render_still() -> None:
    prepared = code_scene("x = 1\ny = 2\n")
    stills = StillRenderer(prepared)
    for t in (0.5, 6.0, 0.5):  # reuses one scene and canvas, in any order
        assert np.array_equal(stills.render(t).to_array(), render_still(prepared, t).to_array())


def test_default_output_never_overwrites(tmp_path: Path) -> None:
    prepared = code_scene("x = 1\n", aspect="16:9")
    first = default_output("demo", prepared.doc, True, tmp_path)
    assert first.name.startswith("demo-16x9-preview-") and first.suffix == ".mp4"
    first.write_bytes(b"x")
    second = default_output("demo", prepared.doc, True, tmp_path)
    assert second != first and second.stem.endswith("-2")
