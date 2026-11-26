from pathlib import Path

import pytest

from movielock import lockscript
from movielock.library import load_library
from movielock.validate import errors_only, validate_doc

REPO = Path(__file__).resolve().parent.parent

HEADER = "MOVIELOCK video 30fps map=studio\nCAST haze = haze @ -1,0 > inf\nCAST inf = infernus @ 1,0 > haze\n"


def check(text, library, **kw):
    doc, smap = lockscript.parse_lockscript(text)
    return validate_doc(doc, library, smap, **kw)


def test_demo_script_is_valid(demo_root, demo_library):
    doc, smap = lockscript.load_script(demo_root / "demo.lock")
    issues = validate_doc(doc, demo_library, smap)
    assert errors_only(issues) == []
    # the TTS line produces a warning because TTS is off by default
    assert any("text-to-speech is off" in i.message for i in issues)


def test_repo_library_loads():
    lib = load_library(REPO / "assets")
    assert "haze" in lib.heroes and "infernus" in lib.heroes
    assert lib.heroes["haze"].animations["idle"]["loop"] is True
    assert lib.heroes["haze"].visemes["A"] == "mouth_closed"
    assert lib.resolve_hero("hero_inferno") == "infernus"
    assert lib.resolve_hero("Lady Geist") == "lady_geist"
    assert lib.resolve_hero("hero_haze") == "haze"
    assert lib.resolve_hero("nobody") is None


def test_example_validates_structurally():
    lib = load_library(REPO / "assets")
    doc, smap = lockscript.load_script(REPO / "examples" / "rooftop.lock")
    issues = validate_doc(doc, lib, smap, check_files=False)
    # only unknown voice ids (no voices imported in the repo) are errors
    errs = errors_only(issues)
    assert errs and all("unknown voice line" in e.message for e in errs)


@pytest.mark.parametrize("body, expected, line", [
    ("CAST x = hazee\nSHOT wide\n", "unknown hero 'hazee' for cast 'x' (did you mean 'haze'?)", 4),
    ("SHOT wide on=bob\n", "on=bob: no cast member", 4),
    ("SHOT wide\n  bob ~ idle\n", "unknown cast 'bob'", 5),
    ("SHOT wide\n  haze ~ moonwalk\n", "hero 'haze' has no animation 'moonwalk'", 5),
    ("SHOT wide\n  haze ! demo_haze_99\n", "unknown voice line 'demo_haze_99' (did you mean", 5),
    ("SHOT wide\n  * kaboom\n", "unknown sound effect 'kaboom'", 5),
    ("SHOT wide id=a\nSHOT wide id=a\n", "shot id 'a' is used twice", 5),
    ("SHOT two_shot on=haze toward=haze\n", "must be different", 4),
])
def test_semantic_errors(demo_library, body, expected, line):
    errs = errors_only(check(HEADER + body, demo_library))
    assert any(expected in e.message for e in errs), [str(e) for e in errs]
    assert any(e.line == line for e in errs)


def test_unknown_map_and_music(demo_library):
    errs = errors_only(check("MOVIELOCK video map=moon\nMUSIC jazz\nSHOT wide\n", demo_library))
    msgs = " | ".join(e.message for e in errs)
    assert "unknown map 'moon'" in msgs and "unknown music 'jazz'" in msgs


def test_faces_unknown(demo_library):
    errs = errors_only(check("CAST a = haze > zed\nSHOT wide\n", demo_library))
    assert "faces unknown cast 'zed'" in errs[0].message


def test_schema_errors_from_yaml(demo_library):
    doc = {"format": "cinema", "shots": [{"camera": "wide", "events": [{"type": "voice", "actor": "a"}]}],
           "bogus": 1}
    errs = errors_only(validate_doc(doc, demo_library))
    msgs = [str(e) for e in errs]
    assert any("format" in m and "'cinema'" in m for m in msgs)
    assert any("'line' is a required property" in m for m in msgs)
    assert any("bogus" in m for m in msgs)


def test_voice_belongs_to_other_hero_warns(demo_library):
    issues = check(HEADER + "SHOT wide\n  haze ! demo_inf_01\n", demo_library)
    assert not errors_only(issues)
    assert any("belongs to infernus" in i.message for i in issues)


def test_missing_voice_file(demo_library, tmp_path):
    lib = demo_library
    vl = lib.voices["demo_haze_01"]
    original = vl.file
    vl.file = tmp_path / "gone.wav"
    try:
        errs = errors_only(check(HEADER + "SHOT wide\n  haze ! demo_haze_01\n", lib))
        assert "voice file missing" in errs[0].message
    finally:
        vl.file = original
