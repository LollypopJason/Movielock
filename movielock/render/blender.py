"""Run blender/render_shot.py for one shot and turn its PNG frames into a clip."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from ..config import Config
from ..errors import MovielockError
from .assemble import frames_to_clip

SCRIPT = Path(__file__).resolve().parents[2] / "blender" / "render_shot.py"
_PROGRESS = re.compile(r"MOVIELOCK_PROGRESS (\d+)/(\d+)")


def blender_script() -> Path:
    if not SCRIPT.is_file():
        raise MovielockError(f"Blender script not found: {SCRIPT}")
    return SCRIPT


def render_shot_blender(cfg: Config, plan_path: Path, shot: dict, work: Path, clip: Path, quality: dict,
                        progress=None, log=print) -> Path:
    blender = cfg.tool("blender")
    frames_dir = work / "frames" / shot["id"]
    frames_dir.mkdir(parents=True, exist_ok=True)
    for old in frames_dir.glob("*.png"):
        old.unlink()
    cmd = [blender, "-b", "--factory-startup", "-noaudio", "-P", str(blender_script()), "--",
           str(plan_path), shot["id"], str(frames_dir)]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
    tail: list[str] = []
    assert proc.stdout is not None
    for line in proc.stdout:
        line = line.rstrip()
        tail = (tail + [line])[-40:]
        m = _PROGRESS.search(line)
        if m and progress:
            progress(int(m.group(1)) / max(1, int(m.group(2))))
        elif line.startswith("MOVIELOCK_WARNING") and log:
            log("warning: " + line.split(" ", 1)[-1])
    code = proc.wait()
    rendered = sorted(frames_dir.glob("*.png"))
    if code != 0 or len(rendered) < shot["frames"]:
        raise MovielockError(f"Blender failed on shot {shot['id']} (exit {code}, {len(rendered)}/"
                             f"{shot['frames']} frames). Last output:\n" + "\n".join(tail[-15:]))
    return frames_to_clip(cfg.tool("ffmpeg"), frames_dir, clip, int(_fps(plan_path)), shot["frames"], quality)


def _fps(plan_path: Path) -> int:
    import json
    return json.loads(Path(plan_path).read_text(encoding="utf-8"))["fps"]
