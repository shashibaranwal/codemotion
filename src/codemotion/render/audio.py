"""Sound effects: built-in synthesized sounds, audio files, and the mixed soundtrack.

Built-in sounds are generated with numpy (no sample files, so nothing to license or
download) from a seed derived from the cue, so the same script always sounds the same.
Audio files are decoded with the bundled ffmpeg. The soundtrack is stereo float32 at
``AUDIO_SAMPLE_RATE``, exactly as long as the video.
"""

from __future__ import annotations

import subprocess
import sys
import wave
import zlib
from collections.abc import Callable
from pathlib import Path

import numpy as np
import numpy.typing as npt

from codemotion import config
from codemotion.errors import UserFacingError
from codemotion.render.encoder import ffmpeg_exe
from codemotion.script.doc import SceneDoc, SoundCue

Samples = npt.NDArray[np.float32]  # shape (n, 2), -1..1
SR = config.AUDIO_SAMPLE_RATE


def _time(seconds: float) -> npt.NDArray[np.float64]:
    return np.arange(round(seconds * SR)) / SR


def _swept_noise(
    rng: np.random.Generator, seconds: float, low: float, high: float, shape: Callable[..., object]
) -> npt.NDArray[np.float64]:
    """White noise through a band-pass whose centre follows ``shape(progress)`` from ``low``
    to ``high`` Hz; filtered frame by frame in the frequency domain (overlap-add)."""
    n = round(seconds * SR)
    size, hop = 2048, 512
    window = np.hanning(size)
    noise = rng.standard_normal(n + size)
    out = np.zeros(n + size)
    freqs = np.fft.rfftfreq(size, 1 / SR)
    for start in range(0, n, hop):
        progress = start / max(1, n - 1)
        centre = low + (high - low) * float(shape(progress))  # type: ignore[arg-type]
        width = centre * 0.9  # about one octave
        band = np.exp(-0.5 * ((freqs - centre) / width) ** 2)
        frame = np.fft.irfft(np.fft.rfft(noise[start : start + size] * window) * band, size)
        out[start : start + size] += frame * window
    return out[:n]


def _stereo(mono: npt.NDArray[np.float64], pan: npt.NDArray[np.float64] | float = 0.0) -> Samples:
    """Equal-power pan: -1 left, 0 centre, 1 right (per sample or constant)."""
    angle = (np.asarray(pan) + 1) * np.pi / 4
    return np.stack([mono * np.cos(angle), mono * np.sin(angle)], axis=1).astype(np.float32)


def _normalize(samples: Samples) -> Samples:
    peak = float(np.abs(samples).max())
    return samples * (config.SOUND_PRESET_GAIN / peak) if peak > 0 else samples


def whoosh(rng: np.random.Generator) -> Samples:
    """A soft, airy whoosh (~0.75 s) that swells, brightens and moves left to right."""
    seconds = 0.75
    t = _time(seconds) / seconds
    noise = _swept_noise(rng, seconds, 350, 2600, lambda p: np.sin(np.pi * p**0.8))
    envelope = np.sin(np.pi * t**0.7) ** 2
    return _normalize(_stereo(noise * envelope, pan=(t - 0.5) * 0.7))


def riser(rng: np.random.Generator) -> Samples:
    """A one-second build-up: brightening noise and a rising tone, cut off at the end."""
    seconds = 1.0
    t = _time(seconds)
    p = t / seconds
    noise = _swept_noise(rng, seconds, 300, 4000, lambda q: q**1.6)
    freq = 220 * 4**p  # two octaves up
    tone = 0.35 * np.sin(2 * np.pi * np.cumsum(freq) / SR)
    envelope = p**2 * np.clip((seconds - t) / 0.03, 0, 1)
    return _normalize(_stereo((noise + tone) * envelope))


def chime(rng: np.random.Generator) -> Samples:
    """A soft two-note confirmation chime (E6 then B6)."""
    t = _time(1.4)
    out = np.zeros((t.size, 2), np.float32)
    for freq, delay, pan in ((1318.5, 0.0, -0.25), (1975.5, 0.09, 0.25)):
        local = np.clip(t - delay, 0, None)
        partials = (np.sin(2 * np.pi * freq * local) + 0.25 * np.sin(4 * np.pi * freq * local)
                    + 0.08 * np.sin(6 * np.pi * freq * local))  # fmt: skip
        envelope = np.clip(local / 0.004, 0, 1) * np.exp(-local / 0.45) * (t >= delay)
        out += _stereo(partials * envelope, pan)
    return _normalize(out)


def pop(rng: np.random.Generator) -> Samples:
    """A short, round pop (a falling sine blip)."""
    t = _time(0.14)
    freq = 160 + 540 * np.exp(-t / 0.02)
    body = np.sin(2 * np.pi * np.cumsum(freq) / SR)
    envelope = np.clip(t / 0.002, 0, 1) * np.exp(-t / 0.035)
    return _normalize(_stereo(body * envelope))


def click(rng: np.random.Generator) -> Samples:
    """A crisp UI click."""
    t = _time(0.03)
    noise = np.diff(rng.standard_normal(t.size + 1))  # high-passed
    return _normalize(_stereo(noise * np.exp(-t / 0.004)))


PRESETS: dict[str, Callable[[np.random.Generator], Samples]] = {
    "whoosh": whoosh,
    "chime": chime,
    "pop": pop,
    "click": click,
    "riser": riser,
}


def preset(name: str, seed: int = 0) -> Samples:
    return PRESETS[name](np.random.default_rng(seed))


def decode_file(path: Path) -> Samples:
    """Decode an audio file to stereo float32 at ``AUDIO_SAMPLE_RATE`` with ffmpeg."""
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    command = [ffmpeg_exe(), "-v", "error", "-i", str(path), "-f", "f32le",
               "-ac", str(config.AUDIO_CHANNELS), "-ar", str(SR), "-"]  # fmt: skip
    try:
        result = subprocess.run(command, capture_output=True, timeout=60, creationflags=flags)
    except (OSError, subprocess.TimeoutExpired):
        result = None
    if result is None or result.returncode != 0 or not result.stdout:
        raise UserFacingError(f"The sound '{path.name}' could not be read as audio.", "sound")
    data = np.frombuffer(result.stdout, dtype=np.float32)
    return data[: data.size - data.size % 2].reshape(-1, 2).copy()


def _cue_seed(cue: SoundCue, index: int) -> int:
    return zlib.crc32(f"{index}:{cue.sound}:{cue.at}".encode())


def mix(doc: SceneDoc) -> Samples:
    """The video's soundtrack: every cue placed and summed, soft-limited below 0 dBFS."""
    total = round(doc.duration * SR)
    out = np.zeros((total, 2), np.float32)
    files: dict[str, Samples] = {}
    for index, cue in enumerate(doc.sounds):
        start = round(cue.at * SR)
        if start >= total or cue.volume == 0:
            continue
        if cue.is_preset:
            clip = preset(cue.sound, _cue_seed(cue, index))
        else:
            if cue.sound not in files:
                files[cue.sound] = decode_file(Path(cue.sound))
            clip = files[cue.sound]
        piece = clip[: total - start] * cue.volume
        out[start : start + len(piece)] += piece
    peak = float(np.abs(out).max()) if total else 0.0
    if peak > config.AUDIO_HEADROOM:
        knee = config.AUDIO_HEADROOM
        out = (knee * np.tanh(out / knee)).astype(np.float32)
    return out


def write_wav(path: Path, samples: Samples) -> Path:
    """16-bit stereo WAV (the encoder's audio input)."""
    pcm = (np.clip(samples, -1, 1) * 32767).round().astype("<i2")
    with wave.open(str(path), "wb") as file:
        file.setnchannels(config.AUDIO_CHANNELS)
        file.setsampwidth(2)
        file.setframerate(SR)
        file.writeframes(pcm.tobytes())
    return path
