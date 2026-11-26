"""Fill in missing voice-line transcripts with local speech-to-text.

Engines (movielock.yaml -> transcribe.engine):
  faster-whisper  pip install faster-whisper   (fast on CPU, recommended)
  whisper         pip install openai-whisper
  command         any program that prints the transcript; transcribe.command = "mytool {file}"
  auto            the first of the above that is available

Transcripts are written back into assets/voices/index.yaml. They stay on your machine.
"""

from __future__ import annotations

import shlex
import subprocess
from pathlib import Path
from typing import Callable

from .config import Config
from .errors import MovielockError
from .importer import _rel
from .library import Library, write_voice_index

Transcriber = Callable[[Path], str]


def make_transcriber(cfg: Config) -> tuple[str, Transcriber]:
    tcfg = cfg["transcribe"]
    engine = (tcfg.get("engine") or "auto").lower()
    model_name = tcfg.get("model", "small")
    language = tcfg.get("language") or None
    errors = []

    if engine in ("auto", "faster-whisper"):
        try:
            from faster_whisper import WhisperModel  # type: ignore

            model = WhisperModel(model_name, device="auto", compute_type="int8")

            def fw(path: Path) -> str:
                segments, _ = model.transcribe(str(path), language=language, beam_size=5)
                return " ".join(s.text.strip() for s in segments).strip()
            return "faster-whisper", fw
        except ImportError:
            errors.append("faster-whisper not installed (pip install faster-whisper)")

    if engine in ("auto", "whisper"):
        try:
            import whisper  # type: ignore

            wmodel = whisper.load_model(model_name)

            def ow(path: Path) -> str:
                return str(wmodel.transcribe(str(path), language=language)["text"]).strip()
            return "whisper", ow
        except ImportError:
            errors.append("openai-whisper not installed (pip install openai-whisper)")

    if engine in ("auto", "command"):
        command = (tcfg.get("command") or "").strip()
        if command:
            template = shlex.split(command)

            def cmd(path: Path) -> str:
                argv = [a.replace("{file}", str(path)) for a in template]
                proc = subprocess.run(argv, capture_output=True, text=True)
                if proc.returncode != 0:
                    raise MovielockError(f"transcribe command failed: {proc.stderr.strip()[-300:]}")
                return proc.stdout.strip()
            return "command", cmd
        errors.append("transcribe.command is empty")

    raise MovielockError("no speech-to-text engine available:\n  " + "\n  ".join(errors))


def transcribe_missing(library: Library, voices_dir: Path, transcriber: Transcriber, *,
                       heroes: list[str] | None = None, limit: int | None = None, save_every: int = 25,
                       log=print) -> int:
    """Transcribe voice lines that have a file but no transcript; returns how many were filled."""
    wanted = {library.resolve_hero(h) or h for h in heroes} if heroes else None
    todo = [v for v in library.voices.values()
            if not v.transcript and v.file.is_file() and (wanted is None or v.hero in wanted)]
    if limit:
        todo = todo[:limit]

    def save():
        entries = [{"id": v.id, "hero": v.hero, "file": _rel(v.file, Path(voices_dir).resolve()),
                    "transcript": v.transcript, **({"tags": v.tags} if v.tags else {})}
                   for v in sorted(library.voices.values(), key=lambda v: (v.hero, v.id))]
        write_voice_index(voices_dir, entries)

    done = 0
    for i, line in enumerate(todo, 1):
        try:
            line.transcript = transcriber(line.file)
            done += 1
        except Exception as exc:  # noqa: BLE001 - skip this file, keep going
            if log:
                log(f"  {line.id}: {exc}")
        if log and (i % 10 == 0 or i == len(todo)):
            log(f"  transcribed {i}/{len(todo)}")
        if i % save_every == 0:
            save()
    if todo:
        save()
    return done
