"""Command-line interface.

``SOURCE`` is a video script (a .py file that imports codemotion), a video program (a
Pillow/OpenCV script that writes its own video, run by ``render``) or a plain code file,
which is shown as a code card (showcase mode). Detection is automatic; ``--mode`` forces it.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Annotated, Any

import typer
from rich.console import Console
from rich.markup import escape
from rich.progress import BarColumn, TaskProgressColumn, TextColumn, TimeRemainingColumn
from rich.progress import Progress as ProgressBar
from rich.table import Table

from codemotion import config
from codemotion.analysis.normalize import decode
from codemotion.errors import UserFacingError
from codemotion.models import Aspect, ScenePlan, Style, parse_request
from codemotion.pipeline import PreparedScene, prepare_scene, scene_plan
from codemotion.render.encoder import ExportFormat
from codemotion.render.renderer import (
    Progress,
    default_output,
    program_output,
    render_still,
    render_video,
)
from codemotion.script.program import make_video
from codemotion.script.source import (
    Loaded,
    SourceKind,
    load_text,
    source_kind,
    treat_as_script,
)
from codemotion.script.source import SourceMode as Mode
from codemotion.script.template import STARTER
from codemotion.themes.loader import list_themes

app = typer.Typer(
    name="codemotion",
    help="Turn a Python video script (or a code file) into a short motion-graphic video.",
    no_args_is_help=True,
    add_completion=False,
)
err = Console(stderr=True)
out = Console()


SourceArg = Annotated[
    str, typer.Argument(help="Video script or code file (.py), or '-' to read stdin.")
]
ModeOpt = Annotated[
    Mode,
    typer.Option(
        "--mode",
        help="auto: scripts import codemotion; programs (Pillow/OpenCV) write their own "
        "video and are run by 'render'; anything else is shown as code.",
    ),
]
_CODE = "Code files only"
DurationOpt = Annotated[
    float | None,
    typer.Option(
        help=f"Length in seconds ({config.DURATION_MIN:g}-{config.DURATION_MAX:g}, step "
        f"{config.DURATION_STEP:g}). Code files: prompted for if omitted.",
        rich_help_panel=_CODE,
    ),
]
AspectOpt = Annotated[Aspect | None, typer.Option(help="Format.", rich_help_panel=_CODE)]
FpsOpt = Annotated[int | None, typer.Option(help="30 or 60.", rich_help_panel=_CODE)]
ThemeOpt = Annotated[str | None, typer.Option(help="Theme id.", rich_help_panel=_CODE)]
StyleOpt = Annotated[Style | None, typer.Option(help="Code reveal.", rich_help_panel=_CODE)]
OutroOpt = Annotated[str | None, typer.Option(help="'hold' or 'fade'.", rich_help_panel=_CODE)]
FilenameOpt = Annotated[str | None, typer.Option(help="Tab title.", rich_help_panel=_CODE)]
PreviewOpt = Annotated[bool, typer.Option(help="Fast low-res preview (540p, 15 fps).")]
WorkersOpt = Annotated[
    int | None,
    typer.Option(
        min=1,
        help="Render processes (default: automatic, up to "
        f"{config.RENDER_MAX_WORKERS}; 1 renders in this process).",
    ),
]


def _read_source(source: str) -> str:
    if source == "-":
        return decode(sys.stdin.buffer.read())
    path = Path(source)
    if not path.is_file():
        raise UserFacingError(f"File not found: {source}", field="source")
    return decode(path.read_bytes())


def _resolve_duration(duration: float | None, stdin_used: bool) -> float:
    if duration is not None:
        return duration
    required = UserFacingError(
        f"Duration is required: pass --duration "
        f"({config.DURATION_MIN:g}-{config.DURATION_MAX:g} seconds).",
        field="duration",
    )
    if stdin_used or not sys.stdin.isatty():
        raise required
    while True:
        try:
            value: float = typer.prompt(
                f"Duration in seconds ({config.DURATION_MIN:g}-{config.DURATION_MAX:g}, "
                f"step {config.DURATION_STEP:g})",
                default=config.DURATION_DEFAULT,
                type=float,
            )
        except typer.Abort:  # EOF, e.g. stdin redirected from NUL on Windows
            raise required from None
        try:
            parse_request({"code": "pass", "duration": value})
        except UserFacingError as exc:
            err.print(f"[red]{escape(exc.message)}[/red]")
            continue
        return value


def _load(
    source: str, mode: Mode, duration: float | None, text: str | None = None, **code_options: Any
) -> Loaded:
    """Turn SOURCE into a scene document: run a script, or build the code-card scene.

    ``text`` is SOURCE's contents when already read (stdin can be read only once).
    """
    text = _read_source(source) if text is None else text
    if source_kind(text, mode) is SourceKind.program:
        raise UserFacingError(PROGRAM_IN_CLI, "script")
    given = {k: v for k, v in code_options.items() if v is not None}
    stdin = source == "-"
    if treat_as_script(text, mode):
        ignored = [f"--{k}" for k in given] + (["--duration"] if duration is not None else [])
        loaded = load_text(text, Path.cwd(), mode, script_path=None if stdin else Path(source))
        for line in loaded.output.rstrip().splitlines():
            err.print(f"[dim]script> {escape(line)}[/dim]")
        if ignored:
            loaded.warnings.append(
                f"{', '.join(ignored)} ignored: the script sets the video's options."
            )
        return loaded
    given["duration"] = _resolve_duration(duration, stdin)
    return load_text(text, Path.cwd(), mode, given)


def _fail(exc: UserFacingError) -> typer.Exit:
    err.print(f"[bold red]Error:[/bold red] {escape(exc.message)}")
    return typer.Exit(code=1)


def _warn(messages: list[str]) -> None:
    for message in messages:
        err.print(f"[yellow]Warning:[/yellow] {escape(message)}")


def _prepare(loaded: Loaded, preview: bool) -> tuple[PreparedScene, ScenePlan]:
    prepared = prepare_scene(loaded.doc, preview)
    plan = scene_plan(prepared, loaded.warnings)
    return prepared, plan


def _default_output(source: str, prepared: PreparedScene) -> Path:
    stem = "snippet" if source == "-" else Path(source).stem
    return default_output(stem, prepared.doc, prepared.preview, config.OUTPUT_DIR)


PROGRAM_IN_CLI = (
    "This is a video program: it draws its own frames and writes a video. "
    "Run it with 'codemotion render' to get its video."
)
_PROGRESS_COLUMNS = (
    TextColumn("{task.description}"),
    BarColumn(),
    TaskProgressColumn(),
    TextColumn("ETA"),
    TimeRemainingColumn(),
)
_PROGRAM_STAGES = {"program": "Running the program", "import": "Importing its video",
                   "gif": "Exporting GIF", "webm": "Exporting WEBM"}  # fmt: skip


def _render_program(
    source: str, text: str, output: Path | None, formats: tuple[ExportFormat, ...]
) -> None:
    """Run a video program and save the video it writes (see ``script.program``)."""
    script_path = None if source == "-" else Path(source)
    stem = "snippet" if script_path is None else script_path.stem
    target = output or program_output(stem, config.OUTPUT_DIR)
    if target.suffix.lower() != ".mp4":
        raise UserFacingError("The output file must end in .mp4.", field="output")
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        with ProgressBar(*_PROGRESS_COLUMNS, console=err, transient=True) as bar:
            task = bar.add_task(_PROGRAM_STAGES["program"], total=1000)

            def on_progress(p: Progress) -> None:
                stage = _PROGRAM_STAGES.get(p.stage, p.stage)
                bar.update(task, completed=p.done, description=stage)

            result = make_video(
                text,
                target,
                script_path,
                formats,
                on_output=lambda line: bar.console.print(f"[dim]program> {escape(line)}[/dim]"),
                progress=on_progress,
            )
    except KeyboardInterrupt:
        err.print("[yellow]Cancelled; no video was written.[/yellow]")
        raise typer.Exit(code=130) from None
    info = result.info
    out.print(
        f"Wrote [bold]{escape(str(result.video))}[/bold] ({info.duration:g} s, "
        f"{info.width}x{info.height} @ {info.fps:g} fps) from the program's "
        f"{escape(result.written.name)}"
    )
    for path in result.exports:
        out.print(f"Wrote [bold]{escape(str(path))}[/bold]")


@app.command()
def render(
    source: SourceArg,
    preview: PreviewOpt = False,
    output: Annotated[
        Path | None, typer.Option("-o", "--output", help="MP4 to write (default: ./output/).")
    ] = None,
    mode: ModeOpt = Mode.auto,
    duration: DurationOpt = None,
    aspect: AspectOpt = None,
    fps: FpsOpt = None,
    theme: ThemeOpt = None,
    style: StyleOpt = None,
    outro: OutroOpt = None,
    filename: FilenameOpt = None,
    gif: Annotated[bool, typer.Option("--gif", help="Also export a GIF (540p, 15 fps).")] = False,
    webm: Annotated[bool, typer.Option("--webm", help="Also export a WebM (VP9).")] = False,
    workers: WorkersOpt = None,
) -> None:
    """Render a video script (or a code file) to an MP4, optionally also GIF and WebM.

    A video program (a Pillow/OpenCV script that writes its own video) is run instead,
    and the video it writes is saved as the MP4.
    """
    formats: list[ExportFormat] = []
    if gif:
        formats.append("gif")
    if webm:
        formats.append("webm")
    try:
        text = _read_source(source)
        if source_kind(text, mode) is SourceKind.program:
            _render_program(source, text, output, tuple(formats))
            return
        loaded = _load(source, mode, duration, text=text, aspect=aspect, fps=fps, theme=theme,
                       style=style, outro=outro, filename=filename)  # fmt: skip
        prepared, plan = _prepare(loaded, preview)
        _warn(plan.warnings)
        target = output or _default_output(source, prepared)
        if target.suffix.lower() != ".mp4":
            raise UserFacingError("The output file must end in .mp4.", field="output")
        with ProgressBar(*_PROGRESS_COLUMNS, console=err, transient=True) as bar:
            task = bar.add_task("Rendering", total=prepared.total_frames)

            def on_progress(p: Progress) -> None:
                what = "Rendering" if p.stage == "frames" else f"Exporting {p.stage.upper()}"
                bar.update(task, completed=p.done, description=what)

            video = render_video(prepared, target, on_progress, source=source,
                                 warnings=plan.warnings, formats=formats,
                                 workers=workers)  # fmt: skip
    except UserFacingError as exc:
        raise _fail(exc) from None
    except KeyboardInterrupt:
        err.print("[yellow]Cancelled; no video was written.[/yellow]")
        raise typer.Exit(code=130) from None
    out.print(
        f"Wrote [bold]{escape(str(video))}[/bold] ({prepared.total_frames} frames, "
        f"{prepared.doc.duration:g} s, {prepared.width}x{prepared.height} @ {prepared.fps} fps)"
    )
    for fmt in formats:
        out.print(f"Wrote [bold]{escape(str(video.with_suffix('.' + fmt)))}[/bold]")


@app.command()
def plan(
    source: SourceArg,
    as_json: Annotated[bool, typer.Option("--json", help="Print the plan as JSON.")] = False,
    preview: PreviewOpt = False,
    mode: ModeOpt = Mode.auto,
    duration: DurationOpt = None,
    aspect: AspectOpt = None,
    fps: FpsOpt = None,
    style: StyleOpt = None,
) -> None:
    """Show elements, timings and warnings without rendering."""
    try:
        loaded = _load(source, mode, duration, aspect=aspect, fps=fps, style=style)
        _, result = _prepare(loaded, preview)
    except UserFacingError as exc:
        raise _fail(exc) from None
    if as_json:
        data = {"plan": result.model_dump(mode="json"),
                "scene": loaded.doc.model_dump(mode="json", by_alias=True)}  # fmt: skip
        typer.echo(json.dumps(data, indent=2))  # ASCII-escaped: safe on any console or pipe
        return
    kind = "script" if loaded.is_script else "code file"
    out.print(
        f"{kind}: {result.duration:g} s, {result.total_frames} frames @ {result.fps} fps, "
        f"{result.width}x{result.height}"
    )
    table = Table("element", "type", "shown (s)", "anims", "notes")
    depth: dict[str, int] = {}
    for e in result.elements:
        depth[e.id] = depth[e.parent] + 1 if e.parent is not None else 0
        notes = ""
        if e.reveal is not None:
            notes = f"{e.reveal} {e.reveal_start:g}-{e.reveal_end:g} s, font {e.font_size:g} px"
            notes += ", scrolls" if e.scroll else ""
        name = "  " * depth[e.id] + e.id  # group members are indented
        table.add_row(name, e.type, f"{e.start:g}-{e.end:g}", str(e.animations), notes)
    out.print(table)
    if result.sounds:
        sounds = Table("sound", "at (s)", "volume")
        for cue in result.sounds:
            sounds.add_row(escape(cue.name), f"{cue.at:g}", f"{cue.volume:g}")
        out.print(sounds)
    _warn(result.warnings)


@app.command()
def still(
    source: SourceArg,
    t: Annotated[
        float | None, typer.Option("--t", help="Time in seconds (default: the end).")
    ] = None,
    output: Annotated[Path, typer.Option("-o", "--output", help="PNG file to write.")] = Path(
        "frame.png"
    ),
    preview: PreviewOpt = False,
    mode: ModeOpt = Mode.auto,
    duration: DurationOpt = None,
    aspect: AspectOpt = None,
    fps: FpsOpt = None,
    theme: ThemeOpt = None,
    style: StyleOpt = None,
    outro: OutroOpt = None,
    filename: FilenameOpt = None,
) -> None:
    """Render a single frame at time T to a PNG."""
    try:
        loaded = _load(source, mode, duration, aspect=aspect, fps=fps, theme=theme,
                       style=style, outro=outro, filename=filename)  # fmt: skip
        prepared, result = _prepare(loaded, preview)
        _warn(result.warnings)
        if t is None:
            # The last moment before any fade-out, so the frame shows the finished scene.
            t = max(0.0, prepared.doc.duration - prepared.doc.fade_out - 1 / prepared.fps)
        canvas = render_still(prepared, t)
        output.parent.mkdir(parents=True, exist_ok=True)
        canvas.save_png(output)
    except UserFacingError as exc:
        raise _fail(exc) from None
    except OSError as exc:
        raise _fail(UserFacingError(f"Could not write {output}: {exc.strerror}.")) from None
    out.print(
        f"Wrote [bold]{escape(str(output))}[/bold] ({prepared.width}x{prepared.height}, t={t:g} s)"
    )


@app.command()
def new(
    path: Annotated[Path, typer.Argument(help="Where to create the script.")] = Path("video.py"),
    force: Annotated[bool, typer.Option(help="Overwrite an existing file.")] = False,
) -> None:
    """Create a starter video script."""
    if path.exists() and not force:
        raise _fail(UserFacingError(f"{path} already exists; pass --force to overwrite it."))
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(STARTER.format(name=path.name), encoding="utf-8")
    except OSError as exc:
        raise _fail(UserFacingError(f"Could not write {path}: {exc.strerror}.")) from None
    out.print(f"Created [bold]{escape(str(path))}[/bold]. Render it with: codemotion render {path}")


@app.command(name="app")
def desktop(
    script: Annotated[
        Path | None, typer.Argument(help="Script or code file to open.", show_default=False)
    ] = None,
) -> None:
    """Open the desktop app."""
    if script is not None and not script.is_file():
        raise _fail(UserFacingError(f"File not found: {script}"))
    from codemotion.desktop.app import run  # Qt loads only for this command

    raise typer.Exit(code=run(script))


@app.command()
def bench(
    workers: WorkersOpt = None,
    as_json: Annotated[bool, typer.Option("--json", help="Print results as JSON.")] = False,
) -> None:
    """Measure render speed and memory against the performance targets."""
    from codemotion.bench import describe_targets, run_bench

    try:
        status = None if as_json else (lambda line: err.print(f"[dim]{escape(line)}[/dim]"))
        results = run_bench(workers, status)
    except UserFacingError as exc:
        raise _fail(exc) from None
    if as_json:
        data = [{"item": r.item, "value": round(r.value, 3), "target": r.target,
                 "unit": r.unit, "passed": r.passed} for r in results]  # fmt: skip
        typer.echo(json.dumps(data, indent=2))  # ASCII-escaped: safe on any console or pipe
        return
    table = Table("measurement", "result", "target", "")
    for r in results:
        mark = "[green]ok[/green]" if r.passed else "[red]over[/red]"
        table.add_row(r.item, f"{r.value:.2f} {r.unit}", f"< {r.target:g} {r.unit}", mark)
    out.print(table)
    out.print(f"[dim]{escape(describe_targets())}[/dim]")


@app.command()
def themes() -> None:
    """List available themes."""
    table = Table("id", "name", "accent")
    for theme in list_themes():
        c = theme.accent.color
        table.add_row(theme.id, theme.name, f"#{c.r:02X}{c.g:02X}{c.b:02X}")
    out.print(table)


def main() -> None:
    app()
