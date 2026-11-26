"""Assembly: shot clips -> concat, audio mix (adelay + amix, music ducked), ASS captions, H.264."""

from __future__ import annotations

from pathlib import Path

from .ffmpeg import filter_script_args, run

AFMT = "aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo"


def frames_to_clip(ffmpeg: str, frames_dir: Path, out: Path, fps: int, frames: int, quality: dict) -> Path:
    """PNG sequence (0001.png ...) from Blender -> intermediate clip."""
    cmd = [ffmpeg, "-y", "-v", "error", "-framerate", str(fps), "-start_number", "1",
           "-i", str(frames_dir / "%04d.png"), "-frames:v", str(frames),
           "-c:v", "libx264", "-preset", "veryfast" if quality.get("name") != "final" else "medium",
           "-crf", "12" if quality.get("name") == "final" else str(min(int(quality.get("crf", 23)), 20)),
           "-pix_fmt", "yuv420p", "-r", str(fps), str(out)]
    run(cmd, what="frames to clip")
    return out


def concat_clips(ffmpeg: str, clips: list[Path], out: Path) -> Path:
    listing = out.with_suffix(".txt")
    listing.write_text("".join(f"file '{c.resolve().as_posix()}'\n" for c in clips), encoding="utf-8")
    run([ffmpeg, "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(listing), "-c", "copy", str(out)],
        what="concat")
    return out


def mix_audio(ffmpeg: str, plan: dict, out: Path, audio_cfg: dict) -> Path:
    """Mix all voice/sfx items at their absolute times; music loops under them and ducks."""
    total = plan["duration"]
    items = [a for a in plan["audio"] if Path(a["file"]).is_file()]
    music = plan.get("music")
    if music and not Path(music["file"]).is_file():
        music = None
    inputs: list[str] = []
    chains: list[str] = []
    dialogue, effects = [], []

    for i, a in enumerate(items):
        inputs += ["-i", a["file"]]
        ms = int(round(a["start"] * 1000))
        label = f"a{i}"
        chains.append(f"[{i}:a]{AFMT},adelay=delays={ms}:all=1,volume={a['volume']:.4f}[{label}]")
        (dialogue if a.get("duck") else effects).append(label)

    def mix(labels, name):
        if len(labels) == 1:
            chains.append(f"[{labels[0]}]anull[{name}]")
        else:
            chains.append("".join(f"[{x}]" for x in labels)
                          + f"amix=inputs={len(labels)}:normalize=0:dropout_transition=0[{name}]")

    finals = []
    if dialogue:
        mix(dialogue, "dlg")
    if effects:
        mix(effects, "fx")
        finals.append("fx")
    if music:
        idx = len(items)
        inputs += ["-stream_loop", "-1", "-i", music["file"]]
        chains.append(f"[{idx}:a]{AFMT},volume={music['volume']:.4f},atrim=0:{total:.4f}[mus]")
        if dialogue and audio_cfg.get("duck", True):
            chains.append("[dlg]asplit=2[dlgmix][dlgkey]")
            chains.append("[mus][dlgkey]sidechaincompress=threshold=0.02:ratio=8:attack=20:release=400[musd]")
            finals += ["dlgmix", "musd"]
        else:
            finals.append("mus")
            if dialogue:
                finals.append("dlg")
    elif dialogue:
        finals.append("dlg")

    sr = int(audio_cfg.get("sample_rate", 48000))
    if not finals:
        cmd = [ffmpeg, "-y", "-v", "error", "-f", "lavfi", "-i", f"anullsrc=r={sr}:cl=stereo",
               "-t", f"{total:.4f}", "-c:a", "pcm_s16le", str(out)]
        run(cmd, what="silent audio")
        return out
    if len(finals) == 1:
        chains.append(f"[{finals[0]}]anull[pre]")
    else:
        chains.append("".join(f"[{x}]" for x in finals)
                      + f"amix=inputs={len(finals)}:normalize=0:dropout_transition=0[pre]")
    chains.append(f"[pre]alimiter=limit=0.95:level=disabled,apad,atrim=0:{total:.4f},aresample={sr}[out]")
    script = out.with_suffix(".filter.txt")
    script.write_text(";\n".join(chains), encoding="utf-8")
    cmd = [ffmpeg, "-y", "-v", "error", *inputs, *filter_script_args(ffmpeg, script),
           "-map", "[out]", "-c:a", "pcm_s16le", str(out)]
    run(cmd, what="audio mix")
    return out


def _ass_time(t: float) -> str:
    cs = int(round(max(0.0, t) * 100))
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _ass_text(text: str) -> str:
    return text.replace("\\", "/").replace("{", "(").replace("}", ")").replace("\n", "\\N")


def write_ass(plan: dict, out: Path, cap_cfg: dict, font_name: str | None = None) -> Path:
    W, H = plan["width"], plan["height"]
    size = max(12, int(H * float(cap_cfg.get("size", 0.045)) * (0.75 if H > W else 1.0)))
    margin_v = int(H * (0.16 if H > W else 0.06))  # keep clear of reel/short UI at the bottom
    margin_h = int(W * 0.06)
    font = font_name or cap_cfg.get("font", "Arial")
    outline = max(1, int(cap_cfg.get("outline", 3) * H / 1080))
    lines = [
        "[Script Info]", "ScriptType: v4.00+", f"PlayResX: {W}", f"PlayResY: {H}", "WrapStyle: 0",
        "ScaledBorderAndShadow: yes", "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, "
        "Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding",
        f"Style: Default,{font},{size},&H00FFFFFF,&H00FFFFFF,&H00000000,&H64000000,-1,0,0,0,100,100,0,0,1,"
        f"{outline},1,2,{margin_h},{margin_h},{margin_v},1",
        "", "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    for c in plan["captions"]:
        text = _ass_text(c["text"])
        if cap_cfg.get("speaker_names", True) and c.get("speaker"):
            text = "{\\c&H4AD8FF&}" + _ass_text(c["speaker"]) + ":{\\c&HFFFFFF&} " + text
        lines.append(f"Dialogue: 0,{_ass_time(c['start'])},{_ass_time(c['end'])},Default,,0,0,0,,{text}")
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out


def final_mux(ffmpeg: str, video: Path, audio: Path, out: Path, plan: dict, quality: dict, work: Path,
              captions: Path | None, has_fonts: bool) -> Path:
    cmd = [ffmpeg, "-y", "-v", "error", "-i", str(video.resolve()), "-i", str(audio.resolve())]
    if captions is not None:
        cmd += ["-vf", f"ass={captions.name}" + (":fontsdir=fonts" if has_fonts else "")]
    preset = quality.get("preset", "slow") if quality.get("name") == "final" else "veryfast"
    cmd += ["-c:v", "libx264", "-preset", preset, "-crf", str(quality.get("crf", 23)), "-pix_fmt", "yuv420p"]
    cmd += ["-map", "0:v:0", "-map", "1:a:0", "-c:a", "aac", "-b:a", "192k", "-t", f"{plan['duration']:.4f}",
            "-movflags", "+faststart", str(out.resolve())]
    run(cmd, cwd=work, what="final encode")
    return out
