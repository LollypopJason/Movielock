import shutil
from pathlib import Path

import pytest

from movielock.config import load_config
from movielock.demo import build_demo
from movielock.library import load_library

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def _repo_cwd(monkeypatch):
    monkeypatch.chdir(ROOT)


@pytest.fixture(scope="session")
def demo_root(tmp_path_factory) -> Path:
    root = tmp_path_factory.mktemp("demo")
    build_demo(root)
    return root


@pytest.fixture
def demo_config(demo_root):
    return load_config(demo_root / "movielock.yaml")


@pytest.fixture
def demo_library(demo_config):
    return load_library(demo_config.assets_dir, demo_config.voices_dir)


def has_ffmpeg() -> bool:
    return bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


needs_ffmpeg = pytest.mark.skipif(not has_ffmpeg(), reason="ffmpeg not installed")
