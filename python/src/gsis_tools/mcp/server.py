"""MCP サーバー(stdio)。ツールの呼び出しを、gmsh 専用の 1 つのスレッドで順に実行する。"""

from __future__ import annotations

import asyncio
import base64
import concurrent.futures
import functools
import json
import logging
import os
import sys
import traceback
from typing import Any

import anyio
import mcp_types as types
from mcp.server import Server
from mcp.server.stdio import stdio_server

from .. import __version__
from .engine import ModelEngine
from .errors import ToolFailure
from .stdio_guard import WireFds, wire_streams
from .tools import INSTRUCTIONS, annotations_for, available_tools, input_schema, normalize
from .workspace import Workspace

logger = logging.getLogger("gsis_tools.mcp")

SERVER_NAME = "gsis-gmsh"


def _json(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"), default=_default)


def _default(o):
    try:
        import numpy as np

        if isinstance(o, np.generic):
            return o.item()
        if isinstance(o, np.ndarray):
            return o.tolist()
    except Exception:  # pragma: no cover
        pass
    if isinstance(o, (set, tuple)):
        return list(o)
    return str(o)


def result_to_mcp(result: dict[str, Any]) -> types.CallToolResult:
    """エンジンの結果(辞書)を MCP の結果にする。画像は ``_image_png`` で渡す。"""
    image = result.pop("_image_png", None) if isinstance(result, dict) else None
    content: list[Any] = [types.TextContent(type="text", text=_json(result))]
    if image is not None:
        content.append(types.ImageContent(type="image", data=base64.b64encode(image).decode("ascii"),
                                          mime_type="image/png"))
    return types.CallToolResult(content=content)


def error_to_mcp(err: dict[str, Any]) -> types.CallToolResult:
    return types.CallToolResult(content=[types.TextContent(type="text", text=_json(err))], is_error=True)


class ToolRunner:
    """エンジンと、gmsh 専用のスレッド。"""

    def __init__(self, engine: ModelEngine, *, allow_python: bool):
        self.engine = engine
        self.allow_python = allow_python
        self.specs = available_tools(allow_python)
        self.names = {s.name for s in self.specs}
        self.executor = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="gmsh")
        engine.arg_normalizer = normalize

    def list_tools(self) -> list[types.Tool]:
        return [
            types.Tool(name=s.name, title=s.title, description=s.description, input_schema=input_schema(s),
                       annotations=types.ToolAnnotations(**annotations_for(s)))
            for s in self.specs
        ]

    def call_sync(self, name: str, arguments: dict[str, Any] | None) -> types.CallToolResult:
        """gmsh のスレッドの中で呼ぶ。"""
        try:
            if name not in self.names:
                if name == "run_python":
                    raise ToolFailure("run_python は無効です(サーバーを --allow-python 付きで起動したときだけ使えます)")
                raise ToolFailure(f"未知のツールです: {name}")
            args = normalize(name, arguments or {})
            res = self.engine.call(name, args)
            return result_to_mcp(res if isinstance(res, dict) else {"result": res})
        except ToolFailure as exc:  # 作業フォルダの制限(PathRejected)もここ
            return error_to_mcp(exc.to_dict())
        except Exception as exc:  # 想定外: 理由を返し、詳細はログへ
            logger.error("ツール %s で想定外のエラー:\n%s", name, traceback.format_exc())
            return error_to_mcp({"error": f"内部エラー({type(exc).__name__}): {exc}",
                                 "hint": "サーバーの標準エラーのログに詳細があります"})

    async def call(self, name: str, arguments: dict[str, Any] | None) -> types.CallToolResult:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self.executor, functools.partial(self.call_sync, name, arguments))

    def close(self, timeout: float = 20.0) -> bool:
        """gmsh を閉じる。gmsh のスレッドが timeout 秒たっても空かなければ False(メッシュ生成の途中など)。"""
        try:
            self.executor.submit(self.engine.close).result(timeout=timeout)
            ok = True
        except Exception:
            ok = False
        self.executor.shutdown(wait=False)
        return ok


def build_server(runner: ToolRunner) -> Server:
    async def on_list_tools(ctx, params) -> types.ListToolsResult:
        return types.ListToolsResult(tools=runner.list_tools())

    async def on_call_tool(ctx, params: types.CallToolRequestParams) -> types.CallToolResult:
        return await runner.call(params.name, params.arguments)

    return Server(
        SERVER_NAME,
        version=__version__,
        title="GSIS-PBC gmsh modeling",
        instructions=INSTRUCTIONS,
        on_list_tools=on_list_tools,
        on_call_tool=on_call_tool,
    )


async def _serve(server: Server, fds: WireFds) -> None:
    stdin, stdout = wire_streams(fds)
    async with stdio_server(stdin=stdin, stdout=stdout) as (r, w):
        await server.run(r, w, server.create_initialization_options())


def serve(args, fds: WireFds) -> int:
    logging.basicConfig(level=getattr(logging, args.log_level), stream=sys.stderr,
                        format="%(levelname)s %(name)s: %(message)s")
    ws = Workspace(args.workspace, args.read_dir)
    logger.info("作業フォルダ: %s", ws.root)
    engine = ModelEngine(ws, allow_python=args.allow_python, threads=args.threads)
    runner = ToolRunner(engine, allow_python=args.allow_python)
    server = build_server(runner)
    try:
        anyio.run(_serve, server, fds)
    except KeyboardInterrupt:  # pragma: no cover
        pass
    finally:
        if not runner.close():  # pragma: no cover - 接続が切れたときに長い処理が残っている
            logger.warning("gmsh の処理が終わらないので、待たずに終了します")
            sys.stderr.flush()
            os._exit(0)
    return 0
