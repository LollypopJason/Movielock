"""AI script writer: plain-English idea -> Lockscript, using the Claude API.

The system prompt teaches Lockscript and lists the asset library (heroes, animations,
maps, sounds, music and every voice line id with its transcript), so the model only
uses things that exist. The result is validated; errors are sent back for a repair attempt.

Needs the official SDK (``pip install anthropic``) and an API key in the environment
variable named by writer.api_key_env (default ANTHROPIC_API_KEY).
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

from .config import Config
from .errors import MovielockError, ParseError
from .library import Library
from .lockscript import parse_lockscript
from .validate import errors_only, validate_doc

LANGUAGE_GUIDE = """\
# Lockscript reference

A Lockscript file describes a short 3D video. One statement per line; `#` starts a comment.

MOVIELOCK <format> <fps>fps map=<map_id> captions=on|off
    format: video (16:9), reel (9:16 vertical short), square (1:1)
TITLE <text>
MUSIC <music_id> [vol=<0..1>]                  # optional background music, ducked under dialogue
CAST <name> = <hero_id> @ <x>,<y> > <name|yaw>  # actor name used in the script, hero, position (metres), who they face
SHOT <camera> [on=<name>] [toward=<name>] [id=<id>] [<seconds>s]
    cameras: wide, medium, closeup, over_shoulder (on = face we see, toward = whose shoulder),
             orbit, dolly_in, low_angle, two_shot (on + toward framed together)
    the duration is a minimum: shots grow to fit their dialogue (0.25 s between lines, 0.6 s after the last)
  <name> ~ <animation> [@<t>] [once|loop]        # play an animation (t = seconds into the shot)
  <name> ! <voice_line_id> [@<t>] ["caption"]    # a real recorded voice line; caption defaults to its transcript
  <name> ? "<text>" [@<t>]                       # text-to-speech line
  * <sound_id> [@<t>] [vol=<0..1>]               # sound effect

Rules for good scripts:
- Lines without @t play one after another. Use @t only for overlaps or precise timing.
- Characters stand on the ground plane; typical spacing is 2.5-3.5 m, facing each other for dialogue.
- Idle and talking animations are automatic; only add `~` lines for deliberate actions.
- Vary the cameras: open wide, cut closer for punchlines, use over_shoulder / two_shot for exchanges.
- Keep it short: a reel is usually 15-45 seconds, 4-10 shots.
- The dialogue must make sense with the *transcripts* of the voice lines you pick: build the story
  around what the lines actually say. Put the transcript in a caption only if you want different text shown.
"""

EXAMPLE = """\
MOVIELOCK reel 30fps map=studio captions=on
TITLE Example
CAST a = hero_one @ -1.5,0 > b
CAST b = hero_two @ 1.5,0 > a
SHOT wide on=a 2s
  * whoosh @0.2 vol=0.6
  a ! hero_one_line_03
SHOT over_shoulder on=b toward=a
  b ! hero_two_line_11
  b ~ point @0.4 once
SHOT closeup on=a
  a ! hero_one_line_07
"""


def library_listing(library: Library, focus_heroes: set[str] | None = None, max_lines: int = 2500) -> str:
    out = ["# Asset library (use ONLY these ids)", "", "## Heroes (hero_id: name; animations)"]
    for h in library.heroes.values():
        out.append(f"- {h.id}: {h.name}; animations: {', '.join(h.animations) or 'none'}")
    out += ["", "## Maps"] + [f"- {m.id}: {m.name}" for m in library.maps.values()]
    out += ["", "## Sound effects"] + [f"- {s.id}" + (f": {s.description}" if s.description else "")
                                       for s in library.sounds.values()]
    out += ["", "## Music"] + [f"- {s.id}" + (f": {s.description}" if s.description else "")
                               for s in library.music.values()]
    out += ["", "## Voice lines (id | hero | transcript)"]
    voices = [v for v in library.voices.values() if v.file.is_file()] or list(library.voices.values())
    if focus_heroes:
        voices.sort(key=lambda v: (v.hero not in focus_heroes, v.hero, v.id))
    else:
        voices.sort(key=lambda v: (v.hero, v.id))
    if not voices:
        out.append("(none imported yet - you may only use text-to-speech lines)")
    for v in voices[:max_lines]:
        out.append(f"{v.id} | {v.hero} | {v.transcript or '(no transcript)'}")
    if len(voices) > max_lines:
        out.append(f"... {len(voices) - max_lines} more lines omitted")
    return "\n".join(out)


def system_prompt(library: Library, tts_enabled: bool, focus_heroes: set[str] | None = None) -> str:
    tts_rule = ("Text-to-speech (`?`) is available, but prefer real voice lines (`!`)."
                if tts_enabled else
                "Text-to-speech is OFF: `?` lines become silent captions. Use real voice lines (`!`) for "
                "dialogue; use `?` only if no recorded line fits, and keep those rare.")
    return "\n\n".join([
        "You write short scripts for Movielock, a non-commercial fan tool that renders 3D videos of "
        "Deadlock heroes talking, using their real in-game voice lines. Write a fun, coherent scene from "
        "the user's idea.",
        LANGUAGE_GUIDE,
        f"Example of the format (ids here are placeholders):\n```lockscript\n{EXAMPLE}```",
        tts_rule,
        "Reply with exactly one ```lockscript code block containing the whole script, then at most two "
        "sentences about your choices. Use only hero ids, animation names, map ids, sound ids, music ids "
        "and voice line ids from the library below.",
        library_listing(library, focus_heroes),
    ])


def extract_script(text: str) -> str:
    m = re.search(r"```(?:lockscript|lock|text)?\s*\n(.*?)```", text, re.S)
    if m:
        return m.group(1).strip() + "\n"
    if "SHOT" in text:
        return text.strip() + "\n"
    raise MovielockError("the writer's reply did not contain a script")


@dataclass
class WriteResult:
    script: str
    notes: str
    valid: bool
    issues: list = field(default_factory=list)
    attempts: int = 1


def _mentioned_heroes(idea: str, library: Library) -> set[str]:
    low = idea.lower()
    found = set()
    for h in library.heroes.values():
        names = {h.id.replace("_", " "), h.name.lower(), *(a.lower() for a in h.aliases)}
        if any(re.search(rf"\b{re.escape(n)}\b", low) for n in names if n):
            found.add(h.id)
    return found


def _client(cfg: Config):
    try:
        import anthropic
    except ImportError:
        raise MovielockError("the AI writer needs the Anthropic SDK: pip install anthropic") from None
    env = cfg["writer"].get("api_key_env", "ANTHROPIC_API_KEY")
    key = os.environ.get(env)
    if key:
        return anthropic, anthropic.Anthropic(api_key=key)
    try:  # the SDK can also use ANTHROPIC_AUTH_TOKEN or an `ant auth login` profile
        return anthropic, anthropic.Anthropic()
    except Exception:  # noqa: BLE001
        raise MovielockError(f"set {env} to your Anthropic API key to use the AI writer") from None


def _call(anthropic, client, cfg: Config, system: str, messages: list) -> tuple[str, list]:
    w = cfg["writer"]
    kwargs = dict(
        model=w.get("model", "claude-opus-5-5"),
        max_tokens=int(w.get("max_tokens", 16000)),
        system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
        messages=messages,
        output_config={"effort": w.get("effort", "medium")},
    )
    fallbacks = w.get("fallbacks", "default")
    try:
        if fallbacks:
            response = client.beta.messages.create(betas=["server-side-fallback-2026-07-01"],
                                                   fallbacks=fallbacks, **kwargs)
        else:
            response = client.messages.create(**kwargs)
    except anthropic.AuthenticationError:
        raise MovielockError("the Anthropic API rejected the API key") from None
    except anthropic.NotFoundError:
        raise MovielockError(f"unknown model '{kwargs['model']}' (writer.model in movielock.yaml)") from None
    except anthropic.RateLimitError:
        raise MovielockError("rate limited by the Anthropic API; try again in a minute") from None
    except anthropic.APIStatusError as exc:
        raise MovielockError(f"Anthropic API error {exc.status_code}: {exc.message}") from None
    except anthropic.APIConnectionError:
        raise MovielockError("could not reach the Anthropic API (network)") from None
    if response.stop_reason == "refusal":
        raise MovielockError("the model declined to write this script; try rephrasing the idea")
    text = "".join(b.text for b in response.content if b.type == "text")
    if response.stop_reason == "max_tokens":
        raise MovielockError("the reply was cut off (raise writer.max_tokens in movielock.yaml)")
    return text, response.content


def write_script(idea: str, library: Library, cfg: Config, *, fmt: str | None = None,
                 log=print) -> WriteResult:
    idea = idea.strip()
    if not idea:
        raise MovielockError("describe your idea first")
    anthropic, client = _client(cfg)
    tts = bool(cfg["tts"].get("enabled"))
    system = system_prompt(library, tts, _mentioned_heroes(idea, library))
    request = f"Idea: {idea}"
    if fmt:
        request += f"\nFormat: {fmt}"
    messages = [{"role": "user", "content": request}]
    repairs = int(cfg["writer"].get("repair_attempts", 1))
    attempt = 0
    while True:
        attempt += 1
        if log:
            log("asking the writer..." if attempt == 1 else "asking the writer to fix the errors...")
        text, content = _call(anthropic, client, cfg, system, messages)
        try:
            script = extract_script(text)
            doc, smap = parse_lockscript(script)
            issues = validate_doc(doc, library, smap, tts_enabled=tts)
        except (ParseError, MovielockError) as exc:
            script = script if "script" in locals() else text
            issues = [_Issue(str(exc))]
        errs = errors_only(issues)
        notes = text[text.rfind("```") + 3:].strip() if "```" in text else ""
        if not errs or attempt > repairs:
            return WriteResult(script, notes, not errs, issues, attempt)
        messages = messages + [
            {"role": "assistant", "content": content},
            {"role": "user", "content": "The script has errors. Fix them and reply with the full corrected "
                                        "script in one ```lockscript block:\n" + "\n".join(str(e) for e in errs)},
        ]


@dataclass
class _Issue:
    message: str
    severity: str = "error"

    def __str__(self):
        return f"error: {self.message}"

    def to_dict(self):
        return {"severity": "error", "message": self.message, "line": None, "path": ""}
