"""Download voice lines and hero models from the community Deadlock API (deadlock-api.com).

Endpoints used (all public, no key):
  GET {api}/v1/assets/heroes    hero list: class_name ("hero_inferno"), name ("Infernus"), colours,
                                and models.hero_select (a GLB URL)
  GET {api}/v1/assets/sounds    nested file tree of every hosted sound -> CDN URL (MP3).
                                Voice lines live under vo/<class name without "hero_">/...
  GET {api}/v1/assets/models    nested file tree of hosted models (one <pose>.glb per pose)

The API serves no voice-line transcripts. Use `movielock transcribe` (local speech-to-text),
a CSV, or your install's closecaption file for those.

Files are downloaded to your machine only; never commit them.
"""

from __future__ import annotations

import json
import re
import struct
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .config import Config
from .errors import MovielockError
from .library import Library

USER_AGENT = "movielock-fan-tool/0.1 (+https://github.com/LollypopJason/Movielock)"


# --------------------------------------------------------------------------- HTTP + cache

def _http_get(url: str, timeout: float = 60.0, retries: int = 3) -> bytes:
    last = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except urllib.error.HTTPError as exc:
            if exc.code in (400, 401, 403, 404):
                raise MovielockError(f"{url}: HTTP {exc.code}") from None
            last = exc
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            last = exc
        time.sleep(1.5 * (attempt + 1))
    raise MovielockError(f"could not download {url}: {last}")


class DeadlockAPI:
    def __init__(self, cfg: Config, fetch=None):
        api_cfg = cfg["deadlock_api"]
        self.base = api_cfg["base_url"].rstrip("/")
        self.cache_dir = cfg.cache_dir / "deadlock_api"
        self.max_age = float(api_cfg.get("cache_hours", 24)) * 3600
        self.fetch = fetch or _http_get

    def _json(self, path: str, refresh: bool = False):
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        name = re.sub(r"[^A-Za-z0-9]+", "_", path).strip("_") + ".json"
        cached = self.cache_dir / name
        if not refresh and cached.is_file() and time.time() - cached.stat().st_mtime < self.max_age:
            return json.loads(cached.read_text(encoding="utf-8"))
        data = self.fetch(f"{self.base}{path}")
        parsed = json.loads(data)
        cached.write_bytes(data)
        return parsed

    def heroes(self, refresh=False) -> list[dict]:
        return self._json("/v1/assets/heroes?only_active=true", refresh)

    def sounds(self, refresh=False) -> dict:
        return self._json("/v1/assets/sounds", refresh)

    def models(self, refresh=False) -> dict:
        return self._json("/v1/assets/models", refresh)


def flatten(tree, prefix: tuple = ()) -> list[tuple[str, str]]:
    """Nested {name: {...} | url} tree -> [("a/b/c", url)]."""
    out = []
    if isinstance(tree, dict):
        for key, value in tree.items():
            out.extend(flatten(value, prefix + (str(key),)))
    elif isinstance(tree, str):
        out.append(("/".join(prefix), tree))
    return out


def _short(class_name: str) -> str:
    return class_name[5:] if class_name.startswith("hero_") else class_name


def resolve_api_heroes(api_heroes: list[dict], library: Library, wanted: list[str] | None) -> list[tuple[str, dict]]:
    """Pair library hero ids with API hero records. ``wanted`` = library ids / names / class names."""
    pairs = []
    by_lib: dict[str, dict] = {}
    for h in api_heroes:
        hid = library.resolve_hero(h["class_name"]) or library.resolve_hero(h["name"])
        if hid:
            by_lib[hid] = h
    if wanted:
        for w in wanted:
            hid = library.resolve_hero(w)
            if not hid:
                raise MovielockError(f"unknown hero '{w}' (see `movielock library`)")
            if hid not in by_lib:
                raise MovielockError(f"hero '{hid}' is not in the Deadlock API hero list")
            pairs.append((hid, by_lib[hid]))
    else:
        pairs = sorted(by_lib.items())
    return pairs


# --------------------------------------------------------------------------- voice lines

@dataclass
class FetchReport:
    planned: int = 0
    downloaded: int = 0
    skipped_existing: int = 0
    failed: list[str] = field(default_factory=list)
    per_hero: dict[str, int] = field(default_factory=dict)

    def lines(self) -> list[str]:
        out = [f"{h}: {n} lines" for h, n in self.per_hero.items()]
        out.append(f"downloaded {self.downloaded}, already present {self.skipped_existing}, failed {len(self.failed)}")
        out += [f"  failed: {f}" for f in self.failed[:10]]
        return out


def voice_files(sound_tree: dict, short: str) -> list[tuple[str, str]]:
    vo = (sound_tree.get("vo") or {}).get(short) or {}
    return flatten(vo)


def _download_all(jobs: list[tuple[str, Path]], fetch, workers: int, report: FetchReport, progress=None) -> None:
    def one(url: str, dest: Path):
        data = fetch(url)
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(dest.suffix + ".part")
        tmp.write_bytes(data)
        tmp.replace(dest)

    done = 0
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = {pool.submit(one, url, dest): url for url, dest in jobs}
        for fut in as_completed(futures):
            done += 1
            try:
                fut.result()
                report.downloaded += 1
            except Exception as exc:  # noqa: BLE001 - keep going, report at the end
                report.failed.append(f"{futures[fut]} ({exc})")
            if progress and (done % 50 == 0 or done == len(jobs)):
                progress(done, len(jobs))


def fetch_voices(cfg: Config, library: Library, heroes: list[str] | None, *, match: str | None = None,
                 exclude: str | None = None, limit: int | None = None, dry_run: bool = False,
                 api: DeadlockAPI | None = None, fetch=None, log=print) -> FetchReport:
    """Download voice lines for the given heroes into the voices folder (one subfolder per hero)."""
    api = api or DeadlockAPI(cfg)
    fetch = fetch or _http_get
    workers = int(cfg["deadlock_api"].get("workers", 4))
    tree = api.sounds()
    pairs = resolve_api_heroes(api.heroes(), library, heroes)
    if exclude is None:
        exclude = cfg["deadlock_api"].get("exclude") or None
    inc = re.compile(match, re.I) if match else None
    exc = re.compile(exclude, re.I) if exclude else None
    report = FetchReport()
    jobs = []
    for hid, h in pairs:
        files = voice_files(tree, _short(h["class_name"]))
        files = [(p, u) for p, u in files if (not inc or inc.search(p)) and not (exc and exc.search(p))]
        if limit:
            files = files[:limit]
        report.per_hero[hid] = len(files)
        for rel, url in files:
            ext = Path(url.split("?")[0]).suffix or ".mp3"
            dest = cfg.voices_dir / hid / (rel + ext)
            if dest.is_file() and dest.stat().st_size > 0:
                report.skipped_existing += 1
            else:
                jobs.append((url, dest))
    report.planned = len(jobs)
    if dry_run or not jobs:
        return report
    if log:
        log(f"downloading {len(jobs)} voice lines with {workers} workers ...")
    _download_all(jobs, fetch, workers, report,
                  progress=(lambda d, n: log(f"  {d}/{n}")) if log else None)
    return report


# --------------------------------------------------------------------------- models

def glb_animation_names(path: Path) -> list[str]:
    data = Path(path).read_bytes()
    if data[:4] != b"glTF":
        raise MovielockError(f"{path} is not a GLB file")
    (chunk_len,) = struct.unpack("<I", data[12:16])
    gltf = json.loads(data[20:20 + chunk_len])
    return [a.get("name") or f"animation_{i}" for i, a in enumerate(gltf.get("animations", []))]


def model_files_for(hero: dict, model_tree: dict) -> dict[str, str]:
    """pose name -> GLB URL, found next to the hero's hero_select model."""
    select = (hero.get("models") or {}).get("hero_select")
    poses: dict[str, str] = {}
    if select:
        poses["hero_select"] = select
        marker = "/models/"
        folder = select.split(marker, 1)[1].rsplit("/", 1)[0] if marker in select else None
        if folder:
            for rel, url in flatten(model_tree):
                if rel.rsplit("/", 1)[0] == folder and rel.endswith(".glb"):
                    poses[Path(rel).stem] = url
    return poses


def fetch_models(cfg: Config, library: Library, heroes: list[str] | None, *, pose: str = "idle",
                 api: DeadlockAPI | None = None, fetch=None, log=print) -> dict[str, dict]:
    """Download one GLB per hero and record it in assets/library.local.yaml. Returns the overrides."""
    api = api or DeadlockAPI(cfg)
    fetch = fetch or _http_get
    tree = api.models()
    overrides: dict[str, dict] = {}
    for hid, h in resolve_api_heroes(api.heroes(), library, heroes):
        poses = model_files_for(h, tree)
        chosen = pose if pose in poses else ("idle" if "idle" in poses else
                                             "hero_select" if "hero_select" in poses else None)
        if not chosen:
            if log:
                log(f"{hid}: no model in the API")
            continue
        dest = cfg.assets_dir / "heroes" / hid / f"{chosen}.glb"
        if not dest.is_file():
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(fetch(poses[chosen]))
        anims = glb_animation_names(dest)
        entry: dict = {"model": dest.relative_to(cfg.assets_dir).as_posix(), "scale": 1.0,
                       # these models have no mouth shape keys or jaw bone: nod the head a little instead
                       "visemes": {}, "jaw_bone": "head", "jaw_open": 4.0, "jaw_axis": "X"}
        if anims:
            entry["animations"] = {"idle": {"action": anims[0], "loop": True}}
        overrides[hid] = entry
        if log:
            log(f"{hid}: {chosen}.glb ({', '.join(anims) or 'no animations'})")
    if overrides:
        write_local_overrides(cfg.assets_dir, {"heroes": overrides})
    return overrides


def write_local_overrides(assets_dir: Path, new: dict) -> Path:
    path = Path(assets_dir) / "library.local.yaml"
    data = {}
    if path.is_file():
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    for section, entries in new.items():
        data.setdefault(section, {})
        for key, value in entries.items():
            data[section][key] = {**(data[section].get(key) or {}), **value}
    header = ("# Local overrides for library.yaml, written by `movielock fetch-models`.\n"
              "# Merged on top of library.yaml (animations/visemes replace the base map). Not committed.\n")
    path.write_text(header + yaml.safe_dump(data, sort_keys=True), encoding="utf-8")
    return path
