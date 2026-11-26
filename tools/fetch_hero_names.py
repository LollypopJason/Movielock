#!/usr/bin/env python3
"""Download the hero list (internal class name -> display name) from the community Deadlock API.

    python tools/fetch_hero_names.py [--url URL] [--out assets/hero_names.json]

The importer uses assets/hero_names.json to recognise sound folders named after the game's
internal class names (e.g. hero_atlas -> Abrams) that aren't in library.yaml aliases yet.

About sounds: as of this writing deadlock-api.com does NOT offer voice lines, sound files or
voice-line transcripts (its assets API serves heroes, items, map data, images and models), so
voice lines must be exported from your own game install (see docs/ASSETS.md).
"""

import argparse
import json
import sys
import urllib.request
from pathlib import Path

DEFAULT_URLS = [
    "https://api.deadlock-api.com/v1/assets/heroes?only_active=true",
    "https://assets.deadlock-api.com/v2/heroes?only_active=true",
]


def fetch(url: str) -> list:
    req = urllib.request.Request(url, headers={"User-Agent": "movielock-fan-tool"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", help="hero list endpoint (default: try the known endpoints)")
    ap.add_argument("--out", type=Path, default=Path(__file__).resolve().parent.parent / "assets" / "hero_names.json")
    args = ap.parse_args(argv)

    errors = []
    for url in [args.url] if args.url else DEFAULT_URLS:
        try:
            heroes = fetch(url)
            break
        except Exception as exc:  # noqa: BLE001 - report and try the next endpoint
            errors.append(f"{url}: {exc}")
    else:
        print("could not download the hero list:\n  " + "\n  ".join(errors), file=sys.stderr)
        return 1

    names = {h["class_name"]: h["name"] for h in heroes if h.get("class_name") and h.get("name")}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(dict(sorted(names.items())), indent=2) + "\n", encoding="utf-8")
    print(f"wrote {len(names)} heroes to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
