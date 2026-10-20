# CodeMotion — Build Specification

> A local desktop tool that turns a Python code snippet into a short, polished motion-graphic video (≤ 12 s), suitable for Reels, Shorts, LinkedIn and presentations. Everything runs offline on the user's PC.

**How to use this document with Claude Code:** put this file in an empty repository as `SPEC.md`, then prompt:
`Read SPEC.md fully. Implement Milestone 1 only, run the tests, and stop for review.`
Proceed one milestone at a time (section 14). Section 15 contains the rules Claude Code must follow throughout.

---

## 1. Product definition

### 1.1 Problem
Developers and content creators who want to share code on social media currently screenshot it or hand-animate it in After Effects / Premiere. Both are slow, and screenshots don't perform well as video content.

### 1.2 What the tool does
The user pastes Python code, chooses a duration (required, max 12 s) and a few style options, and gets an MP4 in which the code is **presented as a motion graphic**: an editor-style card animates in, the code is revealed (typed or cascaded) with syntax highlighting, key lines are spotlighted, optional program output appears in a terminal panel, and the video ends on a clean hold/outro.

### 1.3 Interpretation note (confirm before building)
"Generate a motion graphic video from Python code" is interpreted as **visualizing the code itself** (code-showcase animation). Two other readings exist and are deliberately **out of scope for v1**, but the architecture must not block them:
- **Code-as-script**: the Python code *describes* an animation (Manim-style) and the tool executes it.
- **Execution visualization**: stepping through the program and animating variables/call stack (Python Tutor-style).

If the product owner meant one of these, stop and revise sections 4–8 before implementation.

### 1.4 Users
- Primary: a developer/content creator producing social posts for a personal brand or a software company.
- Secondary: educators making short clips for students.

### 1.5 Goals
1. Paste code → watchable video in under a minute, no editing skills needed.
2. Output looks professionally designed by default (good typography, easing, spacing, safe areas).
3. 100% local and offline: no accounts, no network calls, no telemetry.
4. Deterministic: same input + settings ⇒ identical video.

### 1.6 Non-goals (v1)
- Audio / music / typing sounds.
- Languages other than Python (but the tokenizer layer must be language-agnostic so adding more is trivial).
- Timeline editing / keyframe UI.
- Cloud rendering, AI/LLM features.

---

## 2. Requirements

### 2.1 Functional requirements

| ID | Requirement |
|----|-------------|
| F1 | Accept Python code via (a) web UI editor, (b) CLI file path, (c) CLI stdin. |
| F2 | **Duration is required input.** UI shows the field explicitly; CLI prompts interactively if `--duration` is missing. Range **3.0–12.0 s**, step 0.5 s. Values outside range are rejected with a clear message. |
| F3 | Validate code with `ast.parse`. Syntax errors are shown (line, column, message) as a **warning**, not a blocker — partial snippets are legitimate. |
| F4 | Syntax highlighting via Pygments `PythonLexer`, mapped to theme colors. |
| F5 | Animation styles: `typewriter`, `line_cascade`, `fade_in` (instant code, card animation only). |
| F6 | Optional focus/highlight of line ranges (e.g. `3-5,9`): dim other lines, accent bar, gentle camera zoom. |
| F7 | Optional output panel: user types expected output text (default), **or** opts in to run the code locally to capture stdout (see 9.2). |
| F8 | Optional text overlays: title (top), caption/handle (bottom), watermark image (local PNG). |
| F9 | Aspect presets: `9:16` (1080×1920), `1:1` (1080×1080), `16:9` (1920×1080). FPS: 30 or 60. |
| F10 | Themes: at least 4 bundled (2 dark, 1 light, 1 high-contrast), defined in TOML files, user-extensible by dropping a file into a themes folder. |
| F11 | **Plan before render**: an endpoint/command returns the computed timeline, layout (font size, scroll needed?) and warnings, so the UI can show issues before a full render. |
| F12 | **Preview**: render a single still at any time `t`, and a fast low-res preview video (540p, 15 fps). |
| F13 | Full render to MP4 (H.264, yuv420p, faststart). Optional GIF and WebM export. |
| F14 | Progress reporting (percentage + ETA) and cancellation. |
| F15 | Render history: outputs saved to `./output/` with a JSON sidecar of the settings used; UI lists recent renders. |
| F16 | Settings can be saved/loaded as a JSON preset. |

### 2.2 Input limits (enforced, configurable constants)

| Limit | Value | Behaviour when exceeded |
|-------|-------|-------------------------|
| Max lines | 60 | Reject with message |
| Max line length | 100 chars | Reject with message (no auto-wrap; wrapped code misleads readers) |
| Max total chars | 3,000 | Reject |
| Tabs | — | Converted to 4 spaces |
| Trailing whitespace / trailing blank lines | — | Stripped |
| Encoding | UTF-8 | Non-UTF-8 rejected |

### 2.3 Non-functional requirements

| ID | Requirement |
|----|-------------|
| N1 | **Offline**: zero network access at runtime. Fonts, JS/CSS, ffmpeg are bundled or installed with dependencies. No CDNs. A test enforces this (section 13). |
| N2 | **Local-only server**: bind to `127.0.0.1` only, never `0.0.0.0`. |
| N3 | **Performance**: 12 s @ 30 fps, 1080×1920 renders in **≤ 30 s** on a typical 4-core laptop; still preview in ≤ 300 ms. |
| N4 | **Determinism**: no wall-clock or unseeded randomness in rendering. Any "randomness" (e.g. typing rhythm) uses a seed derived from the input hash. |
| N5 | **Cross-platform**: Windows 10+, macOS 12+ (Intel & Apple Silicon), Ubuntu 22.04+. |
| N6 | **Python 3.11+**, managed with `uv`; one-command install and run. |
| N7 | Frame-exact duration: output frame count = `round(duration × fps)`; ffprobe duration within ±1 frame. |
| N8 | Memory: never hold all frames in RAM; stream frames to the encoder. |

---

## 3. Tech stack decision

Python is sustainable for this tool: the hard parts (tokenizing, 2D drawing, encoding) all have mature, well-maintained Python libraries, and the whole thing ships as one `uv` project.

| Concern | Choice | Why |
|---------|--------|-----|
| Language/runtime | Python 3.11+ | Requested; good ecosystem for this. |
| Packaging | `uv` + `pyproject.toml` (src layout) | Fast, reproducible lockfile, single command to run. |
| Tokenizing | **Pygments** | De-facto standard, robust on broken code, supports 500+ languages for future expansion. |
| Code validation | stdlib `ast` | No dependency. |
| 2D rendering | **skia-python** | Anti-aliased sub-pixel text (no jitter on moving text), blur, shadows, gradients, rounded rects, fast. Prebuilt wheels for Win/macOS/Linux. |
| Pixel buffers | numpy | Zero-copy hand-off from Skia to encoder. |
| Encoding | **ffmpeg via `imageio-ffmpeg`** (bundled static binary), driven directly via `subprocess` pipe | No system ffmpeg install needed; streaming raw frames to stdin. |
| Models/validation | pydantic v2 | Single source of truth for request schema (shared by CLI, API, presets). |
| CLI | Typer + Rich | Prompts, progress bars. |
| Local server | FastAPI + Uvicorn | Simple JSON API, SSE progress. |
| Frontend | Plain HTML/CSS/JS, **no build step**, CodeMirror 5 vendored locally (MIT) | Fully offline, nothing to compile, easy to maintain. |
| Fonts | JetBrains Mono (code) + Inter (captions), both OFL, bundled with license files | Consistent output on every OS. |
| Tests | pytest, pytest-socket, ruff, mypy | Quality + offline enforcement. |

### Rejected alternatives (record in `docs/decisions.md`)
- **Manim**: heavy native deps (cairo, pango, optional LaTeX), slow, opinionated scene model — overkill for a fixed set of layouts.
- **MoviePy**: convenient for compositing clips, but slow for per-frame vector drawing and its API changed significantly in v2.
- **Remotion (React)**: excellent output, but requires Node + headless Chromium and has a paid company license above a small team size. Not "Python-first".
- **Pillow-only rendering**: viable but text positions snap to whole pixels, causing visible jitter in slow motion; weaker effects. Keep as a possible fallback backend only.

**Risk & mitigation:** if `skia-python` ever lacks a wheel for a target platform, the renderer sits behind a `Canvas` protocol (section 6.6) so a `pycairo` or Pillow backend can be swapped in without touching scenes.

---

## 4. UX specification

### 4.1 Web UI layout (single page at `http://127.0.0.1:7878`)
```
┌──────────────────────────────────────────────────────────────────┐
│ CodeMotion                                     [Load preset][Save]│
├───────────────────────────────┬──────────────────────────────────┤
│ CODE                          │ PREVIEW                          │
│ ┌───────────────────────────┐ │ ┌──────────────┐  scrubber ─●── │
│ │ CodeMirror (Python mode)  │ │ │  still frame │  t = 4.2 s     │
│ │                           │ │ │  at time t   │                │
│ └───────────────────────────┘ │ └──────────────┘                 │
│ ⚠ Syntax warning: line 4 ...  │ Warnings / plan summary          │
├───────────────────────────────┤                                  │
│ SETTINGS                      │ [Quick preview]  [Render video]  │
│ Duration *  [ 8.0 ] s (3–12)  │ ███████████░░░ 72%  ETA 6 s  [✕] │
│ Format   (9:16)(1:1)(16:9)    │                                  │
│ FPS      (30)(60)             │ <video player of last render>    │
│ Theme    [Midnight ▾]         │ [Download MP4] [GIF] [Open folder]│
│ Style    [Typewriter ▾]       │                                  │
│ Focus lines [ 3-5 ]           │ RECENT RENDERS                   │
│ File name  [ main.py ]        │ thumbnail • name • 8s • 9:16     │
│ Title / Caption / Watermark   │                                  │
│ Output panel: (off)(text)(run)│                                  │
└───────────────────────────────┴──────────────────────────────────┘
```

### 4.2 Interaction rules
- Duration field is visibly marked required; default value shown is **8.0** but the field is highlighted until the user touches it or confirms.
- Any settings change → debounced (400 ms) call to `/api/plan` and `/api/preview?t=<scrubber>`; warnings update live.
- Render button disabled while a render is running (one job at a time); shows Cancel instead.
- Errors are human-readable ("Your code has 74 lines; the maximum is 60."), never stack traces.
- Opening the app auto-launches the default browser (`--no-browser` to disable).

### 4.3 CLI
```
codemotion serve [--port 7878] [--no-browser]
codemotion render INPUT.py|- [--duration 8] [--aspect 9:16] [--fps 30]
                  [--theme midnight] [--style typewriter] [--focus 3-5]
                  [--title "..."] [--caption "..."] [--watermark logo.png]
                  [--output-text "..."] [--run-code] [--preset preset.json]
                  [-o out.mp4] [--gif] [--webm]
codemotion plan INPUT.py --duration 8 ...      # prints timeline + warnings as a table / --json
codemotion still INPUT.py --t 4.2 -o frame.png
codemotion themes                               # list themes
```
If `--duration` is omitted, the CLI **prompts** for it (validated 3–12).

---

## 5. Motion design specification

These defaults define the "designed" look. All numbers are relative to a 1080 px short edge and scale proportionally.

### 5.1 Composition
- **Background**: two-stop linear gradient from the theme, slowly rotating (≤ 15° over the whole video) plus 2 large, heavily blurred color blobs drifting slowly. Subtle film grain (opacity 3%, seeded).
- **Editor card**: rounded rect (radius 28), theme card color, drop shadow (blur 60, y-offset 24, 35% black), 1 px inner border at 8% white.
- **Title bar**: three window dots (generic colors from theme, not branded), filename tab centered/left (default `main.py`).
- **Code area**: padding 48; line numbers in muted color, right-aligned gutter; line height 1.5 × font size.
- **Safe areas** (social UI overlays): 9:16 → top 12%, bottom 18%, sides 6%. 1:1 and 16:9 → 6% all sides. Card and text must stay inside.

### 5.2 Font sizing (layout engine)
1. Compute the largest font size such that the longest line + gutter fits the card width and all lines fit the card height inside the safe area.
2. Clamp to `[min_font, max_font]` = 9:16: [26, 56]; 1:1: [24, 52]; 16:9: [22, 48] px.
3. If the height doesn't fit at `min_font`, enable **camera scroll** (5.4) and emit warning `"Code is long; it will scroll."`.
4. If width doesn't fit at `min_font`, reject: `"Line N is too wide for this format; shorten it or use 16:9."`

### 5.3 Phases
| Phase | Default share | Content |
|-------|---------------|---------|
| Intro | 0.8 s fixed | Background fades in; card scales 0.92→1.0 and fades in with spring easing; title slides down. |
| Reveal | flexible | Code appears according to style. |
| Focus | 1.8 s (if focus set) | Non-focused lines → 35% opacity, accent bar (theme accent @ 15% alpha) sweeps in, camera zooms to 1.06–1.12 centered on range. |
| Output | 1.6 s (if output set) | Terminal panel slides up below/over card; `$ python main.py` prompt, then output typed quickly. |
| Hold/Outro | ≥ 1.0 s | Everything settles; caption/handle fades in; last 0.3 s gentle fade or none (option `outro: fade|hold`). |

### 5.4 Reveal styles
- **typewriter**: characters revealed in reading order; blinking block cursor (530 ms period) when idle; small seeded pauses at line ends (≤ 60 ms). Leading indentation appears instantly (don't "type" spaces).
- **line_cascade**: each line animates opacity 0→1 and translateY +24→0 over 0.35 s, `easeOutCubic`; stagger = `min(0.08 s, 0.6 × reveal_time / lines)`.
- **fade_in**: all code fades in over 0.5 s with the card.
- **Camera scroll** (long code): camera keeps the active line in the lower third, moving with critically damped spring (no overshoot).

### 5.5 Easing library
`linear, easeInOutCubic, easeOutCubic, easeOutBack, easeOutExpo, spring(stiffness, damping)` — implemented as pure functions `f(p: float) -> float` on [0, 1]. Spring is pre-sampled analytically (no simulation drift).

### 5.6 Timeline planner algorithm (deterministic, frame-based)
Work in **integer frames** to avoid rounding drift: `N = round(duration × fps)`.
1. Reserve fixed frames: intro, hold (min 1.0 s), plus focus and output if enabled.
2. `reveal_frames = N − reserved`. If `reveal_frames < 0.6 s`, drop optional phases in priority order **output → focus**, each time adding a warning, and recompute. If still too short, shrink intro to 0.5 s and hold to 0.6 s. If still impossible, return error (only possible at very short durations with huge code — in practice the 3 s minimum prevents this).
3. For `typewriter`: required speed = `chars / reveal_seconds`. If > **45 chars/s** (readability cap), automatically switch to `line_cascade` and warn `"Too much code to type in Xs; using line cascade instead."`
4. If any leftover frames remain (e.g. typing at capped speed finishes early), add them to Hold.
5. Assert: sum of phase frames == N.

The plan output (JSON) contains: phase list with start/end frames, effective style, font size, scroll flag, warnings.

---

## 6. Architecture

### 6.1 Pipeline
```
code + settings
   │
   ▼
[validate] ── RenderRequest (pydantic) ── limits, normalization (tabs, whitespace)
   │
   ▼
[analyze] ── ast check (warnings) ── Pygments tokens → Line[Token(text, kind)]
   │
   ▼
[layout]  ── font size, card rect, glyph positions per char (measured with Skia)
   │
   ▼
[plan]    ── Timeline (phases in frames, effective style, warnings)
   │
   ▼
[scene]   ── Scene graph: nodes whose properties are functions of frame index
   │
   ▼
[render]  ── for frame in 0..N-1: Canvas.draw(scene.at(frame)) → RGBA bytes
   │
   ▼
[encode]  ── stream to ffmpeg stdin → MP4 (+ optional GIF/WebM)
```
Each stage is a pure function of its inputs (except encode), which makes it testable and deterministic.

### 6.2 Repository layout
```
codemotion/
├─ pyproject.toml
├─ uv.lock
├─ README.md
├─ CLAUDE.md                     # short pointer to SPEC.md + coding rules
├─ SPEC.md
├─ docs/decisions.md
├─ src/codemotion/
│  ├─ __init__.py
│  ├─ __main__.py                # python -m codemotion
│  ├─ cli.py
│  ├─ config.py                  # constants, limits, aspect presets, paths
│  ├─ models.py                  # pydantic: RenderRequest, Plan, Phase, JobStatus...
│  ├─ errors.py                  # UserFacingError with friendly messages
│  ├─ analysis/
│  │  ├─ normalize.py
│  │  ├─ validate.py             # ast check → list[CodeWarning]
│  │  └─ tokenize.py             # Pygments → list[Line]
│  ├─ layout/
│  │  ├─ fonts.py                # load bundled fonts, metrics cache
│  │  └─ layout.py               # fit font size, card rect, glyph positions
│  ├─ timeline/
│  │  ├─ easing.py
│  │  ├─ keyframes.py            # Track(keyframes) → value at frame
│  │  └─ planner.py
│  ├─ scenes/
│  │  ├─ base.py                 # Node protocol: draw(canvas, frame, ctx)
│  │  ├─ background.py
│  │  ├─ window.py               # card, shadow, title bar, tab
│  │  ├─ code_block.py           # text, gutter, reveal logic
│  │  ├─ cursor.py
│  │  ├─ focus.py
│  │  ├─ terminal.py
│  │  ├─ overlays.py             # title, caption, watermark
│  │  └─ compose.py              # build Scene from (layout, plan, theme, request)
│  ├─ render/
│  │  ├─ canvas.py               # Canvas protocol + SkiaCanvas implementation
│  │  ├─ renderer.py             # frame loop, progress callback, cancel token
│  │  └─ encoder.py              # ffmpeg pipe, MP4/GIF/WebM, ffprobe check
│  ├─ themes/
│  │  ├─ loader.py               # load + validate TOML, user themes dir
│  │  ├─ midnight.toml  ├─ ember.toml  ├─ paper.toml  └─ contrast.toml
│  ├─ runner/
│  │  └─ executor.py             # optional opt-in code execution
│  ├─ server/
│  │  ├─ app.py                  # FastAPI app, routes
│  │  ├─ jobs.py                 # single-worker job queue, SSE progress, cancel
│  │  └─ static/
│  │     ├─ index.html  ├─ app.js  ├─ styles.css
│  │     └─ vendor/codemirror/   # vendored, with LICENSE
│  └─ assets/fonts/              # JetBrainsMono-*.ttf, Inter-*.ttf, OFL.txt
├─ tests/
│  ├─ fixtures/ (sample snippets, golden frames)
│  └─ test_*.py
└─ output/                        # gitignored
```

### 6.3 Core data models (pydantic v2, abbreviated)
```python
class Aspect(str, Enum):
    portrait = "9:16"
    square = "1:1"
    landscape = "16:9"


class Style(str, Enum):
    typewriter = "typewriter"
    line_cascade = "line_cascade"
    fade_in = "fade_in"


class OutputMode(str, Enum):
    off = "off"
    text = "text"
    run = "run"


class RenderRequest(BaseModel):
    code: str
    duration: float = Field(ge=3.0, le=12.0, multiple_of=0.5)  # required, no default
    aspect: Aspect = Aspect.portrait
    fps: Literal[30, 60] = 30
    theme: str = "midnight"
    style: Style = Style.typewriter
    focus: str | None = None  # "3-5,9" → parsed & validated against line count
    filename: str = "main.py"
    title: str | None = Field(None, max_length=60)
    caption: str | None = Field(None, max_length=80)
    watermark_path: Path | None = None
    output_mode: OutputMode = OutputMode.off
    output_text: str | None = Field(None, max_length=600)
    outro: Literal["fade", "hold"] = "hold"
    formats: set[Literal["mp4", "gif", "webm"]] = {"mp4"}
    preview: bool = False  # True → 540p, 15 fps


class Phase(BaseModel):
    name: str
    start: int
    end: int  # frames, end exclusive


class Plan(BaseModel):
    total_frames: int
    fps: int
    effective_style: Style
    font_size: float
    scroll: bool
    phases: list[Phase]
    warnings: list[str]
```

### 6.4 Theme file format (`themes/midnight.toml`)
```toml
name = "Midnight"
[background]  gradient = ["#0B1020", "#1B1440"]  blobs = ["#5B5BF7", "#C04BD8"]
[card]        fill = "#11162A"  border = "#FFFFFF14"  shadow = "#00000059"
[chrome]      dots = ["#FF5F57", "#FEBC2E", "#28C840"]  tab_text = "#9AA4BF"
[text]        default = "#E6E9F2"  gutter = "#4A5272"  cursor = "#E6E9F2"
[accent]      color = "#7C7CFF"
[tokens]      # keys map to Pygments token types; fallback to parent type then text.default
Keyword = "#C792EA"  "Name.Function" = "#82AAFF"  "Name.Class" = "#FFCB6B"
"Name.Builtin" = "#89DDFF"  String = "#C3E88D"  Number = "#F78C6C"
Comment = "#5C6688"  Operator = "#89DDFF"  "Name.Decorator" = "#FFCB6B"
[terminal]    fill = "#0A0D1A"  prompt = "#7C7CFF"  text = "#C8CCDA"
```
Themes are validated on load (all colors parse, required keys present). Users can add themes to `~/.codemotion/themes/`.

### 6.5 Scene model
- A `Node` has `draw(canvas, frame, ctx)`; animated properties are `Track`s of keyframes (frame, value, easing) evaluated per frame.
- `compose.build_scene(request, layout, plan, theme) -> Scene` is the only place that knows phase timing; nodes don't read the plan directly.
- **Caching**: per-line Skia `TextBlob`s and the static background layer (if not animated) are built once; per frame only transforms/opacity/clip change.

### 6.6 Canvas abstraction
```python
class Canvas(Protocol):
    def clear(self, color) -> None
    def save(self) / restore(self) / translate(x, y) / scale(s) / clip_rect(rect)
    def rect(self, rect, paint) / round_rect(self, rect, r, paint)
    def shadow(self, rect, r, blur, dy, color)
    def text_blob(self, blob, x, y, paint)
    def image(self, img, rect, opacity)
    def pixels(self) -> memoryview   # RGBA, row-major
```
`SkiaCanvas` is the v1 implementation. Scenes depend only on this protocol.

### 6.7 Renderer & encoder
- Single process first (M2). Encoder command (via `imageio_ffmpeg.get_ffmpeg_exe()`):
  `ffmpeg -y -f rawvideo -pix_fmt rgba -s WxH -r FPS -i - -c:v libx264 -preset medium -crf 18 -pix_fmt yuv420p -movflags +faststart out.mp4`
- Frames written to stdin as raw bytes; stderr captured and surfaced on failure.
- Write to a temp file in `output/`, rename atomically on success; delete on cancel/failure.
- GIF: second pass from the MP4 using `palettegen`/`paletteuse`, scaled to 540 px wide, 15 fps.
- M5 optimization: `ProcessPoolExecutor` workers each own a SkiaCanvas and render frame ranges; the main process writes bytes to ffmpeg strictly in order (bounded queue to cap memory). Skia objects are never pickled — workers rebuild the scene from the `RenderRequest`.
- Verify output with ffprobe-equivalent check (`ffmpeg -i` parse or frame count) in tests.

### 6.8 Local API
| Method | Path | Purpose |
|--------|------|---------|
| GET | `/` | UI |
| GET | `/api/themes` | list themes (name, preview colors) |
| GET | `/api/presets` | aspect/fps/style options + limits (UI reads limits from here, not hard-coded) |
| POST | `/api/plan` | body `RenderRequest` → `Plan` + code warnings |
| POST | `/api/still?t=4.2` | → PNG (scaled to 540 px short edge) |
| POST | `/api/render` | → `{job_id}`; 409 if a job is running |
| GET | `/api/jobs/{id}` | status, progress, output paths |
| GET | `/api/jobs/{id}/events` | SSE: `progress`, `done`, `error` |
| POST | `/api/jobs/{id}/cancel` | cooperative cancel (checked every frame) |
| GET | `/api/files/{name}` | serve outputs from `output/` only (path traversal guarded) |
| GET | `/api/history` | recent renders from sidecar JSON files |

Errors: `UserFacingError` → HTTP 422 `{ "error": "<friendly message>", "field": "duration" }`.

---

## 7. Optional code execution (F7, `output_mode = "run"`)
Off by default. UI shows a clear notice: *"This runs your code on this computer. Only run code you trust."*
- `subprocess.run([sys.executable, "-I", script], cwd=<temp dir>, env=<minimal env>, timeout=5, capture_output=True)`
- stdout truncated to 600 chars / 12 lines; stderr shown in red if exit code ≠ 0.
- No stdin (`stdin=DEVNULL`). Temp dir removed afterwards.
- Document honestly that this is **isolation, not a sandbox** (no network/filesystem restriction cross-platform).

---

## 8. Error handling & edge cases
- Empty code → "Paste some Python code first."
- Only whitespace/comments → allowed (it's still showable).
- Non-ASCII identifiers/strings/emoji → fonts must render; emoji not in the font fall back to a bundled fallback or are replaced with `□` and a warning.
- Very short lines with huge font → clamp to `max_font`.
- Focus range out of bounds → 422 with message.
- Watermark: PNG/JPG only, ≤ 5 MB, scaled to max 12% of short edge, bottom-right inside safe area.
- ffmpeg missing/failed → message including the last 10 lines of ffmpeg stderr.
- Disk full / write error → friendly message, partial file removed.
- Cancel mid-render → ffmpeg terminated, temp file removed, job status `cancelled`.

---

## 9. Security & privacy
- Server binds `127.0.0.1`; reject requests whose `Host` header isn't `127.0.0.1:<port>` or `localhost:<port>` (DNS-rebinding guard).
- No outbound network calls anywhere; no analytics; no update checks.
- File serving restricted to `output/` with resolved-path checks.
- User code is never executed unless `output_mode="run"` is explicitly chosen per render.

---

## 10. Performance targets & budget
| Item | Target |
|------|--------|
| `/api/plan` | < 50 ms |
| `/api/still` | < 300 ms |
| Quick preview (540p, 15 fps, 12 s) | < 5 s |
| Full 1080×1920 @ 30 fps, 12 s | < 30 s (M5, 4 cores) |
| Peak RAM during render | < 800 MB |

Add a `codemotion bench` command (M5) that renders fixtures and prints fps.

---

## 11. Installation & running (README must include)
```
# prerequisites: uv (https://docs.astral.sh/uv/)
git clone <repo> && cd codemotion
uv sync
uv run codemotion serve          # opens http://127.0.0.1:7878
uv run codemotion render examples/fib.py --duration 8 -o fib.mp4
```
No system ffmpeg or fonts required.

---

## 12. Bundled examples
`examples/` with 5 snippets of varying length: `hello.py` (3 lines), `fib.py` (10), `fastapi_app.py` (20), `dataclass.py` (25), `long_script.py` (55, forces scroll). Used by tests, bench and README GIFs.

---

## 13. Testing strategy
- **Unit**
  - `normalize`: tabs, trailing whitespace, limits.
  - `tokenize`: concatenating all token texts reproduces the normalized source exactly, for every fixture.
  - `planner`: phase frames sum to `N` for all combinations of duration (3–12 by 0.5) × fps × style × focus × output; fallback rules trigger with expected warnings; typewriter speed cap.
  - `layout`: font size within clamps; scroll flag correct; width rejection.
  - `easing`: f(0)=0, f(1)=1, monotonic where expected; spring settles.
  - `themes`: all bundled themes load and validate.
- **Golden-frame snapshots**: render frames at fixed `t` for fixtures; compare to stored PNGs with per-pixel tolerance (mean abs diff < 1.0). Update via `pytest --update-golden`.
- **Integration**: render each fixture at preview quality → file exists, frame count == round(d × fps) (±0), resolution correct, codec h264.
- **Determinism**: render twice → identical frame hashes.
- **Offline**: whole test suite runs with `pytest-socket` `--disable-socket` (allowing only localhost for API tests).
- **API**: FastAPI TestClient for every endpoint, including 409 on concurrent render, cancel, path traversal attempt, host-header rejection.
- CI-ready commands: `uv run ruff check . && uv run ruff format --check . && uv run mypy src && uv run pytest`.

---

## 14. Milestones (build in order; stop for review after each)

**M1 — Static frame**
Scaffold (uv, src layout, ruff, mypy, pytest), config/models/errors, normalize + validate + tokenize, fonts, layout, themes (midnight only), SkiaCanvas, background + window + code_block (fully revealed). `codemotion still` writes a PNG.
*Done when:* stills look correct for all 3 aspects; tokenize/layout tests pass; golden frames committed.

**M2 — Animation + video**
Easing, keyframes, planner, compose, renderer, encoder. Styles `typewriter` + `fade_in`, intro/hold/outro. `codemotion render` and `plan` work; duration prompt when missing.
*Done when:* MP4s for all fixtures play correctly; frame-count and determinism tests pass.

**M3 — Full motion design**
`line_cascade`, camera scroll, focus, terminal panel (text mode), overlays (title/caption/watermark), remaining 3 themes, GIF/WebM export, optional run mode.
*Done when:* every option in section 2.1 works from the CLI; planner fallback warnings verified.

**M4 — Local web app**
FastAPI routes, job queue with SSE progress and cancel, UI per section 4 with vendored CodeMirror, live plan + still preview, history, presets, browser auto-open, security guards.
*Done when:* full flow works offline in the browser; API tests pass.

**M5 — Performance & polish**
Multiprocess rendering, caching, `bench` command, README with GIFs, `docs/decisions.md`, error-message review, cross-platform smoke test notes.
*Done when:* performance targets in section 10 are met or documented with measurements.

---

## 15. Rules for Claude Code
1. Read this whole spec before writing code. Implement **one milestone at a time** and stop for review.
2. Keep stages pure and side-effect-free except the encoder, server and executor.
3. Never add a dependency not listed in section 3 without asking; never add a CDN link or any runtime network call.
4. All user-visible errors go through `UserFacingError` with plain-English messages.
5. Type-hint everything; `mypy --strict` on `src/` must pass from M2 onward.
6. Every new module gets tests in the same milestone.
7. Use constants from `config.py` — no magic numbers in scene code except the design values from section 5, which also live in `config.py`.
8. After each milestone: run the full check command (section 13), render the example fixtures, and report what was built, what's left, and any spec deviations with reasons.
9. If something in this spec is ambiguous or seems wrong, ask instead of guessing.
10. Fonts and vendored JS must be added with their license files.

---

## 16. Future extensions (design for, don't build)
- More languages (Pygments lexer + language-specific filename default).
- Diff animation: show "before → after" code with morphing lines.
- Typing sounds / background music track.
- Execution visualization mode (variables, call stack) — section 1.3.
- Brand kits (logo, colors, fonts) saved per client.
- Batch rendering from a folder of snippets.
- Packaged desktop app (PyInstaller / Briefcase) for non-developers.
