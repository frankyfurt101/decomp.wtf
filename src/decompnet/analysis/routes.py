"""Authenticated local UI/API for analysis projects, with no matching endpoints."""
from pathlib import Path

import anyio
from starlette.responses import HTMLResponse, JSONResponse

from .engine import AnalysisEngine
from .inputs import InputError


def register(mcp, home, guard, static):
    engine = AnalysisEngine(home / "switch-analysis")

    @mcp.custom_route("/switch", methods=["GET"])
    async def page(request):
        return guard(request) or HTMLResponse(static("switch.html").decode())

    @mcp.custom_route("/api/switch/projects", methods=["GET"])
    async def projects(request):
        if bad := guard(request):
            return bad
        return JSONResponse({"projects": await anyio.to_thread.run_sync(engine.projects),
                             "tools": await anyio.to_thread.run_sync(engine.tool_status)})

    @mcp.custom_route("/api/switch/project", methods=["GET"])
    async def project(request):
        if bad := guard(request):
            return bad
        try:
            return JSONResponse(await anyio.to_thread.run_sync(engine.snapshot, request.query_params.get("id", ""), False))
        except (InputError, OSError, ValueError) as e:
            return JSONResponse({"error": str(e)}, status_code=400)

    @mcp.custom_route("/api/switch/function", methods=["GET"])
    async def function(request):
        if bad := guard(request):
            return bad
        try:
            project = await anyio.to_thread.run_sync(engine.snapshot, request.query_params.get("id", ""))
            address = request.query_params.get("address", "")
            row = next((f for f in project["functions"] if f["address"] == address), None)
            if row is None:
                raise InputError("function is not in this analysis sample")
            return JSONResponse(row)
        except (InputError, OSError, ValueError) as e:
            return JSONResponse({"error": str(e)}, status_code=400)

    @mcp.custom_route("/api/switch/import", methods=["POST"])
    async def import_file(request):
        if bad := guard(request, write=True):
            return bad
        try:
            data = await request.json()
            if not isinstance(data, dict) or data.get("confirmed") is not True:
                raise InputError("confirm that this is your own game or an original test program")
            source, title = data.get("path"), data.get("title", "")
            if not isinstance(source, str) or not source.strip() or not isinstance(title, str):
                raise InputError("provide an executable path and optional title")
            return JSONResponse(await anyio.to_thread.run_sync(engine.import_file, source.strip(), title))
        except (InputError, OSError, ValueError) as e:
            return JSONResponse({"error": str(e)}, status_code=400)

    @mcp.custom_route("/api/switch/start", methods=["POST"])
    async def start(request):
        if bad := guard(request, write=True):
            return bad
        try:
            data = await request.json()
            if not isinstance(data, dict) or not isinstance(data.get("id"), str):
                raise InputError("provide an analysis project id")
            return JSONResponse(await anyio.to_thread.run_sync(engine.start, data["id"], data.get("limit", 64)))
        except (InputError, OSError, ValueError) as e:
            return JSONResponse({"error": str(e)}, status_code=400)

    @mcp.custom_route("/api/switch/cancel", methods=["POST"])
    async def cancel(request):
        if bad := guard(request, write=True):
            return bad
        try:
            data = await request.json()
            if not isinstance(data, dict) or not isinstance(data.get("id"), str):
                raise InputError("provide an analysis project id")
            return JSONResponse(await anyio.to_thread.run_sync(engine.cancel, data["id"]))
        except (InputError, OSError, ValueError) as e:
            return JSONResponse({"error": str(e)}, status_code=400)

    return engine
