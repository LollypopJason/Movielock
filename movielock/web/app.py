"""Local web app: idea -> Write script -> edit -> Validate / Render -> watch and download.

Run with `movielock web` and open http://127.0.0.1:5005. It is meant for your own machine
only (it binds to localhost by default and has no authentication).
"""

from __future__ import annotations

import threading
import time
import traceback
import uuid
from pathlib import Path

from flask import Flask, abort, jsonify, render_template, request, send_from_directory

from ..config import Config
from ..errors import MovielockError, ParseError, ValidationFailed
from ..library import load_library
from ..lockscript import dumps, loads
from ..validate import validate_doc


class Jobs:
    """In-memory background jobs (write / render) with progress for polling."""

    def __init__(self):
        self._jobs: dict[str, dict] = {}
        self._lock = threading.Lock()
        self.render_slot = threading.Semaphore(1)  # one render at a time

    def create(self, kind: str) -> dict:
        job = {"id": uuid.uuid4().hex[:12], "kind": kind, "status": "queued", "progress": 0.0,
               "message": "queued", "log": [], "result": None, "error": None, "created": time.time()}
        with self._lock:
            self._jobs[job["id"]] = job
            # forget old finished jobs
            if len(self._jobs) > 50:
                for jid in sorted(self._jobs, key=lambda k: self._jobs[k]["created"])[:10]:
                    if self._jobs[jid]["status"] in ("done", "error"):
                        self._jobs.pop(jid, None)
        return job

    def get(self, job_id: str) -> dict | None:
        with self._lock:
            job = self._jobs.get(job_id)
            return dict(job, log=list(job["log"][-200:])) if job else None

    def update(self, job: dict, **kw):
        with self._lock:
            job.update(kw)

    def log(self, job: dict, line: str):
        with self._lock:
            job["log"].append(line)


def _issues(doc, smap, cfg: Config):
    library = load_library(cfg.assets_dir, cfg.voices_dir)
    return [i.to_dict() for i in validate_doc(doc, library, smap, tts_enabled=bool(cfg["tts"].get("enabled")))]


def create_app(cfg: Config) -> Flask:
    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = 2 * 1024 * 1024
    jobs = Jobs()
    app.extensions["movielock_jobs"] = jobs

    def parse(text: str, kind: str):
        try:
            return loads(text, kind)
        except ParseError as exc:
            raise _BadScript([{"severity": "error", "message": exc.message, "line": exc.line, "path": ""}])

    @app.errorhandler(MovielockError)
    def _err(exc):
        return jsonify({"error": str(exc)}), 400

    @app.get("/")
    def index():
        return render_template("index.html")

    @app.get("/api/library")
    def library():
        lib = load_library(cfg.assets_dir, cfg.voices_dir)
        return jsonify({
            "heroes": [{"id": h.id, "name": h.name, "color": h.color, "animations": list(h.animations),
                        "model": bool(h.model and h.model.is_file())} for h in lib.heroes.values()],
            "maps": [{"id": m.id, "name": m.name} for m in lib.maps.values()],
            "sounds": list(lib.sounds), "music": list(lib.music),
            "voices": [{"id": v.id, "hero": v.hero, "transcript": v.transcript, "file": v.file.is_file()}
                       for v in lib.voices.values()],
            "tts": bool(cfg["tts"].get("enabled")),
            "writer_model": cfg["writer"].get("model"),
        })

    @app.post("/api/validate")
    def validate():
        body = request.get_json(force=True, silent=True) or {}
        kind = body.get("kind", "lockscript")
        try:
            doc, smap = parse(body.get("script", ""), kind)
        except _BadScript as bad:
            return jsonify({"ok": False, "issues": bad.issues})
        issues = _issues(doc, smap, cfg)
        return jsonify({"ok": not any(i["severity"] == "error" for i in issues), "issues": issues})

    @app.post("/api/convert")
    def convert():
        body = request.get_json(force=True, silent=True) or {}
        try:
            doc, _ = parse(body.get("script", ""), body.get("kind", "lockscript"))
        except _BadScript as bad:
            return jsonify({"error": bad.issues[0]["message"], "issues": bad.issues}), 400
        to = body.get("to", "yaml")
        if to not in ("lockscript", "yaml", "json"):
            abort(400)
        return jsonify({"script": dumps(doc, to), "kind": to})

    @app.post("/api/write")
    def write():
        body = request.get_json(force=True, silent=True) or {}
        idea = (body.get("idea") or "").strip()
        if not idea:
            return jsonify({"error": "describe your idea first"}), 400
        fmt = body.get("format") if body.get("format") in ("video", "reel", "square") else None
        job = jobs.create("write")

        def run():
            from ..writer import write_script
            jobs.update(job, status="running", message="writing script", progress=0.1)
            try:
                lib = load_library(cfg.assets_dir, cfg.voices_dir)
                res = write_script(idea, lib, cfg, fmt=fmt, log=lambda m: jobs.log(job, m))
                jobs.update(job, status="done", progress=1.0, message="script written", result={
                    "script": res.script, "notes": res.notes, "valid": res.valid, "attempts": res.attempts,
                    "issues": [i.to_dict() for i in res.issues]})
            except MovielockError as exc:
                jobs.update(job, status="error", error=str(exc), message="failed")
            except Exception as exc:  # noqa: BLE001
                jobs.update(job, status="error", error=f"{type(exc).__name__}: {exc}", message="failed")
                jobs.log(job, traceback.format_exc())

        threading.Thread(target=run, daemon=True).start()
        return jsonify({"job": job["id"]})

    @app.post("/api/render")
    def render():
        from ..render.pipeline import render_doc
        body = request.get_json(force=True, silent=True) or {}
        try:
            doc, smap = parse(body.get("script", ""), body.get("kind", "lockscript"))
        except _BadScript as bad:
            return jsonify({"error": "fix the script first", "issues": bad.issues}), 400
        issues = _issues(doc, smap, cfg)
        if any(i["severity"] == "error" for i in issues):
            return jsonify({"error": "fix the script first", "issues": issues}), 400
        quality = body.get("quality", "preview")
        renderer = body.get("renderer") or None
        if quality not in cfg["quality"] or renderer not in (None, "auto", "blender", "storyboard"):
            abort(400)
        job = jobs.create("render")
        name = (doc.get("title") or "movie")[:60]
        out_name = f"{_safe(name)}_{quality}_{job['id']}.mp4"

        def run():
            jobs.update(job, status="waiting", message="waiting for the previous render")
            with jobs.render_slot:
                jobs.update(job, status="running", message="starting")
                try:
                    result = render_doc(doc, cfg, name=name, quality=quality, renderer=renderer, smap=smap,
                                        out=cfg.renders_dir / out_name,
                                        progress=lambda f, m: jobs.update(job, progress=f, message=m),
                                        log=lambda m: jobs.log(job, m))
                    jobs.update(job, status="done", progress=1.0, message="done", result={
                        "video": f"/videos/{result.output.name}", "renderer": result.renderer,
                        "duration": result.duration, "warnings": result.warnings})
                except ValidationFailed as exc:
                    jobs.update(job, status="error", error=str(exc), message="failed")
                except MovielockError as exc:
                    jobs.update(job, status="error", error=str(exc), message="failed")
                except Exception as exc:  # noqa: BLE001
                    jobs.update(job, status="error", error=f"{type(exc).__name__}: {exc}", message="failed")
                    jobs.log(job, traceback.format_exc())

        threading.Thread(target=run, daemon=True).start()
        return jsonify({"job": job["id"], "warnings": [i for i in issues if i["severity"] == "warning"]})

    @app.get("/api/jobs/<job_id>")
    def job_status(job_id):
        job = jobs.get(job_id)
        if job is None:
            abort(404)
        return jsonify(job)

    @app.get("/videos/<path:name>")
    def videos(name):
        if "/" in name or not name.endswith(".mp4"):
            abort(404)
        return send_from_directory(Path(cfg.renders_dir).resolve(), name,
                                   as_attachment=request.args.get("download") == "1")

    return app


class _BadScript(Exception):
    def __init__(self, issues):
        self.issues = issues


def _safe(text: str) -> str:
    import re
    return re.sub(r"[^A-Za-z0-9_-]+", "_", text).strip("_") or "movie"
