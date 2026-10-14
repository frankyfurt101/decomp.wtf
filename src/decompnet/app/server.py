"""The local decomp.wtf server: the GUI, its API, and the MCP endpoint agents connect to.

It listens on 127.0.0.1 only. The GUI needs a per-launch secret (handed to the browser in the URL the
app opens, then kept in a same-site cookie); agents need their own bearer token. Host headers are
checked on every request, so web pages can't reach it through DNS rebinding.
"""

from __future__ import annotations

import json
import mimetypes
import re
import os
import secrets
import shutil
import subprocess
import sys
import threading
import time
import uuid
from importlib import resources
from pathlib import Path

import anyio
from mcp.server.mcpserver import Context
from mcp.server.transport_security import TransportSecuritySettings
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

from .. import __version__, toolchains
from ..network import NetworkError
from ..rom.n64 import RomError
from . import agents, chat, collab, connect, tools
from .engine import COMPILERS, MW_VERSIONS, SHORT_NAMES, TOOLS, RomNetwork

COOKIE = "decomp_live"
MAX_DISC = 16 * 1024 * 1024 * 1024  # dual-layer DVD and then some


class Jobs:
    """Background work the GUI can follow: imports and installs."""

    def __init__(self):
        self.jobs: dict[str, dict] = {}

    def start(self, kind: str, fn) -> str:
        jid = uuid.uuid4().hex[:12]
        job = {"id": jid, "kind": kind, "state": "running", "log": [], "result": None, "error": None, "t": time.time()}
        self.jobs[jid] = job

        def log(line: str) -> None:
            job["log"].append(str(line))

        def run():
            try:
                job["result"] = fn(log)
                job["state"] = "done"
            except (NetworkError, RomError, toolchains.ToolchainError, ValueError, RuntimeError, OSError) as e:
                job["error"], job["state"] = str(e), "error"
            except subprocess.CalledProcessError as e:
                said = e.stderr or e.output or ""  # what the command said, so the error shows why
                said = (said.decode("utf-8", "replace") if isinstance(said, bytes) else said).strip()
                job["error"], job["state"] = f"command failed ({e.returncode})" + (f": {said[-400:]}" if said else ""), "error"
        threading.Thread(target=run, daemon=True, name=f"job-{kind}").start()
        return jid


class Settings:
    def __init__(self, path: Path):
        self.path = path
        try:
            self.data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.data = {}

    def save(self) -> None:
        self.path.write_text(json.dumps(self.data, indent=2), encoding="utf-8")
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass

    def public(self) -> dict:
        api = dict(self.data.get("api", {}))
        if api.get("api_key"):
            api["api_key"] = "•" * 8 + api["api_key"][-4:]
            api["has_key"] = True
        return {"api": api, "per_session": self.data.get("per_session", agents.PER_SESSION), "last_project": self.data.get("last_project"),
                "autopilot": self.data.get("autopilot", True), "budget_usd": self.data.get("budget_usd") or 0,
                "launcher": _launcher(self.data.get("launcher") or {}), "recent_models": self.data.get("recent_models") or {}}


def _launcher(saved: dict) -> dict:
    """The last launcher choice; one saved for Gemini CLI (retired) comes back as Antigravity with its default model."""
    if saved.get("kind") and agents.kind_id(saved["kind"]) != saved["kind"]:
        return {**saved, "kind": agents.kind_id(saved["kind"]), "model": ""}
    return saved


def _static(name: str) -> bytes | None:
    if ".." in name or name.startswith(("/", "\\")):
        return None
    try:
        return resources.files("decompnet.app").joinpath("static", name).read_bytes()
    except (FileNotFoundError, IsADirectoryError, OSError):
        return None


def open_folder(path: Path) -> None:
    if sys.platform == "win32":
        os.startfile(str(path))  # noqa: S606 (opening the user's own folder)
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def build(net: RomNetwork, home: Path, port: int, secret: str):
    mcp_url = f"http://127.0.0.1:{port}/mcp"
    manager = agents.Manager(net, home, mcp_url)
    jobs = Jobs()
    settings = Settings(home / "settings.json")
    manager.budget = lambda: float(settings.data.get("budget_usd") or 0)

    def remember(running: list[dict]) -> None:
        settings.data["running_agents"] = running
        settings.save()
    manager.persist = remember
    allowed_hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}

    def agent_of(ctx: Context):
        headers = ctx.headers or {}
        auth = headers.get("authorization") or headers.get("Authorization") or ""
        agent = net.authenticate(auth.removeprefix("Bearer ").strip() or None)
        slug = manager.project_of(agent.id) or settings.data.get("last_project")
        if not slug:
            raise NetworkError("no project is open in decomp.wtf; open a ROM in the app first")
        return agent, slug

    threading.Thread(target=collab.local_addresses, daemon=True).start()  # `tailscale status` can take seconds
    # rooms this computer belongs to reconnect (members keep their place; their agents wait while the host is away)
    net.restore_links()

    # helpers that were working when the app last stopped carry on
    resume = [r for r in settings.data.get("running_agents", []) if isinstance(r, dict)]
    for r in resume:
        try:
            net.project_row(r["slug"])
            options = dict(r.get("options") or {})
            if r["kind"] == "api":
                options.update({k: v for k, v in settings.data.get("api", {}).items() if k == "api_key"})
            manager.start(r["kind"], r["slug"], 1, options)
        except (NetworkError, ValueError, KeyError):
            continue

    mcp = tools.build_mcp(net, agent_of)
    chat.register(mcp, net, manager, jobs, settings, port, secret)

    # Each agent sees the tools for its job: the agent you talk to sees everything; a helper only the work tools
    # for its project's kind (fewer tool definitions in every request, nothing it shouldn't call).
    from mcp_types import ListToolsResult, PaginatedRequestParams

    async def scoped_tools(ctx, params):
        everything = await mcp.list_tools()
        auth = (getattr(ctx.request, "headers", None) or {}).get("authorization", "")
        try:
            agent = net.authenticate(auth.removeprefix("Bearer ").strip() or None)
        except NetworkError:
            return ListToolsResult(tools=everything)
        if net.is_chat(agent):
            return ListToolsResult(tools=everything)
        slug = manager.project_of(agent.id) or settings.data.get("last_project")
        keep = set(tools.tool_names(bool(slug and net.fork_slug(slug))))
        return ListToolsResult(tools=[t for t in everything if t.name in keep])

    mcp._lowlevel_server.add_request_handler("tools/list", PaginatedRequestParams, scoped_tools)

    def guard(request: Request, write: bool = False) -> Response | None:
        if request.headers.get("host") not in allowed_hosts:
            return JSONResponse({"error": "bad host"}, status_code=403)
        if not secrets.compare_digest(request.cookies.get(COOKIE, ""), secret):
            return JSONResponse({"error": "open decomp.wtf from the app or the `decomp` command"}, status_code=401)
        if write and request.headers.get("x-decomp") != "1":
            return JSONResponse({"error": "missing header"}, status_code=403)
        return None

    async def body(request: Request) -> dict:
        try:
            data = await request.json()
            return data if isinstance(data, dict) else {}
        except ValueError:
            return {}

    from ..analysis.routes import register as register_analysis
    register_analysis(mcp, home.parent, guard, _static)

    async def call(fn, *args):
        return await anyio.to_thread.run_sync(fn, *args)

    def fail(e: Exception, code: int = 400) -> JSONResponse:
        return JSONResponse({"error": str(e)}, status_code=code)

    # ------------------------------------------------------------------ pages

    @mcp.custom_route("/", methods=["GET"])
    async def index(request: Request) -> Response:
        if request.headers.get("host") not in allowed_hosts:
            return JSONResponse({"error": "bad host"}, status_code=403)
        key = request.query_params.get("k")
        if key:
            if not secrets.compare_digest(key, secret):
                return HTMLResponse("This link is from an earlier session. Open decomp.wtf again.", status_code=401)
            resp = RedirectResponse("/switch" if request.query_params.get("view") == "switch" else "/", status_code=303)
            resp.set_cookie(COOKIE, secret, httponly=True, samesite="strict", max_age=30 * 86400)
            return resp
        if not secrets.compare_digest(request.cookies.get(COOKIE, ""), secret):
            return HTMLResponse("<!doctype html><meta charset=utf-8><title>decomp.wtf</title>"
                                "<body style='font:16px system-ui;background:#0b0c0e;color:#e8e6e1;padding:3rem'>"
                                "Open decomp.wtf from its app icon or by running <code>decomp</code>.", status_code=401)
        return HTMLResponse(_static("index.html").decode(), headers={"Cache-Control": "no-store"})

    @mcp.custom_route("/assets/{name:path}", methods=["GET"])
    async def asset(request: Request) -> Response:
        if request.headers.get("host") not in allowed_hosts:
            return Response(status_code=403)
        name = request.path_params["name"]
        data = _static(name)
        if data is None:
            return Response(status_code=404)
        ctype = mimetypes.guess_type(name)[0] or "application/octet-stream"
        return Response(data, media_type=ctype, headers={"Cache-Control": "no-cache"})

    @mcp.custom_route("/health", methods=["GET"])
    async def health(request: Request) -> JSONResponse:
        return JSONResponse({"ok": True, "app": "decomp-wtf", "version": __version__})

    # ------------------------------------------------------------------ state

    def state() -> dict:
        projects = []
        for p in net.store.all("SELECT slug FROM projects ORDER BY id DESC"):
            try:
                projects.append(net.overview(p["slug"]))
            except (NetworkError, OSError, KeyError):
                continue
        tcs = [t for t in toolchains.status() if t["id"] in COMPILERS or t["id"] in TOOLS]
        for t in tcs:
            t["use"] = COMPILERS.get(t["id"]) or TOOLS[t["id"]]
            t["name"] = SHORT_NAMES.get(t["id"], t["name"])
        return {"version": __version__, "platform": toolchains.current_platform(), "home": str(home.parent),
                "projects": projects, "toolchains": tcs, "agents": agents.detect(),
                "api_presets": agents.API_PRESETS, "settings": settings.public(), "mcp_url": mcp_url,
                "codewarrior": {"installed": toolchains.installed("mwcc") is not None,
                                "ds_installed": toolchains.installed("mwccarm") is not None, **MW_VERSIONS}}

    def project_list() -> list[dict]:
        """The sidebar: every project with its progress and how many helper agents work on it right now."""
        running: dict[str, int] = {}
        for s in manager.sessions.values():
            if s.state in ("starting", "working", "waiting"):
                running[s.slug] = running.get(s.slug, 0) + 1
        out = []
        for p in net.store.all("SELECT slug FROM projects ORDER BY id DESC"):
            try:
                ov = net.overview(p["slug"])
            except (NetworkError, OSError, KeyError):
                continue
            out.append({k: ov.get(k) for k in ("slug", "title", "kind", "origin", "platform", "platform_name", "ready",
                                                "functions", "matched", "bytes", "matched_bytes", "last_activity")}
                       | {"working": running.get(p["slug"], 0), "room": (ov.get("room") or {}).get("role"),
                          "autopilot": (ov.get("autopilot") or {}).get("state")})
        return out

    @mcp.custom_route("/api/projects", methods=["GET"])
    async def api_projects(request: Request) -> Response:
        return guard(request) or JSONResponse(await call(project_list))

    @mcp.custom_route("/api/state", methods=["GET"])
    async def api_state(request: Request) -> Response:
        return guard(request) or JSONResponse(await call(state))

    @mcp.custom_route("/api/import", methods=["POST"])
    async def api_import(request: Request) -> Response:
        """A game the user owns: the demo, an upload streamed to disk (discs are gigabytes), or a local path."""
        if (bad := guard(request, write=True)):
            return bad
        q = request.query_params
        toolchain = q.get("toolchain") or None
        if q.get("demo") == "1":
            game: bytes | Path | None = _static("demo/demo-quest-64.z64")
            if game is None:
                return fail(ValueError("the demo ROM is missing from this install"))
            name = "demo-quest-64.z64"
        else:
            if q.get("confirmed") != "1":
                return fail(ValueError("confirm that this is your own legally obtained copy of the game"))
            if q.get("path"):
                game = Path(q["path"].strip().strip('"')).expanduser()
                if not game.is_file():
                    return fail(ValueError(f"no file at {game}"))
                name = game.name
            else:
                name = Path(q.get("name") or "game.bin").name
                upload_dir = home / "uploads" / uuid.uuid4().hex[:8]  # keeps the file's own name
                upload_dir.mkdir(parents=True, exist_ok=True)
                game = upload_dir / name
                size = 0
                with open(game, "wb") as f:
                    async for chunk in request.stream():
                        size += len(chunk)
                        if size > MAX_DISC:
                            f.close()
                            game.unlink(missing_ok=True)
                            return fail(ValueError("that file is larger than any game disc"))
                        f.write(chunk)
                if size == 0:
                    game.unlink(missing_ok=True)
                    return fail(ValueError("the file was empty"))
        uploaded = isinstance(game, Path) and game.parent.parent == home / "uploads"

        def work(log):
            try:
                slug = net.import_rom(game, toolchain=toolchain, confirmed=True, name=name, log=log)
            finally:
                if uploaded:
                    shutil.rmtree(game.parent, ignore_errors=True)
            settings.data["last_project"] = slug
            settings.save()
            if settings.data.get("autopilot", True) and not net.fork_slug(slug):  # free matches first, in the background
                try:
                    net.start_autopilot(slug)
                except NetworkError:
                    pass
            return {"slug": slug}
        return JSONResponse({"job": jobs.start("import", work)})

    @mcp.custom_route("/api/job", methods=["GET"])
    async def api_job(request: Request) -> Response:
        if (bad := guard(request)):
            return bad
        job = jobs.jobs.get(request.query_params.get("id", ""))
        return JSONResponse(job) if job else fail(ValueError("no such job"), 404)

    # ------------------------------------------------------------------ projects

    def with_project(fn):
        async def handler(request: Request) -> Response:
            if (bad := guard(request, write=request.method == "POST")):
                return bad
            data = await body(request) if request.method == "POST" else {}
            slug = data.get("slug") or request.query_params.get("slug", "")
            try:
                return JSONResponse(await call(lambda: fn(slug, request, data)))
            except (NetworkError, ValueError, toolchains.ToolchainError, OSError) as e:
                return fail(e)
        return handler

    def p_overview(slug, request, data):
        settings.data["last_project"] = slug
        settings.save()
        return net.overview(slug)

    def p_live(slug, request, data):
        q = request.query_params
        out = net.live(slug, int(q.get("e", 0) or 0), int(q.get("a", 0) or 0))
        try:
            since = json.loads(q.get("s") or "{}")
        except ValueError:
            since = {}
        out["sessions"] = [s for s in manager.snapshot(since) if s["slug"] == slug]
        return out

    def p_compiler(slug, request, data):
        net.set_compiler(slug, str(data.get("toolchain", "")), data.get("flags") or None)
        return net.overview(slug)

    def p_export(slug, request, data):
        def work(log):
            res = net.export_rom(slug, log=log)
            return {k: v for k, v in res.items() if k != "log"} | {"log": res.get("log", "")[-1500:]}
        return {"job": jobs.start("export", work)}

    def p_open(slug, request, data):
        open_folder(Path(net.project_row(slug)["root"]))
        return {"ok": True}

    def p_delete(slug, request, data):
        manager.stop()
        proj = net.project_row(slug)
        net.remove_project(slug)
        if data.get("files"):
            shutil.rmtree(proj["root"], ignore_errors=True)
        if settings.data.get("last_project") == slug:
            settings.data["last_project"] = None
            settings.save()
        return {"ok": True}

    def p_manual(slug, request, data):
        name = f"manual-{secrets.token_hex(2)}"
        agent, token = net.local_agent(name)
        manager.assignments[agent.id] = slug
        return {"agent": name, "url": mcp_url, "token": token,
                "claude": f'claude mcp add --transport http decomp {mcp_url} --header "Authorization: Bearer {token}"',
                "json": json.dumps({"mcpServers": {"decomp": {"type": "http", "url": mcp_url,
                                                              "headers": {"Authorization": f"Bearer {token}"}}}}, indent=2)}

    def p_try(slug, request, data):
        """You, editing a function in the GUI: judged like an agent's attempt (and credited to "you")."""
        agent, _ = net.local_agent("you")
        res = net.attempt(agent, str(data.get("name", "")), str(data.get("source", "")), slug)
        return {"score": res["score"], "matched": res["matched"], "text": res["text"][-4000:]}

    def p_release(slug, request, data):
        agent, _ = net.local_agent("you")
        try:
            net.release(agent, str(data.get("name", "")), "", slug)
        except NetworkError:
            pass  # not yours (any more): nothing to give back
        return {"ok": True}

    def p_instructions(slug, request, data):
        net.set_instructions(slug, str(data.get("text", "")))
        return net.overview(slug)

    def p_priority(slug, request, data):
        return {"priority": net.set_priority(slug, str(data.get("name", "")), bool(data.get("on", True)))}

    def p_note(slug, request, data):
        """A note from you on a function (or the whole project): agents read notes when they claim it."""
        agent, _ = net.local_agent("you")
        text = str(data.get("text", "")).strip()
        if not text:
            raise ValueError("write the note first")
        return {"message": net.add_note(agent, slug, text[:2000], str(data.get("function", "")) or None)}

    def p_autopilot(slug, request, data):
        if data.get("action") == "stop":
            net.stop_autopilot(slug)
        else:
            net.start_autopilot(slug)
        return net.overview(slug)

    routes = {
        "/api/project": (p_overview, ["GET"]), "/api/map": (lambda s, r, d: net.function_map(s), ["GET"]),
        "/api/live": (p_live, ["GET"]),
        "/api/function": (lambda s, r, d: net.function_detail(s, r.query_params.get("name", "")), ["GET"]),
        "/api/diff": (lambda s, r, d: net.function_diff(s, r.query_params.get("name", ""),
                                                        r.query_params.get("which", "latest")), ["GET"]),
        "/api/compiler": (p_compiler, ["POST"]), "/api/export": (p_export, ["POST"]), "/api/open": (p_open, ["POST"]),
        "/api/delete": (p_delete, ["POST"]), "/api/agents/manual": (p_manual, ["POST"]),
        "/api/try": (p_try, ["POST"]), "/api/release": (p_release, ["POST"]),
        "/api/autopilot": (p_autopilot, ["POST"]), "/api/instructions": (p_instructions, ["POST"]),
        "/api/priority": (p_priority, ["POST"]), "/api/note": (p_note, ["POST"]),
        "/api/stats": (lambda s, r, d: net.stats(s), ["GET"]),
    }
    for path, (fn, methods) in routes.items():
        mcp.custom_route(path, methods=methods)(with_project(fn))

    # ------------------------------------------------------------------ forks of public decomps

    @mcp.custom_route("/api/catalog", methods=["GET"])
    async def api_catalog(request: Request) -> Response:
        if (bad := guard(request)):
            return bad
        return JSONResponse({"projects": await call(net.forks.catalog)})

    @mcp.custom_route("/api/fork", methods=["POST"])
    async def api_fork(request: Request) -> Response:
        if (bad := guard(request, write=True)):
            return bad
        data = await body(request)

        def work(log):
            slug = net.forks.clone(str(data.get("repo", "")), name=str(data.get("name") or ""),
                                   platform=str(data.get("platform") or ""), version=data.get("version") or None, log=log)
            settings.data["last_project"] = slug
            settings.save()
            return {"slug": slug}
        return JSONResponse({"job": jobs.start("fork", work)})

    def fork_of(slug: str):
        if not net.fork_slug(slug):
            raise NetworkError(f"{slug} isn't a fork")
        return net.project_row(slug)

    @mcp.custom_route("/api/fork/game", methods=["POST"])
    async def api_fork_game(request: Request) -> Response:
        """The user's game file, streamed to disk (GameCube and Wii discs are gigabytes)."""
        if (bad := guard(request, write=True)):
            return bad
        q = request.query_params
        slug = q.get("slug", "")
        if q.get("confirmed") != "1":
            return fail(ValueError("confirm that this is your own legally obtained copy of the game"))
        try:
            proj = fork_of(slug)
        except NetworkError as e:
            return fail(e)
        tmp = Path(proj["root"]) / ".decomp-wtf" / f"upload-{uuid.uuid4().hex[:8]}.tmp"
        tmp.parent.mkdir(parents=True, exist_ok=True)
        size = 0
        with open(tmp, "wb") as f:
            async for chunk in request.stream():
                size += len(chunk)
                if size > MAX_DISC:
                    f.close()
                    tmp.unlink(missing_ok=True)
                    return fail(ValueError("that file is larger than any game disc"))
                f.write(chunk)
        name = Path(q.get("name") or "game.iso").name

        def work(log):
            try:
                return net.forks.setup(slug, tmp, name, log=log, move=True)
            finally:
                tmp.unlink(missing_ok=True)
        return JSONResponse({"job": jobs.start("fork-setup", work)})

    @mcp.custom_route("/api/fork/setup", methods=["POST"])
    async def api_fork_setup(request: Request) -> Response:
        """Set up with a game file already on this computer (`path`), or with none (`path` empty: run the build)."""
        if (bad := guard(request, write=True)):
            return bad
        data = await body(request)
        slug = str(data.get("slug", ""))
        try:
            fork_of(slug)
        except NetworkError as e:
            return fail(e)
        path = str(data.get("path") or "").strip().strip('"')
        if path:
            if not data.get("confirmed"):
                return fail(ValueError("confirm that this is your own legally obtained copy of the game"))
            src = Path(path).expanduser()
            if not src.is_file():
                return fail(ValueError(f"no file at {src}"))

        def work(log):
            if path:
                return net.forks.setup(slug, src, src.name, log=log)
            return net.forks.setup(slug, None, log=log)
        return JSONResponse({"job": jobs.start("fork-setup", work)})

    @mcp.custom_route("/api/fork/check", methods=["POST"])
    async def api_fork_check(request: Request) -> Response:
        """Pick a hand-built project up (or re-measure one): reads its objdiff.json and progress."""
        if (bad := guard(request, write=True)):
            return bad
        slug = str((await body(request)).get("slug", ""))
        try:
            fork_of(slug)
        except NetworkError as e:
            return fail(e)
        return JSONResponse({"job": jobs.start("fork-check", lambda log: net.forks.check(slug, log=log))})

    @mcp.custom_route("/api/fork/start-files", methods=["POST"])
    async def api_fork_start_files(request: Request) -> Response:
        """Give the code no source file covers source files (dtk-template projects), verified by the game's checksum."""
        if (bad := guard(request, write=True)):
            return bad
        slug = str((await body(request)).get("slug", ""))
        try:
            fork_of(slug)
        except NetworkError as e:
            return fail(e)
        return JSONResponse({"job": jobs.start("fork-start-files", lambda log: net.forks.start_files(slug, log=log))})

    mcp.custom_route("/api/fork/needs", methods=["GET"])(with_project(lambda s, r, d: net.forks.needs(fork_of(s)["slug"])))

    # ------------------------------------------------------------------ installs, agents, settings

    @mcp.custom_route("/api/toolchain", methods=["POST"])
    async def api_toolchain(request: Request) -> Response:
        if (bad := guard(request, write=True)):
            return bad
        data = await body(request)
        tid, accept = str(data.get("id", "")), bool(data.get("accept_license"))
        return JSONResponse({"job": jobs.start("toolchain", lambda log: (toolchains.install(tid, accept_license=accept, log=log)
                                                                         and {"id": tid}))})

    @mcp.custom_route("/api/agents/install", methods=["POST"])
    async def api_agent_install(request: Request) -> Response:
        if (bad := guard(request, write=True)):
            return bad
        kind = agents.KINDS.get(agents.kind_id(str((await body(request)).get("kind", ""))))
        if kind is None or not kind.install:
            return fail(ValueError("nothing to install"))
        cmd = kind.install["windows" if sys.platform == "win32" else "unix"]
        env = None
        if sys.platform == "win32":
            env = dict(os.environ, PATH=agents.search_path())  # Node.js installed since the app started counts
            if cmd.startswith("npm ") and not shutil.which("npm", path=env["PATH"]):
                return fail(ValueError(f"{kind.name} is installed with npm: install Node.js from https://nodejs.org "
                                       "first (the LTS version), then try again"))

        def work(log):
            log(f"$ {cmd}")
            if sys.platform == "win32":
                # Node.js's npm is a PowerShell script (npm.ps1) that Windows' default execution policy won't run;
                # output as UTF-8 so it reads right here; never wait at a prompt nobody sees
                argv = ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command",
                        f"[Console]::OutputEncoding = [Text.Encoding]::UTF8; {cmd}"]
            else:
                argv = ["bash", "-lc", cmd]
            proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                                    errors="replace", env=env)
            for line in proc.stdout:
                log(line.rstrip())
            if proc.wait() != 0:
                raise RuntimeError(f"the installer exited with code {proc.returncode}")
            agents._versions.pop(kind.id, None)
            return {"kind": kind.id}
        return JSONResponse({"job": jobs.start("agent-install", work)})

    @mcp.custom_route("/api/agents/start", methods=["POST"])
    async def api_agents_start(request: Request) -> Response:
        if (bad := guard(request, write=True)):
            return bad
        data = await body(request)
        kind, slug = str(data.get("kind", "")), str(data.get("slug", ""))
        options = {k: v for k, v in (data.get("options") or {}).items() if k in ("model", "per_session")}
        if kind == "api":
            api = dict(settings.data.get("api", {}))
            api.update({k: v for k, v in (data.get("options") or {}).items()
                        if k in ("provider", "base_url", "model") and v})
            if not api.get("base_url") or not api.get("model"):
                return fail(ValueError("choose a provider and model first"))
            options.update(api)
            options["provider_name"] = agents.API_PRESETS.get(api.get("provider", ""), {}).get("name", "API")
        options.setdefault("per_session", settings.data.get("per_session", agents.PER_SESSION))
        try:
            net.project_row(slug)
            ids = manager.start(kind, slug, int(data.get("count", 1)), options)
        except (NetworkError, ValueError) as e:
            return fail(e)
        # remembered, so the next start is one click; models typed in come back as suggestions
        model = str(options.get("model") or "")
        settings.data["launcher"] = {"kind": kind, "model": model, "count": max(1, min(int(data.get("count", 1)), 8))}
        if model and kind != "api":
            recent = settings.data.setdefault("recent_models", {}).setdefault(kind, [])
            settings.data["recent_models"][kind] = [model] + [m for m in recent if m != model][:7]
        settings.save()
        return JSONResponse({"started": ids})

    @mcp.custom_route("/api/connect", methods=["GET", "POST"])
    async def api_connect(request: Request) -> Response:
        """Your agents (Claude Code, Codex, ...) with decomp.wtf's tools: whether they have them, and adding them."""
        if (bad := guard(request, write=request.method == "POST")):
            return bad
        if request.method == "POST":
            kind = str((await body(request)).get("id", ""))
            try:
                msg = await anyio.to_thread.run_sync(lambda: connect.connect(kind))
            except (ValueError, OSError, subprocess.TimeoutExpired) as e:
                return fail(e)
            return JSONResponse({"ok": True, "message": msg})

        def rows():
            from concurrent.futures import ThreadPoolExecutor

            found = [a for a in agents.detect() if a["id"] in connect.LABEL]
            with ThreadPoolExecutor(len(found) or 1) as pool:  # each agent's own CLI answers; ask them all at once
                linked = list(pool.map(lambda a: a["installed"] and connect.status(a["id"]), found))
            return [{"id": a["id"], "name": a["name"], "installed": a["installed"], "connected": ok,
                     "command": connect.shown(a["id"])} for a, ok in zip(found, linked)]
        return JSONResponse({"agents": await anyio.to_thread.run_sync(rows), "bridge": connect.bridge_command()})

    @mcp.custom_route("/api/agents/token", methods=["POST"])
    async def api_agents_token(request: Request) -> Response:
        """An identity for an agent the user talks to, connecting through `decomp mcp`."""
        if (bad := guard(request, write=True)):
            return bad
        data = await body(request)
        try:
            agent, token = net.chat_agent(str(data.get("name") or "chat"))
        except NetworkError as e:
            return fail(e)
        return JSONResponse({"agent": agent.name, "token": token, "url": mcp_url})

    @mcp.custom_route("/api/agents/stop", methods=["POST"])
    async def api_agents_stop(request: Request) -> Response:
        if (bad := guard(request, write=True)):
            return bad
        data = await body(request)
        if data.get("remove"):
            manager.remove(str(data.get("id")))
        elif data.get("slug"):  # every helper on one project
            for s in list(manager.sessions.values()):
                if s.slug == data["slug"] and s.state in ("starting", "working", "waiting"):
                    manager.stop(s.id)
        else:
            manager.stop(data.get("id") or None)
        return JSONResponse({"ok": True})

    @mcp.custom_route("/api/settings", methods=["POST"])
    async def api_settings(request: Request) -> Response:
        if (bad := guard(request, write=True)):
            return bad
        data = await body(request)
        if isinstance(data.get("api"), dict):
            api = settings.data.setdefault("api", {})
            for k in ("provider", "base_url", "model"):
                if k in data["api"]:
                    api[k] = str(data["api"][k]).strip()
            if data["api"].get("api_key") and "•" not in data["api"]["api_key"]:
                api["api_key"] = str(data["api"]["api_key"]).strip()
            if data["api"].get("clear_key"):
                api.pop("api_key", None)
        if "per_session" in data:
            settings.data["per_session"] = max(1, min(int(data["per_session"]), 20))
        if "autopilot" in data:
            settings.data["autopilot"] = bool(data["autopilot"])
        if "budget_usd" in data:
            settings.data["budget_usd"] = max(0.0, float(data["budget_usd"] or 0))
        settings.save()
        return JSONResponse(settings.public())

    @mcp.custom_route("/api/quit", methods=["POST"])
    async def api_quit(request: Request) -> Response:
        if (bad := guard(request, write=True)):
            return bad

        def bye():
            manager.stop()
            manager.on_stop()
            time.sleep(0.5)
            os._exit(0)
        threading.Thread(target=bye, daemon=True).start()
        return JSONResponse({"ok": True})

    # ------------------------------------------------------------------ rooms (collab.py)
    # The tunnel only ever reaches this separate little server, never the GUI or the MCP endpoint above.

    share = {"servers": {}, "tunnel": None, "links": {}}

    def collab_server(public_port: int | None = None) -> int:
        """The room server: on 127.0.0.1 behind a quick tunnel, or on every network interface at `public_port` for
        a room shared at an address the host controls (LAN, Tailscale, a server). Only the room's API is served
        there, never the GUI or the agents' endpoint."""
        import socket

        import uvicorn

        key = ("0.0.0.0", public_port) if public_port else ("127.0.0.1", None)
        if key in share["servers"]:
            return share["servers"][key][1]
        host = key[0]
        if public_port:
            port = public_port
        else:
            with socket.socket() as sock:
                sock.bind(("127.0.0.1", 0))
                port = sock.getsockname()[1]
        server = uvicorn.Server(uvicorn.Config(collab.host_app(net, net.rooms), host=host, port=port, log_level="warning"))
        threading.Thread(target=server.run, daemon=True, name=f"collab-{port}").start()
        for _ in range(100):
            if server.started:
                break
            if getattr(server, "should_exit", False):
                break
            time.sleep(0.05)
        if not server.started:
            raise NetworkError(f"couldn't open port {port} for the room (is something else using it?)")
        share["servers"][key] = (server, port)
        return port

    def open_room(slug: str, mode: str, address: str = "", port: int = 0, log=print, keep: bool = False) -> str:
        room = net.rooms.get(slug) or collab.Room(net, slug)
        if mode == "address":
            address = address.strip().rstrip("/")
            if not re.match(r"^https?://[^/\s]+$", address):
                raise NetworkError("the address teammates use, like http://192.168.1.20:6481 or https://decomp.example.org")
            collab_server(port or 6481)
            code = room.open_invite(keep=keep, mode="address", address=address, port=port or 6481)
            base = address
        else:
            local = collab_server()
            code = room.open_invite(keep=keep, mode="tunnel")
            if share["tunnel"] is None or share["tunnel"].proc is None or share["tunnel"].proc.poll() is not None:
                log("opening a private tunnel to this computer (Cloudflare quick tunnel, no account needed)")
                share["tunnel"] = collab.Tunnel(local)
                share["tunnel"].start(log)
            base = share["tunnel"].url
        net.rooms[slug] = room
        link = f"{base}/join/{'open' if room.settings['visibility'] == 'public' else code}#room={slug}"
        share["links"][slug] = link
        return link

    # rooms shared at an address of the host's own come back when the app starts, with the same invite
    for p in net.store.all("SELECT slug, config FROM projects"):
        c = json.loads(p["config"]).get("collab") or {}
        if c.get("open") and c.get("mode") == "address" and c.get("address"):
            try:
                open_room(p["slug"], "address", c["address"], int(c.get("port") or 6481), keep=True)
            except (NetworkError, OSError):
                pass

    @mcp.custom_route("/api/collab", methods=["GET"])
    async def api_collab(request: Request) -> Response:
        if (bad := guard(request)):
            return bad
        slug = request.query_params.get("slug", "")
        try:
            info = await call(net._room_info, slug)
        except NetworkError as e:
            return fail(e)
        room = net.rooms.get(slug)
        cfg = (json.loads(net.project_row(slug)["config"]).get("collab") or {}) if slug else {}
        return JSONResponse({"sharing": room is not None, "link": share["links"].get(slug), "room": info,
                             "settings": room.settings if room else {"visibility": cfg.get("visibility", "private"),
                                                                       "approve": bool(cfg.get("approve"))},
                             "pending": room.pending() if room else [],
                             "mode": cfg.get("mode", "tunnel"), "address": cfg.get("address", ""), "port": cfg.get("port", 6481),
                             "was_sharing": bool(cfg.get("open")) and room is None,
                             "addresses": await anyio.to_thread.run_sync(collab.local_addresses),
                             "tunnel_supported": collab.cloudflared_available()})

    @mcp.custom_route("/api/collab/start", methods=["POST"])
    async def api_collab_start(request: Request) -> Response:
        if (bad := guard(request, write=True)):
            return bad
        data = await body(request)
        slug = str(data.get("slug", ""))
        if slug in net.links:
            return fail(ValueError("this project belongs to a friend's room; only its host can invite people"))
        if net.fork_slug(slug):
            return fail(ValueError("rooms are for games started from a ROM. To work on a git project together, share "
                                   "its repository (GitHub, GitLab, your own server)"))
        mode = "address" if data.get("mode") == "address" else "tunnel"

        def work(log):
            room = net.rooms.get(slug) or collab.Room(net, slug)
            room.configure(data.get("visibility"), data.get("approve"))
            return {"link": open_room(slug, mode, str(data.get("address", "")), int(data.get("port") or 6481), log=log,
                                      keep=bool(data.get("keep")))}
        return JSONResponse({"job": jobs.start("share", work)})

    @mcp.custom_route("/api/collab/manage", methods=["POST"])
    async def api_collab_manage(request: Request) -> Response:
        """The host's controls: who's in, public or private, a fresh invite link."""
        if (bad := guard(request, write=True)):
            return bad
        data = await body(request)
        slug, action, name = str(data.get("slug", "")), str(data.get("action", "")), str(data.get("name", ""))
        room = net.rooms.get(slug)
        if room is None:
            return fail(ValueError("this project isn't being shared"))
        try:
            if action == "remove":
                room.remove(name)
            elif action in ("approve", "decline"):
                room.approve(name, action == "approve")
            elif action == "settings":
                room.configure(data.get("visibility"), data.get("approve"))
                c = room.cfg.get("collab") or {}
                share["links"][slug] = open_room(slug, c.get("mode", "tunnel"), c.get("address", ""),
                                                 int(c.get("port") or 6481), keep=True)
            elif action == "rotate":
                c = room.cfg.get("collab") or {}
                share["links"][slug] = open_room(slug, c.get("mode", "tunnel"), c.get("address", ""),
                                                 int(c.get("port") or 6481), keep=False)
            else:
                return fail(ValueError("unknown action"))
        except NetworkError as e:
            return fail(e)
        return JSONResponse({"ok": True, "link": share["links"].get(slug), "members": room.members(),
                             "pending": room.pending(), "settings": room.settings})

    @mcp.custom_route("/api/service", methods=["GET", "POST"])
    async def api_service(request: Request) -> Response:
        """Run decomp.wtf as a service that starts with the computer (keeps a team's room open)."""
        from . import service

        if (bad := guard(request, write=request.method == "POST")):
            return bad
        if request.method == "POST":
            action = str((await body(request)).get("action", ""))
            try:
                msg = await anyio.to_thread.run_sync(service.install if action == "install" else service.remove)
            except (RuntimeError, OSError) as e:
                return fail(e)
            return JSONResponse({"message": msg, "installed": service.installed()})
        return JSONResponse({"installed": await anyio.to_thread.run_sync(service.installed)})

    @mcp.custom_route("/api/collab/stop", methods=["POST"])
    async def api_collab_stop(request: Request) -> Response:
        if (bad := guard(request, write=True)):
            return bad
        slug = str((await body(request)).get("slug", ""))
        room = net.rooms.pop(slug, None)
        if room is not None:
            room.close()
        share["links"].pop(slug, None)
        if not net.rooms and share["tunnel"] is not None:
            share["tunnel"].stop()
            share["tunnel"] = None
        return JSONResponse({"ok": True})

    @mcp.custom_route("/api/collab/join", methods=["POST"])
    async def api_collab_join(request: Request) -> Response:
        if (bad := guard(request, write=True)):
            return bad
        q = request.query_params
        if q.get("confirmed") != "1":
            return fail(ValueError("confirm that this is your own legally obtained copy of the game"))
        data = await request.body()
        link, name = q.get("link", ""), q.get("name", "")

        def work(log):
            slug = collab.join_room(net, link, name, data, log=log)
            settings.data["last_project"] = slug
            settings.save()
            return {"slug": slug}
        return JSONResponse({"job": jobs.start("join", work)})

    @mcp.custom_route("/api/collab/reconnect", methods=["POST"])
    async def api_collab_reconnect(request: Request) -> Response:
        if (bad := guard(request, write=True)):
            return bad
        data = await body(request)
        slug, link = str(data.get("slug", "")), str(data.get("link", ""))
        return JSONResponse({"job": jobs.start("reconnect", lambda log: {"slug": collab.reconnect(net, slug, link, log=log)})})

    @mcp.custom_route("/api/collab/leave", methods=["POST"])
    async def api_collab_leave(request: Request) -> Response:
        if (bad := guard(request, write=True)):
            return bad
        try:
            await call(net.unlink, str((await body(request)).get("slug", "")))
        except NetworkError as e:
            return fail(e)
        return JSONResponse({"ok": True})

    def shutdown_rooms() -> None:
        if share["tunnel"] is not None:
            share["tunnel"].stop()
        for link in list(net.links.values()):
            link.stop()

    manager.on_stop = shutdown_rooms

    security = TransportSecuritySettings(enable_dns_rebinding_protection=True, allowed_hosts=sorted(allowed_hosts),
                                         allowed_origins=[f"http://{h}" for h in sorted(allowed_hosts)])
    return mcp, manager, security
