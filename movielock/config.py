"""Settings from movielock.yaml (all keys optional; defaults below)."""

from __future__ import annotations

import copy
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .errors import MovielockError, ToolMissing

DEFAULTS: dict[str, Any] = {
    "paths": {
        "assets": "assets",            # contains library.yaml
        "voices": "assets/voices",     # contains index.yaml and the voice files
        "renders": "renders",
        "cache": ".movielock_cache",
    },
    "tools": {
        "blender": "blender",
        "rhubarb": "rhubarb",
        "ffmpeg": "ffmpeg",
        "ffprobe": "ffprobe",
    },
    "renderer": "auto",               # auto | blender | storyboard
    "writer": {
        "model": "claude-opus-5-5",
        "api_key_env": "ANTHROPIC_API_KEY",
        "max_tokens": 4096,
        "api_url": "https://api.anthropic.com/v1/messages",
        "repair_attempts": 1,
    },
    "timing": {
        "gap": 0.25,                  # silence between consecutive lines
        "tail": 0.6,                  # hold after the last line of a shot
        "lead": 0.3,                  # wait before the first line of a shot
        "min_shot": 1.5,              # shots without lines last at least this long
        "words_per_second": 2.6,      # for caption-only lines (TTS off)
    },
    "audio": {
        "sample_rate": 48000,
        "duck": True,                 # lower the music under dialogue
        "voice_volume": 1.0,
        "sfx_volume": 0.9,
    },
    "captions": {
        "font": "Arial",
        "size": 0.045,                # fraction of the video height
        "outline": 3,
        "speaker_names": True,
    },
    "storyboard": {"font_file": ""},
    "quality": {
        "preview": {"scale": 0.5, "engine": "eevee", "samples": 8, "crf": 28,
                    "preset": "veryfast", "motion_blur": False, "denoise": False},
        "draft": {"scale": 0.75, "engine": "eevee", "samples": 32, "crf": 23,
                  "preset": "veryfast", "motion_blur": False, "denoise": False},
        "final": {"scale": 1.0, "engine": "cycles", "samples": 256, "crf": 18,
                  "preset": "slow", "motion_blur": True, "denoise": True},
    },
    "tts": {
        "enabled": False,
        # Command run once per line, without a shell. Placeholders: {text} {out} {hero} {ref}
        # e.g. "piper --model voices/en_US-ryan-medium.onnx --output_file {out}" with stdin: true
        "command": "",
        "stdin": False,               # also send {text} to the command's stdin
        # {ref} = a reference clip of the hero's real voice, for voice-cloning TTS engines.
        # OFF by default: cloning a voice actor without consent is legally risky.
        "voice_reference": False,
        "references": {},             # hero -> path of reference clip (only used if voice_reference)
    },
    "web": {"host": "127.0.0.1", "port": 5005},
}

RESOLUTIONS = {"video": (1920, 1080), "reel": (1080, 1920), "square": (1080, 1080)}


def _merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


@dataclass
class Config:
    data: dict
    root: Path
    source: Path | None = None
    _cache: dict = field(default_factory=dict, repr=False)

    # paths ------------------------------------------------------------------
    def path(self, key: str) -> Path:
        p = Path(os.path.expanduser(str(self.data["paths"][key])))
        return p if p.is_absolute() else (self.root / p)

    @property
    def assets_dir(self) -> Path:
        return self.path("assets")

    @property
    def voices_dir(self) -> Path:
        return self.path("voices")

    @property
    def renders_dir(self) -> Path:
        return self.path("renders")

    @property
    def cache_dir(self) -> Path:
        return self.path("cache")

    # sections -----------------------------------------------------------------
    def __getitem__(self, key: str) -> Any:
        return self.data[key]

    def quality(self, name: str) -> dict:
        presets = self.data["quality"]
        if name not in presets:
            raise MovielockError(f"unknown quality '{name}' (choose: {', '.join(presets)})")
        return dict(presets[name], name=name)

    # tools --------------------------------------------------------------------
    def tool(self, name: str, required: bool = True) -> str | None:
        """Resolve an external program from tools.<name>; returns None if missing and not required."""
        configured = str(self.data["tools"].get(name) or name)
        candidate = os.path.expanduser(configured)
        if not os.path.isabs(candidate) and os.sep in candidate:
            candidate = str(self.root / candidate)
        found = candidate if os.path.isfile(candidate) and os.access(candidate, os.X_OK) else shutil.which(candidate)
        if not found and required:
            hints = {
                "blender": "Install Blender 4.2+ and set tools.blender in movielock.yaml, "
                           "or render with --renderer storyboard.",
                "rhubarb": "Install Rhubarb Lip Sync (github.com/DanielSWolf/rhubarb-lip-sync).",
                "ffmpeg": "Install FFmpeg and make sure it is on PATH (or set tools.ffmpeg).",
                "ffprobe": "ffprobe ships with FFmpeg.",
            }
            raise ToolMissing(f"'{configured}' not found. {hints.get(name, '')}".strip())
        return found


def load_config(path: str | Path | None = None, overrides: dict | None = None) -> Config:
    """Load movielock.yaml. Search order: explicit path, $MOVIELOCK_CONFIG, ./movielock.yaml."""
    candidates = []
    if path:
        candidates.append(Path(path))
    elif os.environ.get("MOVIELOCK_CONFIG"):
        candidates.append(Path(os.environ["MOVIELOCK_CONFIG"]))
    else:
        candidates.append(Path.cwd() / "movielock.yaml")
    data: dict = {}
    source = None
    for cand in candidates:
        if cand.is_file():
            loaded = yaml.safe_load(cand.read_text(encoding="utf-8")) or {}
            if not isinstance(loaded, dict):
                raise MovielockError(f"{cand} must contain a mapping")
            data = loaded
            source = cand.resolve()
            break
        if path:
            raise MovielockError(f"config file not found: {cand}")
    merged = _merge(DEFAULTS, data)
    if overrides:
        merged = _merge(merged, overrides)
    root = source.parent if source else Path.cwd()
    return Config(merged, root, source)
