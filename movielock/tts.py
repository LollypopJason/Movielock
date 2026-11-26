"""Optional text-to-speech through an external command (off by default).

movielock.yaml:
    tts:
      enabled: true
      command: "piper --model voices/en_US-ryan-medium.onnx --output_file {out}"
      stdin: true          # piper reads the text from stdin

Placeholders: {text} {out} {hero} {ref}. The command is split into arguments *before*
placeholders are filled in, and run without a shell, so script text can't inject commands.

{ref} is a reference clip of the hero's real voice, for voice-cloning engines. It stays empty
unless tts.voice_reference is true. Cloning a voice actor's voice without their consent is
legally risky (right of publicity, the game's terms, and in some places specific AI-voice laws):
only use it for private experiments, never publish the result.
"""

from __future__ import annotations

import hashlib
import shlex
import subprocess
from pathlib import Path

from .errors import MovielockError


def synthesize(text: str, hero: str, tts_cfg: dict, cache_dir: Path, library=None) -> Path:
    command = (tts_cfg.get("command") or "").strip()
    if not command:
        raise MovielockError("tts.enabled is true but tts.command is empty in movielock.yaml")
    ref = ""
    if tts_cfg.get("voice_reference"):
        ref = str((tts_cfg.get("references") or {}).get(hero) or "")
        if not ref and library is not None:
            lines = [v for v in library.voices_for(hero) if v.file.is_file()]
            ref = str(lines[0].file) if lines else ""
    key = hashlib.sha1(f"{command}|{hero}|{ref}|{text}".encode()).hexdigest()[:16]
    cache_dir.mkdir(parents=True, exist_ok=True)
    out = cache_dir / f"tts_{key}.wav"
    if out.is_file() and out.stat().st_size > 44:
        return out
    values = {"text": text, "out": str(out), "hero": hero, "ref": ref}
    try:
        argv = [arg.format(**values) for arg in shlex.split(command)]
    except (KeyError, IndexError, ValueError) as exc:
        raise MovielockError(f"bad tts.command template: {exc} (placeholders: {{text}} {{out}} {{hero}} {{ref}})")
    proc = subprocess.run(argv, input=text if tts_cfg.get("stdin") else None, capture_output=True, text=True)
    if proc.returncode != 0 or not out.is_file():
        raise MovielockError(f"TTS command failed ({argv[0]}): {proc.stderr.strip()[-400:]}")
    return out
