"""The README's Lockscript and YAML examples describe the same script, and both validate structurally."""
from pathlib import Path

from movielock import lockscript
from movielock.validate import errors_only, validate_doc

README = (Path(__file__).resolve().parent.parent / "README.md").read_text(encoding="utf-8")


def blocks(lang):
    """Fenced code blocks with the given info string (fences are matched in pairs)."""
    out, current, info = [], None, None
    for line in README.splitlines(keepends=True):
        if line.startswith("```"):
            if current is None:
                current, info = [], line[3:].strip()
            else:
                if info == lang:
                    out.append("".join(current))
                current = None
        elif current is not None:
            current.append(line)
    return out


def test_readme_examples_match():
    lock = next(b for b in blocks("") if b.startswith("MOVIELOCK video 30fps map=studio"))
    yml = next(b for b in blocks("yaml") if b.startswith("movielock: 1"))
    a, _ = lockscript.loads(lock, "lockscript")
    b, _ = lockscript.loads(yml, "yaml")
    assert a == b
    assert not errors_only(validate_doc(a))


def test_readme_language_example_parses():
    lock = next(b for b in blocks("") if b.startswith("MOVIELOCK reel 30fps map=rooftop_night"))
    doc, _ = lockscript.parse_lockscript(lock)
    assert not errors_only(validate_doc(doc))
