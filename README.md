# Movielock

Turn a short script into a finished 3D video of Deadlock heroes talking and acting: a 16:9 video,
a 9:16 reel/short, or a square clip. It uses real in-game voice lines and lip sync. An AI writer can
draft the script from a plain-English idea.

```
idea → AI writer → script → validate → plan (plan.json) → render shots (Blender) → mix audio + captions (FFmpeg) → MP4
```

> **Non-commercial fan tool.** Movielock is not affiliated with or endorsed by Valve. Deadlock, its heroes,
> models and voice lines belong to Valve. This repository contains **no game assets**: you export them from
> your own copy of the game (see [docs/ASSETS.md](docs/ASSETS.md)) and they stay on your machine.
> Don't sell what you make with it, and respect Valve's terms when you share videos.

## Quick start

Requirements: Python 3.11+, [FFmpeg](https://ffmpeg.org/) on your PATH. For real 3D renders you also need
[Blender](https://www.blender.org/) 4.2 or newer. For better lip sync, add
[Rhubarb Lip Sync](https://github.com/DanielSWolf/rhubarb-lip-sync) (optional).

```bash
pip install -e .            # pyyaml, jsonschema, flask
pip install anthropic       # only for the AI writer

# 1. Try the whole pipeline with no game files (synthetic placeholder audio, storyboard preview)
movielock demo demo --render

# 2. Get assets (once). Quickest: download from the community Deadlock API
movielock fetch-voices haze infernus          # voice lines (MP3) -> assets/voices + index.yaml
movielock fetch-models haze infernus          # rigged GLB models with an idle pose (optional)
pip install faster-whisper && movielock transcribe haze infernus   # fill in transcripts locally
movielock library
#    ...or export higher-quality models/animations yourself (docs/ASSETS.md)

# 3. Write, check and render
export ANTHROPIC_API_KEY=...            # for the AI writer
movielock write "Haze and Infernus argue about who stole the last urn" --format reel -o urn.lock
movielock validate urn.lock
movielock render urn.lock --quality preview     # quick look
movielock render urn.lock --quality final       # Cycles, denoised, motion blur

# Or do all of it in the browser
movielock web                           # http://127.0.0.1:5005
```

If Blender isn't found, `render` falls back to the **storyboard** renderer. It needs only FFmpeg and
draws each shot as a panel: shot and camera label, a top-down stage map with the cast, camera and
aim point, who is speaking, a mouth box driven by the lip sync, the current animation, sound-effect
markers and captions. Use it to check timing and staging. Force it with `--renderer storyboard`.

## The script language (Lockscript)

```
MOVIELOCK reel 30fps map=rooftop_night captions=on
TITLE Rooftop Standoff
MUSIC tense_loop vol=0.35
CAST haze = haze @ -1.5,0 > inf        # name = hero @ x,y[,z] > faces
CAST inf  = infernus @ 1.5,0 > haze
SHOT wide on=haze 3s
  haze ~ idle                           # ~ animation [@t] [once|loop]
  haze ! haze_taunt_01                  # ! real voice line id [@t] ["caption"]
  inf ? "You talk too much."            # ? text-to-speech line (TTS off by default)
  * explosion_small @1.2 vol=0.7        # * sound effect
```

| Statement | Meaning |
|---|---|
| `MOVIELOCK <format> <n>fps map=<id> captions=on\|off` | Header. Format: `video` (16:9, 1920×1080), `reel` (9:16, 1080×1920), `square` (1080×1080). Also accepts `16:9`, `9:16`, `1:1`. Defaults: video, 30 fps, captions on. |
| `TITLE <text>` | Title (used for file names). |
| `MUSIC <id> [vol=<n>]` | Background music from `library.yaml`. It loops and is ducked under dialogue. |
| `CAST <name> = <hero> [@ x,y[,z]] [> <name or yaw>]` | An actor. `name` is what the script uses, `hero` a library id. Positions are in metres. Yaw 0 faces −Y (toward the default camera side). Omit the position to spread the cast along X, and omit `>` to face the others. |
| `SHOT <camera> [on=<name>] [toward=<name>] [id=<id>] [<n>s]` | A new shot. Cameras: `wide`, `medium`, `closeup`, `over_shoulder` (we see `on`'s face over `toward`'s shoulder), `orbit`, `dolly_in`, `low_angle`, `two_shot` (`on` + `toward`). The duration is a minimum: a shot grows to fit its lines. Without `on=`, the camera follows the first speaker. |
| `<name> ~ <anim> [@t] [once\|loop]` | Play an animation from the hero's map, starting `t` seconds into the shot. `once` plays it on top of the base animation and then returns. Without `once`/`loop`, the library's setting decides. `idle` and `talk` happen automatically. |
| `<name> ! <voice_id> [@t] ["caption"]` | A real voice line. The caption defaults to its transcript. |
| `<name> ? "<text>" [@t]` | Text-to-speech. With TTS off (the default) it becomes a silent, timed caption with mouth movement. |
| `* <sound_id> [@t] [vol=<n>]` | Sound effect. |

Indentation is optional. Events belong to the SHOT above them, and `#` starts a comment (outside quotes).

**Timing.** Lines without `@t` play one after another: 0.15 s lead, 0.25 s gap, and a 0.6 s hold after the
last line (all set in `movielock.yaml`). Line length comes from the audio file. Lines with `@t` start
exactly there.

### The same script as YAML / JSON

Every Lockscript has an equivalent structured form, described by the JSON Schema in
[`movielock/data/lockscript.schema.json`](movielock/data/lockscript.schema.json). `movielock convert` goes
both ways, and the web app has a Lockscript/YAML toggle.

```
MOVIELOCK video 30fps map=studio
CAST haze = haze @ -1.5,0 > inf
CAST inf  = infernus @ 1.5,0 > haze
SHOT two_shot on=haze toward=inf
  haze ! haze_taunt_01
  inf ~ point @0.4 once
  * whoosh @0.2 vol=0.6
```

```yaml
movielock: 1
format: video
fps: 30
map: studio
cast:
- {name: haze, hero: haze, position: [-1.5, 0], faces: inf}
- {name: inf, hero: infernus, position: [1.5, 0], faces: haze}
shots:
- camera: two_shot
  'on': haze          # quote it: YAML 1.1 reads a bare `on` as true (Movielock fixes that up anyway)
  toward: inf
  events:
  - {type: voice, actor: haze, line: haze_taunt_01}
  - {type: anim, actor: inf, anim: point, at: 0.4, once: true}
  - {type: sfx, sound: whoosh, at: 0.2, volume: 0.6}
```

A longer example is in [`examples/rooftop.lock`](examples/rooftop.lock) / [`examples/rooftop.yaml`](examples/rooftop.yaml).

**Validation** reports problems with line numbers and suggestions: unknown cast, hero, map, music,
sound or voice id ("did you mean ..."), animations missing from a hero's map, missing voice files,
duplicate names, and TTS lines while TTS is off.

## Asset setup (short version)

Full guide: **[docs/ASSETS.md](docs/ASSETS.md)**.

* `assets/library.yaml` holds data only. **Heroes** have a glTF model path, scale, height (camera aiming),
  `yaw_offset`, an animation-name map, a Rhubarb viseme → shape-key map, a `jaw_bone` fallback and a
  preview colour. **Maps** have a model, HDRI and sky colour. **Sounds** and **music** have file paths.
  New heroes need no code.
* `assets/voices/index.yaml` holds the voice lines (`id, hero, file, transcript`). It is built by
  `tools/import_sounds.py` from a folder with one subfolder per hero, plus an optional transcripts CSV
  or Valve closecaption file.
* Export models, maps and animations with **Source 2 Viewer** (ValveResourceFormat) to GLB.
* `.gitignore` keeps models, maps, HDRIs, sounds and renders out of git. Never commit them.
* **Downloading instead of exporting:** `movielock fetch-voices <heroes>` pulls voice lines from the
  community [Deadlock API](https://deadlock-api.com) (`/v1/assets/sounds`, about 100,000 lines in total, so
  name heroes and use `--match`/`--limit`). `movielock fetch-models <heroes>` pulls its rigged GLBs, which have
  one idle/select pose and no mouth shapes, so lip sync falls back to a small head nod. They are recorded in a
  git-ignored `assets/library.local.yaml`. Neither source has transcripts: `movielock transcribe` fills them
  with local speech-to-text (faster-whisper/Whisper). deadlock.wiki has transcribed lines, but it blocks
  scripted access, so copy what you need into a CSV by hand and import it with `--transcripts`.

## Rendering

| Quality | Engine | Scale | Notes |
|---|---|---|---|
| `preview` | EEVEE, 8 samples | 50% | fastest |
| `draft` | EEVEE, 32 samples | 75% | |
| `final` | Cycles, 256 samples | 100% | denoise, motion blur, x264 `slow` |

All presets are editable in `movielock.yaml`. For each shot, `blender/render_shot.py` (run as
`blender -b -P render_shot.py -- plan.json <shot_id> <out_dir>`) does the following:

* imports the map and heroes (glTF), places and turns them (yaw 0 = facing −Y, plus `yaw_offset`);
* plays the mapped actions on NLA tracks: a base layer (idle, or a looping anim from `@t`), a talk layer
  while the hero speaks, and one-shot actions on top with short blends;
* keys viseme shape keys from the mouth cues (or rotates the jaw bone as a fallback);
* places the camera from the planner's preset keys (aimed with a Track-To target, using hero height).
  Closeups, over-the-shoulder and dolly shots get depth of field;
* sets up 3-point area lighting around the subject plus the HDRI or sky colour;
* renders PNG frames with EEVEE (draft/preview) or Cycles with denoising and motion blur (final), on GPU if available.

Missing hero models are replaced by placeholder mannequins with a moving mouth, so staging can be
checked before export. Set `MOVIELOCK_SAVE_BLEND=1` to also save each shot's `.blend` for inspection.

**Assembly (FFmpeg):** frames → shot clips → concat; audio placed with `adelay` and mixed with `amix`,
with music ducked by a sidechain compressor keyed on dialogue; captions written as an ASS file and
burned in; H.264 + AAC output (`-preset veryfast` except for final).

### plan.json

`movielock plan script.lock -o plan.json` writes the renderer-independent plan: resolution, fps and
frame ranges; cast with absolute model paths, position, yaw, height, animation and viseme maps; and
per shot the camera keys (position, aim point, lens, DOF), lines with times, animation strips per actor
(layer, start/end, loop) and mouth cues. It also holds a flat absolute-time audio list, captions and warnings.
Lip sync uses Rhubarb if installed (with the transcript as a hint), otherwise a loudness-based fallback.

## AI writer

`movielock write "idea"` (or the web app's **Write script** button) calls the Claude API with a system
prompt that explains Lockscript and lists your library: heroes and their animations, maps, sounds, music,
and every voice line id with its transcript. Heroes named in the idea are listed first. The result is
parsed and validated. If it has errors, they are sent back for one repair attempt (`writer.repair_attempts`).

* Needs `pip install anthropic` and `ANTHROPIC_API_KEY` (or `writer.api_key_env`).
* `writer.model` is `claude-opus-5-5` by default, and `writer.effort` sets how hard it thinks.
* Server-side refusal fallback is on (`writer.fallbacks: default`): if the model declines a request, the
  API retries it on a fallback model. Set it to `false` to turn that off.

## Voices, TTS and cloning

By default Movielock **only uses real in-game voice lines**. Text-to-speech is off (`tts.enabled: false`).
`?` lines then become silent captions with a text-based mouth pattern. You can plug in a local TTS engine
with a command template:

```yaml
tts:
  enabled: true
  command: "piper --model voices/en_US-ryan-medium.onnx --output_file {out}"   # placeholders: {text} {out} {hero} {ref}
  stdin: true
```

The command runs without a shell (arguments are split before the text is filled in). `{ref}` (a reference
clip of the hero's real voice, for cloning engines) stays empty unless `tts.voice_reference: true`.
**Leave voice cloning off.** Cloning a voice actor's voice without their consent is legally risky
(publicity/personality rights, the game's terms, and AI-voice laws in several places). Use it only for
private experiments, and never publish or share cloned output.

## Interfaces

**CLI**

```
movielock write "idea" [--format reel] [-o script.lock]
movielock validate script.lock [--json]
movielock render script.lock [--quality preview|draft|final] [--renderer auto|blender|storyboard] [-o out.mp4]
movielock convert script.lock script.yaml        # or .json, or back; --to to print another form
movielock plan script.lock -o plan.json
movielock library                                 # what's installed / missing
movielock import-sounds <folder> [--transcripts x.csv] [--captions cc.txt] [--copy]
movielock fetch-voices <heroes...> [--match REGEX] [--exclude REGEX] [--limit N] [--dry-run] [--all]
movielock fetch-models <heroes...> [--pose idle|hero_select] [--all]
movielock transcribe [<heroes...>] [--limit N]
movielock demo [dir] [--render]
movielock web [--port 5005]
```

Use `--config path/to/movielock.yaml` to work with another project (the demo makes its own).

**Web app** (`movielock web`): idea box → **Write script** → editable script (Lockscript or YAML, autosaved in
the browser) → **Validate** (click an issue to jump to the line) / **Render** with quality and renderer
choice, progress and log → video player and **Download**. A searchable library panel inserts voice lines
at the cursor. It binds to 127.0.0.1 and is meant for your own machine.

## Settings

`movielock.yaml` contains the Blender / Rhubarb / FFmpeg paths, asset, voice, render and cache folders,
writer model, timing, audio levels, caption style, quality presets, TTS and web host/port. Every key is
optional and commented.

## Development

```bash
pip install -e .[dev,ai]          # [transcribe] adds faster-whisper
pytest
```

Tests cover the parser round trip (Lockscript ↔ YAML ↔ JSON), validation messages, planner timings and
staging, camera presets, lip-sync fallbacks, the voice importer, the Deadlock API downloaders and transcription (against a
fake HTTP layer), the AI writer (with a fake client), the web API, and an end-to-end storyboard render with generated test audio that checks the output with
ffprobe. `blender/render_shot.py` was also run under the `bpy` module (Blender 4.2 and 5.0) with a
rigged test figure, to check NLA strips, action slots, shape keys and the jaw fallback.

## Roadmap

* **More camera moves:** crane, handheld shake, whip pans, rack focus, shot-to-shot match cuts, a `cut` vs
  `blend` transition per shot.
* **Multi-character staging:** walk-to targets (`haze > walk 2,1`), look-at with head/eye bones, groups and
  formations, automatic blocking to avoid occlusion.
* **Unreal option:** an Unreal Engine renderer driven by the same plan.json (Movie Render Queue).
* **Style passes:** toon/outline, comic-panel, painterly and film-grain looks, colour grading presets.
* Per-line emotion tags that pick matching voice lines, facial expressions beyond visemes, subtitles
  styles per hero, and music cues per shot.

## Credits

[Blender](https://www.blender.org/), [FFmpeg](https://ffmpeg.org/),
[Rhubarb Lip Sync](https://github.com/DanielSWolf/rhubarb-lip-sync) by Daniel Wolf,
[Source 2 Viewer / ValveResourceFormat](https://valveresourceformat.github.io/),
the community [Deadlock API](https://deadlock-api.com) (hero data, sound and model CDN),
[faster-whisper](https://github.com/SYSTRAN/faster-whisper). Deadlock is a trademark of Valve Corporation.
