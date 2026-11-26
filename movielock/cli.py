"""Command line: movielock write | validate | render | convert | plan | library | import-sounds | demo | web"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__
from .config import load_config
from .errors import MovielockError
from .library import load_library
from .lockscript import detect_kind, dumps, load_script


def _cfg(args):
    return load_config(args.config)


def cmd_validate(args) -> int:
    cfg = _cfg(args)
    library = load_library(cfg.assets_dir, cfg.voices_dir)
    from .validate import validate_doc
    doc, smap = load_script(args.script)
    issues = validate_doc(doc, library, smap, tts_enabled=bool(cfg["tts"].get("enabled")))
    if args.json:
        print(json.dumps([i.to_dict() for i in issues], indent=2))
    else:
        for i in issues:
            print(f"{args.script}: {i}")
    errors = [i for i in issues if i.severity == "error"]
    if not args.json:
        print(f"{len(errors)} error(s), {len(issues) - len(errors)} warning(s)" if issues else "OK: no problems found")
    return 1 if errors else 0


def cmd_render(args) -> int:
    from .render.pipeline import render_file
    cfg = _cfg(args)
    result = render_file(Path(args.script), cfg, quality=args.quality, renderer=args.renderer,
                         out=Path(args.out) if args.out else None, keep_work=not args.clean)
    print(f"\nwrote {result.output} ({result.duration:.1f}s, {result.renderer})")
    print(f"plan: {result.plan_path}")
    return 0


def cmd_plan(args) -> int:
    from .planner import build_plan, write_plan
    cfg = _cfg(args)
    library = load_library(cfg.assets_dir, cfg.voices_dir)
    doc, smap = load_script(args.script)
    plan = build_plan(doc, library, cfg, args.quality, smap)
    if args.out:
        write_plan(plan, Path(args.out))
        print(f"wrote {args.out}")
    else:
        print(json.dumps(plan, indent=2))
    return 0


def cmd_convert(args) -> int:
    doc, _ = load_script(args.input)
    if args.output:
        kind = args.to or detect_kind(args.output)
        Path(args.output).write_text(dumps(doc, kind), encoding="utf-8")
        print(f"wrote {args.output}")
    else:
        src_kind = detect_kind(args.input)
        kind = args.to or ("yaml" if src_kind == "lockscript" else "lockscript")
        sys.stdout.write(dumps(doc, kind))
    return 0


def cmd_write(args) -> int:
    from .writer import write_script
    cfg = _cfg(args)
    if args.model:
        cfg.data["writer"]["model"] = args.model
    library = load_library(cfg.assets_dir, cfg.voices_dir)
    log = (lambda m: print(m, file=sys.stderr))
    result = write_script(args.idea, library, cfg, fmt=args.format, log=log)
    if args.out:
        Path(args.out).write_text(result.script, encoding="utf-8")
        print(f"wrote {args.out}", file=sys.stderr)
    else:
        sys.stdout.write(result.script)
    if result.notes:
        print(f"\nwriter: {result.notes}", file=sys.stderr)
    for issue in result.issues:
        print(str(issue), file=sys.stderr)
    if not result.valid:
        print("the script still has errors after the repair attempt; edit it and run validate", file=sys.stderr)
        return 1
    return 0


def cmd_library(args) -> int:
    cfg = _cfg(args)
    lib = load_library(cfg.assets_dir, cfg.voices_dir)
    s = lib.summary()
    print(f"library: {lib.source}")
    print(f"  {s['heroes']} heroes, {s['maps']} maps, {s['sounds']} sounds, {s['music']} music, "
          f"{s['voices']} voice lines ({s['voices_with_files']} with files)")
    for h in lib.heroes.values():
        n = len(lib.voices_for(h.id))
        model = "model ok" if h.model and h.model.is_file() else "no model"
        print(f"  {h.id:12s} {h.name:12s} {model:9s} {n:4d} lines  anims: {', '.join(h.animations)}")
    for m in lib.maps.values():
        model = "model ok" if m.model and m.model.is_file() else ("no model" if m.model else "plain floor")
        print(f"  map {m.id:16s} {model}")
    return 0


def cmd_import(args) -> int:
    from .importer import import_sounds, load_closecaption_kv, load_hero_names, load_transcripts_csv
    cfg = _cfg(args)
    library = load_library(cfg.assets_dir, cfg.voices_dir)
    transcripts = {}
    if args.captions:
        transcripts.update(load_closecaption_kv(Path(args.captions)))
    if args.transcripts:
        transcripts.update(load_transcripts_csv(Path(args.transcripts)))
    report = import_sounds(Path(args.source), library, cfg.voices_dir, transcripts=transcripts, copy=args.copy,
                           merge=not args.replace, hero_names=load_hero_names(cfg.assets_dir / "hero_names.json"))
    print("\n".join(report.lines()))
    return 0


def cmd_demo(args) -> int:
    from .demo import build_demo
    root = Path(args.dir)
    cfg_path = build_demo(root)
    print(f"demo project in {root} (synthetic placeholder audio, no game files)")
    if args.render:
        from .render.pipeline import render_file
        cfg = load_config(cfg_path)
        result = render_file(root / "demo.lock", cfg, quality=args.quality, renderer=args.renderer)
        print(f"\nwrote {result.output}")
    else:
        print(f"try: movielock --config {cfg_path} render {root / 'demo.lock'} --renderer storyboard")
    return 0


def cmd_web(args) -> int:
    from .web.app import create_app
    cfg = _cfg(args)
    app = create_app(cfg)
    host = args.host or cfg["web"]["host"]
    port = args.port or int(cfg["web"]["port"])
    print(f"Movielock web app on http://{host}:{port}")
    app.run(host=host, port=port, debug=False, threaded=True)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="movielock", description="Script -> 3D video of Deadlock heroes "
                                                              "(non-commercial fan tool).")
    p.add_argument("--version", action="version", version=f"movielock {__version__}")
    p.add_argument("--config", help="path to movielock.yaml (default: ./movielock.yaml)")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("write", help="write a script from an idea with the AI writer")
    s.add_argument("idea")
    s.add_argument("-o", "--out", help="save to this file (.lock, .yaml or .json)")
    s.add_argument("--format", choices=["video", "reel", "square"])
    s.add_argument("--model", help="override writer.model")
    s.set_defaults(func=cmd_write)

    s = sub.add_parser("validate", help="check a script against the schema and asset library")
    s.add_argument("script")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_validate)

    s = sub.add_parser("render", help="render a script to MP4")
    s.add_argument("script")
    s.add_argument("--quality", default="preview", choices=["preview", "draft", "final"])
    s.add_argument("--renderer", choices=["auto", "blender", "storyboard"])
    s.add_argument("-o", "--out")
    s.add_argument("--clean", action="store_true", help="delete frames and shot clips afterwards")
    s.set_defaults(func=cmd_render)

    s = sub.add_parser("plan", help="write plan.json without rendering")
    s.add_argument("script")
    s.add_argument("--quality", default="preview", choices=["preview", "draft", "final"])
    s.add_argument("-o", "--out")
    s.set_defaults(func=cmd_plan)

    s = sub.add_parser("convert", help="convert Lockscript <-> YAML/JSON")
    s.add_argument("input")
    s.add_argument("output", nargs="?")
    s.add_argument("--to", choices=["lockscript", "yaml", "json"])
    s.set_defaults(func=cmd_convert)

    s = sub.add_parser("library", help="show heroes, maps and voice line counts")
    s.set_defaults(func=cmd_library)

    s = sub.add_parser("import-sounds", help="build the voice index from a folder (see tools/import_sounds.py)")
    s.add_argument("source")
    s.add_argument("--transcripts")
    s.add_argument("--captions")
    s.add_argument("--copy", action="store_true")
    s.add_argument("--replace", action="store_true")
    s.set_defaults(func=cmd_import)

    s = sub.add_parser("demo", help="create a demo project with synthetic audio (no game files needed)")
    s.add_argument("dir", nargs="?", default="demo")
    s.add_argument("--render", action="store_true")
    s.add_argument("--quality", default="preview", choices=["preview", "draft", "final"])
    s.add_argument("--renderer", default="storyboard", choices=["auto", "blender", "storyboard"])
    s.set_defaults(func=cmd_demo)

    s = sub.add_parser("web", help="start the local web app")
    s.add_argument("--host")
    s.add_argument("--port", type=int)
    s.set_defaults(func=cmd_web)
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except MovielockError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
