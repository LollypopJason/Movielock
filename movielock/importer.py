"""Build assets/voices/index.yaml from a folder of exported voice lines.

Layout expected (one subfolder per hero; deeper folders become tags)::

    sounds/
      haze/            <- hero from the folder name (id, display name, alias or class name)
        haze_taunt_01.wav
        kills/haze_kill_03.mp3
      hero_inferno/
        ...

Transcripts can come from a CSV (columns: id or file, transcript) and/or a Valve
closecaption KeyValues text file (``"token" "text"`` pairs).
"""

from __future__ import annotations

import csv
import json
import os
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from .library import Library, load_voice_index, write_voice_index

AUDIO_EXTS = {".wav", ".mp3", ".ogg", ".flac", ".m4a"}


@dataclass
class ImportReport:
    added: int = 0
    kept: int = 0
    with_transcript: int = 0
    skipped_folders: dict[str, int] = field(default_factory=dict)
    duplicates: list[str] = field(default_factory=list)
    index_path: Path | None = None

    def lines(self) -> list[str]:
        out = [f"voice lines indexed: {self.added} new, {self.kept} kept from the old index",
               f"with transcripts: {self.with_transcript}"]
        for folder, n in sorted(self.skipped_folders.items()):
            out.append(f"skipped folder '{folder}' ({n} files): unknown hero - add it to a hero's "
                       f"aliases in library.yaml")
        if self.duplicates:
            out.append(f"renamed {len(self.duplicates)} duplicate id(s), e.g. {self.duplicates[0]}")
        if self.index_path:
            out.append(f"wrote {self.index_path}")
        return out


def slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9_.\-]+", "_", text.lower()).strip("_.-")
    return s or "line"


def load_transcripts_csv(path: Path) -> dict[str, str]:
    """Read a CSV with a key column (id/file/name/filename) and a text column (transcript/text/caption)."""
    out: dict[str, str] = {}
    with open(path, newline="", encoding="utf-8-sig") as f:
        sample = f.read(4096)
        f.seek(0)
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
        except csv.Error:
            dialect = csv.excel
        reader = csv.DictReader(f, dialect=dialect)
        cols = {c.lower().strip(): c for c in reader.fieldnames or []}
        key_col = next((cols[c] for c in ("id", "file", "filename", "name", "path") if c in cols), None)
        text_col = next((cols[c] for c in ("transcript", "text", "caption", "subtitle", "line") if c in cols), None)
        if not key_col or not text_col:
            raise ValueError(f"{path}: need a key column (id/file) and a text column (transcript/text); "
                             f"found {reader.fieldnames}")
        for row in reader:
            key, text = (row.get(key_col) or "").strip(), (row.get(text_col) or "").strip()
            if key and text:
                out[_key(key)] = text
    return out


_KV_PAIR = re.compile(r'"((?:[^"\\]|\\.)*)"\s+"((?:[^"\\]|\\.)*)"')
_CC_TAG = re.compile(r"<[^>]*>")


def load_closecaption_kv(path: Path) -> dict[str, str]:
    """Parse token/text pairs from a Valve closecaption KeyValues text file."""
    raw = path.read_bytes()
    text = raw.decode("utf-16") if raw[:2] in (b"\xff\xfe", b"\xfe\xff") else raw.decode("utf-8", "replace")
    out = {}
    for token, value in _KV_PAIR.findall(text):
        value = _CC_TAG.sub("", value.replace('\\"', '"')).strip()
        if token.lower() in ("language",) or not value:
            continue
        out[_key(token)] = value
    return out


def _key(name: str) -> str:
    """Normalise a file name / id / caption token for matching."""
    base = os.path.basename(name.replace("\\", "/"))
    stem = base.rsplit(".", 1)[0] if "." in base and base.rsplit(".", 1)[1].lower() in {
        e.strip(".") for e in AUDIO_EXTS | {".vsnd", ".vsnd_c"}} else base
    return re.sub(r"[^a-z0-9]+", "_", stem.lower()).strip("_")


def load_hero_names(path: Path) -> dict[str, str]:
    """Optional class_name -> display name map written by tools/fetch_hero_names.py."""
    if not path.is_file():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return {str(k): str(v) for k, v in data.items()}


def import_sounds(source: Path, library: Library, voices_dir: Path, *, transcripts: dict[str, str] | None = None,
                  copy: bool = False, merge: bool = True, hero_names: dict[str, str] | None = None,
                  dry_run: bool = False) -> ImportReport:
    source, voices_dir = Path(source).resolve(), Path(voices_dir).resolve()
    transcripts = transcripts or {}
    hero_names = {_key(k): v for k, v in (hero_names or {}).items()}
    report = ImportReport()

    existing = load_voice_index(voices_dir) if merge else {}
    entries: dict[str, dict] = {}
    for v in existing.values():
        if v.file.is_file():
            entries[v.id] = {"id": v.id, "hero": v.hero, "file": _rel(v.file, voices_dir),
                             "transcript": v.transcript, **({"tags": v.tags} if v.tags else {})}
    report.kept = len(entries)
    known_files = {e["file"] for e in entries.values()}

    def resolve(folder: str) -> str | None:
        hero = library.resolve_hero(folder)
        if not hero and _key(folder) in hero_names:
            hero = library.resolve_hero(hero_names[_key(folder)])
        return hero

    for hero_dir in sorted(p for p in source.iterdir() if p.is_dir()):
        files = sorted(p for p in hero_dir.rglob("*") if p.suffix.lower() in AUDIO_EXTS and p.is_file())
        if not files:
            continue
        hero = resolve(hero_dir.name)
        if not hero:
            report.skipped_folders[hero_dir.name] = len(files)
            continue
        for f in files:
            if copy and not _inside(f, voices_dir):
                dest = voices_dir / hero / f.relative_to(hero_dir)
                if not dry_run:
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(f, dest)
                f_ref = dest
            else:
                f_ref = f
            rel = _rel(f_ref, voices_dir)
            if rel in known_files:
                continue
            vid = slug(f.stem)
            if not vid.startswith(hero):
                vid = f"{hero}_{vid}"
            if vid in entries:
                base, n = vid, 2
                while f"{base}_{n}" in entries:
                    n += 1
                vid = f"{base}_{n}"
                report.duplicates.append(vid)
            tags = [slug(p) for p in f.relative_to(hero_dir).parent.parts]
            text = (transcripts.get(_key(f.name)) or transcripts.get(_key(vid))
                    or transcripts.get(_key(str(f.relative_to(source)))) or "")
            entry = {"id": vid, "hero": hero, "file": rel, "transcript": text}
            if tags:
                entry["tags"] = tags
            entries[vid] = entry
            known_files.add(rel)
            report.added += 1

    # Fill transcripts for kept entries too, if new data is available.
    for e in entries.values():
        if not e.get("transcript"):
            e["transcript"] = transcripts.get(_key(e["file"])) or transcripts.get(_key(e["id"])) or ""
    report.with_transcript = sum(1 for e in entries.values() if e.get("transcript"))
    ordered = sorted(entries.values(), key=lambda e: (e["hero"], e["id"]))
    if not dry_run:
        report.index_path = write_voice_index(voices_dir, ordered)
    return report


def _inside(path: Path, folder: Path) -> bool:
    try:
        path.resolve().relative_to(folder)
        return True
    except ValueError:
        return False


def _rel(path: Path, base: Path) -> str:
    path = Path(path).resolve()
    if _inside(path, base):
        return path.relative_to(base).as_posix()
    return path.as_posix()
