"""MCP サーバー(``gsis-mcp``)を子プロセスで起動してつなぐ(公式の MCP Python SDK のクライアント)。

- サーバーは GUI と同じ Python で ``python -m gsis_tools.mcp --workspace <作業フォルダ> --gui-tools`` として
  stdio で起動する。OpenCASCADE がブーリアンなどで落ちても、GUI のプロセスは落ちない
- 子プロセスに渡す環境変数は SDK の既定の安全なもの(PATH・TEMP など)だけ。API キーは渡らない
- 接続の文脈(``stdio_client`` と ``ClientSession``)は 1 つのタスク(持ち主)が開いて閉じる(anyio の約束)。
  ツールの呼び出しは別のタスクから行ってよい
- 閉じるとき(止めるときも)は SDK の手順: 標準入力を閉じ、2 秒待ってもプロセスが終わらなければ、
  プロセスの木ごと止める(Windows はジョブオブジェクト)
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger("gsis_tools.gui.ai.mcp")


class McpServerDied(Exception):
    """サーバーとの通信が切れた(プロセスが落ちた、応答がない)。"""


@dataclass
class McpResult:
    text: str  # 応答の文字列(このサーバーでは JSON)
    data: Any  # text を JSON として読んだもの(読めなければ None)
    images: list[bytes] = field(default_factory=list)
    is_error: bool = False


def parse_call_result(res: Any) -> McpResult:
    texts: list[str] = []
    images: list[bytes] = []
    for c in getattr(res, "content", None) or []:
        t = getattr(c, "type", None)
        if t == "text":
            texts.append(c.text)
        elif t == "image":
            try:
                images.append(base64.b64decode(c.data))
            except (ValueError, TypeError):  # pragma: no cover
                pass
    text = "\n".join(texts)
    data = None
    if texts:
        try:
            data = json.loads(texts[0])
        except ValueError:
            data = None
    return McpResult(text=text, data=data, images=images, is_error=bool(getattr(res, "is_error", False)))


def default_server_command(workspace: Path, extra: list[str] | None = None) -> tuple[str, list[str]]:
    return sys.executable, ["-m", "gsis_tools.mcp", "--workspace", str(workspace), "--gui-tools", *(extra or [])]


class McpConnection:
    """1 つのサーバーのプロセスとの接続(asyncio のループの中で使う)。"""

    def __init__(self, workspace: Path, *, command: str | None = None, args: list[str] | None = None,
                 log_path: Path | None = None, env: dict[str, str] | None = None):
        self.workspace = Path(workspace)
        cmd, a = default_server_command(self.workspace)
        self.command = command or cmd
        self.args = list(args) if args is not None else a
        self.log_path = log_path
        self.env = dict(env or {})
        self.env.setdefault("PYTHONIOENCODING", "utf-8")
        self.session: Any = None
        self.tools: list[Any] = []
        self.instructions: str | None = None
        self.server_name = ""
        self.server_version = ""
        self.alive = False
        self._owner: asyncio.Task | None = None
        self._stop: asyncio.Event | None = None

    async def start(self, timeout: float = 180.0) -> None:
        self.workspace.mkdir(parents=True, exist_ok=True)
        loop = asyncio.get_running_loop()
        ready: asyncio.Future = loop.create_future()
        self._stop = asyncio.Event()
        self._owner = asyncio.create_task(self._own(ready), name="gsis-mcp-owner")
        try:
            await asyncio.wait_for(asyncio.shield(ready), timeout)
        except BaseException:
            await self.close()
            raise

    async def _own(self, ready: asyncio.Future) -> None:
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client
        import mcp_types as types

        from ... import __version__

        errlog: Any = None
        try:
            if self.log_path is not None:
                self.log_path.parent.mkdir(parents=True, exist_ok=True)
                errlog = open(self.log_path, "a", encoding="utf-8", errors="replace")
            else:
                errlog = open(os.devnull, "w", encoding="utf-8")
            params = StdioServerParameters(command=self.command, args=self.args, env=self.env,
                                           cwd=str(self.workspace))
            async with stdio_client(params, errlog=errlog) as (r, w):
                async with ClientSession(r, w, client_info=types.Implementation(name="gsis-gui",
                                                                                version=__version__)) as s:
                    init = await s.initialize()
                    tl = await s.list_tools()
                    self.session = s
                    self.tools = list(tl.tools)
                    self.instructions = init.instructions
                    self.server_name = init.server_info.name
                    self.server_version = init.server_info.version
                    self.alive = True
                    if not ready.done():
                        ready.set_result(True)
                    await self._stop.wait()
        except asyncio.CancelledError:
            if not ready.done():
                ready.set_exception(McpServerDied("MCP サーバーの起動を止めました"))
            raise
        except BaseException as exc:  # 起動の失敗・接続の切断
            if not ready.done():
                ready.set_exception(McpServerDied(f"MCP サーバーを起動できませんでした: {type(exc).__name__}: {exc}"))
            else:
                logger.info("MCP サーバーとの接続が終わりました: %s", exc)
        finally:
            self.alive = False
            self.session = None
            if errlog is not None:
                try:
                    errlog.close()
                except Exception:  # pragma: no cover
                    pass

    async def call(self, name: str, args: dict[str, Any] | None = None, timeout: float | None = None) -> McpResult:
        s = self.session
        if s is None or not self.alive:
            raise McpServerDied("MCP サーバーが動いていません")
        try:
            res = await s.call_tool(name, args or {}, read_timeout_seconds=timeout)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.alive = False
            raise McpServerDied(f"MCP サーバーとの通信が切れました({type(exc).__name__}: {exc})") from exc
        return parse_call_result(res)

    async def close(self, timeout: float = 15.0) -> None:
        """閉じる(呼び出しの途中でも止める。SDK がプロセスを終わらせる)。"""
        owner = self._owner
        self._owner = None
        if self._stop is not None:
            self._stop.set()
        if owner is None:
            return
        try:
            await asyncio.wait_for(asyncio.shield(owner), timeout)
        except (asyncio.TimeoutError, TimeoutError):
            owner.cancel()
            try:
                await owner
            except BaseException:
                pass
        except BaseException:
            pass
        self.alive = False
