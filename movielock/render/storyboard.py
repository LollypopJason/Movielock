"""Storyboard preview renderer: FFmpeg only, no Blender needed.

Each shot becomes a panel showing the shot number and camera, a top-down stage map
(cast as coloured squares with a facing dot, the camera and its aim point), a mouth box
next to whoever is talking (driven by the lip sync cues), the current animation, sound
effect markers and, when captions are off, the dialogue text.
"""

from __future__ import annotations

import math
import re
from pathlib import Path

from ..camera import facing_vector
from .ffmpeg import filter_script_args, run

MOUTH_OPEN = {"X": 0.08, "A": 0.04, "B": 0.3, "C": 0.6, "D": 1.0, "E": 0.7, "F": 0.45, "G": 0.25, "H": 0.5}
BG = "0x15171c"


def _color(hex_color: str, alpha: float = 1.0) -> str:
    c = hex_color.lstrip("#")
    if not re.fullmatch(r"[0-9a-fA-F]{6}", c):
        c = "888888"
    return f"0x{c}@{alpha:.2f}"


def _between(a: float, b: float) -> str:
    return f"enable='between(t,{a:.3f},{b:.3f})'"


class _Graph:
    def __init__(self, work: Path, shot_id: str, font: str | None):
        self.parts: list[str] = []
        self.work = work
        self.shot_id = shot_id
        self.font = font
        self.n = 0

    def box(self, x, y, w, h, color, enable=None):
        opt = f"drawbox=x={int(x)}:y={int(y)}:w={max(1, int(w))}:h={max(1, int(h))}:color={color}:t=fill"
        self.parts.append(opt + (f":{enable}" if enable else ""))

    def text(self, text, x, y, size, color="white", enable=None, box=False):
        if not text:
            return
        tdir = self.work / "text"
        tdir.mkdir(parents=True, exist_ok=True)
        name = f"{self.shot_id}_{self.n:04d}.txt"
        self.n += 1
        (tdir / name).write_text(text, encoding="utf-8")
        font = f"fontfile={self.font}" if self.font else "font=Sans"
        opt = (f"drawtext={font}:textfile=text/{name}:expansion=none:fontsize={max(8, int(size))}"
               f":fontcolor={color}:x={x}:y={y}")
        if box:
            opt += ":box=1:boxcolor=black@0.45:boxborderw=8"
        self.parts.append(opt + (f":{enable}" if enable else ""))

    def script(self) -> str:
        return "[0:v]" + (",\n".join(self.parts) if self.parts else "null") + "[v]"


def _top_layer_segments(strips: list[dict], duration: float) -> list[tuple[float, float, str]]:
    """Which animation is visible over time (highest layer wins)."""
    points = sorted({0.0, duration, *(s["start"] for s in strips), *(s["end"] for s in strips)})
    segs: list[list] = []
    for a, b in zip(points, points[1:]):
        if b - a < 1e-6:
            continue
        mid = (a + b) / 2
        active = [s for s in strips if s["start"] <= mid < s["end"]]
        name = max(active, key=lambda s: s["layer"])["name"] if active else "-"
        if segs and segs[-1][2] == name and abs(segs[-1][1] - a) < 1e-6:
            segs[-1][1] = b
        else:
            segs.append([a, b, name])
    return [tuple(s) for s in segs]


def render_shot_storyboard(plan: dict, shot: dict, work: Path, out: Path, ffmpeg: str, font: str | None,
                           quality: dict) -> Path:
    W, H, fps = plan["width"], plan["height"], plan["fps"]
    dur = shot["duration"]
    g = _Graph(work, shot["id"], font)
    u = min(W, H) / 100.0  # layout unit
    m = 4 * u
    cast = {c["name"]: c for c in plan["cast"]}
    n_shots = len(plan["shots"])

    # header
    g.box(0, 0, W, 13 * u, "0x000000@0.55")
    g.text(f"SHOT {shot['index'] + 1}/{n_shots}  {shot['camera']['type'].upper().replace('_', ' ')}",
           int(m), int(2 * u), 5.2 * u)
    sub = f"id {shot['id']}"
    if shot.get("on"):
        sub += f"   on {shot['on']}"
        if shot.get("toward"):
            sub += f" > {shot['toward']}"
    sub += f"   {dur:.2f}s"
    g.text(sub, int(m), int(8.2 * u), 3.2 * u, "0xbbbbbb")
    g.text(plan.get("title", ""), f"w-tw-{int(m)}", int(2.6 * u), 3.0 * u, "0x999999")

    # stage map area
    top = 15 * u
    bottom_reserved = H * (0.30 if H > W else 0.27)
    side = min(W - 2 * m, H - top - bottom_reserved)
    mx0, my0 = (W - side) / 2, top
    g.box(mx0, my0, side, side, "0x1f232b@1.0")
    keys = shot["camera"]["keys"]
    pts = [(c["position"][0], c["position"][1]) for c in cast.values()]
    pts += [(k["pos"][0], k["pos"][1]) for k in keys] + [(k["target"][0], k["target"][1]) for k in keys]
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    span = max(max(xs) - min(xs), max(ys) - min(ys), 2.0) + 2.0
    cx, cy = (max(xs) + min(xs)) / 2, (max(ys) + min(ys)) / 2
    scale = side / span

    def to_screen(x, y):
        return mx0 + side / 2 + (x - cx) * scale, my0 + side / 2 - (y - cy) * scale

    # grid
    step = 1.0 if span < 12 else 5.0
    gx = math.floor((cx - span / 2) / step) * step
    while gx <= cx + span / 2:
        sx, _ = to_screen(gx, 0)
        if mx0 <= sx <= mx0 + side:
            g.box(sx, my0, 1, side, "0xffffff@0.06")
        gx += step
    gy = math.floor((cy - span / 2) / step) * step
    while gy <= cy + span / 2:
        _, sy = to_screen(0, gy)
        if my0 <= sy <= my0 + side:
            g.box(mx0, sy, side, 1, "0xffffff@0.06")
        gy += step

    # camera path, aim point, camera
    for i, k in enumerate(keys):
        sx, sy = to_screen(k["pos"][0], k["pos"][1])
        size = 1.6 * u if i in (0, len(keys) - 1) else 0.8 * u
        g.box(sx - size / 2, sy - size / 2, size, size, "0xffffff@0.9" if i == 0 else "0xffffff@0.45")
    tx, ty = to_screen(keys[0]["target"][0], keys[0]["target"][1])
    g.box(tx - 0.6 * u, ty - 0.6 * u, 1.2 * u, 1.2 * u, "0xffd84a@0.9")
    sx, sy = to_screen(keys[0]["pos"][0], keys[0]["pos"][1])
    g.text("CAM", int(sx + 1.2 * u), int(sy + 0.6 * u), 2.2 * u, "0xffffff")

    # cast squares, facing dots, names, animation labels, mouth boxes
    sq = max(2.2 * u, min(6 * u, 0.5 * scale))
    speaking = {}
    for ln in shot["lines"]:
        speaking.setdefault(ln["actor"], []).append(ln)
    for name, c in cast.items():
        px, py = to_screen(c["position"][0], c["position"][1])
        color = c["hero"]["color"]
        for ln in speaking.get(name, []):  # glow while talking
            g.box(px - sq / 2 - 0.8 * u, py - sq / 2 - 0.8 * u, sq + 1.6 * u, sq + 1.6 * u,
                  "0xffffff@0.35", _between(ln["start"], ln["end"]))
        g.box(px - sq / 2, py - sq / 2, sq, sq, _color(color))
        fx, fy = facing_vector(c["yaw"])
        dx, dy = px + fx * sq * 0.75, py - fy * sq * 0.75
        g.box(dx - 0.5 * u, dy - 0.5 * u, u, u, "0xffffff@0.95")
        g.text(c["hero"]["name"], f"{int(px)}-tw/2", int(py + sq / 2 + 0.8 * u), 2.6 * u)
        actor = shot["actors"].get(name, {})
        for a, b, anim in _top_layer_segments(actor.get("anims", []), dur):
            if anim != "-":
                g.text(anim, f"{int(px)}-tw/2", int(py + sq / 2 + 4 * u), 2.1 * u, "0xaaaaaa", _between(a, b))
        mw = sq * 0.7
        for cue in actor.get("mouth", []):
            if cue["shape"] == "X":
                continue
            mh = max(2, sq * 0.6 * MOUTH_OPEN.get(cue["shape"], 0.3))
            g.box(px - mw / 2, py + sq * 0.05 - mh / 2 + sq * 0.1, mw, mh, "0x111111@0.95",
                  _between(cue["start"], cue["end"]))

    # speaker strip + dialogue (text only when captions won't be burned in)
    band_y = my0 + side + 2 * u
    for ln in shot["lines"]:
        color = cast[ln["actor"]]["hero"]["color"] if ln["actor"] in cast else "#888888"
        en = _between(ln["start"], ln["end"])
        g.box(m, band_y, W - 2 * m, 0.9 * u, _color(color), en)
        label = f"{ln['speaker']}" + ("  (TTS off)" if ln["kind"] == "tts" and not ln["file"] else "")
        g.text(label, int(m), int(band_y + 2 * u), 3.4 * u, "white", en)
        if not plan.get("captions_enabled"):
            g.text(ln["text"], int(m), int(band_y + 7 * u), 3.0 * u, "0xdddddd", en)

    for fx_ in shot.get("sfx", []):
        a = fx_["start"]
        g.text(f"SFX {fx_['sound']}", f"w-tw-{int(m)}", int(band_y + 2 * u), 3.0 * u, "0xffd84a",
               _between(a, a + 0.8))

    script = work / f"{shot['id']}.storyboard.txt"
    script.write_text(g.script(), encoding="utf-8")
    out.parent.mkdir(parents=True, exist_ok=True)
    cmd = [ffmpeg, "-y", "-v", "error", "-f", "lavfi", "-i", f"color=c={BG}:s={W}x{H}:r={fps}",
           *filter_script_args(ffmpeg, script.relative_to(work)), "-map", "[v]",
           "-frames:v", str(shot["frames"]), "-c:v", "libx264", "-preset", "veryfast",
           "-crf", str(min(int(quality.get("crf", 23)), 20)), "-pix_fmt", "yuv420p", "-r", str(fps),
           str(out.resolve())]
    run(cmd, cwd=work, what=f"storyboard shot {shot['id']}")
    return out
