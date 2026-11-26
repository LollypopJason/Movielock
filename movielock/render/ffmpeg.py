"""Running ffmpeg with long filter graphs, portably."""

from __future__ import annotations

import re
import shutil
import subprocess
from functools import lru_cache
from pathlib import Path

from ..errors import MovielockError

FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
    "C:/Windows/Fonts/arial.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/Library/Fonts/Arial Bold.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
]


@lru_cache(maxsize=8)
def ffmpeg_major(ffmpeg: str) -> int:
    try:
        out = subprocess.run([ffmpeg, "-version"], capture_output=True, text=True).stdout
    except OSError:
        return 0
    m = re.search(r"ffmpeg version n?(\d+)", out)
    return int(m.group(1)) if m else 7  # git builds: assume modern


def filter_script_args(ffmpeg: str, script: Path, complex_: bool = True) -> list[str]:
    """Arguments that load a filter graph from a file (option name changed in FFmpeg 7)."""
    if ffmpeg_major(ffmpeg) >= 7:
        return ["-/filter_complex" if complex_ else "-/vf", str(script)]
    return ["-filter_complex_script" if complex_ else "-filter_script:v", str(script)]


def run(cmd: list[str], cwd: Path | None = None, what: str = "ffmpeg") -> None:
    proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if proc.returncode != 0:
        tail = "\n".join(proc.stderr.strip().splitlines()[-12:])
        raise MovielockError(f"{what} failed (exit {proc.returncode}):\n{tail}")


def find_font(configured: str = "") -> str | None:
    for cand in [configured, *FONT_CANDIDATES]:
        if cand and Path(cand).is_file():
            return cand
    fc = shutil.which("fc-match")
    if fc:
        out = subprocess.run([fc, "-f", "%{file}", "sans:bold"], capture_output=True, text=True).stdout.strip()
        if out and Path(out).is_file():
            return out
    return None


def install_font(work_dir: Path, configured: str = "") -> str | None:
    """Copy a font into work_dir/fonts/ so filters can use a short relative path (no escaping)."""
    src = find_font(configured)
    if not src:
        return None
    fonts = work_dir / "fonts"
    fonts.mkdir(parents=True, exist_ok=True)
    dest = fonts / ("font" + Path(src).suffix.lower())
    if not dest.exists():
        shutil.copyfile(src, dest)
    return f"fonts/{dest.name}"
