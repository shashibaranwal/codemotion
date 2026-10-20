"""M6: multiprocess rendering, bench, launcher and packaging helpers."""

from __future__ import annotations

import hashlib
import json
import struct
import sys
from pathlib import Path

import pytest
from conftest import code_scene
from typer.testing import CliRunner

from codemotion import bench, config, launcher, sysinfo
from codemotion.cli import app
from codemotion.errors import UserFacingError
from codemotion.render import parallel
from codemotion.render.renderer import CancelToken, RenderCancelledError, render_frames
from codemotion.script import runner as script_runner

ROOT = Path(__file__).resolve().parent.parent
runner = CliRunner()


def test_sysinfo_reports_memory_and_cpus() -> None:
    assert sysinfo.peak_memory() > 1_000_000
    assert sysinfo.usable_cpus() >= 1


def test_openblas_is_limited_before_numpy_loads() -> None:
    import os

    assert os.environ.get("OPENBLAS_NUM_THREADS") == "1"


def test_auto_workers_keeps_small_renders_in_process() -> None:
    small = code_scene("x = 1", preview=True, duration=3)  # 45 frames at 540p
    assert parallel.auto_workers(small) == 1
    big = code_scene("x = 1", preview=False, duration=12)
    assert 1 <= parallel.auto_workers(big) <= config.RENDER_MAX_WORKERS
    if sysinfo.usable_cpus() > 2:
        assert parallel.auto_workers(big) > 1


def _hashes(workers: int) -> list[str]:
    hashes: list[str] = []
    prepared = code_scene("def f(x):\n    return x * 2\n", preview=True, duration=3)
    render_frames(prepared, lambda px: hashes.append(hashlib.sha256(px).hexdigest()),
                  workers=workers)  # fmt: skip
    return hashes


def test_parallel_frames_match_single_process_frames() -> None:
    single = _hashes(1)
    assert len(single) == 45
    assert _hashes(2) == single  # same frames, same order


def test_parallel_render_reports_progress_and_memory() -> None:
    prepared = code_scene("x = 1", preview=True, duration=3)
    done: list[int] = []
    stats = parallel.render_frames_parallel(prepared, lambda px: None, 2, done.append)
    assert done == list(range(1, 46))
    assert stats.workers == 2 and stats.worker_peak_memory > 0


def test_parallel_render_cancels() -> None:
    prepared = code_scene("x = 1", preview=True, duration=6)
    token = CancelToken()
    frames: list[int] = []

    def sink(px: memoryview) -> None:
        frames.append(1)
        if len(frames) == 5:
            token.cancel()

    with pytest.raises(RenderCancelledError):
        render_frames(prepared, sink, cancel=token, workers=2)
    assert len(frames) == 5


def test_child_command_in_checkout_and_packaged_app(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    script, out = tmp_path / "s.py", tmp_path / "o.json"
    command = script_runner.child_command(script, out)
    assert command[1:3] == ["-m", "codemotion.script.child"]
    gui = tmp_path / "CodeMotion.exe"
    cli = tmp_path / ("codemotion-cli.exe" if sys.platform == "win32" else "codemotion-cli")
    cli.write_bytes(b"")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(gui))
    assert script_runner.child_command(script, out) == [
        str(cli),
        launcher.CHILD_FLAG,
        str(script),
        str(out),
    ]


def test_launcher_runs_the_script_child(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    script, out = tmp_path / "video.py", tmp_path / "doc.json"
    script.write_text("from codemotion import Video\nvideo = Video(duration=3)\n", "utf-8")
    monkeypatch.setattr(sys, "argv", ["codemotion-cli", launcher.CHILD_FLAG, str(script),
                                      str(out)])  # fmt: skip
    with pytest.raises(SystemExit) as exit_info:
        launcher.cli()
    assert exit_info.value.code == 0
    assert json.loads(out.read_text("utf-8"))["doc"]["duration"] == 3


def test_desktop_self_test_passes(tmp_path: Path, qtbot: object) -> None:
    from codemotion.desktop.selftest import self_test

    report = tmp_path / "report.txt"
    assert self_test(report) == 0
    text = report.read_text("utf-8")
    assert text.count("PASS") == 4 and "FAIL" not in text


def test_bench_measures_every_target() -> None:
    script = (
        "from codemotion import Text, Video\nvideo = Video(duration=3)\nvideo.add(Text('hi'))\n"
    )
    lines: list[str] = []
    results = bench.run_bench(workers=1, status=lines.append, script=script)
    assert [r.unit for r in results] == ["s", "ms", "ms", "s", "s", "MB"]
    assert all(r.value > 0 for r in results)
    assert len(lines) == 4
    assert bench.BenchResult("x", 1, 2, "s").passed and not bench.BenchResult("x", 3, 2, "s").passed


def test_bench_script_is_valid() -> None:
    doc = script_runner.run_script_source(bench.BENCH_SCRIPT, ROOT).doc
    assert doc.duration == 12 and doc.aspect.value == "9:16"


def test_cli_bench_json(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = [bench.BenchResult("Full render", 9.5, 30, "s")]
    monkeypatch.setattr(bench, "run_bench", lambda workers=None, status=None, script="": fake)
    result = runner.invoke(app, ["bench", "--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data == [{"item": "Full render", "value": 9.5, "target": 30, "unit": "s",
                     "passed": True}]  # fmt: skip
    table = runner.invoke(app, ["bench"])
    assert "Full render" in table.output and "ok" in table.output


def test_cli_render_workers_option(tmp_path: Path) -> None:
    code = tmp_path / "code.py"
    code.write_bytes(b"x = 1\n")
    out = tmp_path / "v.mp4"
    args = ["render", str(code), "--duration", "3", "--preview", "--workers", "2", "-o", str(out)]
    result = runner.invoke(app, args)
    assert result.exit_code == 0, result.output
    assert out.is_file()
    assert runner.invoke(app, [*args[:-2], "--workers", "0"]).exit_code != 0


def test_broken_worker_is_explained(monkeypatch: pytest.MonkeyPatch) -> None:
    from concurrent.futures.process import BrokenProcessPool

    class Broken:
        def done(self) -> bool:
            return True

        def result(self) -> tuple[int, int]:
            raise BrokenProcessPool("boom")

        def cancel(self) -> bool:
            return True

    class Pool:
        def __init__(self, *args: object, **kwargs: object) -> None: ...
        def __enter__(self) -> Pool:
            return self

        def __exit__(self, *exc: object) -> None: ...
        def submit(self, *args: object) -> Broken:
            return Broken()

        def shutdown(self, **kwargs: object) -> None: ...

    monkeypatch.setattr(parallel, "ProcessPoolExecutor", Pool)
    prepared = code_scene("x = 1", preview=True, duration=3)
    with pytest.raises(UserFacingError, match="render worker stopped"):
        parallel.render_frames_parallel(prepared, lambda px: None, 2)


def test_icon_files_are_valid() -> None:
    png = config.ICON_FILE.read_bytes()
    assert png.startswith(b"\x89PNG")
    ico = (ROOT / "packaging" / "icon.ico").read_bytes()
    reserved, kind, count = struct.unpack("<HHH", ico[:6])
    assert (reserved, kind, count) == (0, 1, 5)
