"""script -> validate -> plan -> render shots -> mix audio + captions -> MP4."""

from __future__ import annotations

import re
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from ..config import Config
from ..errors import MovielockError
from ..library import load_library
from ..lockscript import load_script
from ..planner import build_plan, write_plan
from .assemble import concat_clips, final_mux, mix_audio, write_ass
from .blender import render_shot_blender
from .ffmpeg import install_font
from .storyboard import render_shot_storyboard

Progress = Callable[[float, str], None]


@dataclass
class RenderResult:
    output: Path
    plan_path: Path
    work_dir: Path
    renderer: str
    duration: float
    warnings: list[str] = field(default_factory=list)


def _slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "_", text).strip("_") or "movie"


def choose_renderer(cfg: Config, requested: str | None, log) -> str:
    choice = (requested or cfg["renderer"] or "auto").lower()
    if choice not in ("auto", "blender", "storyboard"):
        raise MovielockError(f"unknown renderer '{choice}' (auto, blender, storyboard)")
    if choice == "auto":
        if cfg.tool("blender", required=False):
            return "blender"
        log("Blender not found: rendering a storyboard preview instead (set tools.blender in movielock.yaml)")
        return "storyboard"
    if choice == "blender":
        cfg.tool("blender")  # raises a helpful error if missing
    return choice


def render_doc(doc: dict, cfg: Config, *, name: str = "movie", quality: str = "preview",
               renderer: str | None = None, out: Path | None = None, smap: dict | None = None,
               progress: Progress | None = None, log=print, keep_work: bool = True) -> RenderResult:
    def report(frac: float, msg: str):
        if progress:
            progress(max(0.0, min(1.0, frac)), msg)
        if log:
            log(f"[{int(frac * 100):3d}%] {msg}")

    ffmpeg = cfg.tool("ffmpeg")
    cfg.tool("ffprobe")
    report(0.0, "loading library")
    library = load_library(cfg.assets_dir, cfg.voices_dir)
    which = choose_renderer(cfg, renderer, log or (lambda _m: None))

    report(0.03, "planning (timings, lip sync, cameras)")
    plan = build_plan(doc, library, cfg, quality, smap, log=log)
    plan["renderer"] = which

    stamp = time.strftime("%Y%m%d-%H%M%S")
    work = (cfg.renders_dir / "work" / f"{_slug(name)}-{stamp}").resolve()
    work.mkdir(parents=True, exist_ok=True)
    plan_path = write_plan(plan, work / "plan.json")
    font = install_font(work, cfg["storyboard"].get("font_file", ""))

    clips: list[Path] = []
    total_frames = max(1, plan["frames"])
    done = 0
    for shot in plan["shots"]:
        frac = 0.1 + 0.75 * done / total_frames
        report(frac, f"rendering shot {shot['index'] + 1}/{len(plan['shots'])} ({shot['id']}, {which})")
        clip = work / "clips" / f"{shot['index']:03d}_{shot['id']}.mp4"
        clip.parent.mkdir(parents=True, exist_ok=True)
        if which == "storyboard":
            render_shot_storyboard(plan, shot, work, clip, ffmpeg, font, plan["quality"])
        else:
            def shot_progress(f, _shot=shot, _done=done):
                report(0.1 + 0.75 * (_done + f * _shot["frames"]) / total_frames,
                       f"rendering shot {_shot['index'] + 1}/{len(plan['shots'])} frames")
            render_shot_blender(cfg, plan_path, shot, work, clip, plan["quality"], progress=shot_progress,
                                log=log)
        clips.append(clip)
        done += shot["frames"]

    report(0.86, "joining shots")
    video = concat_clips(ffmpeg, clips, work / "video.mp4")
    report(0.9, "mixing audio")
    audio = mix_audio(ffmpeg, plan, work / "audio.wav", cfg["audio"])
    captions = None
    if plan["captions_enabled"] and plan["captions"]:
        captions = write_ass(plan, work / "captions.ass", cfg["captions"])
    report(0.94, "encoding final video")
    if out is None:
        out = cfg.renders_dir / f"{_slug(name)}_{quality}.mp4"
    out = Path(out).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    final_mux(ffmpeg, video, audio, out, plan, plan["quality"], work, captions, font is not None)
    if not keep_work:
        shutil.rmtree(work / "clips", ignore_errors=True)
        shutil.rmtree(work / "frames", ignore_errors=True)
    report(1.0, f"done: {out}")
    return RenderResult(out, plan_path, work, which, plan["duration"], plan["warnings"])


def render_file(path: Path, cfg: Config, **kw) -> RenderResult:
    doc, smap = load_script(path)
    kw.setdefault("name", Path(path).stem)
    return render_doc(doc, cfg, smap=smap, **kw)
