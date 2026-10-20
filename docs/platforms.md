# Platforms

CodeMotion is built and tested on **Windows 11** (Python 3.13). The code is written to run on
macOS and Linux too (SPEC N5), but those haven't been tested yet. This page lists what
differs per platform and what to check before calling a platform supported.

## What every platform needs

| Piece | Source | Notes |
|-------|--------|-------|
| Python 3.11+ | uv | `uv sync` installs everything below |
| skia-python | PyPI wheels | Windows, macOS (x86-64, arm64) and Linux (manylinux) wheels exist |
| ffmpeg | imageio-ffmpeg wheel | bundled static build; no system ffmpeg needed |
| Qt 6 (desktop app) | PySide6 wheels | video playback uses Qt's FFmpeg backend, also in the wheel |
| Fonts | `src/codemotion/assets/fonts` | Inter and JetBrains Mono (OFL), loaded from files |

## Differences handled in code

- **Process start method.** Render workers always use `spawn` (the Windows default; macOS
  and Linux would otherwise `fork`), so every platform behaves the same and no state from
  the parent (Qt, open files) leaks into workers. Each worker rebuilds the scene from the
  `SceneDoc`.
- **Shared memory.** Frames travel from workers through `multiprocessing.shared_memory`.
  On Linux this lives in `/dev/shm`; containers with a tiny `/dev/shm` (Docker's default
  is 64 MB) should be started with `--shm-size=256m` or rendered with `--workers 1`.
- **Peak memory** (`codemotion bench`) is the private commit on Windows and the resident
  set (`ru_maxrss`) elsewhere; resident sets count shared pages in every process, so the
  macOS/Linux figure overstates multiprocess renders a little.
- **OpenBLAS** is limited to one thread on import (`OPENBLAS_NUM_THREADS=1`); otherwise
  numpy commits ~30 MB per core in every worker. CodeMotion doesn't use BLAS.
- **Script child process.** In a source checkout it is `python -m codemotion.script.child`;
  in the packaged app the console executable runs it (`codemotion-cli --codemotion-script-child`),
  because a windowed app has no output streams to capture.
- **Windows file names ignore case**, so the packaged CLI is `codemotion-cli.exe`, not
  `codemotion.exe` (which would collide with `CodeMotion.exe`).
- **No console windows.** On Windows, ffmpeg and script children start with
  `CREATE_NO_WINDOW`.
- **Default output folder** in the packaged app is `~/Videos/CodeMotion` (the install
  folder may be read-only); in a checkout it is `./output`.

## Packaging

`uv run python packaging/build.py` builds `build/dist/CodeMotion/` with PyInstaller, runs
smoke tests on the result (CLI, a script render with worker processes and a GIF, a code
file, standard-library imports from a script, and `CodeMotion.exe --self-test`), then
zips it. It currently refuses to run on other platforms.

To port it:

- **macOS:** build a `.app` with PyInstaller's `BUNDLE` step (windowed executable), add an
  `.icns` icon, keep the console executable inside `Contents/MacOS` next to the app binary
  (the runner looks for it next to `sys.executable`), then sign and notarize. Check that
  Qt Multimedia's FFmpeg plugin is collected, and that `~/Movies` is a better default
  output folder than `~/Videos`.
- **Linux:** a onedir build works the same way. Qt needs the `xcb` platform plugin's
  system libraries (`libxcb-cursor0` and friends). An AppImage is the simplest
  distribution.

## Before calling a platform supported

1. `uv sync && uv run pytest` passes (the desktop tests run on offscreen Qt).
2. `uv run codemotion bench` meets the SPEC section 10 targets, or the results are
   recorded in `docs/decisions.md`.
3. The packaged build's smoke tests pass, and a person has opened the app, scrubbed,
   played a quick preview and rendered a video.
