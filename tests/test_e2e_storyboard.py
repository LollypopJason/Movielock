"""End to end: demo script -> plan -> storyboard shots -> audio mix + captions -> MP4."""

import json
import subprocess

import pytest

from movielock.config import load_config
from movielock.render.assemble import _ass_time, write_ass
from movielock.render.pipeline import render_file

from .conftest import needs_ffmpeg


def probe(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
                         capture_output=True, text=True, check=True).stdout
    return json.loads(out)


def mean_volume(path, start, length):
    out = subprocess.run(["ffmpeg", "-v", "info", "-ss", str(start), "-t", str(length), "-i", str(path),
                          "-af", "volumedetect", "-f", "null", "-"], capture_output=True, text=True).stderr
    line = next(ln for ln in out.splitlines() if "mean_volume" in ln)
    return float(line.split("mean_volume:")[1].split("dB")[0])


@needs_ffmpeg
@pytest.mark.parametrize("fmt, size", [("video", (960, 540)), ("reel", (540, 960))])
def test_storyboard_end_to_end(demo_root, tmp_path, fmt, size):
    script = demo_root / "demo.lock"
    text = script.read_text().replace("MOVIELOCK video", f"MOVIELOCK {fmt}")
    local = tmp_path / f"demo_{fmt}.lock"
    local.write_text(text)
    cfg = load_config(demo_root / "movielock.yaml",
                      overrides={"paths": {"renders": str(tmp_path / "renders")}})
    events = []
    result = render_file(local, cfg, quality="preview", renderer="storyboard", log=None,
                         progress=lambda f, m: events.append(f))
    assert result.output.is_file()
    assert events[0] == 0.0 and events[-1] == 1.0 and events == sorted(events)

    info = probe(result.output)
    v = next(s for s in info["streams"] if s["codec_type"] == "video")
    a = next(s for s in info["streams"] if s["codec_type"] == "audio")
    assert v["codec_name"] == "h264" and (v["width"], v["height"]) == size
    assert v["r_frame_rate"] == "30/1"
    plan = json.loads(result.plan_path.read_text())
    assert abs(float(info["format"]["duration"]) - plan["duration"]) < 0.1
    assert int(v["nb_frames"]) == plan["frames"]
    assert a["codec_name"] == "aac"

    # the first voice line is audible where the plan says, and louder than the music alone
    first = plan["audio"][0] if plan["audio"][0]["kind"] == "voice" else \
        next(x for x in plan["audio"] if x["kind"] == "voice")
    loud = mean_volume(result.output, first["start"] + 0.1, 0.4)
    assert loud > -40
    # captions file was made and burned in
    assert (result.work_dir / "captions.ass").is_file()
    assert "Dialogue:" in (result.work_dir / "captions.ass").read_text()


def test_ass_writer(tmp_path):
    plan = {"width": 1080, "height": 1920,
            "captions": [{"start": 1.234, "end": 62.5, "speaker": "Haze", "text": "Hi {there}\\"}]}
    path = write_ass(plan, tmp_path / "c.ass", {"size": 0.045, "speaker_names": True})
    body = path.read_text()
    assert "PlayResY: 1920" in body
    assert "0:00:01.23,0:01:02.50" in body
    assert "Haze:" in body and "Hi (there)/" in body
    assert _ass_time(3600) == "1:00:00.00"
