"""Script validation: JSON Schema structure + checks against the asset library."""

from __future__ import annotations

import difflib
import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import jsonschema

from .library import DEFAULT_ANIMS, Library

SCHEMA_PATH = Path(__file__).parent / "data" / "lockscript.schema.json"


@lru_cache(maxsize=1)
def schema() -> dict:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


@dataclass
class Issue:
    severity: str          # "error" | "warning"
    message: str
    path: tuple = ()
    line: int | None = None

    def __str__(self) -> str:
        where = f"line {self.line}: " if self.line else (f"{_fmt_path(self.path)}: " if self.path else "")
        return f"{self.severity}: {where}{self.message}"

    def to_dict(self) -> dict:
        return {"severity": self.severity, "message": self.message, "line": self.line,
                "path": _fmt_path(self.path)}


def _fmt_path(path: tuple) -> str:
    out = ""
    for p in path:
        out += f"[{p}]" if isinstance(p, int) else (f".{p}" if out else str(p))
    return out


def _line_for(path: tuple, smap: dict) -> int | None:
    path = tuple(path)
    while True:
        if path in smap:
            return smap[path]
        if not path:
            return None
        path = path[:-1]


def _suggest(value: str, options) -> str:
    options = list(options)
    close = difflib.get_close_matches(value, options, n=3, cutoff=0.6)
    if close:
        return f" (did you mean {', '.join(repr(c) for c in close)}?)"
    if not options:
        return " (none defined)"
    if len(options) <= 12:
        return f" (known: {', '.join(sorted(options))})"
    return ""


def validate_doc(doc: dict, library: Library | None = None, smap: dict | None = None,
                 tts_enabled: bool = False, check_files: bool = True) -> list[Issue]:
    """Return all problems found. Errors block rendering; warnings don't."""
    smap = smap or {}
    issues: list[Issue] = []

    def add(sev, msg, path=()):
        issues.append(Issue(sev, msg, tuple(path), _line_for(tuple(path), smap)))

    validator = jsonschema.Draft202012Validator(schema())
    for err in sorted(validator.iter_errors(doc), key=lambda e: list(map(str, e.absolute_path))):
        add("error", _schema_message(err), tuple(err.absolute_path))
    if any(i.severity == "error" for i in issues):
        return issues  # structural problems: semantic checks would just add noise

    cast = {c["name"]: c for c in doc.get("cast", [])}
    names_seen = set()
    for i, c in enumerate(doc.get("cast", [])):
        if c["name"] in names_seen:
            add("error", f"cast name '{c['name']}' is used twice", ("cast", i))
        names_seen.add(c["name"])
        if library is not None and c["hero"] not in library.heroes:
            add("error", f"unknown hero '{c['hero']}' for cast '{c['name']}'"
                         f"{_suggest(c['hero'], library.heroes)}", ("cast", i))
        faces = c.get("faces")
        if isinstance(faces, str):
            if faces not in cast:
                add("error", f"'{c['name']}' faces unknown cast '{faces}'{_suggest(faces, cast)}", ("cast", i))
            elif faces == c["name"]:
                add("error", f"'{c['name']}' can't face itself", ("cast", i))

    if library is not None:
        if doc.get("map") and doc["map"] not in library.maps:
            add("error", f"unknown map '{doc['map']}'{_suggest(doc['map'], library.maps)}", ("map",))
        elif doc.get("map") and check_files:
            m = library.maps[doc["map"]]
            if m.model and not m.model.is_file():
                add("warning", f"map model file missing: {m.model} (the Blender renderer will use a plain floor)",
                    ("map",))
        music = doc.get("music")
        if music:
            mid = music if isinstance(music, str) else music["id"]
            if mid not in library.music:
                add("error", f"unknown music '{mid}'{_suggest(mid, library.music)}", ("music",))
            elif check_files and not library.music[mid].file.is_file():
                add("warning", f"music file missing: {library.music[mid].file} (will be silent)", ("music",))

    if not cast:
        add("warning", "no CAST lines: the video will only show the map")

    shot_ids = set()
    for si, shot in enumerate(doc["shots"]):
        sp = ("shots", si)
        if shot.get("id"):
            if shot["id"] in shot_ids:
                add("error", f"shot id '{shot['id']}' is used twice", sp)
            shot_ids.add(shot["id"])
        for key in ("on", "toward"):
            if shot.get(key) and shot[key] not in cast:
                add("error", f"{key}={shot[key]}: no cast member with that name{_suggest(shot[key], cast)}", sp)
        if shot["camera"] == "two_shot" and len(cast) < 2:
            add("warning", "two_shot with fewer than two cast members", sp)
        if shot["camera"] == "over_shoulder" and len(cast) < 2:
            add("warning", "over_shoulder needs two cast members; it will look like a medium shot", sp)
        if shot.get("on") and shot.get("toward") and shot["on"] == shot["toward"]:
            add("error", "on= and toward= must be different cast members", sp)

        for ei, ev in enumerate(shot.get("events", [])):
            ep = sp + ("events", ei)
            kind = ev["type"]
            if kind in ("anim", "voice", "tts"):
                actor = ev["actor"]
                if actor not in cast:
                    add("error", f"unknown cast '{actor}'{_suggest(actor, cast)}", ep)
                    continue
                hero = library.heroes.get(cast[actor]["hero"]) if library else None
            dur = shot.get("duration")
            if ev.get("at") is not None and dur is not None and ev["at"] >= dur and kind != "sfx":
                add("warning", f"@{ev['at']} is at/after the shot's {dur}s duration; the shot will be extended", ep)
            if library is None:
                continue
            if kind == "anim":
                if hero and ev["anim"] not in hero.animations:
                    add("error", f"hero '{hero.id}' has no animation '{ev['anim']}'"
                                 f"{_suggest(ev['anim'], hero.animations)} - add it to library.yaml", ep)
            elif kind == "voice":
                line = library.voices.get(ev["line"])
                if line is None:
                    add("error", f"unknown voice line '{ev['line']}'{_suggest(ev['line'], library.voices)}", ep)
                    continue
                if hero and line.hero != hero.id:
                    add("warning", f"voice line '{line.id}' belongs to {line.hero}, "
                                   f"but '{ev['actor']}' is {hero.id}", ep)
                if check_files and not line.file.is_file():
                    add("error", f"voice file missing for '{line.id}': {line.file}", ep)
            elif kind == "tts":
                if not tts_enabled:
                    add("warning", "text-to-speech is off (tts.enabled in movielock.yaml): "
                                   "this line will be a silent caption", ep)
            elif kind == "sfx":
                snd = library.sounds.get(ev["sound"])
                if snd is None:
                    add("error", f"unknown sound effect '{ev['sound']}'{_suggest(ev['sound'], library.sounds)}", ep)
                elif check_files and not snd.file.is_file():
                    add("warning", f"sound file missing: {snd.file} (will be silent)", ep)

    if library is not None:
        for name, c in cast.items():
            hero = library.heroes.get(c["hero"])
            if hero:
                missing = [a for a in DEFAULT_ANIMS if a not in hero.animations]
                if missing:
                    add("warning", f"hero '{hero.id}' has no {'/'.join(missing)} animation in library.yaml "
                                   f"(it will stand still)", ("cast", list(cast).index(name)))
    return issues


def _schema_message(err: jsonschema.ValidationError) -> str:
    where = _fmt_path(tuple(err.absolute_path))
    if err.validator == "additionalProperties":
        return f"{where or 'script'}: {err.message}"
    if err.validator == "enum":
        return f"{where}: {err.instance!r} is not one of {', '.join(map(str, err.validator_value))}"
    if err.validator == "oneOf" and isinstance(err.instance, dict) and "type" in err.instance:
        # Find the branch matching the event type for a more precise message.
        for sub in err.context:
            idx = sub.relative_schema_path[0] if sub.relative_schema_path else None
            if not isinstance(idx, int) or sub.validator == "const":
                continue
            branch = err.validator_value[idx]
            if branch.get("properties", {}).get("type", {}).get("const") == err.instance["type"]:
                return f"{where}: {sub.message}"
        return f"{where}: event of type '{err.instance['type']}' has missing or unexpected fields"
    if err.validator == "pattern":
        return f"{where}: {err.instance!r} has invalid characters (letters, digits, _ . - only)"
    return f"{where}: {err.message}" if where else err.message


def errors_only(issues: list[Issue]) -> list[Issue]:
    return [i for i in issues if i.severity == "error"]
