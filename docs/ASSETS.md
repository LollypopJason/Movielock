# Setting up assets

Movielock never ships game files. You export heroes, maps, animations and voice lines from
**your own Deadlock install** and put them in `assets/`, which git ignores (see `.gitignore`).
Don't commit or redistribute them, and check Valve's terms before you publish videos.

## 0. Quickest route: download from the community Deadlock API

[deadlock-api.com](https://deadlock-api.com) hosts the game's sounds and some hero models on a public CDN.
Movielock can download them for you:

```bash
movielock fetch-voices haze infernus --dry-run     # how many files?
movielock fetch-voices haze infernus               # MP3s -> assets/voices/<hero>/..., then indexed
movielock fetch-voices haze --match "kill|taunt"   # only some lines (regex on the file path)
movielock fetch-models haze infernus               # GLB -> assets/heroes/<hero>/idle.glb (or hero_select.glb)
pip install faster-whisper
movielock transcribe haze infernus                 # fill in transcripts with local speech-to-text
```

* Voice lines come from `/v1/assets/sounds` (`vo/<class name>/...`). There are about 2,000 to 5,000 per hero
  and about 100,000 in total, so download only the heroes you need. Effort grunts (`emote/`) are skipped by
  default (`deadlock_api.exclude` in `movielock.yaml`, or `--exclude ""`). IDs use your library's hero id
  (`inferno_kill_01` becomes `infernus_kill_01`). The sound and model indexes are cached for a day, and
  downloads use 4 parallel connections. Please keep it that way, since it's a free community service.
* Models come from `/v1/assets/models` and are rigged GLBs with a single idle or hero-select pose.
  They have **no talking animation, no mouth shape keys and no jaw bone**, so `fetch-models` sets
  `jaw_bone: head` with a small `jaw_open` (the head nods along with the speech). It writes this to
  `assets/library.local.yaml` (git-ignored, merged over `library.yaml`), so your own edits stay intact.
  For real lip sync and more animations, export the model yourself (below) and remove the entry
  from `library.local.yaml`.
* **Transcripts** aren't served anywhere a script can fetch them: the API has none, and deadlock.wiki
  (which lists lines with text) puts its pages behind a bot challenge. `movielock transcribe` runs
  [faster-whisper](https://github.com/SYSTRAN/faster-whisper) or Whisper locally (or any command, see
  `transcribe` in `movielock.yaml`) on lines without a transcript and saves the text in `index.yaml`.
  You can also copy lines from the wiki into a CSV by hand and import it with `--transcripts`.

The rest of this guide covers exporting from your own install, which gives full-quality models,
all animations and facial shapes.

## Exporting with Source 2 Viewer

The tool for this is **Source 2 Viewer** (ValveResourceFormat, "VRF"): <https://valveresourceformat.github.io/>.
It opens Source 2 package files (`.vpk`), previews models, animations and sounds, and exports
them to glTF / WAV. Menu names below are from recent versions and may differ slightly in yours.

## 1. Find the game files

Deadlock's content is in your Steam library, typically:

```
<Steam>/steamapps/common/Deadlock/game/citadel/pak01_dir.vpk
```

Open `pak01_dir.vpk` in Source 2 Viewer. Useful places (folder names can change between patches;
use the viewer's search box):

| What | Where to look |
|---|---|
| Hero models | `models/heroes*/<hero>/...` (`.vmdl_c`) |
| Maps / environment pieces | `maps/` and `models/` props |
| Voice lines | `sounds/vo/<hero>/...` (`.vsnd_c`) |
| Sound effects, music | `sounds/` |

Heroes use internal names that differ from their display names (for example the game calls
Abrams `atlas` and Infernus `inferno`). `assets/library.yaml` lists the known ones as `aliases`.
`python tools/fetch_hero_names.py` can download the full class-name → name list from the
community Deadlock API into `assets/hero_names.json`. The importer uses that file too.

## 2. Export a hero (model + animations)

1. In Source 2 Viewer, open the hero's `.vmdl_c` and check the animations play in the preview.
2. Use **Export as glTF** (right-click the file, or the export button) and pick **GLB**.
   Include animations and materials. Exporting all animations makes a big file, but Movielock
   only uses the ones you map.
3. Save it as `assets/heroes/<hero_id>/<hero_id>.glb`, matching the `model:` path in
   `assets/library.yaml` (for example `assets/heroes/haze/haze.glb`).

The viewer also has a command-line version (`Source2Viewer-CLI`) for batch exports. Run it with
`--help` to see the flags for your version (input VPK, a path filter inside the VPK, an output
folder, decompile/export to glTF).

### Map the animations

Open the `.glb` in Blender (File → Import → glTF 2.0) and look at the Actions list
(Dope Sheet → Action Editor, or the NLA editor). Then edit the hero in `assets/library.yaml`:

```yaml
heroes:
  haze:
    name: Haze
    model: heroes/haze/haze.glb
    height: 1.72          # metres, used to aim the cameras at the face
    yaw_offset: 0         # degrees; change to 90/180/-90 if she faces sideways at yaw 0
    animations:
      idle: {action: "<idle action name>", loop: true}
      talk: {action: "<a subtle talking/gesture loop>", loop: true}
      taunt: {action: "<taunt action name>", loop: false}
```

`idle` and `talk` are used automatically. Any other name becomes available in scripts as
`haze ~ taunt`. Blender's glTF importer names actions `<animation>_<object>` in older versions,
and Movielock matches either form.

### Lip sync: visemes or jaw bone

Movielock produces Rhubarb mouth shapes over time (A–H and X). Each hero maps them to its
facial shape keys (glTF morph targets):

```yaml
    visemes:
      A: <closed lips shape key>      # M B P
      B: <slightly open, teeth>       # K S T EE
      C: <open>                       # EH AE
      D: <wide open>                  # AA
      E: <rounded>                    # AO ER
      F: <puckered>                   # UW OW W
      G: <F/V>                        # F V
      H: <L>                          # L
      X: <rest>                       # silence
```

A value can be one shape key name, a list of names, or `{name: weight}` to mix several.
To see what a model has, select its mesh in Blender and open
Object Data Properties → Shape Keys. Source 2 face models usually expose flex controllers or
morphs, so pick the ones that move the mouth.

If the model has no usable shape keys, set `jaw_bone` (and `jaw_open` / `jaw_axis` if the jaw
opens the wrong way). Movielock then rotates that bone instead.

## 3. Export a map

Exporting an entire map is heavy. A cut-down scene works better: open the map (`.vmap_c`) or
individual props in Source 2 Viewer, export to GLB, and if needed trim it in Blender and
re-export. Save it to `assets/maps/<map_id>/...glb` and point `library.yaml` at it:

```yaml
maps:
  rooftop_night:
    model: maps/rooftop_night/rooftop.glb
    hdri: hdri/night_city.exr   # optional environment light (any equirectangular .exr/.hdr)
    sky_color: "#0b1020"        # used when there is no HDRI
    scale: 1.0
    offset: [0, 0, 0]           # move the map so the action happens around the origin
```

Heroes stand on z = 0 around the origin, so use `offset` to put a flat, open area there.
The built-in `studio` map needs no files: it's a plain floor and a sky colour.

## 4. Voice lines

1. In Source 2 Viewer, export the hero's voice line folder (`sounds/vo/<hero>/`) to WAV or MP3.
2. Arrange them one folder per hero (deeper folders become tags):

   ```
   my_sounds/
     haze/            haze_*.wav ...
     hero_inferno/    ...           (class names work too)
   ```

3. Build the index:

   ```bash
   python tools/import_sounds.py my_sounds --copy
   # with transcripts from a CSV (columns: file or id, transcript)
   python tools/import_sounds.py my_sounds --copy --transcripts transcripts.csv
   # or from a Valve closecaption KeyValues text file, if your install has one
   python tools/import_sounds.py my_sounds --copy --captions closecaption_english.txt
   ```

This writes `assets/voices/index.yaml` (id, hero, file, transcript). Importing again merges with
the existing index. Use `--replace` to start over.

**Transcripts matter.** The AI writer builds scenes around what the lines say, and captions show
the transcript. Lines without one still work, but the writer can only guess from the id
(`haze_ally_abrams_killed_in_lane_01` says a lot). Fill gaps with `movielock transcribe` or a CSV.

Or skip the export and use `movielock fetch-voices` (section 0).

## 5. Sound effects and music

Put WAV/OGG/MP3 files under `assets/sfx/` and `assets/music/` and list them:

```yaml
sounds:
  explosion_small: {file: sfx/explosion_small.wav, volume: 0.9, description: short explosion}
music:
  tense_loop: {file: music/tense_loop.ogg, volume: 0.35, description: tense standoff}
```

Descriptions are shown to the AI writer. Music loops for the whole video and ducks under dialogue.

## 6. Check it

```bash
movielock library      # what's found, which models are missing, voice line counts
movielock validate examples/rooftop.lock
movielock render examples/rooftop.lock --quality draft
```

Missing hero models render as plain placeholder mannequins, so you can test staging before
exporting everything.
