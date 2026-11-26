"""Small audio helpers built on the standard library + ffmpeg (no numpy)."""

from __future__ import annotations

import array
import json
import math
import subprocess
import wave
from pathlib import Path

from .errors import MovielockError


def write_wav(path: Path, samples, sample_rate: int = 48000) -> Path:
    """Write mono float samples in [-1, 1] as 16-bit PCM."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    pcm = array.array("h", (int(max(-1.0, min(1.0, s)) * 32767) for s in samples))
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(pcm.tobytes())
    return path


def duration(path: Path, ffprobe: str | None = "ffprobe") -> float:
    """Length of an audio file in seconds (wave module for plain WAV, ffprobe otherwise)."""
    path = Path(path)
    if not path.is_file():
        raise MovielockError(f"audio file not found: {path}")
    if path.suffix.lower() == ".wav":
        try:
            with wave.open(str(path), "rb") as w:
                return w.getnframes() / float(w.getframerate())
        except (wave.Error, EOFError):
            pass  # e.g. float WAV; fall through to ffprobe
    if not ffprobe:
        raise MovielockError(f"need ffprobe to read {path.name}")
    out = subprocess.run(
        [ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "json", str(path)],
        capture_output=True, text=True)
    if out.returncode != 0:
        raise MovielockError(f"ffprobe could not read {path}: {out.stderr.strip()}")
    try:
        return float(json.loads(out.stdout)["format"]["duration"])
    except (KeyError, ValueError) as exc:
        raise MovielockError(f"could not get duration of {path}") from exc


def decode_mono(path: Path, ffmpeg: str = "ffmpeg", sample_rate: int = 16000) -> array.array:
    """Decode any audio file to mono 16-bit samples via ffmpeg."""
    proc = subprocess.run(
        [ffmpeg, "-v", "error", "-i", str(path), "-ac", "1", "-ar", str(sample_rate),
         "-f", "s16le", "-acodec", "pcm_s16le", "-"],
        capture_output=True)
    if proc.returncode != 0:
        raise MovielockError(f"ffmpeg could not decode {path}: {proc.stderr.decode(errors='replace').strip()}")
    samples = array.array("h")
    samples.frombytes(proc.stdout[: len(proc.stdout) // 2 * 2])
    return samples


def to_wav(src: Path, dst: Path, ffmpeg: str = "ffmpeg", sample_rate: int = 16000) -> Path:
    """Convert to mono PCM WAV (what Rhubarb wants)."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run([ffmpeg, "-v", "error", "-y", "-i", str(src), "-ac", "1", "-ar", str(sample_rate),
                           "-acodec", "pcm_s16le", str(dst)], capture_output=True, text=True)
    if proc.returncode != 0:
        raise MovielockError(f"ffmpeg could not convert {src}: {proc.stderr.strip()}")
    return dst


def rms_envelope(samples, sample_rate: int, window: float) -> list[float]:
    """Root-mean-square loudness per window (seconds)."""
    size = max(1, int(sample_rate * window))
    env = []
    for start in range(0, len(samples), size):
        chunk = samples[start:start + size]
        if not chunk:
            break
        env.append(math.sqrt(sum(s * s for s in chunk) / len(chunk)) / 32768.0)
    return env
