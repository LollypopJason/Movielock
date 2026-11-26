"""Mouth cues for lip sync.

Cues use Rhubarb Lip Sync's mouth shapes:
  A closed (M B P)   B slightly open (K S T EE)   C open (EH AE)   D wide open (AA)
  E rounded (AO ER)  F puckered (UW OW W)         G F/V           H L            X rest

Rhubarb is used when installed; otherwise a loudness-based fallback picks shapes from the
audio level, and silent caption-only lines get a text-based pattern.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

from .audio import decode_mono, rms_envelope, to_wav

Cue = dict  # {"start": float, "end": float, "shape": str}


def _merge(cues: list[Cue]) -> list[Cue]:
    out: list[Cue] = []
    for c in cues:
        if c["end"] <= c["start"]:
            continue
        if out and out[-1]["shape"] == c["shape"] and abs(out[-1]["end"] - c["start"]) < 1e-6:
            out[-1]["end"] = c["end"]
        else:
            out.append(dict(c))
    return out


def _cache_key(path: Path, extra: str) -> str:
    st = path.stat()
    h = hashlib.sha1(f"{path.resolve()}|{st.st_size}|{st.st_mtime_ns}|{extra}".encode()).hexdigest()
    return h[:16]


def rhubarb_cues(path: Path, rhubarb: str, ffmpeg: str, cache_dir: Path, transcript: str = "") -> list[Cue]:
    """Run Rhubarb (needs a WAV or OGG; anything else is converted first)."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    key = _cache_key(path, "rhubarb|" + transcript)
    out_json = cache_dir / f"{key}.rhubarb.json"
    if not out_json.is_file():
        src = path
        if path.suffix.lower() not in (".wav", ".ogg"):
            src = to_wav(path, cache_dir / f"{key}.wav", ffmpeg)
        cmd = [rhubarb, "-f", "json", "--extendedShapes", "GHX", "-o", str(out_json)]
        if transcript:
            dialog = cache_dir / f"{key}.txt"
            dialog.write_text(transcript, encoding="utf-8")
            cmd += ["-d", str(dialog)]
        cmd.append(str(src))
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0 or not out_json.is_file():
            raise RuntimeError(f"rhubarb failed on {path.name}: {proc.stderr.strip()[-400:]}")
    data = json.loads(out_json.read_text(encoding="utf-8"))
    return _merge([{"start": float(c["start"]), "end": float(c["end"]), "shape": str(c["value"])}
                   for c in data.get("mouthCues", [])])


def loudness_cues(path: Path, ffmpeg: str, cache_dir: Path | None = None, window: float = 1 / 24) -> list[Cue]:
    """Fallback without Rhubarb: map the audio level to mouth openness."""
    if cache_dir is not None:
        cache_dir.mkdir(parents=True, exist_ok=True)
        cached = cache_dir / f"{_cache_key(path, 'loud')}.loud.json"
        if cached.is_file():
            return json.loads(cached.read_text(encoding="utf-8"))
    sr = 16000
    env = rms_envelope(decode_mono(path, ffmpeg, sr), sr, window)
    cues = loudness_to_cues(env, window)
    if cache_dir is not None:
        cached.write_text(json.dumps(cues), encoding="utf-8")
    return cues


def loudness_to_cues(env: list[float], window: float) -> list[Cue]:
    if not env:
        return []
    ranked = sorted(env)
    ref = ranked[int(0.95 * (len(ranked) - 1))] or max(ranked) or 1.0
    cues: list[Cue] = []
    prev_level = 0.0
    for i, v in enumerate(env):
        level = min(1.0, v / ref) if ref > 0 else 0.0
        if level < 0.08:
            shape = "X"
        elif level < 0.25:
            shape = "B"
        elif level < 0.5:
            # rising sounds look like opening consonants, falling ones like rounded vowels
            shape = "C" if level >= prev_level else "E"
        elif level < 0.8:
            shape = "C"
        else:
            shape = "D"
        # a short closure between loud windows reads as an M/B/P
        if shape == "X" and prev_level > 0.4 and i + 1 < len(env) and env[i + 1] / ref > 0.4:
            shape = "A"
        prev_level = level
        cues.append({"start": round(i * window, 4), "end": round((i + 1) * window, 4), "shape": shape})
    # Drop single-window flickers (except closures) so the mouth doesn't jitter.
    smoothed = []
    for i, c in enumerate(cues):
        if 0 < i < len(cues) - 1 and c["shape"] not in ("A", "X"):
            if cues[i - 1]["shape"] == cues[i + 1]["shape"] != c["shape"] and cues[i - 1]["shape"] != "X":
                c = dict(c, shape=cues[i - 1]["shape"])
        smoothed.append(c)
    merged = _merge(smoothed)
    # Short silences inside speech (between syllables) keep the mouth slightly open.
    for i in range(1, len(merged) - 1):
        c = merged[i]
        if c["shape"] == "X" and c["end"] - c["start"] < 0.1:
            merged[i] = dict(c, shape="B")
    return _merge(merged)


_VOWEL_SHAPES = {"a": "D", "e": "C", "i": "B", "o": "E", "u": "F", "y": "B"}
_CONS_SHAPES = {"m": "A", "b": "A", "p": "A", "f": "G", "v": "G", "l": "H", "w": "F"}


def text_cues(text: str, duration: float) -> list[Cue]:
    """Rough mouth pattern for a line that has text but no audio (TTS turned off)."""
    letters = [ch for ch in text.lower() if ch.isalpha() or ch in " ,.!?"]
    shapes: list[str] = []
    for ch in letters:
        if ch in _VOWEL_SHAPES:
            if not shapes or shapes[-1] != _VOWEL_SHAPES[ch]:
                shapes.append(_VOWEL_SHAPES[ch])
        elif ch in _CONS_SHAPES:
            shapes.append(_CONS_SHAPES[ch])
        elif ch in ",.!?":
            shapes.append("X")
        elif ch != " " and (not shapes or shapes[-1] in "XA"):
            shapes.append("B")
    if not shapes or duration <= 0:
        return [{"start": 0.0, "end": max(duration, 0.0), "shape": "X"}]
    step = duration / (len(shapes) + 1)
    cues = [{"start": round(i * step, 4), "end": round((i + 1) * step, 4), "shape": s} for i, s in enumerate(shapes)]
    cues.append({"start": round(len(shapes) * step, 4), "end": round(duration, 4), "shape": "X"})
    return _merge(cues)


def cues_for(path: Path | None, *, transcript: str = "", duration: float = 0.0, rhubarb: str | None = None,
             ffmpeg: str = "ffmpeg", cache_dir: Path | None = None, warn=None) -> tuple[list[Cue], str]:
    """Best available cues for a line; returns (cues, method)."""
    if path is None:
        return text_cues(transcript, duration), "text"
    if rhubarb:
        try:
            return rhubarb_cues(path, rhubarb, ffmpeg, cache_dir or path.parent, transcript), "rhubarb"
        except Exception as exc:  # noqa: BLE001 - fall back, but tell the user
            if warn:
                warn(f"{exc}; using loudness-based lip sync instead")
    return loudness_cues(path, ffmpeg, cache_dir), "loudness"
