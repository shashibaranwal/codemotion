"""Constants: input limits, aspect presets, design values (SPEC section 5) and paths.

Design values are expressed in pixels at a 1080 px short edge (``REFERENCE_SHORT_EDGE``)
and scaled proportionally for other output sizes (e.g. the 540 px preview).
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Final

# --------------------------------------------------------------------------- paths

FROZEN: Final = bool(getattr(sys, "frozen", False))  # running from the packaged app
PACKAGE_DIR: Final = Path(__file__).resolve().parent
ASSETS_DIR: Final = PACKAGE_DIR / "assets"
FONTS_DIR: Final = ASSETS_DIR / "fonts"
ICON_FILE: Final = ASSETS_DIR / "icon.png"  # made by packaging/make_icon.py
BUNDLED_THEMES_DIR: Final = PACKAGE_DIR / "themes"
USER_THEMES_DIR: Final = Path.home() / ".codemotion" / "themes"
OUTPUT_DIR: Final = Path("output")

MONO_FONT_FILE: Final = FONTS_DIR / "JetBrainsMono-Regular.ttf"
MONO_BOLD_FONT_FILE: Final = FONTS_DIR / "JetBrainsMono-Bold.ttf"
SANS_FONT_FILE: Final = FONTS_DIR / "Inter-Regular.ttf"
SANS_SEMIBOLD_FONT_FILE: Final = FONTS_DIR / "Inter-SemiBold.ttf"

# --------------------------------------------------------------------------- input limits (2.2)

MAX_LINES: Final = 60
MAX_LINE_LENGTH: Final = 100
MAX_TOTAL_CHARS: Final = 3_000
TAB_SIZE: Final = 4
MISSING_GLYPH_REPLACEMENT: Final = "□"  # □

# --------------------------------------------------------------------------- request ranges (2.1)

DURATION_MIN: Final = 3.0
DURATION_MAX: Final = 12.0
DURATION_STEP: Final = 0.5
DURATION_DEFAULT: Final = 8.0
FPS_OPTIONS: Final = (30, 60)
DEFAULT_THEME: Final = "midnight"
DEFAULT_FILENAME: Final = "main.py"
TITLE_MAX_LENGTH: Final = 60
CAPTION_MAX_LENGTH: Final = 80
OUTPUT_TEXT_MAX_LENGTH: Final = 600
IMAGE_MAX_BYTES: Final = 5 * 1024 * 1024
SCRIPT_TIMEOUT_SECONDS: Final = 10.0
TYPEWRITER_DEFAULT_CPS: Final = 30.0  # script Code elements without reveal_length

# --------------------------------------------------------------------------- aspects (F9, 5.1, 5.2)

REFERENCE_SHORT_EDGE: Final = 1080
PREVIEW_SHORT_EDGE: Final = 540
PREVIEW_FPS: Final = 15


@dataclass(frozen=True)
class SafeArea:
    """Fractions of the frame reserved for social-app UI overlays."""

    top: float
    bottom: float
    left: float
    right: float


@dataclass(frozen=True)
class AspectPreset:
    width: int
    height: int
    safe_area: SafeArea
    min_font: float
    max_font: float


# Min fonts are below SPEC 5.2 (26/24/22) so 25-line snippets fit every format and
# portrait takes 56-char lines; see docs/decisions.md.
ASPECT_PRESETS: Final[dict[str, AspectPreset]] = {
    "9:16": AspectPreset(1080, 1920, SafeArea(0.12, 0.18, 0.06, 0.06), 24.0, 56.0),
    "1:1": AspectPreset(1080, 1080, SafeArea(0.06, 0.06, 0.06, 0.06), 21.0, 52.0),
    "16:9": AspectPreset(1920, 1080, SafeArea(0.06, 0.06, 0.06, 0.06), 20.0, 48.0),
}


def frame_size(aspect: str, preview: bool = False) -> tuple[int, int]:
    """Output frame size in pixels; preview scales the short edge down to 540 px."""
    preset = ASPECT_PRESETS[aspect]
    if not preview:
        return preset.width, preset.height
    ratio = PREVIEW_SHORT_EDGE / min(preset.width, preset.height)
    return round(preset.width * ratio), round(preset.height * ratio)


# --------------------------------------------------------------------------- design values (5.1)

# Background
BG_GRADIENT_ANGLE: Final = 60.0  # degrees, 0 = left-to-right, 90 = top-to-bottom
BG_BLOB_SIGMA: Final = 0.42  # Gaussian glow sigma, fraction of short edge
BG_BLOB_OPACITY: Final = 0.5
BG_BLOB_CENTERS: Final = ((0.18, 0.22), (0.85, 0.80))  # fractions of (width, height)
BG_GRAIN_OPACITY: Final = 0.03
BACKDROP_DOWNSAMPLE: Final = 4  # smooth backdrop layers are computed at 1/4 resolution

# Editor card
CARD_RADIUS: Final = 28.0
CARD_SHADOW_BLUR: Final = 60.0
CARD_SHADOW_DY: Final = 24.0
CARD_BORDER_WIDTH: Final = 1.0

# Title bar
TITLE_BAR_HEIGHT: Final = 64.0
TITLE_BAR_DOT_RADIUS: Final = 8.0
TITLE_BAR_DOT_SPACING: Final = 28.0  # centre to centre
TITLE_BAR_DOT_INSET: Final = 32.0  # card left edge to first dot centre
TITLE_BAR_TAB_FONT_SIZE: Final = 24.0
TITLE_BAR_SEPARATOR_WIDTH: Final = 1.0

# Code area
CODE_PADDING: Final = 40.0  # SPEC 5.1 says 48; reduced with the min fonts (docs/decisions.md)
LINE_HEIGHT_FACTOR: Final = 1.5
GUTTER_GAP_CHARS: Final = 2.0  # space between line numbers and code, in character advances
GUTTER_MIN_DIGITS: Final = 2

# --------------------------------------------------------------------------- motion (5.3, 5.4)

# Intro
INTRO_SECONDS: Final = 0.8
INTRO_BG_FADE_SECONDS: Final = 0.4
INTRO_CARD_START_SCALE: Final = 0.92
INTRO_SPRING_STIFFNESS: Final = 180.0
INTRO_SPRING_DAMPING: Final = 20.0  # slightly under-damped: a hint of overshoot

# Background drift over the whole video
BG_ROTATION_DEG: Final = 12.0  # spec: <= 15 degrees
BG_BLOB_DRIFT: Final = 0.06  # fraction of short edge each blob travels

# Reveal styles
FADE_IN_SECONDS: Final = 0.5
FADE_IN_DELAY_SECONDS: Final = 0.3  # code fades in as the card lands
CASCADE_LINE_SECONDS: Final = 0.35
CASCADE_MAX_STAGGER: Final = 0.08
CASCADE_STAGGER_SHARE: Final = 0.6
CASCADE_OFFSET_Y: Final = 24.0
TYPE_LINE_END_PAUSE_MAX: Final = 0.06  # seconds, seeded
TYPE_PAUSE_SHARE_MAX: Final = 0.25  # pauses never take more than this share of the reveal
CURSOR_BLINK_INTERVAL: Final = 0.53  # seconds visible, then the same hidden
CURSOR_IDLE_SECONDS: Final = 0.12  # no new character for this long -> cursor blinks
CURSOR_HEIGHT_FACTOR: Final = 1.25  # x font size

# Code highlight (Code.highlight)
HIGHLIGHT_DIM_OPACITY: Final = 0.35  # other lines
HIGHLIGHT_FILL_OPACITY: Final = 0.15  # accent band behind the lines
HIGHLIGHT_BAR_WIDTH: Final = 4.0

# Outro
OUTRO_FADE_SECONDS: Final = 0.3

# Highlight zoom (Code.highlight(zoom=...)): the card scales about the highlighted lines.
HIGHLIGHT_ZOOM: Final = 1.06
HIGHLIGHT_ZOOM_MAX: Final = 1.5

# Camera scroll for code taller than the card (SPEC 5.6): the active line is kept at this
# fraction of the visible height, following it with a critically damped spring.
SCROLL_ANCHOR: Final = 0.7
SCROLL_SPRING_OMEGA: Final = 9.0  # rad/s; settles in ~0.5 s, never overshoots

# Terminal element
TERMINAL_FONT_SIZE: Final = 28.0
TERMINAL_MIN_FONT_FACTOR: Final = 0.6  # shrink long lines down to 60 % before refusing
TERMINAL_LINE_HEIGHT_FACTOR: Final = 1.45
TERMINAL_PADDING: Final = 28.0
TERMINAL_TITLE_HEIGHT: Final = 48.0
TERMINAL_RADIUS: Final = 20.0
TERMINAL_TITLE_FONT_SIZE: Final = 20.0
TERMINAL_TYPE_CPS: Final = 25.0
TERMINAL_LINE_INTERVAL: Final = 0.08  # seconds between printed output lines
TERMINAL_START_DELAY: Final = 0.3  # after the element starts
TERMINAL_OUTPUT_DELAY: Final = 0.35  # "Enter" -> first output line
TERMINAL_MAX_LINES: Final = 30
TERMINAL_MAX_LINE: Final = 100
TERMINAL_MAX_CHARS: Final = 2_000

# Lines
ARROW_LENGTH_FACTOR: Final = 4.0  # arrowhead length = line width x this (min 14 px)
ARROW_MIN_LENGTH: Final = 14.0

# --------------------------------------------------------------------------- planner (5.6)

PHASE_FOCUS_SECONDS: Final = 1.8
PHASE_OUTPUT_SECONDS: Final = 1.6
HOLD_MIN_SECONDS: Final = 1.0
REVEAL_MIN_SECONDS: Final = 0.6
INTRO_SHRUNK_SECONDS: Final = 0.5
HOLD_SHRUNK_SECONDS: Final = 0.6
TYPEWRITER_MAX_CPS: Final = 45.0  # readability cap
TYPEWRITER_MIN_CPS: Final = 15.0  # never type slower; spare time goes to the hold

# --------------------------------------------------------------------------- sound effects

AUDIO_SAMPLE_RATE: Final = 48_000
AUDIO_CHANNELS: Final = 2
AUDIO_AAC_BITRATE: Final = "192k"
AUDIO_OPUS_BITRATE: Final = "128k"
AUDIO_HEADROOM: Final = 0.89  # mix peak above this is soft-limited (about -1 dBFS)
SOUND_PRESET_GAIN: Final = 0.5  # built-in sounds peak at -6 dBFS before the cue's volume
MAX_SOUNDS: Final = 100  # cues per video
SOUND_FILE_MAX_BYTES: Final = 20 * 1024 * 1024
SOUND_FILE_SUFFIXES: Final = (".wav", ".mp3", ".ogg", ".flac", ".m4a", ".aac", ".opus")

# --------------------------------------------------------------------------- encoder (6.7)

# Multiprocess rendering (render/parallel.py)
RENDER_MAX_WORKERS: Final = 4  # each worker holds a scene (~90 MB); more barely helps
PARALLEL_MIN_WORK: Final = (
    60.0  # megapixel-frames (~2 s of drawing); smaller renders stay in-process
)
PARALLEL_SLOTS_PER_WORKER: Final = 2  # shared frame buffers in flight per worker

X264_PRESET: Final = "medium"
GIF_SHORT_EDGE: Final = 540
GIF_FPS: Final = 15
WEBM_CRF: Final = 32
X264_CRF: Final = 18
FFMPEG_STDERR_LINES: Final = 10

# --------------------------------------------------------------------------- desktop app (4.1)

APP_NAME: Final = "CodeMotion"
# The packaged app bundles the examples next to the package; a checkout has them at the root.
EXAMPLES_DIR: Final = (
    Path(getattr(sys, "_MEIPASS", PACKAGE_DIR.parent)) / "examples"
    if FROZEN
    else PACKAGE_DIR.parent.parent / "examples"
)
# Desktop renders default to ./output in a checkout, and to the user's Videos folder in the
# packaged app (whose working directory is usually its install folder).
DESKTOP_OUTPUT_DIR: Final = Path.home() / "Videos" / "CodeMotion" if FROZEN else OUTPUT_DIR
TRUST_NOTICE: Final = "Scripts are Python and run on this computer. Only run scripts you trust."
AUTO_RUN_DELAY_MS: Final = 600  # run this long after typing stops
SCRUB_STEP_SECONDS: Final = 1 / 30  # scrubber resolution
RECENT_FILES_MAX: Final = 8
EDITOR_FONT_POINT_SIZE: Final = 11
EDITOR_INDENT: Final = "    "

# Window palette (dark) and editor colours, #RRGGBB.
UI_COLORS: Final = {
    "window": "#1E2129",
    "base": "#16181F",
    "alternate": "#1B1E26",
    "text": "#D8DCE6",
    "muted": "#8A93A6",
    "button": "#262A34",
    "highlight": "#4F8CFF",
    "highlighted_text": "#FFFFFF",
    "error": "#F2545B",
    "warning": "#E8B04B",
}
EDITOR_COLORS: Final = {
    "background": "#16181F",
    "text": "#D8DCE6",
    "gutter": "#5C6370",
    "gutter_background": "#1B1E26",
    "current_line": "#1F232C",
    "error_line": "#3A1E24",
    "keyword": "#C678DD",
    "builtin": "#56B6C2",
    "string": "#98C379",
    "number": "#D19A66",
    "comment": "#7F848E",
    "definition": "#61AFEF",
    "decorator": "#E5C07B",
}
