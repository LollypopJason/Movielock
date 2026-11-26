#!/usr/bin/env python3
"""Build assets/voices/index.yaml from a folder of exported hero voice lines.

    python tools/import_sounds.py path/to/sounds [--transcripts lines.csv] [--captions closecaption_english.txt]
                                                 [--copy] [--replace] [--dry-run]

Each first-level subfolder is one hero: its name can be the library id (haze), the display
name (Lady Geist), an alias or the game's class name (hero_inferno). Deeper folders become tags.

--transcripts  CSV with a key column (id, file or filename) and a text column (transcript or text)
--captions     a Valve closecaption KeyValues text file, if your install has one ("token" "text" pairs;
               a token matches a voice file when it equals the file name without extension)
--copy         copy the files into assets/voices/<hero>/ (otherwise the index points at them in place)
--replace      start a fresh index instead of merging with the existing one
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from movielock.config import load_config  # noqa: E402
from movielock.importer import (import_sounds, load_closecaption_kv, load_hero_names,  # noqa: E402
                                load_transcripts_csv)
from movielock.library import load_library  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("source", type=Path, help="folder with one subfolder per hero")
    ap.add_argument("--transcripts", type=Path, help="CSV of transcripts")
    ap.add_argument("--captions", type=Path, help="Valve closecaption KeyValues .txt")
    ap.add_argument("--copy", action="store_true", help="copy files into the voices folder")
    ap.add_argument("--replace", action="store_true", help="don't merge with the existing index")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--config", type=Path, help="movielock.yaml (default: ./movielock.yaml)")
    args = ap.parse_args(argv)

    cfg = load_config(args.config)
    library = load_library(cfg.assets_dir, cfg.voices_dir)
    transcripts = {}
    if args.captions:
        transcripts.update(load_closecaption_kv(args.captions))
    if args.transcripts:
        transcripts.update(load_transcripts_csv(args.transcripts))
    if not args.source.is_dir():
        print(f"not a folder: {args.source}", file=sys.stderr)
        return 2
    report = import_sounds(args.source, library, cfg.voices_dir, transcripts=transcripts, copy=args.copy,
                           merge=not args.replace, hero_names=load_hero_names(cfg.assets_dir / "hero_names.json"),
                           dry_run=args.dry_run)
    print("\n".join(report.lines()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
