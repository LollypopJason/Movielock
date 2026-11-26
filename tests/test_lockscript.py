import pytest

from movielock import lockscript
from movielock.errors import ParseError

EXAMPLE = '''
MOVIELOCK reel 30fps map=rooftop_night captions=on
TITLE Rooftop Standoff   # a comment
MUSIC tense_loop
CAST haze = haze @ -1.5,0 > inf        # name = hero @ x,y[,z] > faces
CAST inf  = infernus @ 1.5,0,0.25 > haze
CAST extra = wraith > 90
SHOT wide on=haze 3s
  haze ~ idle
  haze ! haze_taunt_01
  inf ? "You talk too much. # not a comment"
  * explosion_small @1.2 vol=0.7
SHOT closeup on=inf toward=haze id=reply duration=2.5
  inf ! inf_line_02 @0.5 "He said \\"hi\\""
  inf ~ taunt @ 1 once
  haze ~ walk loop
'''


def test_parse_example():
    doc, smap = lockscript.parse_lockscript(EXAMPLE)
    assert doc["format"] == "reel" and doc["fps"] == 30 and doc["map"] == "rooftop_night"
    assert doc["captions"] is True
    assert doc["title"] == "Rooftop Standoff"
    assert doc["music"] == "tense_loop"
    assert doc["cast"][0] == {"name": "haze", "hero": "haze", "position": [-1.5, 0], "faces": "inf"}
    assert doc["cast"][1]["position"] == [1.5, 0, 0.25]
    assert doc["cast"][2] == {"name": "extra", "hero": "wraith", "faces": 90}
    s1, s2 = doc["shots"]
    assert s1 == {"camera": "wide", "on": "haze", "duration": 3, "events": [
        {"type": "anim", "actor": "haze", "anim": "idle"},
        {"type": "voice", "actor": "haze", "line": "haze_taunt_01"},
        {"type": "tts", "actor": "inf", "text": "You talk too much. # not a comment"},
        {"type": "sfx", "sound": "explosion_small", "at": 1.2, "volume": 0.7},
    ]}
    assert s2["id"] == "reply" and s2["toward"] == "haze" and s2["duration"] == 2.5
    assert s2["events"][0] == {"type": "voice", "actor": "inf", "line": "inf_line_02", "at": 0.5,
                               "caption": 'He said "hi"'}
    assert s2["events"][1] == {"type": "anim", "actor": "inf", "anim": "taunt", "at": 1, "once": True}
    # source map points at lines
    assert smap[("cast", 0)] == 5
    assert smap[("shots", 1, "events", 0)] == 14


@pytest.mark.parametrize("kind", ["lockscript", "yaml", "json"])
def test_round_trip(kind):
    doc, _ = lockscript.parse_lockscript(EXAMPLE)
    text = lockscript.dumps(doc, kind)
    again, _ = lockscript.loads(text, kind)
    assert again == doc
    # and back to Lockscript is stable
    assert lockscript.to_lockscript(again) == lockscript.to_lockscript(doc)


def test_example_files_match():
    a, _ = lockscript.load_script("examples/rooftop.lock")
    b, _ = lockscript.load_script("examples/rooftop.yaml")
    assert a == b


def test_yaml_unquoted_on_key():
    doc, _ = lockscript.loads("shots:\n- camera: wide\n  on: haze\n", "yaml")
    assert doc["shots"][0]["on"] == "haze"


def test_title_quoting_round_trip():
    doc = {"movielock": 1, "title": "Episode #2", "shots": [{"camera": "wide", "events": []}]}
    text = lockscript.to_lockscript(doc)
    assert lockscript.parse_lockscript(text)[0]["title"] == "Episode #2"


@pytest.mark.parametrize("text, message, line", [
    ("SHOT sideways", "unknown camera 'sideways'", 1),
    ("haze ~ idle", "not inside a SHOT", 1),
    ("SHOT wide\n  haze ? unquoted words", "one quoted text", 2),
    ('SHOT wide\n  haze ? "unterminated', "quotes", 2),
    ("SHOT wide\nCAST a = haze", "before the first SHOT", 2),
    ("CAST a = haze @ 1", "bad CAST line", 1),
    ("MOVIELOCK reel wobble", "unknown header option 'wobble'", 1),
    ("SHOT wide\n  haze ! x @soon", "bad time", 2),
    ("SHOT wide\n  * boom loud", "unexpected 'loud'", 2),
    ("SHOT wide 0s", "greater than 0", 1),
    ("FADE out", "unknown keyword", 1),
    ("TITLE nothing else", "no SHOT", None),
])
def test_parse_errors(text, message, line):
    with pytest.raises(ParseError) as exc:
        lockscript.parse_lockscript(text)
    assert message in str(exc.value)
    assert exc.value.line == line


def test_detect_kind():
    assert lockscript.detect_kind("a.lock") == "lockscript"
    assert lockscript.detect_kind("a.yml") == "yaml"
    assert lockscript.detect_kind("x", '{"shots": []}') == "json"
    assert lockscript.detect_kind("x", "MOVIELOCK video") == "lockscript"
