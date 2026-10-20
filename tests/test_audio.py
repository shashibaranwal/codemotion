"""Sound effects: built-in sounds, files, mixing, the soundtrack in MP4/WebM, API and plan."""

from __future__ import annotations

import subprocess
from pathlib import Path

import numpy as np
import pytest
from typer.testing import CliRunner

from codemotion import ScriptError, Text, Video, config
from codemotion.cli import app
from codemotion.errors import UserFacingError
from codemotion.pipeline import prepare_scene, scene_plan
from codemotion.render import audio
from codemotion.render.encoder import export_video, ffmpeg_exe, mp4_command
from codemotion.render.renderer import render_video
from codemotion.script import api
from codemotion.script.doc import SOUND_PRESETS, SceneDoc

SR = config.AUDIO_SAMPLE_RATE
runner = CliRunner()


def doc(*sounds: dict[str, object], duration: float = 3) -> SceneDoc:
    return SceneDoc.model_validate({"duration": duration, "aspect": "1:1", "sounds": list(sounds)})


def streams(path: Path) -> str:
    result = subprocess.run([ffmpeg_exe(), "-hide_banner", "-i", str(path)],
                            capture_output=True, text=True)  # fmt: skip
    return "\n".join(line for line in result.stderr.splitlines() if "Stream" in line)


# --------------------------------------------------------------------------- built-in sounds


@pytest.mark.parametrize("name", SOUND_PRESETS)
def test_presets_are_stereo_normalized_and_deterministic(name: str) -> None:
    sound = audio.preset(name, seed=7)
    assert sound.ndim == 2 and sound.shape[1] == 2 and sound.dtype == np.float32
    assert float(np.abs(sound).max()) == pytest.approx(config.SOUND_PRESET_GAIN, rel=1e-3)
    assert np.array_equal(sound, audio.preset(name, seed=7))
    assert 0.02 < sound.shape[0] / SR < 1.5


def test_whoosh_brightens_then_fades_and_moves_left_to_right() -> None:
    sound = audio.preset("whoosh").astype(np.float64)
    n = len(sound)
    left, right = sound[:, 0], sound[:, 1]
    assert (left[: n // 3] ** 2).sum() > (right[: n // 3] ** 2).sum()
    assert (right[-n // 3 :] ** 2).sum() > (left[-n // 3 :] ** 2).sum()
    energy = [float((sound[i : i + n // 6] ** 2).sum()) for i in range(0, n, n // 6)]
    assert energy.index(max(energy)) in (1, 2, 3)  # swells in the middle


def test_chime_rings_at_its_notes() -> None:
    mono = audio.preset("chime").mean(axis=1).astype(np.float64)
    spectrum = np.abs(np.fft.rfft(mono))
    freqs = np.fft.rfftfreq(mono.size, 1 / SR)
    assert (
        abs(freqs[spectrum.argmax()] - 1318.5) < 15 or abs(freqs[spectrum.argmax()] - 1975.5) < 15
    )


# --------------------------------------------------------------------------- mixing


def test_mix_places_scales_and_skips_cues() -> None:
    track = audio.mix(doc({"sound": "pop", "at": 1.0, "volume": 0.5},
                          {"sound": "click", "at": 5.0}))  # fmt: skip
    assert track.shape == (3 * SR, 2)
    assert not track[: SR - 1].any()  # silent until 1 s
    assert float(np.abs(track[SR : SR + 2000]).max()) == pytest.approx(0.25, rel=0.01)
    assert not track[2 * SR :].any()  # the click at 5 s is past the end


def test_mix_soft_limits_loud_overlaps() -> None:
    cues = [{"sound": "chime", "at": 0, "volume": 4} for _ in range(3)]
    track = audio.mix(doc(*cues))
    assert float(np.abs(track).max()) <= config.AUDIO_HEADROOM


def test_sound_files_round_trip(tmp_path: Path) -> None:
    wav = audio.write_wav(tmp_path / "beep.wav", audio.preset("pop"))
    decoded = audio.decode_file(wav)
    assert decoded.shape == audio.preset("pop").shape
    assert np.allclose(decoded, audio.preset("pop"), atol=2e-4)
    track = audio.mix(doc({"sound": str(wav), "at": 0.5}, {"sound": str(wav), "at": 1.5}))
    assert np.abs(track[int(1.5 * SR) : int(1.6 * SR)]).max() > 0.4
    (tmp_path / "bad.wav").write_bytes(b"not audio")
    with pytest.raises(UserFacingError, match="could not be read"):
        audio.decode_file(tmp_path / "bad.wav")


# --------------------------------------------------------------------------- API & plan


def test_video_sound_api(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    audio.write_wav(tmp_path / "hit.wav", audio.preset("click"))
    api._registry.clear()
    video = Video(duration=3)
    video.add(Text("x"))
    assert video.sound("whoosh", at=1).sound("hit.wav", at=2, volume=0.5) is video
    sounds = video.to_doc().sounds
    assert [s.sound for s in sounds] == ["whoosh", str((tmp_path / "hit.wav").resolve())]
    assert sounds[1].volume == 0.5 and not sounds[1].is_preset
    with pytest.raises(ScriptError, match="neither a built-in sound"):
        video.sound("boing")
    with pytest.raises(ScriptError, match="not found next to the script"):
        video.sound("missing.mp3")
    with pytest.raises(ScriptError, match="sound 'volume'"):
        video.sound("pop", volume=-1)


def test_plan_lists_sounds_and_warns_about_late_ones(tmp_path: Path) -> None:
    scene = doc({"sound": "whoosh", "at": 1}, {"sound": "chime", "at": 9})
    plan = scene_plan(prepare_scene(scene, preview=True))
    assert [(s.name, s.at) for s in plan.sounds] == [("whoosh", 1), ("chime", 9)]
    assert any("chime" in w and "after the video ends" in w for w in plan.warnings)
    with pytest.raises(UserFacingError, match="Sound file not found"):
        prepare_scene(doc({"sound": str(tmp_path / "gone.wav")}), preview=True)


def test_cli_plan_prints_sounds(tmp_path: Path) -> None:
    script = tmp_path / "video.py"
    script.write_text("from codemotion import Video\nvideo = Video(duration=3)\n"
                      "video.sound('chime', at=1.5)\n", encoding="utf-8")  # fmt: skip
    result = runner.invoke(app, ["plan", str(script)])
    assert result.exit_code == 0, result.output
    assert "chime" in result.output and "1.5" in result.output


# --------------------------------------------------------------------------- encoding


def test_mp4_command_muxes_audio_only_when_given(tmp_path: Path) -> None:
    silent = mp4_command("ffmpeg", 10, 10, 30, tmp_path / "a.mp4")
    assert "-an" in silent
    loud = mp4_command("ffmpeg", 10, 10, 30, tmp_path / "a.mp4", tmp_path / "s.wav")
    assert "-an" not in loud and "aac" in loud and str(tmp_path / "s.wav") in loud


def test_render_with_sounds_has_an_audio_track(tmp_path: Path) -> None:
    scene = doc({"sound": "whoosh", "at": 0.5}, {"sound": "chime", "at": 2})
    prepared = prepare_scene(scene, preview=True)
    video = render_video(prepared, tmp_path / "v.mp4", workers=1)
    info = streams(video)
    assert "Audio: aac" in info and "48000 Hz, stereo" in info
    assert not list(tmp_path.glob(".*audio.wav"))  # the temporary soundtrack is gone
    webm = export_video(video, "webm", (prepared.width, prepared.height), 3.0)
    assert "Audio: opus" in streams(webm)
    gif = export_video(video, "gif", (prepared.width, prepared.height), 3.0)
    assert "Audio" not in streams(gif)


def test_render_without_sounds_is_silent(tmp_path: Path) -> None:
    video = render_video(prepare_scene(doc(), preview=True), tmp_path / "v.mp4", workers=1)
    assert "Audio" not in streams(video)
