import json

from movielock.cli import main

from .conftest import needs_ffmpeg


def test_validate_ok(demo_root, capsys):
    cfg = str(demo_root / "movielock.yaml")
    assert main(["--config", cfg, "validate", str(demo_root / "demo.lock")]) == 0
    assert "0 error(s)" in capsys.readouterr().out


def test_validate_errors_json(demo_root, tmp_path, capsys):
    bad = tmp_path / "bad.lock"
    bad.write_text("CAST x = nobody\nSHOT wide\n")
    assert main(["--config", str(demo_root / "movielock.yaml"), "validate", str(bad), "--json"]) == 1
    issues = json.loads(capsys.readouterr().out)
    assert issues[0]["line"] == 1 and "unknown hero 'nobody'" in issues[0]["message"]


def test_parse_error_is_reported(demo_root, tmp_path, capsys):
    bad = tmp_path / "bad.lock"
    bad.write_text("SHOT nowhere\n")
    assert main(["--config", str(demo_root / "movielock.yaml"), "validate", str(bad)]) == 1
    assert "line 1: unknown camera" in capsys.readouterr().err


def test_convert_round_trip(tmp_path):
    y = tmp_path / "r.yaml"
    j = tmp_path / "r.json"
    back = tmp_path / "r.lock"
    assert main(["convert", "examples/rooftop.lock", str(y)]) == 0
    assert main(["convert", str(y), str(j)]) == 0
    assert main(["convert", str(j), str(back)]) == 0
    from movielock.lockscript import load_script
    assert load_script(back)[0] == load_script("examples/rooftop.lock")[0]


@needs_ffmpeg
def test_render_storyboard_cli(demo_root, tmp_path):
    out = tmp_path / "x.mp4"
    assert main(["--config", str(demo_root / "movielock.yaml"), "render", str(demo_root / "demo.lock"),
                 "--renderer", "storyboard", "-o", str(out), "--clean"]) == 0
    assert out.stat().st_size > 1000
