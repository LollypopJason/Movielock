"""Lockscript: the plain-text script language, and conversion to/from the YAML/JSON form.

Example::

    MOVIELOCK reel 30fps map=rooftop_night captions=on
    TITLE Rooftop Standoff
    MUSIC tense_loop vol=0.4
    CAST haze = haze @ -1.5,0 > inf
    CAST inf  = infernus @ 1.5,0 > haze
    SHOT wide on=haze 3s
      haze ~ idle
      haze ! haze_taunt_01
      inf ? "You talk too much."
      * explosion_small @1.2 vol=0.7

The structured form (a dict) is described by ``movielock/data/lockscript.schema.json``.
``parse_lockscript`` returns that dict plus a *source map* (json path -> line number)
so validation messages can point at the right line.
"""

from __future__ import annotations

import json
import re
import shlex
from pathlib import Path
from typing import Any

import yaml

from .errors import ParseError

CAMERAS = ("wide", "medium", "closeup", "over_shoulder", "orbit", "dolly_in", "low_angle", "two_shot")
FORMATS = {"video": "video", "16:9": "video", "landscape": "video",
           "reel": "reel", "9:16": "reel", "short": "reel", "vertical": "reel",
           "square": "square", "1:1": "square"}

_NAME = r"[A-Za-z_][A-Za-z0-9_\-]*"
_ID = r"[A-Za-z0-9_][A-Za-z0-9_.\-]*"
_NUM = r"-?(?:\d+(?:\.\d*)?|\.\d+)"

_CAST_RE = re.compile(
    rf"^CAST\s+(?P<name>{_NAME})"
    rf"(?:\s*=\s*(?P<hero>{_ID}))?"
    rf"(?:\s*@\s*(?P<pos>{_NUM}\s*,\s*{_NUM}(?:\s*,\s*{_NUM})?))?"
    rf"(?:\s*>\s*(?P<faces>{_NUM}|{_NAME}))?\s*$"
)
_EVENT_RE = re.compile(rf"^(?P<actor>{_NAME})\s*(?P<op>[~!?])\s*(?P<rest>.*)$")
_TIME_RE = re.compile(rf"^@\s*(?P<t>{_NUM})s?$")
_DUR_RE = re.compile(rf"^(?P<d>{_NUM})s$")

SourceMap = dict[tuple, int]


# --------------------------------------------------------------------------- helpers

def _num(text: str) -> int | float:
    value = float(text)
    return int(value) if re.fullmatch(r"-?\d+", text.strip()) else value


def _fmt_num(value: float | int) -> str:
    if isinstance(value, bool):
        raise TypeError("bool is not a number")
    if float(value).is_integer():
        return str(int(value))
    return repr(float(value))


def _quote(text: str) -> str:
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _tokens(text: str, lineno: int) -> list[str]:
    try:
        lex = shlex.shlex(text, posix=True)
        lex.whitespace_split = True
        lex.commenters = "#"
        return list(lex)
    except ValueError as exc:  # unbalanced quotes
        raise ParseError(f"{exc} (check your double quotes)", lineno) from None


def _strip_comment(text: str) -> str:
    """Remove a trailing ``# comment`` that is not inside double quotes."""
    in_q = False
    esc = False
    for i, ch in enumerate(text):
        if esc:
            esc = False
            continue
        if ch == "\\" and in_q:
            esc = True
        elif ch == '"':
            in_q = not in_q
        elif ch == "#" and not in_q and (i == 0 or text[i - 1].isspace()):
            return text[:i].rstrip()
    return text.rstrip()


def _split_time(tokens: list[str], lineno: int) -> tuple[list[str], float | None]:
    """Pull an ``@t`` / ``@ t`` token out of a token list."""
    out: list[str] = []
    at = None
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok == "@" and i + 1 < len(tokens):
            tok = "@" + tokens[i + 1]
            i += 1
        if tok.startswith("@"):
            m = _TIME_RE.match(tok)
            if not m:
                raise ParseError(f"bad time '{tok}' (use @1.5 for 1.5 seconds into the shot)", lineno)
            if at is not None:
                raise ParseError("only one @time allowed per line", lineno)
            at = _num(m.group("t"))
            if at < 0:
                raise ParseError("time must not be negative", lineno)
        else:
            out.append(tok)
        i += 1
    return out, at


# --------------------------------------------------------------------------- parser

def parse_lockscript(text: str) -> tuple[dict, SourceMap]:
    """Parse Lockscript text into the structured form and a source map."""
    doc: dict[str, Any] = {"movielock": 1}
    smap: SourceMap = {}
    shots: list[dict] = []
    cast: list[dict] = []
    seen_header = False

    for lineno, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        keyword = line.split(None, 1)[0]
        upper = keyword.upper()

        if upper == "MOVIELOCK":
            if seen_header:
                raise ParseError("MOVIELOCK header appears twice", lineno)
            if shots or cast:
                raise ParseError("MOVIELOCK header must come before CAST and SHOT lines", lineno)
            seen_header = True
            smap[()] = lineno
            for tok in _tokens(line, lineno)[1:]:
                low = tok.lower()
                if low in FORMATS:
                    doc["format"] = FORMATS[low]
                    smap[("format",)] = lineno
                elif m := re.fullmatch(r"(\d+)fps", low):
                    doc["fps"] = int(m.group(1))
                    smap[("fps",)] = lineno
                elif low.startswith("fps="):
                    doc["fps"] = _int_opt(tok, lineno)
                    smap[("fps",)] = lineno
                elif low.startswith("map="):
                    doc["map"] = tok.split("=", 1)[1]
                    smap[("map",)] = lineno
                elif low.startswith("captions="):
                    val = low.split("=", 1)[1]
                    if val not in ("on", "off", "true", "false", "yes", "no"):
                        raise ParseError("captions must be on or off", lineno)
                    doc["captions"] = val in ("on", "true", "yes")
                    smap[("captions",)] = lineno
                elif low.startswith("format="):
                    fmt = low.split("=", 1)[1]
                    if fmt not in FORMATS:
                        raise ParseError(f"unknown format '{fmt}' (use video, reel or square)", lineno)
                    doc["format"] = FORMATS[fmt]
                    smap[("format",)] = lineno
                else:
                    raise ParseError(
                        f"unknown header option '{tok}' "
                        "(expected video|reel|square, <n>fps, map=<id>, captions=on|off)", lineno)
            continue

        if upper == "TITLE":
            rest = line[len(keyword):].strip()
            if rest.startswith('"'):
                toks = _tokens(rest, lineno)
                if len(toks) != 1:
                    raise ParseError("TITLE takes one quoted string", lineno)
                rest = toks[0]
            else:
                rest = _strip_comment(rest)
            if not rest:
                raise ParseError("TITLE needs some text", lineno)
            doc["title"] = rest
            smap[("title",)] = lineno
            continue

        if upper == "MUSIC":
            toks = _tokens(line, lineno)[1:]
            if not toks:
                raise ParseError("MUSIC needs a music id, e.g. MUSIC tense_loop", lineno)
            music_id, volume = toks[0], None
            for tok in toks[1:]:
                if tok.lower().startswith(("vol=", "volume=")):
                    volume = _float_opt(tok, lineno)
                else:
                    raise ParseError(f"unexpected '{tok}' on MUSIC line (only vol=<n> allowed)", lineno)
            _check_id(music_id, "music id", lineno)
            doc["music"] = {"id": music_id, "volume": volume} if volume is not None else music_id
            smap[("music",)] = lineno
            continue

        if upper == "CAST":
            if shots:
                raise ParseError("CAST lines must come before the first SHOT", lineno)
            body = _strip_comment(line)
            body = "CAST" + body[4:]  # normalise keyword case for the regex
            m = _CAST_RE.match(body)
            if not m:
                raise ParseError(
                    "bad CAST line. Use: CAST name = hero @ x,y[,z] > target_or_yaw "
                    "(the @ and > parts are optional)", lineno)
            member: dict[str, Any] = {"name": m.group("name"), "hero": m.group("hero") or m.group("name")}
            if m.group("pos"):
                member["position"] = [_num(p) for p in m.group("pos").split(",")]
            if m.group("faces") is not None:
                f = m.group("faces")
                member["faces"] = _num(f) if re.fullmatch(_NUM, f) else f
            smap[("cast", len(cast))] = lineno
            cast.append(member)
            continue

        if upper == "SHOT":
            toks = _tokens(line, lineno)[1:]
            if not toks:
                raise ParseError(f"SHOT needs a camera: {', '.join(CAMERAS)}", lineno)
            camera = toks[0].lower()
            if camera not in CAMERAS:
                raise ParseError(f"unknown camera '{toks[0]}' (use one of: {', '.join(CAMERAS)})", lineno)
            shot: dict[str, Any] = {"camera": camera}
            for tok in toks[1:]:
                low = tok.lower()
                if m := _DUR_RE.match(low):
                    shot["duration"] = _num(m.group("d"))
                elif "=" in tok:
                    key, val = tok.split("=", 1)
                    key = key.lower()
                    if key in ("on", "toward"):
                        _check_name(val, key, lineno)
                        shot[key] = val
                    elif key == "id":
                        _check_id(val, "shot id", lineno)
                        shot["id"] = val
                    elif key in ("duration", "dur"):
                        shot["duration"] = _float_opt(tok if not val.endswith("s") else f"{key}={val[:-1]}", lineno)
                    else:
                        raise ParseError(f"unknown SHOT option '{key}' (use on=, toward=, id=, duration=)", lineno)
                else:
                    raise ParseError(f"unexpected '{tok}' on SHOT line (durations look like 3s)", lineno)
            if "duration" in shot and shot["duration"] <= 0:
                raise ParseError("shot duration must be greater than 0", lineno)
            shot["events"] = []
            smap[("shots", len(shots))] = lineno
            shots.append(shot)
            continue

        # ---- events
        if (keyword.isupper() and keyword.isalpha() and len(keyword) > 1
                and not _EVENT_RE.match(line) and not line.startswith("*")):
            raise ParseError(f"unknown keyword '{keyword}' (MOVIELOCK, TITLE, MUSIC, CAST, SHOT)", lineno)
        if not shots:
            raise ParseError(f"'{line}' is not inside a SHOT (put a SHOT line above it)", lineno)
        shot = shots[-1]
        body = _strip_comment(line)
        path = ("shots", len(shots) - 1, "events", len(shot["events"]))
        if body.startswith("*"):
            toks, at = _split_time(_tokens(body[1:], lineno), lineno)
            if not toks:
                raise ParseError("sound effect line needs a sound id: * explosion_small", lineno)
            event: dict[str, Any] = {"type": "sfx", "sound": toks[0]}
            _check_id(toks[0], "sound id", lineno)
            if at is not None:
                event["at"] = at
            for tok in toks[1:]:
                if tok.lower().startswith(("vol=", "volume=")):
                    event["volume"] = _float_opt(tok, lineno)
                else:
                    raise ParseError(f"unexpected '{tok}' on sound line (only @time and vol=<n>)", lineno)
        else:
            m = _EVENT_RE.match(body)
            if not m:
                if keyword.isupper() and keyword.isalpha():
                    raise ParseError(f"unknown keyword '{keyword}' "
                                     "(MOVIELOCK, TITLE, MUSIC, CAST, SHOT)", lineno)
                raise ParseError(
                    "can't read this line. Events look like: name ~ anim | name ! voice_id | "
                    'name ? "text" | * sound', lineno)
            actor, op, rest = m.group("actor"), m.group("op"), m.group("rest")
            toks, at = _split_time(_tokens(rest, lineno), lineno)
            if op == "~":
                if not toks:
                    raise ParseError(f"'{actor} ~' needs an animation name", lineno)
                event = {"type": "anim", "actor": actor, "anim": toks[0]}
                _check_id(toks[0], "animation name", lineno)
                if at is not None:
                    event["at"] = at
                for tok in toks[1:]:
                    if tok.lower() == "once":
                        event["once"] = True
                    elif tok.lower() == "loop":
                        event["once"] = False
                    else:
                        raise ParseError(f"unexpected '{tok}' on animation line (only @time and once)", lineno)
            elif op == "!":
                if not toks:
                    raise ParseError(f"'{actor} !' needs a voice line id", lineno)
                event = {"type": "voice", "actor": actor, "line": toks[0]}
                _check_id(toks[0], "voice line id", lineno)
                if at is not None:
                    event["at"] = at
                if len(toks) > 2:
                    raise ParseError('too many words on voice line; quote the caption: "like this"', lineno)
                if len(toks) == 2:
                    event["caption"] = toks[1]
            else:  # "?"
                if len(toks) != 1:
                    raise ParseError(f'text-to-speech needs one quoted text: {actor} ? "Hello there."', lineno)
                if not toks[0].strip():
                    raise ParseError("text-to-speech text is empty", lineno)
                event = {"type": "tts", "actor": actor, "text": toks[0]}
                if at is not None:
                    event["at"] = at
        smap[path] = lineno
        shot["events"].append(event)

    if not shots:
        raise ParseError("script has no SHOT lines")
    if cast:
        doc["cast"] = cast
    doc["shots"] = shots
    return doc, smap


def _check_id(value: str, what: str, lineno: int) -> None:
    if not re.fullmatch(_ID, value):
        raise ParseError(f"invalid {what} '{value}' (letters, digits, _ . - only)", lineno)


def _check_name(value: str, what: str, lineno: int) -> None:
    if not re.fullmatch(_NAME, value):
        raise ParseError(f"invalid {what} name '{value}'", lineno)


def _float_opt(tok: str, lineno: int) -> float | int:
    key, _, val = tok.partition("=")
    try:
        return _num(val)
    except ValueError:
        raise ParseError(f"{key} must be a number, got '{val}'", lineno) from None


def _int_opt(tok: str, lineno: int) -> int:
    key, _, val = tok.partition("=")
    if not val.isdigit():
        raise ParseError(f"{key} must be a whole number, got '{val}'", lineno)
    return int(val)


# --------------------------------------------------------------------------- writer

def to_lockscript(doc: dict) -> str:
    """Serialise the structured form back to Lockscript text."""
    header = ["MOVIELOCK", doc.get("format", "video"), f"{doc.get('fps', 30)}fps"]
    if doc.get("map"):
        header.append(f"map={doc['map']}")
    if "captions" in doc:
        header.append(f"captions={'on' if doc['captions'] else 'off'}")
    out = [" ".join(header)]
    if doc.get("title"):
        title = doc["title"]
        needs_quote = title.startswith('"') or re.search(r"\s#", title) or title.startswith("#")
        out.append(f"TITLE {_quote(title) if needs_quote else title}")
    music = doc.get("music")
    if music:
        if isinstance(music, dict):
            line = f"MUSIC {music['id']}"
            if music.get("volume") is not None:
                line += f" vol={_fmt_num(music['volume'])}"
        else:
            line = f"MUSIC {music}"
        out.append(line)

    cast = doc.get("cast") or []
    width = max((len(c["name"]) for c in cast), default=0)
    for c in cast:
        line = f"CAST {c['name'].ljust(width)}"
        line += f" = {c['hero']}"
        if c.get("position") is not None:
            line += " @ " + ",".join(_fmt_num(v) for v in c["position"])
        if c.get("faces") is not None:
            f = c["faces"]
            line += f" > {f if isinstance(f, str) else _fmt_num(f)}"
        out.append(line)

    for shot in doc.get("shots", []):
        line = f"SHOT {shot['camera']}"
        for key in ("on", "toward", "id"):
            if shot.get(key):
                line += f" {key}={shot[key]}"
        if shot.get("duration") is not None:
            line += f" {_fmt_num(shot['duration'])}s"
        out.append(line)
        for ev in shot.get("events", []):
            out.append("  " + _event_line(ev))
    return "\n".join(out) + "\n"


def _event_line(ev: dict) -> str:
    at = f" @{_fmt_num(ev['at'])}" if ev.get("at") is not None else ""
    kind = ev["type"]
    if kind == "anim":
        return f"{ev['actor']} ~ {ev['anim']}{at}{' once' if ev.get('once') else ''}"
    if kind == "voice":
        cap = f" {_quote(ev['caption'])}" if ev.get("caption") is not None else ""
        return f"{ev['actor']} ! {ev['line']}{at}{cap}"
    if kind == "tts":
        return f"{ev['actor']} ? {_quote(ev['text'])}{at}"
    if kind == "sfx":
        vol = f" vol={_fmt_num(ev['volume'])}" if ev.get("volume") is not None else ""
        return f"* {ev['sound']}{at}{vol}"
    raise ValueError(f"unknown event type {kind!r}")


# --------------------------------------------------------------------------- files

def detect_kind(path: str | Path, text: str | None = None) -> str:
    """Return 'lockscript', 'yaml' or 'json' for a path (falls back to sniffing the text)."""
    suffix = Path(path).suffix.lower()
    if suffix in (".lock", ".lockscript", ".ml", ".txt"):
        return "lockscript"
    if suffix in (".yaml", ".yml"):
        return "yaml"
    if suffix == ".json":
        return "json"
    if text is not None:
        stripped = text.lstrip()
        if stripped.upper().startswith(("MOVIELOCK", "CAST", "SHOT", "TITLE")):
            return "lockscript"
        if stripped.startswith("{"):
            return "json"
        return "yaml"
    return "lockscript"


def loads(text: str, kind: str = "lockscript") -> tuple[dict, SourceMap]:
    if kind == "lockscript":
        return parse_lockscript(text)
    try:
        doc = json.loads(text) if kind == "json" else yaml.safe_load(text)
    except (json.JSONDecodeError, yaml.YAMLError) as exc:
        raise ParseError(f"invalid {kind.upper()}: {exc}") from None
    if not isinstance(doc, dict):
        raise ParseError(f"{kind.upper()} script must be a mapping with a 'shots' list")
    # YAML 1.1 reads an unquoted `on:` key as the boolean True; undo that for shots.
    for shot in doc.get("shots") or []:
        if isinstance(shot, dict) and True in shot:
            shot["on"] = shot.pop(True)
    return doc, {}


def dumps(doc: dict, kind: str = "lockscript") -> str:
    if kind == "lockscript":
        return to_lockscript(doc)
    if kind == "json":
        return json.dumps(doc, indent=2, ensure_ascii=False) + "\n"
    return yaml.safe_dump(doc, sort_keys=False, allow_unicode=True, width=100)


def load_script(path: str | Path) -> tuple[dict, SourceMap]:
    path = Path(path)
    text = path.read_text(encoding="utf-8")
    return loads(text, detect_kind(path, text))


def save_script(doc: dict, path: str | Path) -> None:
    path = Path(path)
    path.write_text(dumps(doc, detect_kind(path)), encoding="utf-8")


def normalize(doc: dict) -> dict:
    """Return a copy with defaults filled in (used by the planner)."""
    doc = json.loads(json.dumps(doc))
    doc.setdefault("movielock", 1)
    doc.setdefault("format", "video")
    doc.setdefault("fps", 30)
    doc.setdefault("captions", True)
    doc.setdefault("cast", [])
    for i, shot in enumerate(doc.get("shots", [])):
        shot.setdefault("id", f"s{i + 1:02d}")
        shot.setdefault("events", [])
    if isinstance(doc.get("music"), str):
        doc["music"] = {"id": doc["music"]}
    return doc
