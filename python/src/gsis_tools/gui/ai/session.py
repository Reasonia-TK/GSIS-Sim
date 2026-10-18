"""MCP サーバーとモデルの状態: 起動、手順ごとの自動保存、落ちたときの起動し直しと読み込み、3D 表示の更新。

- 自動保存: モデルを変え得るツール(読むだけでないもの)が成功するたびに、``model_save`` で
  作業フォルダの ``.gsis_gui/autosave/state.json`` に書く(手順の履歴。メッシュは入らない)
- サーバーが落ちたとき・強制停止のとき: プロセスを止めて起動し直し、自動保存を ``model_load`` で読む
  (保存の後に始めた手順の変更は失われる。メッシュは作り直しが要る)
- 起動したとき: 前回の自動保存があれば読み込む(作業フォルダごとに、前回のモデルから続けられる)
- 3D 表示: GUI 専用のツール ``gui_surface`` を呼び、形状とメッシュが変わったときだけ三角形を受け取る
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .mcp_client import McpConnection, McpResult, McpServerDied
from .tooldefs import tools_for_ai
from .types import ToolDef

logger = logging.getLogger("gsis_tools.gui.ai.session")

GUI_DIR = ".gsis_gui"
AUTOSAVE_REL = f"{GUI_DIR}/autosave/state.json"
SERVER_LOG = "mcp_server.log"

Emit = Callable[[str, dict[str, Any]], None]
ConnectionFactory = Callable[[Path, Path], McpConnection]


def default_connection(workspace: Path, log_path: Path) -> McpConnection:
    return McpConnection(workspace, log_path=log_path)


class ModelSession:
    """作業フォルダ 1 つ分の MCP サーバーとモデル。asyncio のループの中で使う。"""

    def __init__(self, workspace: Path, emit: Emit, *, connection_factory: ConnectionFactory | None = None,
                 load_autosave: bool = True):
        self.workspace = Path(workspace)
        self.emit = emit
        self.factory = connection_factory or default_connection
        self.load_autosave = load_autosave
        self.conn: McpConnection | None = None
        self.surface_key: str | None = None
        self.needs_restart = False  # 呼び出しの途中で止めた(サーバーはまだその処理をしている)
        self.read_only: set[str] = set()
        self.restarts = 0
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------ 起動
    @property
    def gui_dir(self) -> Path:
        return self.workspace / GUI_DIR

    @property
    def autosave_path(self) -> Path:
        return self.workspace / AUTOSAVE_REL

    @property
    def alive(self) -> bool:
        return self.conn is not None and self.conn.alive

    async def start(self) -> None:
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.gui_dir.mkdir(parents=True, exist_ok=True)
        self.emit("server", {"state": "starting", "workspace": str(self.workspace)})
        conn = self.factory(self.workspace, self.gui_dir / SERVER_LOG)
        try:
            await conn.start()
        except BaseException as exc:
            self.emit("server", {"state": "error", "message": f"{exc}"})
            raise
        self.conn = conn
        self.read_only = {t.name for t in conn.tools
                          if getattr(getattr(t, "annotations", None), "read_only_hint", False)}
        from .tooldefs import is_gui_only

        listing = [{"name": t.name, "description": t.description or "", "input_schema": t.input_schema,
                    "read_only": t.name in self.read_only} for t in conn.tools if not is_gui_only(t)]
        self.emit("server", {"state": "ready", "name": conn.server_name, "version": conn.server_version,
                             "tools": len(conn.tools), "tool_list": listing, "workspace": str(self.workspace)})
        self.needs_restart = False
        if self.load_autosave and self.autosave_path.is_file():
            r = await self._call_raw("model_load", {"path": AUTOSAVE_REL})
            if r.is_error:
                self.emit("note", {"kind": "warning", "text": f"前回の自動保存を読めませんでした: {_err(r)}"})
            else:
                self.emit("note", {"kind": "info", "text": "作業フォルダの前回の自動保存(モデルの状態)を読み込みました"})
        await self.refresh_surface(force=True)

    async def close(self) -> None:
        conn, self.conn = self.conn, None
        if conn is not None:
            await conn.close()

    async def restart(self, *, restore: bool = True, reason: str = "") -> None:
        """プロセスを止めて起動し直し、自動保存を読み込む。"""
        self.emit("server", {"state": "restarting", "message": reason})
        await self.close()
        self.restarts += 1
        load, self.load_autosave = self.load_autosave, restore
        try:
            await self.start()
        finally:
            self.load_autosave = load

    # ------------------------------------------------------------------ ツール
    def tools_for_ai(self, *, send_images: bool) -> list[ToolDef]:
        return tools_for_ai(self.conn.tools if self.conn else [], send_images=send_images)

    @property
    def instructions(self) -> str | None:
        return self.conn.instructions if self.conn else None

    def is_read_only(self, name: str) -> bool:
        return name in self.read_only

    async def _call_raw(self, name: str, args: dict[str, Any] | None = None, timeout: float | None = None) -> McpResult:
        if self.conn is None or not self.conn.alive:
            raise McpServerDied("MCP サーバーが動いていません")
        try:
            return await self.conn.call(name, args or {}, timeout=timeout)
        except asyncio.CancelledError:
            self.needs_restart = True  # サーバーは処理を続けているので、使う前に起動し直す
            raise

    async def call(self, name: str, args: dict[str, Any] | None = None, timeout: float | None = None) -> McpResult:
        """ツールを呼ぶ(サーバーが動いていなければ起動し直してから)。サーバーが落ちたら McpServerDied。"""
        if self.needs_restart or self.conn is None or not self.conn.alive:
            await self.restart(restore=True, reason="サーバーを起動し直しています")
        return await self._call_raw(name, args, timeout)

    async def after_change(self) -> None:
        """モデルを変え得る手順の後: 自動保存と 3D 表示の更新。"""
        await self.autosave()
        await self.refresh_surface()

    async def autosave(self) -> None:
        try:
            r = await self._call_raw("model_save", {"path": AUTOSAVE_REL}, timeout=120)
            if r.is_error:
                self.emit("note", {"kind": "warning", "text": f"自動保存に失敗しました: {_err(r)}"})
        except McpServerDied:
            pass

    async def refresh_surface(self, *, force: bool = False) -> None:
        args = {} if force or not self.surface_key else {"known": self.surface_key}
        try:
            r = await self._call_raw("gui_surface", args, timeout=300)
        except McpServerDied:
            return
        if r.is_error or not isinstance(r.data, dict):
            self.emit("note", {"kind": "warning", "text": f"3D 表示を更新できませんでした: {_err(r)}"})
            return
        self.surface_key = r.data.get("key")
        self.emit("surface", r.data)

    async def recover(self, reason: str) -> None:
        """強制停止や異常終了の後: 起動し直して自動保存に戻す。"""
        self.emit("note", {"kind": "warning", "text": reason})
        await self.restart(restore=True, reason=reason)


def _err(r: McpResult) -> str:
    if isinstance(r.data, dict) and r.data.get("error"):
        return str(r.data["error"])
    return r.text[:300]
