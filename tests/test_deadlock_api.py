"""Deadlock API downloaders against a fake HTTP layer (no network). All data here is invented."""
import json
import struct

import pytest

from movielock.config import load_config
from movielock.deadlock_api import DeadlockAPI, fetch_models, fetch_voices, flatten, glb_animation_names
from movielock.errors import MovielockError
from movielock.importer import import_sounds
from movielock.library import load_library, load_voice_index
from movielock.transcribe import transcribe_missing

CDN = "https://cdn.example/sounds"


def fake_glb(anims=("idle_loop",)):
    gltf = json.dumps({"asset": {"version": "2.0"}, "animations": [{"name": a, "channels": [], "samplers": []}
                                                                   for a in anims]}).encode()
    gltf += b" " * (-len(gltf) % 4)
    return b"glTF" + struct.pack("<II", 2, 20 + len(gltf)) + struct.pack("<I", len(gltf)) + b"JSON" + gltf


API_DATA = {
    "/v1/assets/heroes?only_active=true": [
        {"class_name": "hero_haze", "name": "Haze",
         "models": {"hero_select": "https://cdn.example/models/heroes/haze/haze/hero_select.glb"}},
        {"class_name": "hero_inferno", "name": "Infernus", "models": {}},
        {"class_name": "hero_newcomer", "name": "Newcomer", "models": {}},
    ],
    "/v1/assets/sounds": {"vo": {
        "haze": {"emote": {"haze_effort_01": f"{CDN}/vo/haze/emote/haze_effort_01.mp3"},
                 "haze_greeting_01": f"{CDN}/vo/haze/haze_greeting_01.mp3",
                 "haze_kill_01": f"{CDN}/vo/haze/haze_kill_01.mp3"},
        "inferno": {"inferno_kill_01": f"{CDN}/vo/inferno/inferno_kill_01.mp3"},
    }},
    "/v1/assets/models": {"heroes": {"haze": {"haze": {
        "hero_select.glb": "https://cdn.example/models/heroes/haze/haze/hero_select.glb",
        "idle.glb": "https://cdn.example/models/heroes/haze/haze/idle.glb"}}}},
}


class FakeHTTP:
    def __init__(self):
        self.calls = []

    def __call__(self, url):
        self.calls.append(url)
        for path, data in API_DATA.items():
            if url.endswith(path):
                return json.dumps(data).encode()
        if url.endswith(".glb"):
            return fake_glb(("idle_loop", "other") if "idle" in url else ("select_pose",))
        if url.endswith(".mp3"):
            return b"ID3fake-mp3-" + url.encode()
        raise MovielockError(f"{url}: HTTP 404")


@pytest.fixture
def project(demo_root, tmp_path):
    import shutil
    root = tmp_path / "proj"
    shutil.copytree(demo_root / "assets", root / "assets")
    shutil.rmtree(root / "assets" / "voices")  # start without the demo's voice lines
    cfg = load_config(demo_root / "movielock.yaml", overrides={"paths": {
        "assets": str(root / "assets"), "voices": str(root / "assets" / "voices"),
        "cache": str(root / "cache")}})
    return cfg


def test_flatten():
    assert flatten({"a": {"b": "u1"}, "c": "u2"}) == [("a/b", "u1"), ("c", "u2")]


def test_fetch_voices_and_index(project):
    http = FakeHTTP()
    lib = load_library(project.assets_dir, project.voices_dir)
    api = DeadlockAPI(project, fetch=http)
    report = fetch_voices(project, lib, ["haze", "Infernus"], api=api, fetch=http, log=None)
    assert report.per_hero == {"haze": 2, "infernus": 1}  # effort grunts excluded by default
    assert report.downloaded == 3 and not report.failed
    assert (project.voices_dir / "haze" / "haze_greeting_01.mp3").read_bytes().startswith(b"ID3")
    import_sounds(project.voices_dir, lib, project.voices_dir)
    index = load_voice_index(project.voices_dir)
    assert set(index) == {"haze_greeting_01", "haze_kill_01", "infernus_kill_01"}
    assert index["infernus_kill_01"].file.name == "inferno_kill_01.mp3"
    # second run: nothing new downloaded, the API indexes come from the cache
    n_calls = len(http.calls)
    again = fetch_voices(project, lib, ["haze"], api=api, fetch=http, log=None)
    assert again.downloaded == 0 and again.skipped_existing == 2
    assert len(http.calls) == n_calls
    # include grunts explicitly
    grunts = fetch_voices(project, lib, ["haze"], exclude="", match="effort", api=api, fetch=http, log=None)
    assert grunts.per_hero == {"haze": 1}


def test_fetch_voices_unknown_hero(project):
    lib = load_library(project.assets_dir, project.voices_dir)
    with pytest.raises(MovielockError, match="unknown hero"):
        fetch_voices(project, lib, ["nobody"], api=DeadlockAPI(project, fetch=FakeHTTP()), fetch=FakeHTTP(),
                     log=None)


def test_fetch_models_writes_local_overrides(project):
    http = FakeHTTP()
    lib = load_library(project.assets_dir, project.voices_dir)
    got = fetch_models(project, lib, ["haze"], api=DeadlockAPI(project, fetch=http), fetch=http, log=None)
    assert got["haze"]["model"] == "heroes/haze/idle.glb"
    assert glb_animation_names(project.assets_dir / "heroes/haze/idle.glb") == ["idle_loop", "other"]
    lib2 = load_library(project.assets_dir, project.voices_dir)
    hero = lib2.heroes["haze"]
    assert hero.model.is_file()
    assert hero.animations == {"idle": {"action": "idle_loop", "loop": True, "length": None, "speed": 1.0}}
    assert hero.jaw_bone == "head" and hero.visemes == {}
    assert lib2.heroes["infernus"].animations["talk"]  # other heroes untouched


def test_transcribe_missing(project):
    http = FakeHTTP()
    lib = load_library(project.assets_dir, project.voices_dir)
    fetch_voices(project, lib, ["haze"], api=DeadlockAPI(project, fetch=http), fetch=http, log=None)
    import_sounds(project.voices_dir, lib, project.voices_dir)
    lib = load_library(project.assets_dir, project.voices_dir)
    n = transcribe_missing(lib, project.voices_dir, lambda p: f"text for {p.stem}", heroes=["haze"], log=None)
    assert n == 2
    index = load_voice_index(project.voices_dir)
    assert index["haze_kill_01"].transcript == "text for haze_kill_01"
    assert index["haze_kill_01"].file.is_file()
