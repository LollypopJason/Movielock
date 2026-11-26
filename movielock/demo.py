"""A self-contained demo project with synthetic placeholder audio.

Used by ``movielock demo`` and the tests, so the whole pipeline can run without any game files.
The "voice lines" are synthetic hums with a speech-like rhythm, and their transcripts are
invented placeholder text. They are not real game audio.
"""

from __future__ import annotations

import math
import random
from pathlib import Path

import yaml

from .audio import write_wav

SR = 48000

DEMO_SCRIPT = """\
# Demo script: runs with the synthetic demo assets (movielock demo).
MOVIELOCK video 30fps map=studio captions=on
TITLE Demo Standoff
MUSIC demo_loop vol=0.3
CAST haze = haze @ -1.4,0 > inf
CAST inf  = infernus @ 1.4,0 > haze

SHOT wide on=haze id=open 2s
  * whoosh @0.1 vol=0.6
  haze ! demo_haze_01

SHOT over_shoulder on=inf toward=haze
  inf ! demo_inf_01
  inf ~ point @0.3 once

SHOT closeup on=haze
  haze ! demo_haze_02 "Then stop listening."
  * boom @0.8 vol=0.8

SHOT orbit on=inf 2s
  inf ? "Fine. Round two."
"""

DEMO_VOICES = [
    # id, hero, transcript (placeholder text), syllables, pitch
    ("demo_haze_01", "haze", "You're late. Again.", 5, 210),
    ("demo_haze_02", "haze", "Then stop listening.", 6, 220),
    ("demo_inf_01", "infernus", "You talk too much.", 5, 120),
    ("demo_inf_02", "infernus", "Let's make this quick.", 6, 115),
]


def _speechlike(syllables: int, f0: float, seed: int) -> list[float]:
    rng = random.Random(seed)
    out: list[float] = [0.0] * int(0.05 * SR)
    for i in range(syllables):
        dur = rng.uniform(0.12, 0.24)
        n = int(dur * SR)
        pitch = f0 * rng.uniform(0.9, 1.15)
        vowel = rng.choice([(700, 1200), (400, 2000), (300, 800), (600, 1000)])
        for k in range(n):
            t = k / SR
            env = math.sin(math.pi * k / n) ** 2
            s = sum(math.sin(2 * math.pi * pitch * h * t) / h for h in range(1, 6))
            s += 0.3 * math.sin(2 * math.pi * vowel[0] * t) + 0.15 * math.sin(2 * math.pi * vowel[1] * t)
            out.append(0.25 * env * s)
        gap = rng.choice([0.03, 0.05, 0.08]) if (i + 1) % 3 else 0.18
        out.extend([0.0] * int(gap * SR))
    out.extend([0.0] * int(0.05 * SR))
    return out


def _noise_burst(length: float, decay: float, seed: int, lowpass: float = 0.15) -> list[float]:
    rng = random.Random(seed)
    out, prev = [], 0.0
    for k in range(int(length * SR)):
        t = k / SR
        prev += lowpass * (rng.uniform(-1, 1) - prev)
        out.append(0.9 * prev * math.exp(-t / decay) * 3)
    return out


def _whoosh(length: float = 0.6) -> list[float]:
    rng = random.Random(3)
    out, prev = [], 0.0
    n = int(length * SR)
    for k in range(n):
        alpha = 0.02 + 0.3 * k / n
        prev += alpha * (rng.uniform(-1, 1) - prev)
        out.append(0.6 * prev * math.sin(math.pi * k / n))
    return out


def _music_loop(length: float = 8.0) -> list[float]:
    chords = [(110.0, 130.8, 164.8), (98.0, 123.5, 146.8), (87.3, 110.0, 130.8), (98.0, 123.5, 146.8)]
    out = []
    n = int(length * SR)
    per = n // len(chords)
    for k in range(n):
        t = k / SR
        chord = chords[min(k // per, len(chords) - 1)]
        pulse = 0.6 + 0.4 * math.sin(2 * math.pi * 2 * t) ** 2
        out.append(0.12 * pulse * sum(math.sin(2 * math.pi * f * t) for f in chord))
    return out


def build_demo(root: Path) -> Path:
    """Create the demo project in ``root`` and return the path to its movielock.yaml."""
    root = Path(root)
    assets = root / "assets"
    voices = assets / "voices"
    for i, (vid, hero, _, syl, pitch) in enumerate(DEMO_VOICES):
        f = voices / hero / f"{vid}.wav"
        if not f.exists():
            write_wav(f, _speechlike(syl, pitch, seed=i), SR)
    sfx = {"boom": (assets / "sfx" / "boom.wav", lambda: _noise_burst(1.2, 0.35, 1)),
           "whoosh": (assets / "sfx" / "whoosh.wav", _whoosh)}
    for _, (f, make) in sfx.items():
        if not f.exists():
            write_wav(f, make(), SR)
    music = assets / "music" / "demo_loop.wav"
    if not music.exists():
        write_wav(music, _music_loop(), SR)

    anims = {"idle": {"action": "idle", "loop": True}, "talk": {"action": "talk", "loop": True},
             "point": {"action": "point", "loop": False}, "taunt": {"action": "taunt", "loop": False}}
    library = {
        "heroes": {
            "haze": {"name": "Haze", "model": "heroes/haze/haze.glb", "height": 1.72, "color": "#7b5ea7",
                     "animations": anims, "jaw_bone": "jaw", "aliases": ["hero_haze"]},
            "infernus": {"name": "Infernus", "model": "heroes/infernus/infernus.glb", "height": 1.9,
                         "color": "#d2552b", "animations": anims, "jaw_bone": "jaw",
                         "aliases": ["hero_inferno", "inferno"]},
        },
        "maps": {"studio": {"name": "Studio", "sky_color": "#1c1f26", "ground": True}},
        "sounds": {"boom": {"file": "sfx/boom.wav"}, "whoosh": {"file": "sfx/whoosh.wav"}},
        "music": {"demo_loop": {"file": "music/demo_loop.wav", "volume": 0.35}},
    }
    (assets / "library.yaml").write_text(
        "# Demo library (synthetic placeholder assets).\n" + yaml.safe_dump(library, sort_keys=False),
        encoding="utf-8")
    index = {"voices": [{"id": vid, "hero": hero, "file": f"{hero}/{vid}.wav", "transcript": text}
                        for vid, hero, text, _, _ in DEMO_VOICES]}
    (voices / "index.yaml").write_text(yaml.safe_dump(index, sort_keys=False), encoding="utf-8")
    (root / "demo.lock").write_text(DEMO_SCRIPT, encoding="utf-8")
    cfg = root / "movielock.yaml"
    if not cfg.exists():
        cfg.write_text(
            "# Demo project settings (see the main movielock.yaml for all options)\n"
            "paths:\n  assets: assets\n  voices: assets/voices\n  renders: renders\n"
            "  cache: .movielock_cache\n"
            "renderer: storyboard\n", encoding="utf-8")
    return cfg
