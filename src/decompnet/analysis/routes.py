"""Authenticated local UI/API for analysis projects, with no matching endpoints."""
import anyio
from starlette.responses import JSONResponse, RedirectResponse

from .engine import AnalysisEngine
from .inputs import InputError
from .reconstruct import ReconstructionEngine


def register(mcp, home, guard, static):
    engine = AnalysisEngine(home / "switch-analysis")
    reconstruction = ReconstructionEngine(engine)
    engine.reconstruction = reconstruction

    @mcp.custom_route("/switch", methods=["GET"])
    async def page(request):
        return guard(request) or RedirectResponse("/#/switch", status_code=303)

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
            slug = request.query_params.get("id", "")
            data = await anyio.to_thread.run_sync(engine.snapshot, slug, False)
            data["ai"] = await anyio.to_thread.run_sync(reconstruction.snapshot, slug)
            return JSONResponse(data)
        except (InputError, OSError, ValueError) as e:
            return JSONResponse({"error": str(e)}, status_code=400)

    @mcp.custom_route("/api/switch/map", methods=["GET"])
    async def catalog(request):
        if bad := guard(request):
            return bad
        try:
            return JSONResponse(await anyio.to_thread.run_sync(engine.catalog, request.query_params.get("id", "")))
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
                raise InputError("function has not been drafted yet")
            row["candidate"] = await anyio.to_thread.run_sync(reconstruction.candidate, request.query_params.get("id", ""), address)
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
            return JSONResponse(await anyio.to_thread.run_sync(engine.start, data["id"], data.get("limit", 500), data.get("continuous", False)))
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

    @mcp.custom_route("/api/switch/agent/config", methods=["GET"])
    async def agent_config(request):
        if bad := guard(request):
            return bad
        return JSONResponse(await anyio.to_thread.run_sync(reconstruction.config))

    @mcp.custom_route("/api/switch/agent/start", methods=["POST"])
    async def agent_start(request):
        if bad := guard(request, write=True):
            return bad
        try:
            data = await request.json()
            if not isinstance(data, dict) or not isinstance(data.get("id"), str):
                raise InputError("provide an analysis project id")
            return JSONResponse(await anyio.to_thread.run_sync(reconstruction.start, data["id"],
                                data.get("limit", 1), data.get("model", ""), data.get("address", ""), data.get("worker", 1)))
        except (InputError, OSError, ValueError) as e:
            return JSONResponse({"error": str(e)}, status_code=400)

    @mcp.custom_route("/api/switch/agent/stop", methods=["POST"])
    async def agent_stop(request):
        if bad := guard(request, write=True):
            return bad
        try:
            data = await request.json()
            if not isinstance(data, dict) or not isinstance(data.get("id"), str):
                raise InputError("provide an analysis project id")
            return JSONResponse(await anyio.to_thread.run_sync(reconstruction.cancel, data["id"], data.get("worker")))
        except (InputError, OSError, ValueError) as e:
            return JSONResponse({"error": str(e)}, status_code=400)

    return engine
