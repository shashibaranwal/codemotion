# Decisions

## Rejected alternatives (SPEC §3)

- **Manim**: heavy native deps (cairo, pango, optional LaTeX), slow, opinionated scene model.
- **MoviePy**: slow for per-frame vector drawing; API changed significantly in v2.
- **Remotion**: needs Node + headless Chromium; paid company license above a small team size.
- **Pillow-only rendering**: text snaps to whole pixels (visible jitter); weaker effects.
  Kept as a possible fallback backend behind the `Canvas` protocol.

## Milestone 1 choices (not fixed by the spec)

- **Extra modules.** `geometry.py` holds the backend-neutral `Rect` and `Color` types shared
  by layout, themes, scenes and the canvas. `pipeline.py` holds the pure
  validate → analyze → layout front half, so the CLI, API and renderer share it.
- **Canvas protocol additions.** Added `circle()`, `make_text_blob()` and `make_image()`.
  `Paint` is a backend-neutral dataclass (colour, opacity, linear gradient, blur, stroke), so
  scenes never touch Skia objects; text blobs and images are opaque, backend-owned handles.
- **Token kinds** are dotted Pygments types without the `Token.` prefix
  (`Literal.String.Double`). Theme keys are resolved through Pygments' aliases, so `String`
  matches `Literal.String`.
- **Missing glyphs** (emoji, CJK; JetBrains Mono has neither) are replaced with `□` during
  layout, with one warning. This keeps every character in one monospace cell.
- **Title bar.** 64 px tall (was 76 in M1), filename centred (spec: "centered/left"), set in Inter.
  Dot geometry and the separator line are design values in `config.py`.
- **Grain seed** is derived from the SHA-256 of the normalized code only, so changing a caption
  doesn't change the grain pattern.
- **Shadow blur** is treated as a CSS-style blur radius (Gaussian sigma = blur / 2).
- **Scroll at rest.** When code scrolls, the visible area is a whole number of lines, so no
  line is cut in half in a still.
- **Golden frames** are stored at preview resolution (540 px short edge) to keep the
  repository small (~2.4 MB for 6 frames); full-resolution output is covered by size and
  determinism tests.
- **`still --duration`** follows the same rule as `render`: required, and prompted for when
  stdin is interactive.

## Spec overrides (approved after M1 review)

SPEC 5.1/5.2's numbers made portrait reject lines over ~50 characters and made 25-line code
scroll in 1:1 and 16:9. Changed, at the product owner's request:

| Value | SPEC | Now |
|-------|------|-----|
| Min font 9:16 / 1:1 / 16:9 | 26 / 24 / 22 px | 24 / 21 / 20 px |
| Code padding | 48 px | 40 px |
| Title bar | (unspecified; 76 px in M1) | 64 px |

Result: portrait fits 56-character lines and 33 lines; 1:1 fits 61 characters and 25 lines;
16:9 fits 100 characters and 26 lines. Of the examples, only `long_script.py` scrolls.

## Milestone 2 choices

- **Skia raster performance on Windows.** Every skia-python wheel tested (87.6, 120.0b5,
  138.0, 144.1) runs gradients, image draws, blurs and `saveLayer` at ~50 ns/pixel
  (~110 ms for a 1080×1920 gradient), while solid fills, anti-aliased shapes and text are fast.
  This is presumably Skia's raster pipeline without SIMD in these builds. The first M2
  build ran at 1.9 fps. To fix it, with no new dependencies:
  - The **backdrop** (gradient, glows, card shadow, grain) is computed with numpy at 1/4
    resolution, block-upscaled, then given full-resolution grain as precomputed integer
    offsets (which also dither away block edges). Handed to the canvas with `put_pixels`.
  - **Background blobs** are 2D Gaussian glows (separable) instead of blurred circles.
  - The **card shadow** is an exact separable (erf) blur of the card's bounding rectangle;
    the 28 px corner radius is invisible under a 30 px sigma. It lives in the backdrop and
    follows the card's scale and opacity during the intro.
  - **Group opacity** (`Canvas.save_alpha`) multiplies each primitive's alpha instead of
    using an offscreen layer; during the 0.8 s card fade, overlapping card elements show
    through each other slightly.
  Result: ~31 ms/frame at 1080×1920, ~8 ms at 540×960; a 12 s 1080×1920 render takes
  18–21 s single-process, a 12 s preview ~3 s.
- **Grain is static** (same pattern every frame). Animated grain costs bitrate and reads as
  noise at 3 %.
- **Line cascade was built in M2** (spec: M3) because the planner's typewriter speed-cap
  fallback switches to it; without it, long code couldn't be rendered in M2.
- **Typewriter speed floor.** Typing never goes slower than 15 chars/s; spare time goes to
  the hold (SPEC 5.6 step 4).
- **Cursor blink.** "530 ms period" is read as 530 ms on, 530 ms off (the classic caret
  interval). The cursor is solid while typing and starts blinking after 0.12 s idle.
- **fade_in** has no reveal phase: the code fades in over 0.5 s starting 0.3 s into the
  intro, and the rest goes to the hold.
- **Intro.** The backdrop fades in from black over 0.4 s; the card scales 0.92 → 1.0 with an
  analytic spring (stiffness 180, damping 20: slight overshoot) and fades in over the intro.
  Background rotation is 12° and glow drift 6 % of the short edge, eased over the video.
- **Outro `fade`** fades to black over the last 0.3 s.
- **`still` default time** is the start of the hold (code fully shown).
- **Render outputs.** `render` writes `./output/<name>-<aspect>-<timestamp>.mp4` by default,
  through a temp file renamed on success, plus the JSON sidecar from F15 (request, plan,
  warnings).
- **Not in M2:** camera scroll (M3), so long code shows only the lines that fit.

## v2 pivot: video scripts + desktop app (2026-10-20)

The product owner clarified that the tool takes **Python video scripts** (the code
describes the video) and should be a **desktop** app, not a browser app. Choices made with
them: own lightweight script API (not Manim), PySide6, keep the code card as an element and
the plain-code shortcut, keep ≤ 12 s and the three formats. `SPEC.md` was rewritten (v2);
v1 is in `docs/SPEC-v1-code-showcase.md`.

- **Script → document → render.** Scripts run in a child process and only produce a
  validated `SceneDoc` (JSON). Rendering never runs user code, is deterministic, and the
  desktop app can scrub stills from the cached document. Rejected: running scripts in the
  app's own process (a hang or crash would take the app down; state leaks between runs).
- **Isolation, not a sandbox.** Scripts are trusted the way `python script.py` is; the
  child process adds a timeout and crash isolation only.
- **API validates eagerly.** Each API call validates its arguments with the same pydantic
  models, so errors point at the script line that caused them.
- **Units:** positions are frame fractions (format-independent); sizes and offsets are
  design pixels on a 1080 px short edge (match the rest of the design system).
- **One pipeline.** The plain-code shortcut builds a one-element `SceneDoc` from the
  timeline planner, so code files and scripts share prepare/compose/render. The M2
  phase-based compose was replaced.
- **Grain seed** now derives from the whole scene document (was: the code), so code-file
  golden frames were regenerated (grain pattern only).
- **Auto typing speed** for script `Code` elements is 30 chars/s (20 felt slow next to
  other animations); the 45 chars/s cascade fallback still applies.
- **Highlight** dims other lines to 35 % and adds an accent band; the v1 camera zoom is
  deferred to M5.
- **Shadows** of code cards follow the element's position, scale and opacity (rotation is
  ignored by the shadow).
- **Removed:** v1 F7 "run the code to capture output" (scripts are Python and can compute
  any text themselves), the FastAPI server and browser UI.
- **Text elements don't substitute missing glyphs** (Inter covers Latin, Greek, Cyrillic);
  code still uses □ substitution.

## M4: desktop app (2026-10-20)

- **PySide6, full package.** `PySide6-Essentials` 6.12 no longer includes QtMultimedia
  (needed for the player), so the full `PySide6` package is used (≈ 650 MB installed with
  its add-ons; M6 packaging should strip unused Qt modules). `pytest-qt` is a dev
  dependency. Both are in the SPEC §3 stack.
- **Shared loader.** `script/source.py` (`load_text`, `SourceMode`) turns text into a
  `SceneDoc` for both the CLI and the app; `default_output` and a scene-caching
  `StillRenderer` moved into `render/renderer.py` for the same reason.
- **Threads, not QThreadPool.** Workers are plain Python threads that report back through
  queued Qt signals to objects on the UI thread. `LatestWins` runs one job at a time and
  keeps only the newest waiting request, so typing and scrubbing never queue stale work.
  Script runs still happen in the child process (the thread only waits for it).
- **Preview quality.** The editor's stills come from the 540p preview preparation (fast;
  5–35 ms per still on the examples), and Render re-prepares at full quality.
- **Render uses the editor's current text.** If the text or code-card settings changed
  since the last run, Render/Quick preview run the script first and render that result.
- **Highlighting** is a small regex highlighter (keywords, API names, strings including
  triple-quoted across lines, comments, numbers) instead of Pygments, which tokenizes whole
  files and doesn't fit Qt's per-line highlighting.
- **Line endings** of an opened file are kept on save (CRLF stays CRLF).
- **Examples menu** reads `examples/` from the source checkout; the packaged app (M6) must
  bundle them.
- **Settings** use an INI file via `QSettings` (window layout, splitters, auto-run, output
  folder, last folder, recent files, first-run notice shown).
- **Not done:** a Problems entry for warnings carries no line (warnings come from the scene,
  not a script line); the player has no frame stepping.

## M5: motion breadth (2026-10-20)

- **Themes:** `slate` (dark teal/amber), `daylight` (light), `contrast` (black, white,
  yellow accent). Tests check WCAG contrast: text on the card ≥ 4.5:1 (≥ 7:1 for
  `contrast`), syntax colours ≥ 3:1 (≥ 7:1 for `contrast`), terminal text ≥ 4.5:1.
- **Groups** (added after the constraints video needed a hand-written "card" helper):
  children keep frame coordinates; the group transform pivots on its anchor. A translucent
  group draws through a Skia layer **bounded to its content** (~10 ms per 620×320 layer,
  only while translucent; a full-frame layer costs ~95 ms on the Windows wheels).
  Single elements still use per-primitive `save_alpha`. Code-card shadows follow
  enclosing groups (position, scale, opacity).
- **Lines** draw with round caps; `draw_on` animates the existing `reveal` property, so no
  new animatable property was needed. Arrowheads are filled triangles sized from the width.
- **Outlines** (`stroke`) are drawn inside the shape's size (CSS border-box), so outlined
  and filled shapes of the same size line up.
- **Terminal** replaces v1's output panel: the script supplies the output text (nothing is
  executed). Timing: starts 0.3 s after the element, types at 25 chars/s, prints a line
  every 0.08 s after a 0.35 s pause, then a blinking prompt.
- **Camera scroll** precomputes a per-frame target (revealed line at 70 % of the view, or
  a highlight centred, in whole lines) and follows it with an exact critically damped
  spring step (ω = 9 rad/s), so it never overshoots. Preview (15 fps) and full renders
  sample the same spring at different rates; the difference is invisible.
- **Highlight zoom** defaults to 1.06 (v1 had 1.06–1.12); `zoom=1` turns it off. The card
  scales about the highlighted lines inside the element's transform; the backdrop shadow
  doesn't zoom.
- **Grain seed** now hashes the scene document *without default values*, so adding a
  schema field with a default no longer changes existing videos' grain. This changed the
  seed once (golden frames regenerated; only grain differs, checked with block averages).
- **GIF/WebM** are converted from the finished MP4 by ffmpeg (GIF: 540 px short edge,
  15 fps, `palettegen`/`paletteuse` with Bayer dithering; WebM: VP9 CRF 32). Progress is
  read from ffmpeg's `-progress`. Cancelling during export removes the MP4 and sidecar too,
  so a cancelled render never leaves files. The sidecar lists the exports.
- **`stagger`** is a function, not a method: it works on any list of elements and accepts
  either a method name or a function.
- **Performance note:** full renders take 18–27 s for the 9–12 s examples (budget: 30 s for
  12 s at 1080×1920). `terminal_demo` would be ~32 s at 12 s; M6's multiprocess rendering
  is the planned fix.
- **Lint scope:** `motion_graphic_frame_plan.py` (the user's own file) is excluded from
  ruff; an earlier `ruff check --fix` / `ruff format .` run reformatted it.

## M6: performance and packaging (2026-10-20)

- **Where the time went:** drawing, not encoding. A 12 s 1080×1920 render spent ~17 s
  drawing (half the numpy backdrop, half Skia) and ~4 s in x264 (`medium` is only ~1 s
  slower than `veryfast` here, so the preset stays).
- **Multiprocess rendering** with `spawn` workers that rebuild the scene from the
  `SceneDoc` (pure, so frames are identical; tested by hashing). Frames go through shared
  memory (2 slots per worker) instead of pickling 8 MB per frame, and the parent writes
  them to ffmpeg in order with a bounded number in flight (flat memory, SPEC N8). Rejected:
  `Pool.imap` (unbounded result queue, a full copy per frame).
- **Worker count:** up to 4. 4 → 6 workers saved 0.2 s on the full render but cost
  ~180 MB. Renders under 60 megapixel-frames (~2 s of drawing: short or tiny videos) stay
  in-process, because starting workers takes ~0.5 s.
- **OpenBLAS:** importing numpy committed ~380 MB per process on a 12-core machine (one
  buffer per thread). `codemotion/__init__.py` sets `OPENBLAS_NUM_THREADS=1` (overridable;
  CodeMotion never uses BLAS): each worker dropped from ~460 MB to ~90 MB.
- **Peak memory** is measured as private commit on Windows (working sets count the shared
  frame buffers in every process).
- **Results** (`codemotion bench`): every SPEC section 10 target met. See the README table.
- **Packaging:** PyInstaller one-folder build (faster start than one-file, and antivirus
  tools flag self-extracting one-file builds more often). Two executables share the folder:
  windowed `CodeMotion.exe` and console `codemotion-cli.exe`. The console one also runs
  video scripts for the desktop app, because a windowed process has no output streams to
  capture `print()` from. `multiprocessing.freeze_support()` makes both work as render
  workers. The whole standard library is bundled because scripts may import any of it.
- **Trimmed:** Qt's virtual-keyboard plugin (it pulled in Qt Quick and QML), the PDF image
  plugin and Qt's translations: 305 → 279 MB, 117 MB zipped. The largest remaining parts
  are Qt (~90 MB) and the static ffmpeg (84 MB; Qt Multimedia ships its own FFmpeg DLLs
  too, but they're libraries, not the command-line encoder we pipe frames into).
  `opengl32sw.dll` (20 MB) stays as Qt's fallback for machines without working graphics
  drivers.
- **Verification without a screen:** `CodeMotion.exe --self-test REPORT` opens the window
  offscreen, runs the starter script, draws a still and renders a quick preview;
  `packaging/build.py` runs it plus CLI renders after every build.
- **Icon:** drawn with Skia (`packaging/make_icon.py`); the `.ico` embeds PNGs, so no
  imaging library was added.
- **Not done:** macOS/Linux packages (notes in `docs/platforms.md`); code signing (Windows
  SmartScreen will warn on first run of an unsigned download).

## Sound effects (2026-10-20)

Requested after M6 (the frame plans called for whooshes and a chime). Audio had been out of
scope in the spec; this adds sound effects only, not music tools or audio editing.

- **API:** `video.sound(sound, at=0, volume=1)`; cues live in `SceneDoc.sounds` (up to
  100). `sound` is a built-in name or a file next to the script (resolved to an absolute
  path by the API, like images; checked again when the scene is prepared).
- **Built-in sounds are synthesized** with numpy (`render/audio.py`): whoosh (noise
  through a moving band-pass, swelling, panning left to right), chime (two decaying bell
  partials, E6 then B6), pop (falling sine blip), click (filtered noise tick), riser.
  Rejected: bundling sample files (licensing, size) or downloading them (offline rule).
  Each cue's noise is seeded from the cue, so renders stay deterministic.
- **Files** (WAV/MP3/OGG/FLAC/M4A/AAC/Opus, up to 20 MB) are decoded by the bundled ffmpeg
  to 48 kHz stereo float when rendering, not when planning, so typing in the desktop app
  stays fast.
- **Mixing:** cues are summed into one stereo track exactly as long as the video; if the
  peak exceeds about -1 dBFS the mix is soft-limited (tanh) instead of clipping. Built-in
  sounds peak at -6 dBFS before the cue's volume.
- **Encoding:** the track goes to a temporary WAV beside the output and is muxed as AAC
  192 kb/s into the MP4 (`-map 0:v -map 1:a`), then deleted. WebM exports carry it as Opus
  128 kb/s; GIFs drop it. Renders without sounds are unchanged (no audio stream).
- **Desktop:** the player has an audio output, so quick previews and renders play with
  sound; sound cues appear in the Elements list. Stills and scrubbing are silent.
- **Seeds:** adding the `sounds` field didn't change any video's grain, because the seed
  ignores default values (M5).

## No limit on lines of code (2026-10-21)

Requested so that whole files (thousands of lines) can be shown. Overrides SPEC 2.2's
60-line and 3,000-character limits; the 100-character line length stays, because the
layout has to fit the widest line.

- Code taller than its card already scrolls (M5 camera scroll), so nothing else changes
  in the layout; the gutter widens to fit 4+ digit line numbers.
- Drawing starts at the first line the scroll leaves in view, and highlight dimming is
  worked out only for drawn lines, so a frame costs the same for 5,000 lines as for 50
  (about 65 ms at 1920x1080). Preparing 5,000 lines takes under a second.
- Videos are still at most 12 s long. Typing that much code would be faster than
  `TYPEWRITER_MAX_CPS`, so long code falls back to the line cascade (existing rule) and
  the camera scrolls quickly to follow it.

## Long lines wrap (2026-10-21)

Follows from removing the line-count limit: real files have lines over 100 characters, and
portrait cards fit only about 56. Both the 100-character cap and the "too wide for this
format" error are gone.

- The font still shrinks to fit the widest line, down to the format's minimum. Lines that
  don't fit even then wrap onto extra rows (soft wrap, like an editor), with a warning.
  Rejected: shrinking the font further, which made phone-width cards unreadable.
- Rows break after the last space, comma or opening bracket in the row's second half, else
  at the column limit. Continuation rows hang one indent (4 columns) deeper than their
  line and have no line number.
- `Layout.lines` holds rows; each keeps its source line `number`, and `rows_of(n)` maps a
  line to its rows. Highlights, highlight scroll and zoom use whole lines; reveals,
  the cursor and camera scroll work per row. Lines that fit are laid out exactly as
  before, so existing videos don't change.
- A code area narrower than `WRAP_MIN_COLUMNS` (20) at the minimum font is refused.

## No text or terminal size limits (2026-10-21)

Requested along with unlimited code: text and terminal content have no character or line
limits any more.

- Removed: Text elements' 400 characters; the code-card title (60), caption (80) and
  output text (600); the terminal's 30 output lines, 2,000 characters, 100-character
  lines and 100-character command.
- Kept: labels drawn in fixed chrome (file name and terminal title, 40 characters; prompt,
  8), because longer ones run out of their title bar.
- The terminal behaves like a real one. Text shrinks to fit the width down to
  `TERMINAL_MIN_FONT_FACTOR`, then long lines (and a long command) hard-wrap at the column
  limit. The panel grows with its rows up to the height of the safe area; after that the
  oldest rows scroll off the top as new ones print. A terminal under
  `TERMINAL_MIN_COLUMNS` (16) wide at the smallest font is refused. Terminals that fit
  look exactly as before.

## Video programs: Pillow/OpenCV scripts (2026-10-21)

Requested: paste a script like `motion_graphic.py` (Pillow draws frames, OpenCV writes the
MP4) into the desktop app and get its video. A third kind of source next to video scripts
and code files. New dependencies, approved for this: `pillow` and `opencv-python-headless`
(headless: the regular wheel bundles its own Qt, which clashes with PySide6). They are
bundled into the packaged app only for programs; CodeMotion itself doesn't import them.

- **Detection** (`script/program.py`, `source.source_kind`): not a video script, imports
  PIL / cv2 / imageio / moviepy / skvideo / av, and calls a video writer (`VideoWriter`,
  `get_writer`, `mimsave`, `write_videofile`, `save_all=`…). `--mode program` forces it.
- **Running** works like `python program.py` in a child process (the packaged app runs it
  with `codemotion-cli.exe --codemotion-run-program`, since it has no python.exe), with
  stdout and stderr merged and streamed, a 30-minute limit and Cancel (which ends the
  process tree). A saved file runs in its own folder so relative paths work; edited text
  runs from a temp copy with that folder as the working directory (and on PYTHONPATH).
  Unsaved programs run in a temp folder.
- **The video** is the newest video file created or changed in the working folder (3
  levels deep, skipping hidden and venv folders). It is re-encoded to H.264/yuv420p/AAC
  with even dimensions, because programs often write `mp4v`, which many players can't
  play, then gets a sidecar so it is listed with the renders. Rejected: copying it as is.
- **Progress** comes from what the program prints: the last "N/M" on a line ("Rendered
  60/257 frames"). Otherwise the bar waits, then the import and exports report normally.
- **Desktop:** programs are never auto-run (slow, and they write files); the Problems list
  says so. Run plays the video from a temp folder; Render saves it to Renders (plus
  GIF/WebM if chosen). A failure shows the traceback's last line and marks its line.
  CLI: `render` runs programs; `plan` and `still` explain that they need `render`.

## Truncated pastes and "Run as" (2026-10-21)

Reported: a pasted Pillow script "stops at line 96" and came out as a code card. Diagnosis:
the editor pasted everything it was given (2,000-line paste tests fine); the pasted text
itself ended at line 96, inside a function, before the part that writes the video and
calls `main()`. Detection then required a video-writer call, which is exactly the part a
cut-off paste loses, so it silently fell back to a code card, and the desktop app had no
way to override detection. Changes:

- **Detection by import only:** importing PIL / cv2 / imageio / moviepy / skvideo / av
  makes a video program. A writer call is no longer required (it is usually last, and
  libraries write videos in too many ways to list).
- **Cut-off check** (`program.incomplete`, run before a program starts and shown in
  Problems when it is pasted): the text ends inside a statement (an unclosed bracket or
  string, a block with no body), or it defines functions but never calls or references
  any at top level (no `main()`, no `if __name__ == "__main__":`), so running it would do
  nothing. The message names the last line. Other syntax errors are left to the run.
- **Run as** (toolbar): Auto / Video script / Video program / Code card, reset to Auto
  for each file. Auto's entry shows what it detected ("Auto: video program"). A code card
  chosen by Auto says why in Problems and how to run the code instead.
- **Paste bug:** text copied from a web page often comes with web links, and the editor
  dropped such pastes entirely (it only meant to hand dropped *local files* to the
  window). It now pastes the text unless there are local files.
