"""試験用の小さな MCP サーバー(stdio)。プロセスが落ちたときの GUI の扱いを確かめるのに使う。

    python fake_mcp_server.py --workspace DIR

ツール: ``add``(数を履歴に足す)、``crash``(プロセスをすぐ終わらせる)、``slow``(待つ)、``model_save`` /
``model_load``(履歴をファイルに書く・読む)、``gui_surface``(GUI 専用の印付き)。
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import anyio
import mcp_types as types
from mcp.server import Server
from mcp.server.stdio import stdio_server


def main() -> None:
    ws = Path(sys.argv[sys.argv.index("--workspace") + 1])
    state: dict = {"history": []}

    def ok(d: dict) -> types.CallToolResult:
        return types.CallToolResult(content=[types.TextContent(type="text", text=json.dumps(d))])

    async def on_list_tools(ctx, params) -> types.ListToolsResult:
        tools = [types.Tool(name=n, description=n, input_schema={"type": "object", "properties": {}},
                            annotations=types.ToolAnnotations(read_only_hint=False))
                 for n in ("add", "crash", "slow", "model_save", "model_load")]
        tools.append(types.Tool(name="gui_surface", description="gui", input_schema={"type": "object", "properties": {}},
                                annotations=types.ToolAnnotations(read_only_hint=True),
                                meta={"gsis-pbc/gui-only": True}))
        return types.ListToolsResult(tools=tools)

    async def on_call_tool(ctx, params: types.CallToolRequestParams) -> types.CallToolResult:
        name, args = params.name, dict(params.arguments or {})
        if name == "crash":
            os._exit(3)
        if name == "slow":
            await anyio.sleep(float(args.get("seconds", 30)))
        if name == "add":
            state["history"].append(args.get("x", 1))
        if name == "model_save":
            p = ws / args["path"]
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps(state), encoding="utf-8")
        if name == "model_load":
            state.update(json.loads((ws / args["path"]).read_text(encoding="utf-8")))
        if name == "gui_surface":
            return ok({"key": str(len(state["history"])), "model": "fake", "unit": "mm",
                       "steps": len(state["history"]), "volumes": 0, "source": "geometry", "mesh": {},
                       "patches": [], "faces": []})
        return ok({"ok": True, "history": state["history"], "pid": os.getpid()})

    server = Server("fake-gsis", version="0", instructions="試験用のサーバー", on_list_tools=on_list_tools,
                    on_call_tool=on_call_tool)

    async def run() -> None:
        async with stdio_server() as (r, w):
            await server.run(r, w, server.create_initialization_options())

    anyio.run(run)


if __name__ == "__main__":
    main()
