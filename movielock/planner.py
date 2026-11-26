"""Turn a validated script into a render plan (plan.json).

The plan is renderer-independent: absolute file paths, frame ranges, actor placement and
facing, animation strips, mouth cues, camera keys, captions and a flat audio list.
Times inside a shot are *local* seconds (0 = first frame of the shot); the ``audio`` and
``captions`` lists use *absolute* seconds from the start of the video.
"""

from __future__ import annotations

import json
from pathlib import Path

from . import lipsync
from .audio import duration as audio_duration
from .camera import compute_camera, yaw_towards
from .config import RESOLUTIONS, Config
from .errors import MovielockError, ValidationFailed
from .library import Library
from .lockscript import normalize
from .tts import synthesize
from .validate import errors_only, validate_doc

PLAN_VERSION = 1
DEFAULT_ONCE_LENGTH = 1.5   # seconds assumed for a one-shot animation of unknown length


def _even(n: float) -> int:
    return max(2, int(round(n / 2)) * 2)


def _place_cast(cast: list[dict], library: Library) -> dict[str, dict]:
    """Positions (auto-spread if missing) and yaw for every cast member."""
    n = len(cast)
    spacing = 2.4
    placed: dict[str, dict] = {}
    for i, c in enumerate(cast):
        hero = library.hero(c["hero"])
        pos = list(c.get("position") or [(i - (n - 1) / 2) * spacing, 0.0])
        if len(pos) == 2:
            pos.append(0.0)
        placed[c["name"]] = {"name": c["name"], "hero": hero.id, "position": [float(v) for v in pos],
                             "height": hero.height * hero.scale, "faces": c.get("faces")}
    for name, a in placed.items():
        faces = a.pop("faces")
        if isinstance(faces, (int, float)):
            a["yaw"] = float(faces)
        elif isinstance(faces, str) and faces in placed:
            a["yaw"] = yaw_towards(a["position"], placed[faces]["position"])
        elif len(placed) > 1:
            others = [o["position"] for o in placed.values() if o is not a]
            cx = sum(p[0] for p in others) / len(others)
            cy = sum(p[1] for p in others) / len(others)
            a["yaw"] = yaw_towards(a["position"], (cx, cy))
        else:
            a["yaw"] = 0.0
    return placed


def build_plan(doc: dict, library: Library, cfg: Config, quality: str = "preview", smap: dict | None = None,
               log=print) -> dict:
    issues = validate_doc(doc, library, smap, tts_enabled=bool(cfg["tts"].get("enabled")))
    if errors_only(issues):
        raise ValidationFailed(issues)
    warnings = [str(i) for i in issues if i.severity == "warning"]

    doc = normalize(doc)
    q = cfg.quality(quality)
    fps = int(doc["fps"])
    base_w, base_h = RESOLUTIONS[doc["format"]]
    width, height = _even(base_w * q["scale"]), _even(base_h * q["scale"])
    timing = cfg["timing"]
    gap, tail, lead = float(timing["gap"]), float(timing["tail"]), float(timing.get("lead", 0.0))
    wps = float(timing["words_per_second"])
    rhubarb = cfg.tool("rhubarb", required=False)
    ffmpeg = cfg.tool("ffmpeg", required=False) or "ffmpeg"
    ffprobe = cfg.tool("ffprobe", required=False)
    cache = cfg.cache_dir
    lip_methods: set[str] = set()

    def warn(msg):
        warnings.append(f"warning: {msg}")
        if log:
            log(f"warning: {msg}")

    placed = _place_cast(doc["cast"], library)
    heroes = {name: library.hero(a["hero"]) for name, a in placed.items()}
    cast_plan = []
    for name, a in placed.items():
        hp = heroes[name].to_plan()
        cast_plan.append({"name": name, "hero": hp, "position": a["position"], "yaw": a["yaw"],
                          "height": a["height"]})

    shots_plan = []
    audio: list[dict] = []
    captions: list[dict] = []
    frame_cursor = 0
    audio_cfg = cfg["audio"]

    for si, shot in enumerate(doc["shots"]):
        # ---- dialogue timing
        lines = []
        cursor = lead
        for ev in shot["events"]:
            if ev["type"] not in ("voice", "tts"):
                continue
            actor = ev["actor"]
            hero = heroes[actor]
            if ev["type"] == "voice":
                vl = library.voices[ev["line"]]
                f = vl.file
                text = ev.get("caption") if ev.get("caption") is not None else vl.transcript
                transcript = vl.transcript
                length = audio_duration(f, ffprobe)
            else:
                text = transcript = ev["text"]
                f = None
                if cfg["tts"].get("enabled"):
                    f = synthesize(ev["text"], hero.id, cfg["tts"], cache / "tts", library)
                    length = audio_duration(f, ffprobe)
                else:
                    words = max(1, len(ev["text"].split()))
                    length = max(1.0, words / wps)
            start = float(ev["at"]) if ev.get("at") is not None else cursor
            end = start + length
            cursor = max(cursor, end + gap)
            cues, method = lipsync.cues_for(f, transcript=transcript, duration=length, rhubarb=rhubarb,
                                            ffmpeg=ffmpeg, cache_dir=cache / "lipsync", warn=warn)
            lip_methods.add(method)
            lines.append({"actor": actor, "kind": ev["type"], "id": ev.get("line"), "file": str(f) if f else None,
                          "start": round(start, 4), "end": round(end, 4), "text": text or "",
                          "speaker": hero.name, "cues": cues, "lipsync": method})

        # ---- shot length
        content_end = max((ln["end"] for ln in lines), default=None)
        needed = content_end + tail if content_end is not None else float(timing["min_shot"])
        for ev in shot["events"]:
            if ev["type"] == "sfx" and ev.get("at") is not None:
                needed = max(needed, ev["at"] + 0.3)
            if ev["type"] == "anim" and ev.get("at") is not None:
                needed = max(needed, ev["at"] + 0.5)
        explicit = shot.get("duration")
        dur = max(float(explicit), needed) if explicit is not None else needed
        if explicit is not None and needed > float(explicit) + 1e-6 and lines:
            warn(f"shot {shot['id']}: lines need {needed:.2f}s, longer than the {explicit}s given; extended")
        frames = max(1, int(round(dur * fps)))
        dur = frames / fps
        shot_start = frame_cursor / fps

        # ---- animation strips per actor
        actors_plan = {}
        for name in placed:
            hero = heroes[name]
            anims = hero.animations
            strips = []
            # base layer: idle, replaced from @t by looping anims
            base_changes = [(0.0, "idle")] if "idle" in anims else []
            overlays = []
            for ev in shot["events"]:
                if ev["type"] != "anim" or ev["actor"] != name:
                    continue
                spec = anims[ev["anim"]]
                t = float(ev.get("at") or 0.0)
                once = ev.get("once", not spec["loop"])
                if once:
                    length = spec.get("length") or DEFAULT_ONCE_LENGTH
                    overlays.append({"name": ev["anim"], "action": spec["action"], "start": t,
                                     "end": min(dur, t + float(length)), "loop": False, "layer": 2,
                                     "speed": spec["speed"], "length_known": bool(spec.get("length"))})
                else:
                    base_changes = [c for c in base_changes if c[0] < t] + [(t, ev["anim"])]
            base_changes.sort()
            for i, (t, anim) in enumerate(base_changes):
                end = base_changes[i + 1][0] if i + 1 < len(base_changes) else dur
                if end > t:
                    spec = anims[anim]
                    strips.append({"name": anim, "action": spec["action"], "start": t, "end": end, "loop": True,
                                   "layer": 0, "speed": spec["speed"]})
            # talk layer while this actor speaks, unless the base anim was chosen explicitly
            if "talk" in anims:
                for ln in lines:
                    if ln["actor"] != name:
                        continue
                    base_now = next((s for s in strips if s["start"] <= ln["start"] < s["end"]), None)
                    if base_now is None or base_now["name"] == "idle":
                        strips.append({"name": "talk", "action": anims["talk"]["action"], "start": ln["start"],
                                       "end": min(dur, ln["end"]), "loop": True, "layer": 1,
                                       "speed": anims["talk"]["speed"]})
            strips.extend(overlays)
            for s in strips:
                s["start"], s["end"] = round(s["start"], 4), round(s["end"], 4)

            # mouth cues in shot time
            mouth = []
            for ln in sorted((ln for ln in lines if ln["actor"] == name), key=lambda x: x["start"]):
                for c in ln["cues"]:
                    s, e = ln["start"] + c["start"], min(ln["start"] + c["end"], ln["end"])
                    if s < dur:
                        mouth.append({"start": round(s, 4), "end": round(min(e, dur), 4), "shape": c["shape"]})
            actors_plan[name] = {"anims": strips, "mouth": mouth}

        # ---- camera
        on = shot.get("on")
        if not on:
            on = next((ln["actor"] for ln in lines), None)
        cam = compute_camera(shot["camera"], placed, on, shot.get("toward"), dur, width / height)

        # ---- audio and captions (absolute time)
        for ln in lines:
            if ln["file"]:
                audio.append({"kind": ln["kind"], "file": ln["file"], "start": round(shot_start + ln["start"], 4),
                              "volume": float(audio_cfg["voice_volume"]), "duck": True})
            if ln["text"] and doc["captions"]:
                captions.append({"start": round(shot_start + ln["start"], 4),
                                 "end": round(shot_start + ln["end"], 4), "speaker": ln["speaker"],
                                 "text": ln["text"]})
        sfx = []
        for ev in shot["events"]:
            if ev["type"] != "sfx":
                continue
            snd = library.sounds[ev["sound"]]
            at = float(ev.get("at") or 0.0)
            vol = float(ev.get("volume", 1.0)) * snd.volume * float(audio_cfg["sfx_volume"])
            sfx.append({"sound": ev["sound"], "start": at})
            if snd.file.is_file():
                audio.append({"kind": "sfx", "file": str(snd.file), "start": round(shot_start + at, 4),
                              "volume": round(vol, 4), "duck": False})

        shots_plan.append({
            "id": shot["id"], "index": si, "camera": cam, "on": cam["subject"], "toward": cam["partner"],
            "start": round(shot_start, 6), "duration": round(dur, 6),
            "frame_start": frame_cursor, "frames": frames,
            "lines": [{k: v for k, v in ln.items() if k != "cues"} for ln in lines],
            "sfx": sfx, "actors": actors_plan,
        })
        frame_cursor += frames

    total = frame_cursor / fps
    music = doc.get("music")
    music_plan = None
    if music:
        m = library.music[music["id"]]
        vol = float(music["volume"]) if music.get("volume") is not None else m.volume
        if m.file.is_file():
            music_plan = {"id": m.id, "file": str(m.file), "volume": round(vol, 4), "loop": True}

    map_plan = library.maps[doc["map"]].to_plan() if doc.get("map") else None
    plan = {
        "version": PLAN_VERSION,
        "title": doc.get("title") or "Untitled",
        "format": doc["format"], "fps": fps, "width": width, "height": height,
        "duration": round(total, 6), "frames": frame_cursor,
        "quality": q, "captions_enabled": bool(doc["captions"]),
        "map": map_plan, "cast": cast_plan, "shots": shots_plan,
        "audio": sorted(audio, key=lambda a: a["start"]), "music": music_plan,
        "captions": captions, "lipsync": sorted(lip_methods),
        "conventions": {"units": "metres", "up": "+Z", "yaw0_faces": "-Y", "time": "seconds"},
        "warnings": warnings,
    }
    return plan


def write_plan(plan: dict, path: Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(plan, indent=2), encoding="utf-8")
    return path


def load_plan(path: Path) -> dict:
    plan = json.loads(Path(path).read_text(encoding="utf-8"))
    if plan.get("version") != PLAN_VERSION:
        raise MovielockError(f"{path}: unsupported plan version {plan.get('version')}")
    return plan


def mouth_shape_at(mouth: list[dict], t: float) -> str:
    for c in mouth:
        if c["start"] <= t < c["end"]:
            return c["shape"]
    return "X"

