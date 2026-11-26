"""Asset library: heroes, maps, sounds and music (assets/library.yaml) plus voice lines
(assets/voices/index.yaml). Everything here is data; nothing is hard-coded per hero."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .errors import MovielockError

VISEMES = ("A", "B", "C", "D", "E", "F", "G", "H", "X")
DEFAULT_ANIMS = ("idle", "talk")


@dataclass
class Hero:
    id: str
    name: str
    model: Path | None
    scale: float = 1.0
    height: float = 1.8
    yaw_offset: float = 0.0
    color: str = "#888888"
    animations: dict[str, dict] = field(default_factory=dict)   # our name -> {action, loop, length}
    visemes: dict[str, Any] = field(default_factory=dict)       # Rhubarb shape -> shape key(s)
    jaw_bone: str | None = None
    jaw_open: float = 18.0                                     # degrees for a fully open mouth
    aliases: list[str] = field(default_factory=list)

    def to_plan(self) -> dict:
        return {
            "id": self.id, "name": self.name, "model": str(self.model) if self.model else None,
            "model_exists": bool(self.model and self.model.is_file()),
            "scale": self.scale, "height": self.height, "yaw_offset": self.yaw_offset,
            "color": self.color, "animations": self.animations, "visemes": self.visemes,
            "jaw_bone": self.jaw_bone, "jaw_open": self.jaw_open,
        }


@dataclass
class MapDef:
    id: str
    name: str
    model: Path | None
    hdri: Path | None
    sky_color: str = "#20242c"
    scale: float = 1.0
    ground: bool = True
    hdri_strength: float = 1.0
    offset: list[float] = field(default_factory=lambda: [0.0, 0.0, 0.0])

    def to_plan(self) -> dict:
        return {
            "id": self.id, "name": self.name,
            "model": str(self.model) if self.model else None,
            "model_exists": bool(self.model and self.model.is_file()),
            "hdri": str(self.hdri) if self.hdri else None,
            "hdri_exists": bool(self.hdri and self.hdri.is_file()),
            "sky_color": self.sky_color, "scale": self.scale, "ground": self.ground,
            "hdri_strength": self.hdri_strength, "offset": self.offset,
        }


@dataclass
class Sound:
    id: str
    file: Path
    volume: float = 1.0
    description: str = ""


@dataclass
class VoiceLine:
    id: str
    hero: str
    file: Path
    transcript: str = ""
    tags: list[str] = field(default_factory=list)


class Library:
    def __init__(self, heroes, maps, sounds, music, voices, source: Path | None = None):
        self.heroes: dict[str, Hero] = heroes
        self.maps: dict[str, MapDef] = maps
        self.sounds: dict[str, Sound] = sounds
        self.music: dict[str, Sound] = music
        self.voices: dict[str, VoiceLine] = voices
        self.source = source

    # ------------------------------------------------------------------ queries
    def hero(self, hero_id: str) -> Hero:
        try:
            return self.heroes[hero_id]
        except KeyError:
            raise MovielockError(f"unknown hero '{hero_id}'") from None

    def resolve_hero(self, name: str) -> str | None:
        """Map an id, display name, alias or game class name (hero_inferno) to a hero id."""
        key = _norm(name)
        for hero in self.heroes.values():
            if key in {_norm(hero.id), _norm(hero.name), *(_norm(a) for a in hero.aliases)}:
                return hero.id
        if key.startswith("hero"):
            return self.resolve_hero(key[4:]) if len(key) > 4 else None
        return None

    def voices_for(self, hero_id: str) -> list[VoiceLine]:
        return [v for v in self.voices.values() if v.hero == hero_id]

    def summary(self) -> dict:
        return {
            "heroes": len(self.heroes), "maps": len(self.maps), "sounds": len(self.sounds),
            "music": len(self.music), "voices": len(self.voices),
            "voices_with_files": sum(1 for v in self.voices.values() if v.file.is_file()),
        }


def _norm(s: str) -> str:
    return "".join(ch for ch in s.lower() if ch.isalnum())


def _path(base: Path, value: str | None) -> Path | None:
    if not value:
        return None
    p = Path(value).expanduser()
    return p if p.is_absolute() else base / p


def _anim_map(raw: dict | None, where: str) -> dict[str, dict]:
    out = {}
    for name, spec in (raw or {}).items():
        if isinstance(spec, str):
            spec = {"action": spec}
        elif not isinstance(spec, dict) or "action" not in spec:
            raise MovielockError(f"{where}: animation '{name}' needs an action name")
        out[str(name)] = {
            "action": str(spec["action"]),
            "loop": bool(spec.get("loop", name in ("idle", "talk", "walk", "run"))),
            "length": spec.get("length"),
            "speed": float(spec.get("speed", 1.0)),
        }
    return out


def load_library(assets_dir: Path, voices_dir: Path | None = None) -> Library:
    assets_dir = Path(assets_dir)
    lib_file = assets_dir / "library.yaml"
    if not lib_file.is_file():
        raise MovielockError(f"asset library not found: {lib_file}")
    raw = yaml.safe_load(lib_file.read_text(encoding="utf-8")) or {}

    heroes = {}
    for hid, h in (raw.get("heroes") or {}).items():
        h = h or {}
        where = f"library.yaml heroes.{hid}"
        visemes = h.get("visemes") or {}
        bad = [k for k in visemes if str(k) not in VISEMES]
        if bad:
            raise MovielockError(f"{where}: unknown viseme(s) {bad}; use Rhubarb shapes {', '.join(VISEMES)}")
        heroes[str(hid)] = Hero(
            id=str(hid),
            name=str(h.get("name", hid)),
            model=_path(assets_dir, h.get("model")),
            scale=float(h.get("scale", 1.0)),
            height=float(h.get("height", 1.8)),
            yaw_offset=float(h.get("yaw_offset", 0.0)),
            color=str(h.get("color", "#888888")),
            animations=_anim_map(h.get("animations"), where),
            visemes={str(k): v for k, v in visemes.items()},
            jaw_bone=h.get("jaw_bone"),
            jaw_open=float(h.get("jaw_open", 18.0)),
            aliases=[str(a) for a in (h.get("aliases") or [])],
        )

    maps = {}
    for mid, m in (raw.get("maps") or {}).items():
        m = m or {}
        maps[str(mid)] = MapDef(
            id=str(mid), name=str(m.get("name", mid)),
            model=_path(assets_dir, m.get("model")), hdri=_path(assets_dir, m.get("hdri")),
            sky_color=str(m.get("sky_color", "#20242c")), scale=float(m.get("scale", 1.0)),
            ground=bool(m.get("ground", m.get("model") is None)),
            hdri_strength=float(m.get("hdri_strength", 1.0)),
            offset=[float(v) for v in (m.get("offset") or [0, 0, 0])],
        )

    def sound_table(section: str) -> dict[str, Sound]:
        table = {}
        for sid, s in (raw.get(section) or {}).items():
            if isinstance(s, str):
                s = {"file": s}
            if not s or "file" not in s:
                raise MovielockError(f"library.yaml {section}.{sid}: needs a file")
            table[str(sid)] = Sound(str(sid), _path(assets_dir, s["file"]), float(s.get("volume", 1.0)),
                                    str(s.get("description", "")))
        return table

    voices_dir = Path(voices_dir) if voices_dir else assets_dir / "voices"
    voices = load_voice_index(voices_dir)
    return Library(heroes, maps, sound_table("sounds"), sound_table("music"), voices, lib_file)


def load_voice_index(voices_dir: Path) -> dict[str, VoiceLine]:
    index = Path(voices_dir) / "index.yaml"
    if not index.is_file():
        return {}
    raw = yaml.safe_load(index.read_text(encoding="utf-8")) or {}
    entries = raw.get("voices", raw) if isinstance(raw, dict) else raw
    voices = {}
    for e in entries or []:
        if not isinstance(e, dict) or not {"id", "hero", "file"} <= e.keys():
            raise MovielockError(f"{index}: every voice needs id, hero and file (got {e!r})")
        vid = str(e["id"])
        if vid in voices:
            raise MovielockError(f"{index}: duplicate voice id '{vid}'")
        voices[vid] = VoiceLine(vid, str(e["hero"]), _path(Path(voices_dir), str(e["file"])),
                                str(e.get("transcript") or ""), [str(t) for t in e.get("tags") or []])
    return voices


def write_voice_index(voices_dir: Path, voices: list[dict]) -> Path:
    index = Path(voices_dir) / "index.yaml"
    index.parent.mkdir(parents=True, exist_ok=True)
    header = ("# Voice line index, generated by tools/import_sounds.py.\n"
              "# id: used in scripts (haze ! <id>); file: relative to this folder.\n")
    body = yaml.safe_dump({"voices": voices}, sort_keys=False, allow_unicode=True, width=120)
    index.write_text(header + body, encoding="utf-8")
    return index
