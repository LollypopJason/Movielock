import yaml

from movielock.audio import write_wav
from movielock.importer import import_sounds, load_closecaption_kv, load_transcripts_csv
from movielock.library import load_voice_index


def _tone(path):
    write_wav(path, [0.0] * 800, 16000)


def test_import_folder(tmp_path, demo_library):
    src = tmp_path / "sounds"
    _tone(src / "haze" / "haze_taunt_01.wav")
    _tone(src / "haze" / "kills" / "Kill Line 3.wav")
    _tone(src / "hero_inferno" / "inferno_laugh.wav")
    _tone(src / "mystery_guy" / "hello.wav")
    (src / "lines.csv").write_text("file,transcript\nhaze_taunt_01.wav,Placeholder taunt.\n", encoding="utf-8")
    cc = src / "closecaption_test.txt"
    cc.write_text('"lang"\n{\n "Language" "English"\n "Tokens"\n {\n'
                  '  "inferno_laugh" "<clr:255,200,0>Ha ha."\n }\n}\n', encoding="utf-8")

    voices = tmp_path / "voices"
    transcripts = {**load_closecaption_kv(cc), **load_transcripts_csv(src / "lines.csv")}
    report = import_sounds(src, demo_library, voices, transcripts=transcripts, copy=True)

    assert report.added == 3
    assert report.skipped_folders == {"mystery_guy": 1}
    index = load_voice_index(voices)
    assert set(index) == {"haze_taunt_01", "haze_kill_line_3", "infernus_inferno_laugh"}
    assert index["haze_taunt_01"].transcript == "Placeholder taunt."
    assert index["infernus_inferno_laugh"].transcript == "Ha ha."
    assert index["infernus_inferno_laugh"].hero == "infernus"
    assert index["haze_kill_line_3"].tags == ["kills"]
    assert (voices / "haze" / "kills" / "Kill Line 3.wav").is_file()
    raw = yaml.safe_load((voices / "index.yaml").read_text())
    files = {v["id"]: v["file"] for v in raw["voices"]}
    assert files["haze_taunt_01"] == "haze/haze_taunt_01.wav"

    # importing again merges without duplicating
    report2 = import_sounds(src, demo_library, voices, transcripts=transcripts, copy=True)
    assert report2.added == 0 and report2.kept == 3


def test_import_in_place_uses_absolute_paths(tmp_path, demo_library):
    src = tmp_path / "elsewhere"
    _tone(src / "Haze" / "a.wav")
    voices = tmp_path / "voices"
    import_sounds(src, demo_library, voices)
    line = load_voice_index(voices)["haze_a"]
    assert line.file.is_file() and line.file.is_absolute()
