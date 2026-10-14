"""Persistent, bounded analysis jobs; deliberately separate from matching agents."""
from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import signal
import subprocess
import threading
import time
from pathlib import Path

from .. import toolchains
from .inputs import InputError, MAX_FILE, inspect

CAPABILITIES = {"can_analyze": True, "can_draft": True, "can_reconstruct": True, "can_verify": False, "can_export_game": False}
ACTIVE = {"queued", "analyzing", "drafting"}
SLUG = re.compile(r"^[a-f0-9]{64}$")


def write_json(path: Path, data: dict) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    tmp.replace(path)


def stop_process(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    try:
        os.killpg(proc.pid, signal.SIGTERM)
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGKILL)
        proc.wait(timeout=5)
    except ProcessLookupError:
        pass


class AnalysisEngine:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.catalog_cache = {}
        self.record_cache = {}
        self.running: dict[str, tuple[threading.Thread, threading.Event]] = {}
        for path in self.root.glob("*/project.json"):
            try:
                data = json.loads(path.read_text())
                if data["state"] in ACTIVE:
                    data.update(state="interrupted", error="The app stopped during analysis. Resume to continue.")
                    write_json(path, data)
            except (ValueError, KeyError, OSError):
                continue

    def directory(self, slug: str) -> Path:
        if not SLUG.fullmatch(slug):
            raise InputError("invalid analysis project id")
        path = self.root / slug
        if not (path / "project.json").is_file():
            raise InputError("analysis project does not exist")
        return path

    def metadata(self, slug: str) -> dict:
        return json.loads((self.directory(slug) / "project.json").read_text())

    def update(self, slug: str, **fields) -> None:
        with self.lock:
            data = self.metadata(slug)
            data.update(fields, updated=time.time())
            write_json(self.directory(slug) / "project.json", data)

    def projects(self) -> list[dict]:
        return sorted((self.snapshot(p.parent.name, details=False) for p in self.root.glob("*/project.json")
                       if SLUG.fullmatch(p.parent.name)), key=lambda p: p["updated"], reverse=True)

    def import_file(self, source: str, title: str = "") -> dict:
        info = inspect(Path(source))
        slug = info["sha256"]
        with self.lock:
            dest = self.root / slug
            if (dest / "project.json").exists():
                return self.snapshot(slug)
            dest.mkdir(exist_ok=True)
            suffix = ".nso" if info["format"] == "NSO" else ".elf"
            target = dest / ("module" + suffix)
            with Path(info.pop("path")).open("rb") as src, target.open("wb") as dst:
                remaining = MAX_FILE + 1
                while remaining:
                    chunk = src.read(min(1 << 20, remaining))
                    if not chunk:
                        break
                    dst.write(chunk)
                    remaining -= len(chunk)
            # Validate the actual snapshot, including detecting source changes during copy.
            copied = inspect(target)
            if copied["sha256"] != slug:
                target.unlink()
                raise InputError("source changed while copying; retry the import")
            data = info | {"slug": slug, "title": (title.strip() or info["source_name"])[:160],
                           "kind": "analysis", "platform": "switch", "capabilities": CAPABILITIES,
                           "state": "imported", "input": target.name, "updated": time.time(),
                           "limit": 64, "error": None, "verified_matches": 0}
            write_json(dest / "project.json", data)
            return self.snapshot(slug)

    def _records(self, path, details):
        try:
            stat = path.stat()
            stamp = (stat.st_mtime_ns, stat.st_size)
            cached = self.record_cache.get(path)
            if not details and cached and cached[0] == stamp:
                return cached[1]
            rows = []
            with path.open(encoding="utf-8") as stream:
                for line in stream:
                    try:
                        row = json.loads(line)
                    except ValueError:
                        continue
                    if not details and row.get("type") == "function":
                        row = {k: row.get(k) for k in ("type", "name", "address", "relative_address", "size", "status", "time")}
                    rows.append(row)
            if not details:
                self.record_cache[path] = (stamp, rows)
            return rows
        except FileNotFoundError:
            # A completed batch may be rotated while the UI polls it.
            return []

    def snapshot(self, slug: str, details: bool = True) -> dict:
        data = self.metadata(slug)
        functions, summary, complete, events, current = {}, {}, False, [], None
        directory = self.directory(slug)
        base = directory / "base-functions.jsonl"
        paths = [base, directory / "functions.jsonl"]
        for path in paths:
            if not path.exists():
                continue
            for row in self._records(path, details):
                kind = row.get("type")
                if kind == "summary":
                    summary = row
                elif kind == "complete":
                    complete = True
                elif kind == "started":
                    current = row
                    events.append(row)
                elif kind == "function":
                    if path != base:
                        events.append({k: row.get(k) for k in ("type", "name", "address", "status", "time")})
                    if current and current["address"] == row["address"]:
                        current = None
                    if not details:
                        row = {k: row[k] for k in ("name", "address", "relative_address", "size", "status")}
                    functions[row["address"]] = row
        if not summary:
            previous = directory / "previous-functions.jsonl"
            if previous.exists():
                with previous.open() as f:
                    try:
                        first = json.loads(f.readline())
                        if first.get("type") == "summary":
                            summary = first
                    except ValueError:
                        pass
        catalog = directory / "catalog.json"
        rows = list(functions.values())
        return data | {"capabilities": CAPABILITIES, "catalog_version": catalog.stat().st_mtime_ns if catalog.exists() else 0,
                       "summary": summary, "functions": rows, "export_complete": complete,
                       "current_function": current if data["state"] in ACTIVE else None,
                       "events": events, "exported": len(rows),
                       "drafted": sum(f["status"] == "drafted" for f in rows),
                       "failed": sum(f["status"] == "failed" for f in rows)}

    def catalog(self, slug: str) -> list[dict]:
        path = self.directory(slug) / "catalog.json"
        if not path.exists():
            return [{k: f[k] for k in ("name", "address", "size")} for f in self.snapshot(slug, False)["functions"]]
        stamp = path.stat().st_mtime_ns
        cached = self.catalog_cache.get(slug)
        if not cached or cached[0] != stamp:
            cached = (stamp, json.loads(path.read_text()))
            self.catalog_cache[slug] = cached
        return cached[1]

    def tool_status(self) -> dict:
        base = toolchains.path("ghidra")
        loader = any(base.glob("Ghidra/Extensions/*Switch*/extension.properties"))
        arch = {"arm64": "arm_64", "aarch64": "arm_64", "x86_64": "x86_64"}.get(platform.machine(), platform.machine())
        host = "mac" if platform.system() == "Darwin" else "linux"
        native = base / "Ghidra" / "Features" / "Decompiler" / "os" / f"{host}_{arch}" / "decompile"
        return {"ghidra_installed": toolchains.installed("ghidra") is not None,
                "java_version": toolchains.java_version(), "switch_loader_installed": loader,
                "native_decompiler_available": os.access(native, os.X_OK),
                "supported_host": os.name == "posix"}

    def start(self, slug: str, limit: int = 64, continuous: bool = False) -> dict:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 500:
            raise InputError("choose a checkpoint size between 1 and 500")
        if not isinstance(continuous, bool):
            raise InputError("continuous must be true or false")
        with self.lock:
            data = self.metadata(slug)
            if any(t.is_alive() for t, _ in self.running.values()):
                raise InputError("one analysis can run at a time; wait or cancel the active job")
            status = self.tool_status()
            if not status["supported_host"]:
                raise InputError("Switch analysis currently supports macOS and Linux")
            if not status["ghidra_installed"] or status["java_version"] < 21:
                raise InputError("install Ghidra and Java 21+ in Compilers & tools before analyzing")
            if not status["native_decompiler_available"]:
                raise InputError("Ghidra's native decompiler is missing for this host; see docs/switch-analysis.md")
            if data["format"] == "NSO" and not status["switch_loader_installed"]:
                raise InputError("install a Ghidra Switch Loader built for this Ghidra version first")
            event = threading.Event()
            thread = threading.Thread(target=self._run_batches, args=(slug, limit, event, continuous), daemon=True)
            self.running[slug] = (thread, event)
            self.update(slug, state="queued", limit=limit, error=None, job_started=time.time(), continuous=continuous,
                        run_start_offset=self.snapshot(slug, False)["exported"],
                        run_mode="continuous" if continuous else "checkpoint")
            thread.start()
        return self.snapshot(slug, details=False)

    def cancel(self, slug: str) -> dict:
        self.directory(slug)
        with self.lock:
            if slug in self.running:
                self.running[slug][1].set()
        return self.snapshot(slug, details=False)

    def stop(self):
        for thread, event in list(self.running.values()):
            event.set()
        for thread, _ in list(self.running.values()):
            thread.join(6)

    def _run_batches(self, slug, limit, cancel, continuous):
        try:
            while not cancel.is_set():
                before = self.snapshot(slug, False)["exported"]
                self._run(slug, limit, cancel)
                after = self.snapshot(slug, False)
                if not continuous or after["state"] != "completed" or cancel.is_set():
                    break
                if after["exported"] <= before:
                    break
                done = {f["address"] for f in after["functions"]}
                if all(f["address"] in done for f in self.catalog(slug)):
                    break
                self.update(slug, state="queued", phase="Continuing with the next checkpoint")
            if cancel.is_set():
                self.update(slug, state="cancelled", phase="Stopped", error="Stopped; completed decompilations retained.")
        except Exception as e:
            self.update(slug, state="error", error=str(e))
        finally:
            self.update(slug, continuous=False)

    def _run(self, slug: str, limit: int, cancel: threading.Event) -> None:
        proc = None
        try:
            directory = self.directory(slug)
            data = self.metadata(slug)
            source = directory / data["input"]
            with source.open("rb") as f:
                if hashlib.file_digest(f, "sha256").hexdigest() != slug:
                    raise InputError("local executable changed; import it as a new project")
            project_dir = directory / "ghidra"
            project_dir.mkdir(exist_ok=True)
            output = directory / "functions.jsonl"
            # Preserve the previous sample without presenting it as the new job's result.
            previous = self.snapshot(slug)
            offset = previous["exported"]
            base = directory / "base-functions.jsonl"
            base_tmp = base.with_suffix(".tmp")
            with base_tmp.open("w", encoding="utf-8") as f:
                if previous["summary"]:
                    f.write(json.dumps(previous["summary"]) + "\n")
                for row in previous["functions"]:
                    f.write(json.dumps(row) + "\n")
            base_tmp.replace(base)
            if output.exists():
                output.replace(directory / "previous-functions.jsonl")
            script = Path(__file__).with_name("SwitchExport.java")
            # Loader/tool changes invalidate saved analysis. Exporter changes can reuse the program.
            fingerprint = hashlib.sha256(b"switch-analysis-v2")
            fingerprint.update(toolchains.get("ghidra").source.encode())
            for jar in sorted(toolchains.path("ghidra").glob("Ghidra/Extensions/*Switch*/lib/*.jar")):
                with jar.open("rb") as f:
                    fingerprint.update(hashlib.file_digest(f, "sha256").digest())
            signature = fingerprint.hexdigest()
            resumed = ((project_dir / "switch.gpr").exists() and data.get("analysis_saved", False)
                       and data.get("analysis_signature") == signature)
            argv = [str(toolchains.path("ghidra") / "support" / "analyzeHeadless"), str(project_dir), "switch"]
            argv += ["-process", source.name, "-noanalysis"] if resumed else ["-import", str(source), "-overwrite"]
            argv += ["-analysisTimeoutPerFile", "300", "-max-cpu", "2", "-scriptPath", str(script.parent),
                     "-postScript", script.name, str(output), str(limit), str(offset)]
            self.update(slug, state="analyzing", batch_offset=offset, phase="Opening saved program" if resumed else "Loading module and discovering functions", analysis_scope="bounded discovery; up to 300 seconds")
            started = time.monotonic()
            started_wall = time.time()
            # JAVA_TOOL_OPTIONS applies to the JVM, not the user's shell or system configuration.
            env = os.environ.copy()
            env["JAVA_TOOL_OPTIONS"] = "-Xmx2g -XX:ActiveProcessorCount=2"
            env["GHIDRA_HEADLESS_MAXMEM"] = "2G"
            with (directory / "analysis.log").open("w", encoding="utf-8") as log:
                proc = subprocess.Popen(argv, stdout=log, stderr=subprocess.STDOUT, env=env,
                                        start_new_session=True, cwd=directory)
                while proc.poll() is None:
                    if cancel.wait(0.25):
                        stop_process(proc)
                        self.update(slug, state="cancelled", error="Analysis cancelled; partial results retained.")
                        return
                    if time.monotonic() - started > 900:
                        stop_process(proc)
                        self.update(slug, state="error", error="Analysis exceeded the 15-minute job limit; partial results retained.")
                        return
                    if output.exists() and self.metadata(slug)["state"] == "analyzing":
                        self.update(slug, state="drafting", phase="Decompiling functions")
                result = self.snapshot(slug)
                if proc.returncode or not result["export_complete"] or not output.exists() or output.stat().st_mtime < started_wall:
                    raise InputError("Ghidra did not finish the export; inspect the project's analysis.log")
            self.update(slug, state="completed", phase="Batch complete", analysis_saved=True, error=None,
                        analysis_signature=signature,
                        discovery_timed_out=(data.get("discovery_timed_out", False) if resumed else
                                             "REPORT: Analysis timed out" in (directory / "analysis.log").read_text(errors="replace")),
                        ghidra_source=toolchains.get("ghidra").source)
        except Exception as e:
            if proc and proc.poll() is None:
                stop_process(proc)
            self.update(slug, state="error", error=str(e))
