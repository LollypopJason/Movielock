import math

import pytest

from movielock import lockscript
from movielock.audio import duration
from movielock.camera import compute_camera, facing_vector, yaw_towards
from movielock.errors import ValidationFailed
from movielock.lipsync import loudness_to_cues, text_cues
from movielock.planner import build_plan, mouth_shape_at

from .conftest import needs_ffmpeg


def plan_for(text, demo_library, demo_config, quality="preview"):
    doc, smap = lockscript.parse_lockscript(text)
    return build_plan(doc, demo_library, demo_config, quality, smap, log=None)


def test_yaw_conventions():
    assert yaw_towards((0, 0), (0, -1)) == 0
    assert yaw_towards((0, 0), (1, 0)) == 90
    assert abs(abs(yaw_towards((0, 0), (0, 1))) - 180) < 1e-6
    fx, fy = facing_vector(90)
    assert math.isclose(fx, 1) and abs(fy) < 1e-9


@needs_ffmpeg
def test_line_timings(demo_root, demo_library, demo_config):
    lead = demo_config["timing"]["lead"]
    gap, tail = demo_config["timing"]["gap"], demo_config["timing"]["tail"]
    d1 = duration(demo_library.voices["demo_haze_01"].file)
    d2 = duration(demo_library.voices["demo_inf_01"].file)
    plan = plan_for(
        "MOVIELOCK video 30fps map=studio\nCAST haze = haze @ -1,0 > inf\nCAST inf = infernus @ 1,0 > haze\n"
        "SHOT two_shot on=haze toward=inf\n  haze ! demo_haze_01\n  inf ! demo_inf_01\n"
        "SHOT closeup on=inf 4s\n  inf ! demo_inf_01 @0.5\n",
        demo_library, demo_config)
    s1, s2 = plan["shots"]
    l1, l2 = s1["lines"]
    assert l1["start"] == pytest.approx(lead)
    assert l1["end"] == pytest.approx(lead + d1, abs=1e-3)
    assert l2["start"] == pytest.approx(lead + d1 + gap, abs=1e-3)
    expected = lead + d1 + gap + d2 + tail
    assert s1["duration"] == pytest.approx(round(expected * 30) / 30, abs=1e-3)
    assert s1["frames"] == round(expected * 30)
    # explicit duration longer than needed is kept; @time honoured
    assert s2["duration"] == pytest.approx(4.0)
    assert s2["lines"][0]["start"] == 0.5
    assert s2["frame_start"] == s1["frames"]
    # absolute audio times
    starts = [a["start"] for a in plan["audio"]]
    assert starts == pytest.approx([l1["start"], l2["start"], s2["start"] + 0.5], abs=1e-3)
    # captions use transcripts and speaker names
    assert plan["captions"][0]["speaker"] == "Haze"
    assert plan["captions"][0]["text"] == demo_library.voices["demo_haze_01"].transcript
    # 1080p preview at half scale
    assert (plan["width"], plan["height"]) == (960, 540)
    assert plan["duration"] == pytest.approx((s1["frames"] + s2["frames"]) / 30, abs=1e-5)


@needs_ffmpeg
def test_short_explicit_duration_is_extended(demo_library, demo_config):
    plan = plan_for("CAST haze = haze\nSHOT medium 0.5s\n  haze ! demo_haze_02\n", demo_library, demo_config)
    assert plan["shots"][0]["duration"] > 1.0
    assert any("extended" in w for w in plan["warnings"])


@needs_ffmpeg
def test_animation_layers_and_mouth(demo_library, demo_config):
    plan = plan_for(
        "CAST haze = haze @ -1,0 > inf\nCAST inf = infernus @ 1,0 > haze\n"
        "SHOT wide 5s\n  haze ! demo_haze_01\n  inf ~ point @1 once\n  inf ~ taunt @2 loop\n",
        demo_library, demo_config)
    shot = plan["shots"][0]
    haze = shot["actors"]["haze"]
    names = [(s["name"], s["layer"]) for s in haze["anims"]]
    assert ("idle", 0) in names and ("talk", 1) in names
    talk = next(s for s in haze["anims"] if s["name"] == "talk")
    assert talk["start"] == shot["lines"][0]["start"]
    assert haze["mouth"] and haze["mouth"][0]["start"] >= shot["lines"][0]["start"]
    assert {c["shape"] for c in haze["mouth"]} - {"X"}  # the mouth actually moves
    inf = shot["actors"]["inf"]
    base = [s for s in inf["anims"] if s["layer"] == 0]
    assert [(s["name"], s["start"], s["end"]) for s in base] == [("idle", 0, 2), ("taunt", 2, 5)]
    point = next(s for s in inf["anims"] if s["name"] == "point")
    assert point["layer"] == 2 and point["loop"] is False and point["start"] == 1
    assert not inf["mouth"]
    assert mouth_shape_at(inf["mouth"], 1.0) == "X"


def test_tts_off_becomes_timed_caption(demo_library, demo_config):
    plan = plan_for('CAST inf = infernus\nSHOT medium\n  inf ? "one two three four five"\n',
                    demo_library, demo_config)
    line = plan["shots"][0]["lines"][0]
    assert line["file"] is None
    words_per_second = demo_config["timing"]["words_per_second"]
    assert line["end"] - line["start"] == pytest.approx(5 / words_per_second, abs=1e-3)
    assert plan["audio"] == [] or all(a["kind"] != "tts" for a in plan["audio"])
    assert plan["captions"][0]["text"] == "one two three four five"
    assert plan["shots"][0]["actors"]["inf"]["mouth"]


def test_auto_placement_and_facing(demo_library, demo_config):
    plan = plan_for("CAST a = haze\nCAST b = infernus\nSHOT wide\n", demo_library, demo_config)
    a, b = plan["cast"]
    assert a["position"][0] < 0 < b["position"][0]
    assert a["yaw"] == pytest.approx(90) and b["yaw"] == pytest.approx(-90)


def test_invalid_script_raises(demo_library, demo_config):
    with pytest.raises(ValidationFailed) as exc:
        plan_for("SHOT wide\n  ghost ~ idle\n", demo_library, demo_config)
    assert "unknown cast 'ghost'" in str(exc.value)


def test_camera_presets_aim_at_subject():
    actors = {"a": {"position": [-1.5, 0, 0], "yaw": 90, "height": 1.8},
              "b": {"position": [1.5, 0, 0], "yaw": -90, "height": 2.0}}
    for kind in ("wide", "medium", "closeup", "over_shoulder", "orbit", "dolly_in", "low_angle", "two_shot"):
        cam = compute_camera(kind, actors, "a", "b", 3.0, 16 / 9)
        assert cam["keys"], kind
        k0 = cam["keys"][0]
        assert k0["pos"] != k0["target"]
        if kind in ("medium", "closeup", "low_angle"):
            # camera is in front of 'a' (who faces +X) and on the audience side (-Y)
            assert k0["pos"][0] > -1.5 and k0["pos"][1] < 0, kind
            assert k0["target"][:2] == [-1.5, 0]
    close = compute_camera("closeup", actors, "a", None, 3, 16 / 9)
    med = compute_camera("medium", actors, "a", None, 3, 16 / 9)
    assert close["dof"]["focus"] == "a" and med["dof"] is None
    assert close["keys"][0]["target"][2] > med["keys"][0]["target"][2]
    ots = compute_camera("over_shoulder", actors, "a", "b", 3, 16 / 9)
    assert ots["keys"][0]["pos"][0] > 1.5  # behind b's shoulder
    orbit = compute_camera("orbit", actors, "a", None, 3, 16 / 9)
    assert orbit["keys"][-1]["t"] == 3 and orbit["keys"][0]["pos"] != orbit["keys"][-1]["pos"]
    wide_reel = compute_camera("wide", actors, "a", None, 3, 9 / 16)
    wide_video = compute_camera("wide", actors, "a", None, 3, 16 / 9)
    assert abs(wide_reel["keys"][0]["pos"][1]) > abs(wide_video["keys"][0]["pos"][1])


def test_loudness_and_text_cues():
    env = [0, 0, 0.2, 0.5, 0.9, 0.9, 0.5, 0.1, 0, 0]
    cues = loudness_to_cues(env, 0.05)
    assert cues[0]["shape"] == "X" and cues[-1]["shape"] == "X"
    assert "D" in {c["shape"] for c in cues}
    assert cues[-1]["end"] == pytest.approx(0.5)
    t = text_cues("Mamma mia", 1.0)
    assert t[0]["shape"] == "A" and t[-1]["shape"] == "X" and t[-1]["end"] == 1.0


def test_cameras_keep_clear_of_other_actors():
    actors = {"haze": {"position": [-1.4, 0, 0], "yaw": 90, "height": 1.72},
              "inf": {"position": [1.4, 0, 0], "yaw": -90, "height": 1.9}}
    for kind in ("medium", "closeup", "orbit", "dolly_in", "low_angle"):
        for on in actors:
            other = [a for n, a in actors.items() if n != on][0]["position"]
            cam = compute_camera(kind, actors, on, None, 3, 16 / 9)
            for k in cam["keys"]:
                assert math.dist(k["pos"][:2], other[:2]) >= 0.85, (kind, on, k)
