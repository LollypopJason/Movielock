import time

import pytest

from movielock.config import load_config
from movielock.web.app import create_app

from .conftest import needs_ffmpeg


@pytest.fixture
def client(demo_root, tmp_path):
    cfg = load_config(demo_root / "movielock.yaml", overrides={"paths": {"renders": str(tmp_path / "renders")}})
    app = create_app(cfg)
    app.testing = True
    return app.test_client()


def wait(client, job_id, timeout=120):
    end = time.time() + timeout
    while time.time() < end:
        job = client.get(f"/api/jobs/{job_id}").get_json()
        if job["status"] in ("done", "error"):
            return job
        time.sleep(0.2)
    raise AssertionError("job did not finish")


def test_index_and_library(client):
    assert b"Write script" in client.get("/").data
    lib = client.get("/api/library").get_json()
    assert {h["id"] for h in lib["heroes"]} == {"haze", "infernus"}
    assert any(v["id"] == "demo_haze_01" for v in lib["voices"])


def test_validate_endpoint(client, demo_root):
    ok = client.post("/api/validate", json={"script": (demo_root / "demo.lock").read_text()}).get_json()
    assert ok["ok"]
    bad = client.post("/api/validate", json={"script": "SHOT wide\n  bob ~ idle\n"}).get_json()
    assert not bad["ok"]
    assert any(i["line"] == 2 and "unknown cast 'bob'" in i["message"] for i in bad["issues"])
    parse_err = client.post("/api/validate", json={"script": "SHOT nope"}).get_json()
    assert not parse_err["ok"] and "unknown camera" in parse_err["issues"][0]["message"]


def test_convert_endpoint(client, demo_root):
    y = client.post("/api/convert", json={"script": (demo_root / "demo.lock").read_text(), "to": "yaml"}).get_json()
    assert "shots:" in y["script"]
    back = client.post("/api/convert", json={"script": y["script"], "kind": "yaml", "to": "lockscript"}).get_json()
    assert back["script"].startswith("MOVIELOCK video")


def test_render_rejects_invalid(client):
    res = client.post("/api/render", json={"script": "SHOT wide\n  bob ~ idle\n"})
    assert res.status_code == 400 and res.get_json()["issues"]


@needs_ffmpeg
def test_render_job(client, demo_root):
    res = client.post("/api/render", json={"script": (demo_root / "demo.lock").read_text(),
                                           "renderer": "storyboard", "quality": "preview"}).get_json()
    job = wait(client, res["job"])
    assert job["status"] == "done", job
    video = client.get(job["result"]["video"])
    assert video.status_code == 200 and len(video.data) > 1000
    dl = client.get(job["result"]["video"] + "?download=1")
    assert "attachment" in dl.headers["Content-Disposition"]
    assert client.get("/videos/../movielock.yaml").status_code == 404


def test_write_without_sdk_or_key_reports_error(client, monkeypatch):
    from movielock import writer
    from movielock.errors import MovielockError

    def boom(cfg):
        raise MovielockError("set ANTHROPIC_API_KEY to your Anthropic API key to use the AI writer")
    monkeypatch.setattr(writer, "_client", boom)
    job = wait(client, client.post("/api/write", json={"idea": "a duel"}).get_json()["job"])
    assert job["status"] == "error" and "ANTHROPIC_API_KEY" in job["error"]
