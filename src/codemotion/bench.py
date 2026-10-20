"""``codemotion bench``: measure the SPEC section 10 performance targets on this computer.

The benchmark renders a fixed 12-second 9:16 script that uses most features (text, a
typed code card with a highlight, a terminal, shapes), so results are comparable between
machines and versions.
"""

from __future__ import annotations

import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from codemotion import config
from codemotion.pipeline import prepare_scene, scene_plan
from codemotion.render.renderer import StillRenderer, render_video
from codemotion.script.runner import run_script_source
from codemotion.sysinfo import peak_memory

BENCH_SCRIPT = '''
from codemotion import Code, Shape, Terminal, Text, Video, stagger

CODE = """from dataclasses import dataclass


@dataclass
class Order:
    sku: str
    qty: int
    price: float

    @property
    def total(self) -> float:
        return self.qty * self.price


orders = [
    Order("A-1", 3, 9.5),
    Order("B-2", 1, 120.0),
    Order("C-3", 12, 2.25),
]
for order in sorted(orders, key=lambda o: -o.total):
    print(f"{order.sku:<6}{order.total:>9.2f}")
"""

video = Video(duration=12, aspect="9:16", fade_out=0.3)
title = video.add(Text("Benchmark", 64, bold=True, at=(0.5, 0.08)))
title.slide_in("down", start=0.2)
code = video.add(Code(CODE, filename="orders.py", region=(0.06, 0.12, 0.94, 0.62)), start=0.5)
code.pop_in(start=0.5)
code.highlight("10-12", start=7.4, end=10.5)
output = "B-2      120.00\\nC-3       27.00\\nA-1       28.50"
terminal = video.add(Terminal(output, command="python orders.py", at=(0.5, 0.75),
                              reveal_start=8.0), start=7.6)
terminal.slide_in("up", start=7.6)
dots = [video.add(Shape.circle(10, at=(0.3 + 0.1 * i, 0.9))) for i in range(5)]
stagger(dots, "pop_in", start=1.0, every=0.1)
'''


@dataclass(frozen=True)
class BenchResult:
    item: str
    value: float
    target: float
    unit: str  # "s", "ms" or "MB"

    @property
    def passed(self) -> bool:
        return self.value < self.target


def run_bench(
    workers: int | None = None,
    status: Callable[[str], None] | None = None,
    script: str = BENCH_SCRIPT,
) -> list[BenchResult]:
    """Measure every target; ``status`` gets a line before each (slow) step.

    The targets are for the 12-second benchmark ``script``; another script is measured the
    same way (used by the tests to stay fast).
    """

    def say(message: str) -> None:
        if status is not None:
            status(message)

    results: list[BenchResult] = []
    say("Running the script and planning...")
    started = time.perf_counter()
    doc = run_script_source(script, Path.cwd()).doc
    preview = prepare_scene(doc, preview=True)
    scene_plan(preview)
    results.append(BenchResult("Script run + plan", time.perf_counter() - started, 1.0, "s"))

    say("Rendering stills...")
    stills = StillRenderer(preview)
    started = time.perf_counter()
    stills.render(doc.duration / 2)  # includes building the scene, like the app's first still
    first = (time.perf_counter() - started) * 1000
    timings = []
    for t in (doc.duration * f for f in (0.15, 0.35, 0.65, 0.85)):
        started = time.perf_counter()
        stills.render(t)
        timings.append((time.perf_counter() - started) * 1000)
    results.append(BenchResult("Still, first (540p, builds the scene)", first, 300, "ms"))
    results.append(BenchResult("Still, scrubbing (540p, slowest)", max(timings), 300, "ms"))

    with tempfile.TemporaryDirectory(prefix="codemotion-bench-") as tmp:
        folder = Path(tmp)
        say("Rendering the quick preview (540p, 15 fps, 12 s)...")
        started = time.perf_counter()
        render_video(preview, folder / "preview.mp4", workers=workers)
        results.append(
            BenchResult(
                "Quick preview (540p, 15 fps, 12 s)", time.perf_counter() - started, 5.0, "s"
            )
        )
        say("Rendering the full video (1080x1920, 30 fps, 12 s)...")
        full = prepare_scene(doc)
        peaks: list[int] = []
        started = time.perf_counter()
        render_video(full, folder / "full.mp4", workers=workers, worker_memory=peaks.append)
        results.append(
            BenchResult(
                "Full render (1080x1920, 30 fps, 12 s)", time.perf_counter() - started, 30.0, "s"
            )
        )
    memory = (peak_memory() + sum(peaks)) / 1e6
    results.append(BenchResult("Peak memory (this process + workers)", memory, 800, "MB"))
    return results


def describe_targets() -> str:
    return (
        f"Workers: up to {config.RENDER_MAX_WORKERS} processes; renders under "
        f"{config.PARALLEL_MIN_WORK:g} megapixel-frames stay in one process."
    )
