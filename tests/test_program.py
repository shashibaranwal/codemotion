"""Video programs: Pillow/OpenCV scripts that write their own video (script/program.py)."""

from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest
from typer.testing import CliRunner

from codemotion.cli import app
from codemotion.errors import UserFacingError
from codemotion.render.encoder import RenderCancelledError, probe_video
from codemotion.render.renderer import CancelToken, Progress, aspect_label
from codemotion.script.program import (
    incomplete,
    is_program,
    make_video,
    progress_in,
    video_files,
)
from codemotion.script.runner import ScriptRunError
from codemotion.script.source import SourceKind, SourceMode, load_text, source_kind

runner = CliRunner()

# Ten 64x48 frames at 10 fps: a blue dot moving right, written by OpenCV.
PROGRAM = """import cv2
import numpy as np
from PIL import Image, ImageDraw

writer = cv2.VideoWriter({name!r}, cv2.VideoWriter_fourcc(*"mp4v"), 10, (64, 48))
for i in range(10):
    img = Image.new("RGB", (64, 48), "white")
    ImageDraw.Draw(img).ellipse((i * 4, 10, i * 4 + 20, 30), fill="blue")
    writer.write(cv2.cvtColor(np.array(img), cv2.COLOR_RGB2BGR))
    print(f"Rendered {{i + 1}}/10 frames")
writer.release()
"""


def program(name: str = "clip.mp4") -> str:
    return PROGRAM.format(name=name)


# --------------------------------------------------------------------------- detection


def test_programs_are_detected_by_their_imaging_library() -> None:
    assert is_program(program())
    assert is_program("from PIL import Image\nframes[0].save('a.gif', save_all=True)\n")
    assert is_program("import imageio\nimageio.mimsave('a.mp4', frames)\n")
    # The import alone decides: the writer call is often the part a cut-off paste lost.
    cut = "\n".join(program().splitlines()[:3])  # just the imports
    assert "VideoWriter" not in cut and is_program(cut)
    # A video script and plain code are not programs.
    assert not is_program("from codemotion import Video\nimport cv2\ncv2.VideoWriter()\n")
    assert not is_program("def f():\n    return 1\n")


@pytest.mark.parametrize(
    ("source", "line", "reason"),
    [
        ("import cv2\n\ndef render(t):\n    return t\n\ndef glow(img):\n    d = 1\n", 7,
         "never calls"),
        ("import cv2\nx = draw(1,\n       2,\n", 3, "middle of a statement"),
        ('import cv2\ns = """open\n', 2, "middle of a statement"),
        ("import cv2\nfor i in range(3):\n", 2, "middle of a statement"),
    ],
)  # fmt: skip
def test_cut_off_code_is_recognised(source: str, line: int, reason: str) -> None:
    problem = incomplete(source)
    assert problem is not None and problem[1] == line and reason in problem[0]


@pytest.mark.parametrize(
    "source",
    [
        program(),  # calls its functions at top level
        "def main():\n    pass\n\nif __name__ == '__main__':\n    main()\n",
        "import cv2\nx = 1\n",  # no functions at all
        "def f(:\n    pass\n",  # an ordinary syntax error is left to the run
        "def render():\n    pass\n\nframes = list(map(render, range(3)))\n",  # passed by name
    ],
)
def test_complete_code_is_not_flagged(source: str) -> None:
    assert incomplete(source) is None


def test_cut_off_program_is_refused_before_running(tmp_path: Path) -> None:
    source = "import cv2\n\ndef render(t):\n    return t\n"
    with pytest.raises(ScriptRunError, match="cut off") as info:
        make_video(source, tmp_path / "out.mp4")
    assert info.value.line == 4


def test_source_kind_and_modes() -> None:
    assert source_kind(program()) is SourceKind.program
    assert source_kind("x = 1") is SourceKind.code
    assert source_kind("import codemotion") is SourceKind.script
    assert source_kind("x = 1", SourceMode.program) is SourceKind.program
    assert source_kind(program(), SourceMode.code) is SourceKind.code
    with pytest.raises(UserFacingError, match="video program"):
        load_text(program(), Path.cwd())


@pytest.mark.parametrize(
    ("line", "fraction"),
    [("Rendered 60/240 frames", 0.25), ("frame 3 / 4", 0.75), ("1/2 then 10/10", 1.0),
     ("no numbers", None), ("5/0", None), ("9/4", None)],
)  # fmt: skip
def test_progress_is_read_from_printed_counts(line: str, fraction: float | None) -> None:
    assert progress_in(line) == fraction


def test_aspect_labels() -> None:
    assert aspect_label(720, 1280) == "9:16"
    assert aspect_label(1920, 1080) == "16:9"
    assert aspect_label(500, 500) == "1:1"
    assert aspect_label(640, 480) == "640x480"


def test_video_files_skips_hidden_and_partial_files(tmp_path: Path) -> None:
    for name in ("a.mp4", "sub/b.gif", ".hidden/c.mp4", "d.partial.mp4", "e.txt"):
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_bytes(b"x")
    assert {p.relative_to(tmp_path).as_posix() for p in video_files(tmp_path)} == {
        "a.mp4",
        "sub/b.gif",
    }


# --------------------------------------------------------------------------- running


def test_unsaved_program_runs_and_its_video_is_imported(tmp_path: Path) -> None:
    lines: list[str] = []
    stages: dict[str, int] = {}

    def on_progress(p: Progress) -> None:
        stages[p.stage] = p.done

    out = tmp_path / "out.mp4"
    result = make_video(program(), out, formats=("gif",), on_output=lines.append,
                        progress=on_progress)  # fmt: skip
    assert result.video == out and out.is_file() and out.with_suffix(".gif").is_file()
    assert result.written.name == "clip.mp4"
    assert (result.info.width, result.info.height) == (64, 48)
    assert result.info.duration == pytest.approx(1.0, abs=0.1)
    assert lines[-1] == "Rendered 10/10 frames" and "10/10" in result.output
    assert stages == {"program": 1000, "import": 1000, "gif": 1000}
    sidecar = json.loads(out.with_suffix(".json").read_text(encoding="utf-8"))
    assert sidecar["program"] is True and sidecar["exports"] == ["out.gif"]
    assert sidecar["scene"] == {"aspect": "64x48", "duration": result.info.duration}
    assert probe_video(out).width == 64  # re-encoded, still readable


def test_saved_program_runs_in_its_folder(tmp_path: Path) -> None:
    script = tmp_path / "anim.py"
    script.write_text(program("renders/clip.mp4"), encoding="utf-8")
    (tmp_path / "renders").mkdir()
    result = make_video(script.read_text(encoding="utf-8"), tmp_path / "x.mp4", script)
    assert result.written == tmp_path / "renders" / "clip.mp4"
    # Unsaved edits run instead of the file on disk, still from the file's folder.
    edited = program("edited.mp4")
    result = make_video(edited, tmp_path / "y.mp4", script)
    assert result.written == tmp_path / "edited.mp4"


def test_failing_program_reports_the_line(tmp_path: Path) -> None:
    source = "import cv2\n\nx = 1\nraise ValueError('bad frame')\ncv2.VideoWriter()\n"
    with pytest.raises(ScriptRunError) as info:
        make_video(source, tmp_path / "out.mp4")
    assert info.value.line == 4
    assert "ValueError: bad frame" in info.value.message
    assert not (tmp_path / "out.mp4").exists()


def test_program_without_a_video_explains(tmp_path: Path) -> None:
    with pytest.raises(UserFacingError, match="didn't write a video"):
        make_video("import cv2\nprint('no video')\n# cv2.VideoWriter\n", tmp_path / "out.mp4")


def test_missing_module_lists_what_programs_can_use(tmp_path: Path) -> None:
    with pytest.raises(ScriptRunError, match="Pillow, OpenCV, NumPy"):
        make_video("import not_a_real_module_xyz\n", tmp_path / "out.mp4")


def test_program_can_be_cancelled(tmp_path: Path) -> None:
    source = "import time\nfor i in range(600):\n    print(f'{i}/600')\n    time.sleep(0.1)\n"
    token = CancelToken()
    seen = threading.Event()

    def on_output(line: str) -> None:
        seen.set()
        token.cancel()

    with pytest.raises(RenderCancelledError):
        make_video(source, tmp_path / "out.mp4", on_output=on_output, cancel=token)
    assert seen.is_set() and not (tmp_path / "out.mp4").exists()


def test_program_timeout(tmp_path: Path) -> None:
    with pytest.raises(ScriptRunError, match="took longer than"):
        make_video("import time\ntime.sleep(30)\n", tmp_path / "out.mp4", timeout=1.0)


# --------------------------------------------------------------------------- CLI


def test_cli_renders_a_program(tmp_path: Path) -> None:
    script = tmp_path / "anim.py"
    script.write_text(program(), encoding="utf-8")
    out = tmp_path / "video.mp4"
    result = runner.invoke(app, ["render", str(script), "-o", str(out)])
    assert result.exit_code == 0, result.output
    assert out.is_file() and "from the program's clip.mp4" in result.output.replace("\n", "")
    result = runner.invoke(app, ["plan", str(script)])
    assert result.exit_code == 1 and "codemotion render" in result.output
